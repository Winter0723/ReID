# 游客 H5 与自助制作

独立目录 `VisitorFrontend` 使用 Vue 3、TypeScript、Vite、Vue Router、Pinia、Vant。`TauriFrontend` 面向工作人员，提供“游客自助服务”管理面板。所有游客 API 位于 `/api/v1/scenic/visitor`。本文对应 `allinone`；配置优先级与完整参数见 [配置参考](CONFIGURATION.md)，状态与模块分工见 [项目总指南](PROJECT_GUIDE.md)。

## 开发启动

```powershell
cd VisitorFrontend
npm ci
npm run dev
```

游客页面地址为 `http://127.0.0.1:5174/visitor/`，API 代理优先读取进程变量 `MYREID_API_PORT`，其次读取仓库根目录 Vite 环境配置（包含 `.env.local`），未配置时为 `2889`。本机 Docker 生产组合使用 `2889`，隔离游客测试组合使用 `22889`。按 [开发指南](DEVELOPMENT.md) 启动后端并完成迁移后，在仓库根目录的另一终端启动协调器：

```powershell
cd FastAPIBackend
.\venv\Scripts\python.exe -m app.workers.visitor
```

RQ Worker 负责双版渲染。协调器每 1 秒扫描未完成请求，以 4 个线程处理不同游览，并轮换提交顺序避免等待请求阻塞后续游览。进程重启从已保存阶段继续，不依赖游客网页轮询。协调器和 API、RQ Worker 必须使用相同的 Scenic 数据库、队列配置并共享对象存储缓存目录，以复用跨进程文件锁。当前支持单现场主机/共享文件系统架构，多区域部署需改为数据库租约。

## 后端配置

在后端使用的环境文件中配置，密钥仅保存在服务端：

```dotenv
VISITOR_WECHAT_APPID=公众号AppID
VISITOR_WECHAT_SECRET=公众号AppSecret
VISITOR_PUBLIC_BASE_URL=https://你的游客域名
VISITOR_COOKIE_SECURE=1
SCENIC_EDITING_ENABLED=1
SCENIC_TRACKING_ENABLED=0
VISITOR_PAYMENT_PROVIDER=manual
VISITOR_INDEX_WAIT_SECONDS=600
```

当前流程使用公众号身份与人工付款确认：游客独立完成登记、匹配、挑选、精剪、制作、试看与领取，只有付款由景区负责人或管理员确认。`VISITOR_PAYMENT_PROVIDER=manual` 无需微信商户号、证书或支付通知配置。保留原有微信/测试订单的交付逻辑，旧订单不会随配置改变支付模式。

| 参数 | 可选值 | 边界 |
| --- | --- | --- |
| `VISITOR_AUTH_PROVIDER` | `wechat`（代码默认）/ `test` / `browser` | `wechat` 和 `browser` 均支持独立浏览器身份，公众号配置就绪后同时支持微信授权；`test` 为固定 A/B 测试身份 |
| `VISITOR_PAYMENT_PROVIDER` | `wechat`（代码默认）/ `manual` / `test` | JSAPI 支付、工作人员确认或联调收银台；与工作人员 `PAYMENT_PROVIDER` 独立 |

真实 JSAPI 支付要求公众号身份和商户 AppID 配置匹配、支付通知及证书齐备；测试身份没有真实 OpenID。完整支付配置见 [配置参考](CONFIGURATION.md)。`VISITOR_PUBLIC_BASE_URL` 只填 origin，不附加 `/visitor/` 或 Hash 路由。

- 网页授权回调：`https://你的游客域名/api/v1/scenic/visitor/auth/callback`
- 景区入园二维码：运营端“游客自助服务”保存配置后生成。

先在运营端开放指定景区，填写实际服务介绍、授权说明、授权版本及视频价格。未开放景区不会返回公开信息。价格在登记时冻结，后续调价不会改变已登记游客的售价。变更授权文案时应同时递增授权版本。

