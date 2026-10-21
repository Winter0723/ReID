# MyReID Tauri Frontend

这是 MyReID 当前主要桌面前端，面向 Windows 平台优先维护，也可在 Tauri 支持的平台上开发运行。它使用 Vue/TypeScript 组织业务界面，通过 FastAPI 后端完成核心 ReID 和景区运营流程。

`allinone` 分支还包含独立的 [游客 H5](../VisitorFrontend/README.md)。本目录面向工作人员，使用 Core / Scenic 两套账号；游客身份和页面由 `VisitorFrontend` 管理。以下命令默认在 `TauriFrontend/` 执行，Node.js 版本与 CI 对齐为 22；桌面开发另需 Rust、Windows C++ 构建工具和 WebView2。

需要从整个工程开始了解、同时查找后端启动方式和所有日志/构建产物时，先看
[`../docs/PROJECT_GUIDE.md`](../docs/PROJECT_GUIDE.md)。

## 技术栈

- Tauri 2
- Vite 6
- Vue 3 + TypeScript
- Pinia
- Tailwind CSS 4
- `@tauri-apps/plugin-http`
- `@lucide/vue`

## 目录说明

```text
TauriFrontend/
├── src/
│   ├── App.vue
│   ├── components/ScenicApp.vue     # 景区工作台壳与导航
│   ├── components/scenic/           # 聚焦的登记、匹配、短片、历史、审计视图
│   ├── lib/api/                     # 后端 API 封装
│   ├── lib/videoPreview.ts          # 视频预览辅助
│   ├── stores/                      # auth/core/scenic/system/realtime 域状态与兼容编排门面
│   └── styles.css
├── src-tauri/
│   ├── capabilities/default.json    # 固定的窗口、保存对话框和文件写入权限
│   ├── capabilities/api-origin.generated.json # 启动包装器生成的 HTTP origin 权限
│   ├── src/lib.rs
│   ├── tauri.conf.json
│   └── tauri.api.generated.conf.json # 启动包装器生成的 CSP 配置
├── vite.config.ts                   # Vite proxy 与 Tauri 构建目标
└── package.json
```

## API 模式

前端维护两个认证域：

- `core`: 核心 ReID 系统，调用 `/api/v1/...`。
- `scenic`: 景区运营系统，调用 `/api/v1/scenic/...`。

token 分别存储在：

```text
myreid.core.access
myreid.core.refresh
myreid.scenic.access
myreid.scenic.refresh
```

浏览器开发模式默认走 Vite proxy。Tauri 运行时使用 `@tauri-apps/plugin-http` 直接请求后端。显式设置 `VITE_MYREID_API_BASE_URL` 时，浏览器也会直连该地址；后端没有启用通用 CORS 中间件，浏览器联调应保留同源代理。

默认后端：

```text
http://127.0.0.1:2889
```

浏览器代理端口通过进程变量 `MYREID_API_PORT` 或仓库根目录 Vite 环境文件配置，未配置时为 `2889`。例如连接本机 Docker 生产后端，在启动 Vite 前设置 `$env:MYREID_API_PORT="12889"`。切换端口后重启 Vite。

Tauri 直连地址可显式设置为：

```powershell
$env:VITE_MYREID_API_BASE_URL="http://127.0.0.1:2889"
```

## Windows 开发

先启动后端：

```powershell
..\scripts\run_backend.cmd
```

安装依赖并启动 Tauri：

```powershell
..\scripts\run_tauri_frontend.ps1 -Install
```

后续启动：

```powershell
..\scripts\run_tauri_frontend.cmd
```

只启动浏览器版 Vite：

```powershell
..\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

脚本会优先使用 `npm.cmd`，避免 Windows PowerShell 中 `npm.ps1` 执行策略限制。脚本也会把 `MYREID_API_BASE_URL` 映射到 `VITE_MYREID_API_BASE_URL`。

## 手动命令

```powershell
npm ci
npm run dev
# 桌面模式与上面的 Vite 二选一；先停止手动启动的 Vite
npm run tauri:dev
```

开发地址为 `http://127.0.0.1:5173`，与 Tauri 的 `devUrl` 保持一致。
运营端固定使用 `5173`，游客端固定使用 `5174`；端口不可用时直接报错，不会自动切换。Windows 上若出现 `EADDRINUSE`，用 `Get-NetTCPConnection -State Listen -LocalPort 5173,5174` 检查占用进程；若出现 `listen EACCES`，再用 `netsh interface ipv4 show excludedportrange protocol=tcp` 和对应的 `ipv6` 命令检查系统保留端口，解决绑定问题后重启。
已有后端若显式配置了 `REALTIME_ORIGINS`，需加入 `http://127.0.0.1:5173` 并重启后端。

