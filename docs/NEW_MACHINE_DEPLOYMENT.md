# 空白机器部署：必填配置与完整启动流程

本文针对新安装机器和全新数据卷。源码、下文的私有环境文件、另行下载的模型包准备好后，
Docker 会创建 PostgreSQL 两个数据库、Redis、MinIO、etcd、Milvus、API、RQ worker、恢复服务、
游客协调器、管理端网页和游客 H5。**不需要预先安装数据库、Node、Rust、FFmpeg 或中文字体**；
这些运行依赖由对应镜像安装。配置生成/检查工具只需要宿主机 Python 3.11+，不用安装 Python 包。

“production 能启动”“管理人员能注册”“游客能微信登录”“能接入摄像头”是不同验收条件。
真实 SMTP、域名/DNS、微信 OAuth、相机地址必须由部署者提供；这些无法用通用示例代替。
真实微信支付仍未接通，本文整套流程采用**工作人员核实收款**，见 [支付接入限制](WECHAT_PAYMENT_INTEGRATION.md)。

## 1. 选择版本

| 场景 | 私有文件 | 使用模板 | 外部账号是否必需 |
| --- | --- | --- | --- |
| Docker local：一台机器验证全部页面和制作流程 | `.env.docker-local` | `.env.docker-local.example` | 不需要 SMTP/微信账号；邮箱验证码使用开发模式，游客用测试邀请码登录，收款人工确认 |
| Docker production：正式服务 | `.env.prod` | `.env.prod.example` | 管理端必须有真实 SMTP 和管理员邮箱；游客微信登录还要微信应用及 HTTPS 域名 |
| 宿主机 local：开发 Python/Vue 代码 | `.env.local` | `.env.sqlite.example`，或已有本机 PostgreSQL 时用 `.env.local.example` | 开发模式不要求 SMTP；需自行安装 Python/Node/FFmpeg 等 |

不要把 `.env.local.example` 直接给 Docker：其中 `127.0.0.1` 表示宿主机；在容器内它只代表当前容器。
Docker 内数据库/队列/存储应分别使用 `postgres`、`redis`、`minio`、`milvus` 服务名。

通用部署使用 `docker-compose.yml`。`docker-compose.local-production.yml` 和
`scripts/run_production.ps1` 是 **Windows + NVIDIA GPU + 本机相机桥接** 的专用入口，
要求驱动、宿主 FFmpeg 和桥接配置，不是空白机器的通用启动命令。

## 2. 空白机器准备

1. 使用 Linux x86-64，或 Windows Docker Desktop 的 Linux 容器引擎；安装 Docker Compose v2.30+。
   本文 CPU 路径不需要显卡。ARM/macOS 原生镜像、跨架构模拟和 GPU 路径需另外验收。
2. 给 Docker 分配足够内存和磁盘。建议从 16 GB 内存、40 GB 可用磁盘起步，录像容量另算；
   这只是单机试运行的资源预算，不是生产容量保证。CPU 可以运行推理，但处理速度受硬件影响。
3. 下载源码并解压到固定目录。以下命令均在**仓库根目录**执行；路径尽量不含空格。
   Windows 宿主机开发安装建议使用短路径，例如 `C:\src\ReID`；深层目录中的 PyTorch 安装
   可能遇到 `WinError 206`。出现该错误时，将开发副本移到短路径后重新创建虚拟环境并安装依赖。
4. 新机器首次构建需能访问容器仓库、Debian、PyPI、npm。完成镜像构建和模型安装后，
   业务所需的三份模型从本地只读挂载，不由本文的脚本自动下载。
   当前统一 Python 锁文件包含 PyTorch 的 CUDA 运行库，即使选择 CPU 也会下载这些依赖；
   首次构建镜像体积较大，请等待构建完成，不要将下载时间误认为启动失败。
5. `docker version`、`docker compose version`、`python --version` 应正常返回。
   Windows 若 Python 命令是 `py -3`，相应替换下面的 `python`。

## 3. 单独下载模型包

模型包由项目发布者另行提供链接；源码仓库不附带权重，也不绑定某个私人下载地址。
建议模型压缩包保留以下目录结构，**解压到源码根目录**，不要额外套一层模型包目录：

```text
FastAPIBackend/yolov8n.pt
model/ViT-MSMT17-60.pth
model/clip_weights/ViT-B-16.pt
```

执行：

```sh
python scripts/check_models.py
```