正常请求匹配后进入游客挑选阶段，游客必须确认本人画面并提交制作。无需打开 `VISITOR_AUTO_PUBLISH`，无需工作人员复核或启用人物跟踪；旧参数不控制这一自助流程。候选匹配阈值为 0.72，召回相似度用于候选排序，不代表身份确认。无素材、索引超时和渲染失败仍保留异常状态，游客可以自行重新匹配或重试失败画幅，后台继续提供辅助入口。

## 部署

Compose 的可选 `visitor` profile 启动 `visitor-web` 与 `visitor-coordinator`。例如 Linux 生产部署，准备 `.env.prod`、模型及支付证书后在仓库根目录运行：

```sh
export MYREID_ENV_FILE=.env.prod
docker compose --env-file .env.prod -p myreid-production -f docker-compose.yml -f docker-compose.prod.yml --profile visitor up -d --build
```

`--env-file` 供 Compose 变量替换，`MYREID_ENV_FILE` 选择包括协调器在内的容器环境文件，两者都要匹配。Windows GPU 本机生产使用 `scripts/run_production.ps1 -Build`，脚本已包含 `visitor` profile 和相机桥接；不要与上面的 Linux 组合混用，详见 [部署指南](DEPLOYMENT.md)。

`visitor-web` 默认只监听本机 `127.0.0.1:5174`。通过现有公网 HTTPS 反向代理将独立游客域名转发到该端口。提供的 Nginx 只放行游客静态目录及游客 API；内部运营、摄像头、数据库和存储 API 不通过游客入口暴露。TLS 证书、DNS、公众号域名与商户授权需由实际部署环境配置。

生产必须保留 Secure Cookie，并且前端、OAuth 回调和 API 同源。游客登录 Cookie 为 HttpOnly、SameSite=Lax，数据库只存会话令牌散列，30 天有效，可退出撤销。公开入园二维码只选择景区；访问本人记录、预览、完整视频都要通过游客身份校验。

迁移 `20260929100000_scenic` 引入四张游客表，不改写历史游客、工作人员、成片或订单；它不是当前最新 revision。部署前备份两库及素材，按 [双库运行手册](DATABASE_SPLIT_RUNBOOK.md) 分别升级 `core@head` / `scenic@head`（当前包含 2026-10-12 的 `size_bytes` BIGINT 迁移）。游客归属通过 `visitor_visits.account_id` 绑定；旧 `created_by_username` 继续承载所属项目的业务执行账号，游客动作在审计 `visitor.*` 及明细中记录真实游客账号 ID，不能当作工作人员亲自操作。

## 业务复用与恢复

