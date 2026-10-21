# 景区游客 H5

独立于 Tauri 运营工作台的移动游客端。使用 Vue 3 + TypeScript + Vite + Vue Router + Pinia + Vant。

以下命令在 `VisitorFrontend/` 执行，Node.js 与 CI 对齐为 22。

```sh
npm ci
npm run dev
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

默认入口 `http://127.0.0.1:5174/visitor/#/sites/<景区ID>`。开发代理优先读取进程环境变量 `MYREID_API_PORT`，其次读取仓库根目录 Vite 环境配置（开发时包含 `.env.local`），未配置时使用 `2889`。连接本机 Docker 生产后端时设置为 `12889`，隔离游客测试后端设置为 `22889`，修改后重启 Vite。Docker 部署通过 Nginx 访问 `backend:2889`。

开发服务器固定使用 `5174`，默认监听 `0.0.0.0`，可通过电脑的局域网 IP 从手机访问；端口不可用时会直接报错，不会自动切换端口。后端 `VISITOR_PUBLIC_BASE_URL` 应与手机实际访问的 origin 一致（如 `http://192.168.5.252:5174`），以保证登录回调和 Origin 校验正常。已有配置使用 `5174` 时无需更换端口；修改该地址后需重启后端。仅本机开发可使用 `npm run dev -- --host 127.0.0.1`。

## 页面与身份

静态资源前缀为 `/visitor/`，路由使用 Hash 模式：

| 路由 | 用途 |
| --- | --- |
| `#/sites/:id` | 景区服务介绍、授权入口 |
| `#/sites/:id/register` | 上传照片、框选本人、登记 |
| `#/visits` | 本人的游览记录 |
| `#/works` | 本人的作品 |
| `#/visits/:id` | 匹配、挑选、编辑、制作、订单及领取 |

游客 API 固定为同源 `/api/v1/scenic/visitor`，使用服务端签发的 HttpOnly Cookie，不读取工作人员 JWT。请求封装使用 `credentials: same-origin`、45 秒超时和 `X-Visitor-Request` 标记；后端仍检查会话归属与写请求 Origin。`VISITOR_PUBLIC_BASE_URL` 填实际网页 origin，OAuth 回调和 API 必须同源。

## 联调前提

局域网普通浏览器演示：后端设置 `APP_ENV=local`、`VISITOR_AUTH_PROVIDER=browser`，HTTP 使用 `VISITOR_COOKIE_SECURE=0`、`VISITOR_ALLOW_LAN_HTTP=1`，`VISITOR_PUBLIC_BASE_URL` 与手机实际访问的 origin 一致。打开 `/visitor/#/sites/<景区ID>` 后直接开始登记，无需微信信息或邀请码。同一浏览器 Cookie 保留身份 30 天；不同浏览器记录互相隔离，清除 Cookie 后无法找回。付款提供方单独配置，人工收款可使用 `VISITOR_PAYMENT_PROVIDER=manual`。正式部署仍使用 `wechat` 登录。

前端开发服务不会启动后端任务。API 完成双库迁移后，还需运行 RQ Worker 和 `python -m app.workers.visitor` 协调器；三者共享数据库、队列和对象缓存。详细启动命令见 [开发指南](../docs/DEVELOPMENT.md)。无公众号或商户号时，可先在仓库根目录运行 `scripts/start-visitor-test.ps1`，使用隔离测试栈。

工作人员先开放景区并设置授权、价格。游客登记后依次结束游览、等待封存和匹配、挑选本人片段、保存横竖版草稿、确认制作、试看、付款和领取。关闭页面不取消制作；创建订单后禁止替换交付版本。登录提供方 `wechat/test/browser` 与付款提供方 `wechat/manual/test` 分别配置，旧订单保留创建时的付款模式。

## 代码与构建

| 位置 | 内容 |
| --- | --- |
| `src/app/` | 应用壳与路由 |
| `src/features/` | 登录会话、登记、游览、视频编辑与订单交互 |
| `src/shared/api.ts` | 游客 API、请求封装与共享类型 |
| `e2e/` | 基于接口夹具的浏览器流程测试 |
| `nginx.conf` | 静态资源与游客 API 同源代理 |
| `Dockerfile` | 构建静态文件并由 Nginx 提供服务 |

`npm run build` 已包含 `vue-tsc --noEmit`，输出到 `dist/`；本项目没有单独的 `typecheck` 脚本。测试覆盖前端交互契约，真实微信授权、支付、摄像头、匹配与 GPU 制作仍需现场联调。

Compose 的 `visitor` profile 启动网页和协调器，默认网页端口 `127.0.0.1:15174`；隔离测试为 `25174`。Nginx 将 `/` 重定向到 `/visitor/`，仅代理游客 API，其他业务路径返回 404。

完整部署、微信配置、自助制作、运营接管和测试边界见 [游客端部署说明](../docs/VISITOR_H5.md)。

手动启动、后端接口检查、电脑/手机验收及全部停止命令见 [游客联调操作手册](../docs/VISITOR_MANUAL_TEST.md)。