全部显示 Verified 才继续。长度及 SHA256 以 `model/checkpoints.json` 为准；新模型版本必须同步
更新清单并验证兼容性，不能仅改文件名。Linux 文件应允许容器 UID 10001 读取。
模型缺失时 Compose 会报错，不会自动创建同名空目录。权重不进入镜像或源码包。

容器里的模型配置保持模板值：

| 参数 | Docker 值 | 含义 |
| --- | --- | --- |
| `YOLO_MODEL_PATH` | `/models/yolov8n.pt` | 人物检测模型 |
| `CLIPREID_MODEL_PATH` | `/models/ViT-MSMT17-60.pth` | ReID 模型 |
| `CLIP_CACHE_DIR` | 留空 | 使用已挂载的 `/workspace/model/clip_weights` |
| `MODEL_DEVICE` / `WORKER_MODEL_DEVICE` | `cpu` | 通用新机器路径；不要在普通镜像中直接改成 cuda |
| `EMBEDDING_BACKEND` / `DETECTION_BACKEND` | `clipreid` / `yolo` | 与模型包配套 |
| `EMBEDDING_DIMENSION` | `512` | 不随意修改；变更模型需重建索引 |

## 4. Docker local：不需要外部服务账号

生成新的私有配置，脚本会产生随机数据库密码、MinIO 密码、签名密钥和测试邀请码，**不会覆盖已有文件**：

```sh
python scripts/prepare_deployment.py local
python scripts/check_deployment.py --env-file .env.docker-local
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor config --quiet
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor up -d --build --wait --wait-timeout 900
```

也可以手工复制 `.env.docker-local.example`，但必须自己替换所有 `replace-*` 值。
直接使用生成器时，**local 无需再填写任何外部账号参数**，模型就位后即可启动。

| 参数 | local 必需值 / 操作 |
| --- | --- |
| `APP_ENV` | `development` |
| `SECRET_KEY`、`POSTGRES_PASSWORD`、`MINIO_ROOT_PASSWORD` | 生成器已填随机值；手工配置使用至少 32 字符随机值 |
| `POSTGRES_USER`、`POSTGRES_DB`、`SCENIC_POSTGRES_DB` | 保留模板；两个数据库名字必须不同 |
| `DATABASE_URL`、`SCENIC_DATABASE_URL` | 保留模板的 `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}` 引用，自动跟随密码 |
| `OBJECT_STORAGE_MODE`、`S3_*` | 默认使用内置 MinIO；凭据引用 `MINIO_ROOT_*`，不需购买外部 S3 |
| `REDIS_URL`、`MILVUS_URI` | 保留 `redis://redis:6379/0`、`http://milvus:19530` |
| `EMAIL_CODE_DEV_MODE` | `1`；界面/API 的验证码响应提供 debug_code，仅 local 可用 |
| `VISITOR_AUTH_PROVIDER`、`VISITOR_TEST_INVITE_CODE` | `test`；邀请码已生成，游客登录时输入该值 |
| `VISITOR_COOKIE_SECURE`、`VISITOR_PUBLIC_BASE_URL` | `0`、`http://127.0.0.1:15174`；只在本机 HTTP 测试 |
| `PAYMENT_PROVIDER`、`VISITOR_PAYMENT_PROVIDER` | `manual` / `manual`；不需要微信支付证书 |
| `VISITOR_REQUIRE_STAFF_VERIFICATION` | 保持 `1`，工作人员现场核验后游客才能看匹配画面 |
| `SMTP_*`、`VISITOR_WECHAT_*`、`WECHAT_PAY_*` | local 留空 |

入口：管理端 `http://127.0.0.1:15173/`，游客端 `http://127.0.0.1:15174/visitor/`，
API 健康检查 `http://127.0.0.1:2889/api/v1/readyz`。
游客从景区入口二维码进入具体项目，直接打开根页面并不会创建景区。

端口冲突时改 `MYREID_API_PORT`、`STAFF_WEB_PORT`、`VISITOR_WEB_PORT`；游客端口改变后，
`VISITOR_PUBLIC_BASE_URL` 必须同步；管理端口改变后，将新的管理端来源加入 `REALTIME_ORIGINS`。浏览器管理端使用同源代理，无需改 VITE API 地址。
这些端口默认仅监听服务器回环地址。远程访问可使用 SSH 隧道；不要把本机测试登录暴露到公网。

## 5. Docker production：哪些必须自己填写

```sh
python scripts/prepare_deployment.py production
```