- 登记复用对象存储、图片校验、YOLO 人物检测；必须上传当天半身/全身照并明确框选本人，所选区域有多人或未检测到人物时拒绝登记。
- 结束游览先持久化出园时间和 `sealing` 状态，再由 API 后台封存当前摄像头录像；接口返回不代表封存已完成。素材进入 `waiting` 后由协调器匹配，复用原向量搜索和片段生成。向量搜索增加入园/出园时间边界，并以素材拍摄时间再次校验完整片段；未知日期和越界片段均排除。上传视频的索引时间也应为真实拍摄时间，只有相对播放时间的旧索引需要重建或人工处理。
- 匹配完成进入 `selecting`；游客仅能预览本人匹配到且在入园至出园范围内的片段，不能访问整段摄像头录像。游客挑选后进入 `editing`，复用现有模板、配乐、素材分析、精剪校验与草稿版本管理，支持横竖版分别排序、裁切、构图、原声与封面。
- 游客确认后提交不可变快照及现有 RQ 横竖版渲染；`rendering` 成功后自动进入 `ready`，不需要后台发布。关闭微信不会取消制作，重开可继续编辑或查看制作进度。
- 协调器重启可恢复等待、匹配及渲染阶段；历史编排请求回到游客挑选。重复提交复用原草稿和不可变快照；草稿修订冲突返回 409，防止覆盖新编辑。双版失败时游客可重试失败画幅；重新匹配保留原入园、出园时间，已有订单禁止替换交付版本。
- 实时录像封存与帧索引在同一事务互斥范围内完成，防止旧媒体引用被下一段录像覆盖。旧竞态引用仅在本地存在唯一且开始时间吻合的封存录像时恢复；S3 缺失封存对象可从该录像重新上传。未知或已移除素材返回可读错误，不再产生未处理的 500。
- 运营端异常队列可打开原“匹配 / 生成短片”，完成复核及双版制作后填写成片 ID 发布到原游客请求。没有订单时可重新自动制作；一旦创建订单禁止替换交付版本。
- 支付复用现有 RSA 签名、通知验签解密、金额币种检查与单调付款状态。游客专用通知入口始终执行真实微信验签，原模拟通知不能解锁游客 JSAPI 订单。浏览器支付回调仅触发服务器查单，不能自行将订单标为已支付。
- 登记键有数据库唯一约束；制作与下单使用跨进程锁；商户订单号在网络下单前提交，超时重试复用同一订单，终止订单交由工作人员处理。
- 完整视频访问仅限本人已支付订单，始终指向购买时的双版快照，按现有交付有效期控制下载；领取确认与支付分开。为微信转外部浏览器下载复用已有短时签名资源链接，签名绑定游览、订单和画幅，只在本人已付订单响应中签发；外部浏览器每次仍校验签名、付款状态和交付有效期，不暴露会话 Cookie 或存储密钥。原工作人员 Native 支付及历史领取链接仍保留。

## 验证命令与现场验收

```powershell
.\FastAPIBackend\venv\Scripts\python.exe -m pytest FastAPIBackend/tests/test_visitor.py -q
cd VisitorFrontend
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

运行后端测试前按 [开发指南](DEVELOPMENT.md) 使用隔离测试环境，不能继承生产数据库或对象目录。浏览器测试使用接口契约夹具覆盖登记、授权、照片选择、结束游览、候选预览与选择、双版编辑、保存重开、制作进度、人工付款确认和下载领取，不代表真实微信 OAuth/付款验收。后端测试独立覆盖身份隔离、幂等、时间边界、支付金额与完整视频授权。

上线前仍需真实公众号/商户验收：iOS 和 Android 微信登录、支付成功/取消/关闭后查单、下载与保存；真实景区多人交错、相似服装、低清画面、无素材、跨日期素材；相机封存与 GPU 渲染并行排队。游客应自行预览并确认本人片段。页面不承诺直接保存到相册，也不显示虚构百分比。

## 视觉资源

首页 `VisitorFrontend/public/images/mountains.png` 由内置 Image Gen 生成。提示词：misty layered Chinese green mountains, traditional pagoda at right, pine branches, soft daylight, white negative space in upper half, green forest lower half, no people/text/UI, portrait 3:4。界面采用白底、深林绿色主按钮、单列留白、真实照片与原生视频；所有文案和控件均为 Vue/CSS。

微信接口参考：[JSAPI 调起支付](https://pay.wechatpay.cn/doc/v3/partner/4012090843)、[公众号网页授权](https://developers.weixin.qq.com/doc/service/guide/h5/auth.html)。

## 游客联调模式（无微信商户号）

在仓库根目录运行 `./scripts/start-visitor-test.ps1`。首次运行从 `.env.visitor-test.example` 生成被 Git 忽略的 `.env.visitor-test`，自动生成会话密钥和测试邀请码；后续运行保留配置。启动会构建真实识别与跟踪依赖，需要现有模型文件、Docker 和足够算力。CPU 可运行但较慢。

独立 Compose 项目固定为 `myreid-visitor-test`，数据库、Redis、MinIO、Milvus、素材缓存和文件锁使用该项目独立网络及命名卷；测试队列为 `visitor-test-media`。不叠加生产 Compose 文件，不连接现有生产数据库。测试后端端口为 `22889`，游客同源入口为 `25174`。原生产配置不变。

1. 运营浏览器版设置 `$env:MYREID_API_PORT="22889"`，清除 `MYREID_API_BASE_URL` / `VITE_MYREID_API_BASE_URL` 显式直连变量后运行 `./scripts/run_tauri_frontend.ps1 -BrowserOnly`，使用同源代理；Tauri 桌面版可显式设置测试 API 地址并运行原脚本，在这套测试库注册工作人员、创建景区，打开“游客自助服务”并设置授权和价格。原运营端的识别、剪辑、队列和订单页面照常使用。
2. 打开 `http://127.0.0.1:25174/visitor/#/sites/<景区ID>`，输入 `.env.visitor-test` 中的邀请码，选择游客 A 或 B。返回景区入口可切换身份；A/B 在后端分别建立会话和记录。同一景区的所有测试人员共享这两个固定身份，请只用于联调数据。
3. 上传真实照片、框选本人、同意授权。通过运营端添加真实相机或测试录像，确保素材拍摄时间处于本次登记至结束游览之间；结束游览后走真实检测、匹配、精剪和双版渲染。匹配后由游客挑选、编排并提交双版制作；无素材时可自行重新匹配，不会伪造成片。
4. 试看后点击购买，测试收银台显示服务端订单金额。支持成功、取消、失败、延迟到账和重复通知。取消保留待支付订单；失败允许重试；延迟固定 15 秒，之后点击“刷新到账结果”，由后端完成状态同步（无浏览器后台定时器，重启后仍可恢复）。成功后可重复发送通知，验证订单和到账时间不重复变化。
5. 成功后验证横竖版完整视频、签名下载及确认领取。切到 B 验证看不到 A 的记录；收藏的 A 记录地址对 B 返回 404。