## 验证

```powershell
npm test
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

`npm run build` 会先执行 `vue-tsc --noEmit`，再执行 Vite build。

桌面安装包必须指定实际的非本机 HTTPS API origin，然后使用包装器构建：

```powershell
$env:VITE_MYREID_API_BASE_URL="https://api.your-domain.example"
npm run tauri:build -- --bundles nsis
```

`scripts/run-tauri.mjs` 校验 origin，并生成 HTTP 权限及 WebSocket CSP；两个生成文件均被 Git 忽略。使用 `tauri:dev` / `tauri:build` 才会运行该步骤，直接调用 `npm run tauri dev` / `build` 会绕过它。浏览器 E2E 使用接口夹具，不代表真实后端、摄像头或桌面安装包验收。

默认不会生成持久前端日志：Vite/Tauri/Rust 信息输出到启动终端，Vue 运行错误在
browser/WebView DevTools console。静态文件输出到 `dist/`，桌面编译与安装包输出到
`src-tauri/target/`；完整位置表见项目总指南。

## Vite Proxy

浏览器开发模式下，以下路径会代理到 `http://127.0.0.1:<MYREID_API_PORT>`：

- `/api`
- `/objects`
- `/gallery`
- `/metrics`

`/api` 代理也支持 WebSocket。Tauri 运行时不走 proxy，权限由上述包装器生成；实时识别仍需后端 `REALTIME_ORIGINS` 接受当前前端 Origin，见 [实时识别说明](../docs/realtime-recognition.md)。

## 业务工作台

### 统一界面

工作台按设计参考统一为深蓝灰底色、青绿色操作色、文字侧栏和项目顶部导航。
`src/workspace-theme.css` 定义共享颜色、排版、间距与响应式布局，两个工作台继续复用
已有的 Vue 业务视图、Pinia 状态和 API 模块：

- 运营总览：复用景区 dashboard、工作队列、最近游客和相机墙数据。
- 游客登记：相机抓拍 / 上传照片切换，保留游客授权、票号、备注与联系方式。
- 匹配复核：游客选择、候选卡片及复核摘要，保留筛选、人物框选、出园和片段包含操作。
- 短片工作室：可展开的游客选择、双画幅预览、时间线、制作参数、版本比较及订单交付。
- 跨模态检索：左侧查询与入库控制，右侧结果卡片、可展开的查询摘要和原始媒体预览。

默认入口继续保留原有媒体工作区 / 游客登记流程。未提供真实业务数据时使用空状态，
不会显示设计稿中的示例统计或示意视频。桌面建议宽度为 1440，1120 最小窗口使用紧凑布局；
更窄视口折叠为图标侧栏和单列内容，核心浏览器回归见 `e2e/redesign.spec.ts`。

安装包构建必须显式配置正式 HTTPS API origin。CI 中的 `https://api.example.invalid`
仅用于验证编译和打包，不是可连接生产服务的发行地址。

当前界面使用薄工作台壳、聚焦视图和域状态组织。`src/stores/workbench.ts` 保留为兼容编排门面，状态已按 `auth/core/scenic/system/realtime` 拆分，包含：

- 核心 ReID 工作台：项目、媒体、实时相机、检索、历史、任务和观测。
- 景区运营工作台：景区项目、摄像头点位墙、游客登记、片段匹配、Vlog 生成、订单支付与交付。
- 游客自助服务：开放景区、配置授权与价格、生成入口二维码、处理异常制作请求及人工付款确认。
- 实时轮询：轻量相机墙/预览状态与较低频率的任务、媒体、游客刷新。

关闭桌面或浏览器窗口不会停止后端和摄像头。本机生产部署使用根目录 `scripts/stop_production.ps1` 统一释放服务，见 [本机生产指南](../docs/local-production.md)。