生成器只填写系统能安全生成的密码与令牌。编辑新生成的 `.env.prod`，完成下面两组真实参数。
**管理员邮箱不是默认密码，也不会自动创建账号**：首次在管理端使用该邮箱获取验证码并注册。
核心系统和景区系统账号分开，需要在两个工作台分别注册。

### 5.1 启动并使用管理端所必需

| 参数 | 必填内容 | 不配置的结果 |
| --- | --- | --- |
| `SECRET_KEY`、`METRICS_TOKEN` | 生成器已设置，每项独立随机值，至少 32 字符 | 默认占位符会阻止生产启动 |
| `POSTGRES_PASSWORD`、`MINIO_ROOT_PASSWORD` | 生成器已设置；数据库及 S3 引用会同步 | 密码不一致导致连接失败 |
| `BOOTSTRAP_ADMIN_EMAIL` | 自己能收信的管理员邮箱 | 无法完成首个生产管理员注册 |
| `SMTP_HOST` | 邮件服务商的真实 SMTP 主机名 | 无法发送注册/登录/改密验证码 |
| `SMTP_PORT` | 按服务商填写，例如 587 或 465 | 连接失败 |
| `SMTP_USERNAME`、`SMTP_PASSWORD` | 真实发件账号及 SMTP 授权码/密码 | 邮件认证失败 |
| `SMTP_FROM` | 服务商允许该账号使用的发件地址 | 邮件被拒绝或退信 |
| `SMTP_USE_TLS`、`SMTP_USE_SSL` | 587 通常 `1/0`；465 通常 `0/1`；服从服务商要求 | TLS 模式错误导致发送失败 |
| `SCENIC_ORDER_PRICE_CENTS`、`SCENIC_ORDER_CURRENCY` | 明确售价（分，例如 `990` = ¥9.90）、币种 `CNY` | 生产配置校验失败；新项目游客售价还需后台设置 |
| `APP_ENV` | `production` | 不可用开发开关代替生产配置 |
| `EMAIL_CODE_DEV_MODE`、`ALLOW_LOCAL_JOB_FALLBACK` | 必须 `0/0` | 生产拒绝启动 |
| `VISITOR_COOKIE_SECURE`、`VISITOR_ALLOW_LAN_HTTP` | 必须 `1/0` | 生产拒绝启动 |

`S3_ENDPOINT_URL=http://minio:9000`、`S3_BUCKET=myreid-media-prod` 保留即可使用内置对象存储。
应用会初始化媒体桶。换外部 S3 时同时改 endpoint/bucket/region/access key/secret key，
使用私有桶和所需读写权限；Milvus 仍依赖 Compose 自带 MinIO，不能因此删掉 MinIO 服务。

只需要管理端时可先运行：

```sh
python scripts/check_deployment.py --env-file .env.prod --staff-only
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml --profile staff up -d --build --wait --wait-timeout 900
```

此时管理端绑定回环地址，使用 SSH 隧道或已有 HTTPS 代理访问。

### 5.2 游客整套流程额外必需

| 参数 / 外部设置 | 必填内容 |
| --- | --- |
| `VISITOR_AUTH_PROVIDER` | `wechat`；production 禁止 `test` |
| `VISITOR_WECHAT_APPID`、`VISITOR_WECHAT_SECRET` | 具有对应网页授权能力的微信应用配置，和支付配置是两件事 |
| `VISITOR_PUBLIC_BASE_URL` | 真实游客站点 HTTPS 根地址，例如 `https://visit.example.com`，不带 `/visitor`、fragment 或查询参数 |
| 微信网页授权回调域名 | 按应用平台要求配置游客站点域名；程序回调路径是 `/api/v1/scenic/visitor/auth/callback` |
| `PAYMENT_PROVIDER`、`VISITOR_PAYMENT_PROVIDER` | 保持 `manual/manual`；真实收款在系统外确认 |
| `STAFF_DOMAIN`、`VISITOR_DOMAIN` | 使用下方 HTTPS 覆盖文件时必填：两个不同域名，只填主机名，不含 `https://` 和路径 |
| `ACME_EMAIL` | 使用自动 HTTPS 时必填：证书通知邮箱 |
| `REALTIME_ORIGINS` | 保留需要的现有来源，并追加管理端完整来源，例如 `https://staff.example.com`，否则实时 WebSocket 会被拒绝 |
| `SCENIC_PUBLIC_BASE_URL` | 工作人员订单交付链接使用的 HTTPS 管理端基址，例如 `https://staff.example.com` |