登录与支付分别由 `VISITOR_AUTH_PROVIDER=wechat|test`、`VISITOR_PAYMENT_PROVIDER=wechat|manual|test` 控制，代码默认均为 `wechat`，隔离测试模板显式使用 `test`，不受运营端 `PAYMENT_PROVIDER=fake_wechat` 隐式影响。有公众号授权时可改为真实登录加测试支付，也可将付款改为人工确认。模拟登录的身份没有真实 OpenID，不能用于真实 JSAPI 扣款。已有订单绑定创建时的支付模式，切换模式不会将测试订单变成真实订单，使用新的游览验收。

测试模式要求 `APP_ENV` 为 development/dev/local/test，且邀请码至少 16 位；production/prod/staging/stage 或未知环境拒绝启动，接口也有运行时检查。测试支付只接受本人已创建的测试订单和后端签发的收银台凭据，不接受客户端金额、OpenID 或任意订单号；微信通知入口拒绝测试订单。模拟状态复用原订单金额校验、幂等到账和交付逻辑，测试动作写入审计。

手机微信验收：将 HTTPS 测试域名反向代理到 `25174`，修改 `VISITOR_PUBLIC_BASE_URL` 为该同源 HTTPS 地址、`VISITOR_COOKIE_SECURE=1`，重新运行启动脚本。真实登录还需配置公众号 AppID/Secret 和授权回调域名。测试页面始终显示联调提示。真实微信扣款、通知验签及商户权限仍需商户配置齐备后另行验收。

停止但保留测试数据：

```powershell
docker compose --env-file .env.visitor-test -p myreid-visitor-test -f docker-compose.yml -f docker-compose.visitor-test.yml --profile visitor down
```

完整命令、后端检查、手机网络/HTTPS 配置和停止步骤见 [游客联调操作手册](VISITOR_MANUAL_TEST.md)。


## 公众号身份 + 后台人工确认（局域网联调）

保留真实公众号 OAuth/OpenID 身份；游客申请后仅创建待确认订单，不调用微信支付。
景区项目负责人或全局管理员在“游客自助服务 → 自助制作请求”核对订单号与金额，点击“确认游客付款”，
二次确认后解锁原有横竖视频下载与领取。项目负责人不需要全局 admin 角色；服务端检查该项目的 manage 权限。
普通工作人员和游客不能确认订单，重复确认不会重复记录支付审计。

录像仍按 10 秒分段。自动分段、游客封存、远端备份各自独立调度；游客封存使用 2 个线程，
备份复用现有发布器并使用独立的 2 个线程，不占用视频渲染队列；素材协调器使用 4 个线程处理不同游览。
每项任务独立创建数据库会话，按请求编号去重并沿用跨进程锁，数据库保留待办记录供重启恢复。
摄像头原始预览继续由采集线程提供，录像切换与媒体引用提交仍短暂互斥，防止跨段引用错误。
素材就绪后立即开始匹配，取消固定 30 秒等待；移动端在封存、整理和匹配阶段显示动态等待条，
制作阶段继续展示横竖版实际进度，付款申请后每 2 秒自动检查确认结果。后台待办每 5 秒自动刷新。

在本机 `.env.prod` 配置（AppSecret 不提交 Git，下列 `192.168.1.50` 替换为实际电脑 IPv4）：

```dotenv
VISITOR_AUTH_PROVIDER=wechat
VISITOR_WECHAT_APPID=你的测试号AppID
VISITOR_WECHAT_SECRET=你的测试号AppSecret
VISITOR_PAYMENT_PROVIDER=manual
VISITOR_PUBLIC_BASE_URL=http://192.168.1.50:5174
VISITOR_ALLOW_LAN_HTTP=1
VISITOR_COOKIE_SECURE=0
```

`VISITOR_ALLOW_LAN_HTTP` 默认关闭，显式开启后仅接受 RFC1918 私有 IPv4 地址，
用于测试号局域网联调；不改变 APP_ENV，不启用模拟身份，也不开放公共 HTTP 登录。
部署公网时关闭此开关，使用 HTTPS 和 Secure Cookie。
微信网页授权回调域填写实际电脑地址及端口（示例 `192.168.1.50:5174`）；回调路径为
`/api/v1/scenic/visitor/auth/callback`。手机、电脑须同网，并能访问微信服务。
IP 变化时需同步修改微信后台和本地配置。

```powershell
# 根目录；重建后端，并启动 worker、游客协调器与网页容器
.\scripts\run_production.ps1 -Build
# Docker 网页与 Vite 都固定在 5174，先停止 Docker 网页再切换开发服务器
docker compose --env-file .env.prod -p myreid-production -f docker-compose.yml -f docker-compose.local-production.yml --profile visitor stop visitor-web
# 另一终端运行局域网游客页面（5174），代理到2889
$env:MYREID_API_PORT="2889"
cd VisitorFrontend
npm run dev -- --host 0.0.0.0
```

手机微信打开 `http://192.168.1.50:5174/visitor/#/sites/实际景区ID`（替换 IP），先授权再登记。
管理端仍用 `TauriFrontend` 中的 `npm run dev` 或 `npm run tauri:dev`。
完整流程：启用景区游客服务 → 授权登记 → 提供同期录像 → 结束游览与匹配 →
游客挑选本人片段 → 编排与保存草稿 → 确认双版制作 → 试看 →
申请付款确认 → 管理员确认 → 游客刷新或等待自动刷新 → 下载并确认领取。
人工付款模式不需要微信支付商户号、支付证书或消息接入 URL/Token。
微信与系统浏览器不共享登录 Cookie。已付款作品提供“打开双版领取页”，
复用订单的随机领取令牌，在游客 API 域下打开独立领取页。微信中点击下载也进入该页；
随后使用右上角“在浏览器打开”，无需再次微信登录，即可分别下载两版并确认领取。
令牌仅在本人已付款、未过期响应中提供；每次领取与资源请求仍验证付款、有效期和不可变交付版本。
领取页只授权此订单作品，不授予游客登录、其他游览或摄像头权限。请勿转发领取链接。
已有微信/测试支付订单保留原支付模式，不能借切换配置改成可人工确认的订单。

停止全部容器及相机使用 `.\scripts\stop_production.ps1`，Vite 用 Ctrl+C。