将两个域名的 DNS A/AAAA 指向服务器，开放 TCP 80/443（HTTP/3 可选 UDP 443），确保没有
其他服务占用端口。若使用本仓库 HTTPS 网关，无需先手工准备证书：

```sh
python scripts/check_deployment.py --env-file .env.prod
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.https.yml --profile staff --profile visitor config --quiet
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.https.yml --profile staff --profile visitor up -d --build --wait --wait-timeout 900
```

网关将管理域名转到 `staff-web`，游客域名转到只允许游客 API 的 `visitor-web`。
外网不需开放 2889、15173、15174、5432、6379、9000、19530。
如果已有 HTTPS 代理，省略 `docker-compose.https.yml`，转发管理/游客域名到两个回环端口，
保留 Host 和 WebSocket 升级头。生产游客 Cookie 要求真实 HTTPS，仅把配置写成 https 不会生成证书。
自动签发条件见 [Caddy HTTPS 文档](https://caddyserver.com/docs/automatic-https)。

### 5.3 可以不填的配置

- `WECHAT_PAY_*`、`PAYMENT_PUBLIC_BASE_URL`：人工收款不需要。不要叠加 `docker-compose.wechat.yml`。
- `CUSTOM_*`、`ONNX_*`、`TENSORRT_*`、`GPU_INFERENCE_URL`：使用提供的默认模型时留空。
- `S3_PUBLIC_BASE_URL`：留空，媒体使用短期授权链接。
- `SCENIC_FONT_FILE`、`SCENIC_FONT_FAMILY`、`SCENIC_VIDEO_ENCODER`：Docker 已带中文字体和 FFmpeg，保留模板。
- `CLIP_CACHE_DIR`、`YOLO_CONFIG_DIR`：保留模板，首次部署不必自定义。
- `SCENIC_TRACKING_ENABLED` / `INSTALL_SCENIC_TRACKING`：默认 `0/0` 已能使用基础制作；需要可选精分析时同时设 `1/1` 并重新构建。

## 6. 摄像头与 GPU：有硬件时才配置

无相机也能通过管理端上传图片/视频，使用检索、人工登记、编辑及订单流程。
自动采集必须有可访问的视频源；Docker 不会把浏览器所在电脑的摄像头自动映射给服务器。

```dotenv
CAMERA_SOURCES={"0":"0","entrance":"rtsp://USER:PASSWORD@CAMERA_HOST/live"}
MYREID_CAMERA_SOURCE=entrance
CAMERA_AUTO_START=0
SCENIC_CAMERA_AUTO_START=1
```

界面选择 `entrance`，不要填写密码或 RTSP 地址。启动器默认设备 ID 必须存在于映射中；
示例保留 `0` 并不意味着容器已连接 USB。Linux USB 需要在自己额外的 Compose 覆盖文件中
声明 `devices: [/dev/video0:/dev/video0]`，并将宿主 video 组 GID 加入 `group_add`，允许 UID 10001 访问。
Windows 相机桥接和 GPU 走 [专用部署文档](local-production.md)；该路径还要求兼容的
NVIDIA 驱动及 Docker GPU 支持。仅改变 `MODEL_DEVICE=cuda` 不能使 CPU 镜像获得 GPU 能力。

## 7. 第一次使用的验收顺序

1. 用相同 `--env-file`、`-p`、`-f` 和 profiles 执行 `docker compose ... ps -a`。
   `database-init` 以 exit 0 结束属正常；API、数据库、Redis、MinIO、Milvus 应健康，worker、
   maintenance、visitor-coordinator 应持续运行。首次 CPU 模型加载可能较慢。
2. 管理端 `/api/v1/readyz` 返回 `status=ready`，并且两数据库、模型、Redis 队列和磁盘检查通过。
   仅能打开首页或 `/healthz` 不能证明业务已就绪。
3. local 获取 debug_code 注册；production 必须收到真实邮件。分别注册核心/景区管理员；
   创建项目，上传一份合成或获授权素材，等待入库完成，执行一次检索。
4. 景区后台打开游客自助服务，填写说明、授权文本、价格并保存；使用生成的项目入口二维码。
   local 用邀请码登录，production 必须在微信内完成真实 OAuth 登录。
5. 登记本人照片和授权，工作人员核验本人、账号、票据与照片；采集或准备属于本次游览的素材，
   结束游览，选择片段，确认制作，检查横竖版预览。没有匹配素材不能验证出片效果。
6. 游客提交人工付款请求，具有管理权限的工作人员核实收款并确认，游客下载、领取。
   测试支付成功提示不等于发生了微信扣款。
7. 重启测试环境后确认登记/素材/订单仍在；正式环境上线前完成 [备份恢复演练](BACKUP_RESTORE.md)。

故障时使用 `docker compose ... logs --tail=100 backend worker visitor-coordinator maintenance`。
不要把包含个人数据的日志、完整 `docker compose config` 输出或私有 env 发到公开 issue；
检查配置时使用 `config --quiet`。调整已有数据库密码后不会自动修改卷内用户密码，
已有机器应做凭据迁移，不能靠删除数据卷解决。

## 8. 宿主机 local 开发

此方式不使用上面的 Docker 全栈配置。新建虚拟环境、安装锁定依赖和宿主 FFmpeg/中文字体，
复制 `.env.sqlite.example` 到 `.env.local`，放好三份模型，执行：

```sh
python -m venv FastAPIBackend/venv
# Linux: source FastAPIBackend/venv/bin/activate
# PowerShell: .\FastAPIBackend\venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r FastAPIBackend/requirements.txt
python scripts/check_models.py
python scripts/init_runtime.py --migrate --strict-models
```

使用 `scripts/run_backend.sh` / `scripts/run_backend.ps1` 启动 API。管理端进入 `TauriFrontend`
运行 `npm ci`、`npm run dev`；游客端进入 `VisitorFrontend` 同样运行（需 Node 22）。
另一个激活同一虚拟环境的终端进入 `FastAPIBackend`，运行 `python -m app.workers.visitor`；
需要任务恢复时另开 `python -m app.workers.maintenance`。local 允许本地队列回退，
但多进程/真实 Redis 可靠性验收应使用 Docker 全栈路径。

如要本地游客完整流程，将 `.env.local` 的 `VISITOR_AUTH_PROVIDER=test`、
`VISITOR_PAYMENT_PROVIDER=manual`、`VISITOR_TEST_INVITE_CODE=<自己生成的至少16字符>`、
`VISITOR_COOKIE_SECURE=0`、`VISITOR_PUBLIC_BASE_URL=http://127.0.0.1:5174`。
这些值只用于开发；保持 `APP_ENV=development`、`EMAIL_CODE_DEV_MODE=1`。
配置键全集见 [完整配置参考](CONFIGURATION_REFERENCE.md)。

## 9. 本文能保证与不能替代的验证

模板、生成器和预检负责检查参数完整性、内置服务凭据一致性、模型文件及基本部署约束。
Compose 使用健康依赖等待存储就绪，模型挂载禁止隐式创建目录，原理见
[Docker 启动顺序](https://docs.docker.com/compose/how-tos/startup-order/) 和
[bind 挂载选项](https://docs.docker.com/reference/compose-file/services/#volumes)。
外部 SMTP 投递、真实 DNS/证书签发、微信授权资质、真实摄像头和 GPU 兼容性必须在目标机器验收。
不能把配置预检通过表述为这些外部集成已通过。

### 2026-10-18 实测范围

在 Windows Docker Desktop 的 Linux x86-64 引擎中，以 CPU 模式分别创建两组独立空数据卷，
使用本次源码构建的镜像和另行放置的模型包进行了以下验证：

| 验证 | 结果 |
| --- | --- |
| local / production 全栈首次启动 | 均通过；两库迁移成功，`readyz` 的模型、两数据库、Redis 队列、向量存储和磁盘均就绪 |
| local 核心业务 | 真实注册、创建项目、上传合成图片、RQ 任务完成、Milvus 文本检索、历史查询及 CSV 报告下载通过 |
| local 管理端网页 | 1440×900 Chromium，真实登录后显示项目和已入库素材；无空白页、框架错误层或控制台错误 |
| local 游客网页 | 390×844 Chromium，景区服务启用后邀请码登录成功，Cookie 会话进入照片登记页；游客网关拒绝工作人员 API |
| production 模式隔离 | 两网页可访问；游客运行参数为微信登录/人工收款；测试登录接口返回 404 |
| HTTPS 覆盖文件 | Compose 解析和 Caddy 配置校验通过；未进行真实域名证书签发 |
| 配置工具与 Linux 启动器 | 生成器/预检回归测试通过；8 份示例覆盖全部 110 个 Settings 配置项；JSON、空格及字面 Shell 字符正确保留 |

production 启动测试使用虚拟 SMTP/OAuth 配置，没有向外部邮件或微信服务发起业务操作。
这些测试也没有替代真实摄像头采集、本人照片匹配、完整游客出片和收款验收；部署后仍应执行第 7 节。
