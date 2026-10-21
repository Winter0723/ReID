> 开源许可：AGPL-3.0-only，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE.md)。
> 首次公开请使用[审核后源码快照](docs/OPEN_SOURCE_RELEASE.md)，不要直接公开旧 Git 历史。
> [空白机器部署与必填项](docs/NEW_MACHINE_DEPLOYMENT.md) · [完整配置](docs/CONFIGURATION_REFERENCE.md) · [模型获取](model/README.md) · [微信支付限制](docs/WECHAT_PAYMENT_INTEGRATION.md) · [安全报告](SECURITY.md) · [备份恢复](docs/BACKUP_RESTORE.md) · [数据清理](docs/DATA_RETENTION.md)

# MyReID / 行人重识别与景区视频运营系统

MyReID 由 FastAPI 后端、Tauri + Vue 运营工作台和 Vue 游客 H5 组成，覆盖核心 ReID 检索、实时相机入库、景区游览视频精剪、订单与支付交付。

完整源码仓库：`https://github.com/Winter0723/ReID`。当前为私有仓库，需要获授权的 GitHub 账号访问。

```sh
git clone https://github.com/Winter0723/ReID.git
cd ReID
```

首次安装从[空白机器部署指南](docs/NEW_MACHINE_DEPLOYMENT.md)开始。源码不包含模型权重、账号数据库或真实服务配置。

The supported clients are the Tauri/Vue staff workstation and the visitor H5. Historical surfaces are isolated under [`legacy/`](legacy/README.md) and are outside the supported build.

- 文档导航: [docs/README.md](docs/README.md)
- API 文档: [docs/API.md](docs/API.md)
- 配置模板、默认值与加载优先级: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- 景区精剪配置、操作与验收: [docs/SCENIC_EDITING.md](docs/SCENIC_EDITING.md)
- 从零理解、运行与产物定位: [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)
- 开发与部署维护: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- Tauri 前端说明: [TauriFrontend/README.md](TauriFrontend/README.md)
- 游客端说明与联调: [VisitorFrontend/README.md](VisitorFrontend/README.md)、[手动验收](docs/VISITOR_MANUAL_TEST.md)
- 默认后端地址: `http://127.0.0.1:2889`
- 主 API 前缀: `/api/v1`
- 景区业务前缀: `/api/v1/scenic`

## 三端源码结构

仓库保留三端的完整源码和接口契约，便于统一验收，以及后续按目录拆分：

| 目录 | 职责 | 与其他端的边界 |
| --- | --- | --- |
| `FastAPIBackend/` | 认证、ReID、景区业务、剪辑、支付与交付 | 提供 `/api/v1` HTTP / WebSocket 接口 |
| `TauriFrontend/` | Tauri + Vue 管理工作台 | 通过 `src/lib/api/` 调用后端，Pinia 域状态管理业务 |
| `VisitorFrontend/` | 移动游客入口、登记、进度、订单与领取 | 使用游客 API 和独立登录会话 |

共享部署编排保留在 `docker-compose*.yml`，启动和维护工具保留在 `scripts/`。
后续拆分仓库时应先固定 API 契约、环境变量和部署地址，再迁移对应目录；不要将
本地数据库、模型缓存、构建产物或环境密钥纳入拆分提交。

CI 覆盖后端测试、Tauri 前端测试与 Windows 安装包，以及移动端构建、单元和浏览器测试。

## 功能概览

- FastAPI 后端：应用工厂、模块化路由、JWT 鉴权、RBAC、审计、健康检查、Prometheus 指标和运行观测。
- Tauri + Vue + TypeScript 前端：双工作台模式，覆盖核心 ReID 和景区运营业务。
- 用户与权限：密码登录、邮箱验证码登录、access/refresh token 轮换、管理员用户管理、项目成员权限。
- 核心 ReID：图片/视频上传、SHA-256 去重、YOLO 行人检测、CLIP-ReID 向量、文本检索、图片检索、历史与报告导出。
- 实时识别：参考人物框选、USB/RTSP/视频单目标跟踪、一次性 WebSocket 票据和逐帧确认；与持续入库任务分别管理。
- 实时相机：相机项目采集、预览帧、录像落盘、采样索引、中央相机监控服务。
- 独立摄像头网关：多 USB 热插拔、虚拟设备选择与网络 RTSP 配置，其他应用可直接读取，详见 [网关部署](docs/camera-gateway.md)。
- 景区相机随后端自动识别入库；未引用录像默认保留 12 小时，历史游览素材按引用保护，详见 [相机保留策略](docs/camera-retention.md)。
- 景区业务：独立账号域、项目和相机墙、登记匹配、双画幅草稿、配乐、版本冻结、失败画幅重试与订单交付。
- 游客自助：独立登录会话、授权登记、结束游览、挑选本人片段、横竖版编辑与制作、购买和领取；后台协调器在页面关闭后继续推进任务。
- 支付能力：工作人员 Native 支付；游客 JSAPI、人工确认或隔离测试支付；购买版本固定，付款与确认领取分别记录。
- 存储与检索后端：本地对象存储、MinIO/S3、内存向量库、Milvus。
- 任务系统：Redis/RQ worker，开发环境可启用 API 进程本地 fallback。

## 仓库结构

```text
MyReID/
├── FastAPIBackend/
│   ├── main.py                         # FastAPI 兼容入口
│   ├── database.py                     # 核心 ReID 数据模型
│   ├── scenic_database.py              # 景区业务数据模型
│   ├── app/api/app_factory.py          # 应用工厂、静态资源、路由注册
│   ├── app/api/routes/                 # 核心 v1 路由
│   ├── app/api/scenic/                 # 认证、项目、相机、游客、短片、订单、精剪、H5 八组路由
│   ├── app/core/                       # 配置、安全、观测
│   ├── app/services/                   # 存储、模型、相机、支付、向量服务
│   ├── app/workers/                    # 入库、精剪渲染、游客制作协调器
│   ├── migrations/                     # Alembic 迁移
│   ├── requirements.in                 # Python 直接依赖源文件
│   └── requirements.txt                # 带哈希的部署锁文件
├── TauriFrontend/
│   ├── src/lib/api/                    # 前端 HTTP API 封装
│   ├── src/stores/                     # auth/core/scenic/system/realtime 域状态及兼容编排门面
│   ├── src/components/scenic/          # 景区登记、匹配、短片、历史、审计视图
│   ├── src/components/ScenicApp.vue    # 景区工作台壳与导航
│   ├── src-tauri/                      # Tauri 2 / Rust 桌面壳
│   └── package.json
├── VisitorFrontend/                    # 游客 H5，/visitor/ 静态入口与同源游客 API
│   ├── src/features/                   # 登记、游览、选片精剪、订单与领取
│   ├── src/shared/api.ts               # Cookie 会话请求与错误处理
│   ├── nginx.conf                      # 仅代理游客 API
│   └── e2e/                            # 移动浏览器契约测试
├── legacy/                             # Qt/QML 与 pre-v1 API 只读历史归档
├── scripts/                            # Windows/Linux/macOS 启动、冒烟、备份脚本
├── docs/                               # API、开发、部署维护文档
├── docker-compose*.yml                 # Compose 开发、测试、预发、生产拓扑
└── .env*.example                       # 环境变量模板
```

## Windows 快速启动

### 1. 准备环境

建议 Python 3.11、Node.js 22、Git；桌面开发另需 Rust、C++ Build Tools 和 WebView2。视频精剪需要完整 FFmpeg/FFprobe、subtitles/libass 和中文字体。CI 与容器使用 Python 3.11。

不依赖 Docker、PostgreSQL 和 Redis 的轻量开发，在没有现有配置时复制 SQLite 模板：

```powershell
if (!(Test-Path .env.local)) { Copy-Item .env.sqlite.example .env.local }
```

该模板使用：

```text
APP_ENV=development
DATABASE_URL=sqlite:///./reid_system.db
OBJECT_STORAGE_MODE=local
VECTOR_BACKEND=memory
ALLOW_LOCAL_JOB_FALLBACK=1
EMAIL_CODE_DEV_MODE=1
```

运行主链路需要以下 3 个模型文件。源码仓库不附带权重；请按[模型说明](model/README.md)另行取得兼容版本，放置后执行 `python scripts/check_models.py`：

```text
FastAPIBackend/yolov8n.pt
model/ViT-MSMT17-60.pth
model/clip_weights/ViT-B-16.pt
```

### 2. 启动后端

第一次运行显式创建 Python 3.11 环境并按锁文件安装：

```powershell
py -3.11 -m venv FastAPIBackend\venv
.\FastAPIBackend\venv\Scripts\python.exe -m pip install --require-hashes -r FastAPIBackend\requirements.txt
.\scripts\run_backend.cmd
```

后续直接启动：

```powershell
.\scripts\run_backend.cmd
```

后端默认监听：

```text
http://127.0.0.1:2889
```

等终端报告应用启动完成后，检查 `/api/v1/healthz` 和 `/api/v1/readyz`。`SCENIC_CAMERA_AUTO_START=1` 默认会启动已有景区点位；仅研究离线素材时可在配置中关闭。`CAMERA_AUTO_START=0` 不会关闭景区自动采集。

### 3. 启动 Tauri 前端

第一次运行可让脚本安装 npm 依赖：

```powershell
.\scripts\run_tauri_frontend.ps1 -Install
```

后续启动 Tauri 桌面端：

```powershell
.\scripts\run_tauri_frontend.cmd
```

只跑浏览器版 Vite：

```powershell
.\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

覆盖后端地址：

```powershell
$env:MYREID_API_BASE_URL="http://127.0.0.1:2889"
.\scripts\run_tauri_frontend.cmd
```

浏览器版用 `MYREID_API_PORT` 配置同源代理目标，并清除显式 `VITE_MYREID_API_BASE_URL` 直连值。Tauri 打包使用 `npm run tauri:build`，要求正式 HTTPS API origin，详见 [桌面端说明](TauriFrontend/README.md)。

### 4. 启动游客端

已有后端时，在独立终端运行游客制作协调器，再启动游客前端：

```powershell
# 终端 3；使用与 API 相同的环境配置
Set-Location FastAPIBackend
.\venv\Scripts\python.exe -m app.workers.visitor
```

```powershell
# 终端 4；从仓库根目录进入
Set-Location VisitorFrontend
npm ci
npm run dev
```

游客入口是 `http://127.0.0.1:5174/visitor/#/sites/<景区ID>`。先在运营端创建景区并启用“游客自助服务”；真实登录需要公众号配置。无公众号/商户号时可直接运行 `scripts/start-visitor-test.ps1`，它启动独立测试栈，API 为 `22889`、游客入口为 `25174`，完整步骤见 [游客联调手册](docs/VISITOR_MANUAL_TEST.md)。

Redis 可用时还必须启动消费同一队列的 RQ worker；只有 Redis 不可用且允许 fallback 时才会本地执行。协调器不替代渲染 worker。Windows 本机开发可使用 RQ `SpawnWorker`，命令见 [开发指南](docs/DEVELOPMENT.md)。

## Linux/macOS 快速启动

```bash
if [ ! -f .env.local ]; then cp .env.sqlite.example .env.local; fi
python3.11 -m venv FastAPIBackend/venv
FastAPIBackend/venv/bin/python -m pip install --require-hashes -r FastAPIBackend/requirements.txt
./scripts/run_backend.sh
```

另开终端，从仓库根目录启动运营前端：

```bash
cd TauriFrontend
npm ci
npm run dev
```

如果要接近部署拓扑，可启用 PostgreSQL + Redis：

```bash
./scripts/start_postgres.sh
./scripts/run_backend_postgres.sh  # 前台运行
# 另开终端：./scripts/run_worker.sh
```

## API 与前端通信

后端在 `FastAPIBackend/app/api/app_factory.py` 中挂载核心路由和景区路由。核心路由使用 `database.py` 的数据域，景区路由使用 `scenic_database.py` 的独立数据域。

前端 API 封装位于 `TauriFrontend/src/lib/api/`：

- `http.ts`: 请求基础设施、Tauri/browser transport、token 存储、静态资源 URL 规范化。
- `auth.ts`: 核心与景区账号登录、注册、验证码、退出。
- `projects.ts`: 项目、状态、任务、审计、相机、视频预览。
- `media.ts`: 媒体上传、列表、删除、清空。
- `search.ts`: 文本/图片检索、历史、结果详情、报告导出。
- `scenic.ts`: 景区点位墙、游客、匹配、片段、Vlog、订单和支付。
- `system.ts`: 就绪状态、设备发现、指标和观测。

浏览器开发模式默认通过 Vite proxy 访问 `/api`、`/gallery` 和 `/metrics`；`/objects` 仅保留给本地开发兼容，业务响应使用授权或短时签名 API URL。Tauri 运行时通过 `@tauri-apps/plugin-http` 直接访问 `VITE_MYREID_API_BASE_URL` 或默认后端地址。

完整接口表与请求/响应示例见 [docs/API.md](docs/API.md)。

## 环境变量

主要配置定义在 `FastAPIBackend/app/core/config.py`，模板文件包括：

- `.env.sqlite.example`: 无外部服务的本机轻量开发。
- `.env.local.example`: 本机 PostgreSQL + Redis 开发。
- `.env.example`: Compose 默认开发栈。
- `.env.dev.example`: Compose 开发覆盖。
- `.env.staging.example`: 预发环境。
- `.env.prod.example`: 生产环境。
- `.env.visitor-test.example`: 隔离游客联调，启动脚本自动生成邀请码。

常用变量：

| Variable | 默认/示例 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `development` | 运行环境，`production` 会关闭部分开发 fallback。 |
| `DATABASE_URL` | `sqlite:///./reid_system.db` | SQLAlchemy 数据库连接。生产建议 PostgreSQL。 |
| `SCENIC_DATABASE_URL` | `sqlite:///./scenic_system.db` | 景区独立数据域连接；生产必须与核心库分离。 |
| `SECRET_KEY` | 模板为开发占位值 | JWT 与资源签名密钥，预发/生产必须替换为至少 32 字节随机值。 |
| `OBJECT_STORAGE_MODE` | `local` / `minio` / `s3` | 媒体对象存储模式。 |
| `VECTOR_BACKEND` | `memory` / `milvus` | 向量检索后端。 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | RQ 队列与任务状态。 |
| `ALLOW_LOCAL_JOB_FALLBACK` | dev `1`, prod `0` | Redis 不可用时是否允许 API 本地执行任务。 |
| `CAMERA_AUTO_START` | `0` | 是否在服务启动时常驻占用默认相机；默认由项目相机任务按需获取并释放。 |
| `SCENIC_CAMERA_AUTO_START` | `1` | 随 API 启动景区点位入库，独立于中央相机开关。 |
| `CAMERA_RECORDING_RETENTION_HOURS` | `12` | 未引用景区录像的保留窗口，历史引用素材继续保护。 |
| `EMBEDDING_BACKEND` | `clipreid` | 向量模型后端。 |
| `YOLO_MODEL_PATH` | `FastAPIBackend/yolov8n.pt` | 行人检测模型路径。 |
| `CLIPREID_MODEL_PATH` | `model/ViT-MSMT17-60.pth` | CLIP-ReID checkpoint 路径。 |
| `EMAIL_CODE_DEV_MODE` | local `1`, prod `0` | 开发时返回调试验证码。 |
| `PAYMENT_PROVIDER` | dev `fake_wechat`, prod `wechat_native` | 开发环境可点击确认完成模拟支付；生产环境拒绝模拟支付。 |
| `PAYMENT_PUBLIC_BASE_URL` | `https://pay.example.com` | 微信支付通知使用的公网后端地址。 |

完整变量、三种支付模式及各环境端口见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。

## 验证

后端 Python 编译检查：

```powershell
python -m compileall -q FastAPIBackend\app FastAPIBackend\database.py FastAPIBackend\scenic_database.py
```

后端测试（先按 [开发指南](docs/DEVELOPMENT.md) 隔离测试数据库、对象和 gallery 目录）：

```powershell
.\FastAPIBackend\venv\Scripts\python.exe -m pytest FastAPIBackend\tests
.\FastAPIBackend\venv\Scripts\python.exe scripts\check_openapi_docs.py
```

前端类型检查与构建：

```powershell
cd TauriFrontend
npm run test
npm run typecheck
npm run build
```

游客端（从仓库根目录进入）：

```powershell
cd VisitorFrontend
npm run test
npm run build
npx playwright install chromium
npm run test:e2e
```

Linux/macOS 后端汇总脚本（不含两端 npm/Rust 测试）：

```bash
scripts/test_all.sh
```

## 部署建议

推荐生产拓扑：

- FastAPI API 容器。
- RQ worker 容器。
- 游客制作协调器与游客静态网页容器（`visitor` profile）。
- PostgreSQL 16（core 与 scenic 两个独立数据库）。
- Redis 7。
- MinIO 或 S3。
- Milvus。
- Nginx/Caddy/Ingress 反向代理和 TLS。

生产检查要点：

- 替换 `SECRET_KEY`、数据库密码、S3/MinIO 密钥、SMTP 和微信支付密钥。
- 保持 `APP_ENV=production`、`ENABLE_LEGACY_API=0`、`ALLOW_LOCAL_JOB_FALLBACK=0`、`EMAIL_CODE_DEV_MODE=0`。
- 不在生产依赖 SQLite、`VECTOR_BACKEND=memory` 或模拟支付接口。
- PostgreSQL 与对象存储要一起备份。
- 对外暴露 API 时必须放在 HTTPS 后面。

## 运行时数据

以下内容为本地运行产物，不提交 Git：

- `.env`, `.env.local`, `.env.dev`, `.env.staging`, `.env.prod`
- `FastAPIBackend/venv/`
- `FastAPIBackend/gallery/`
- `FastAPIBackend/object_storage/`
- `FastAPIBackend/reid_system.db`
- `FastAPIBackend/scenic_system.db`
- `FastAPIBackend/object_storage/vector_store.json`
- `FastAPIBackend/object_storage/scenic_vector_store.json`
- `*.db`, `*.db-wal`, `*.db-shm`
- 模型权重及推理导出物：`*.pt`、`*.pth`、`*.onnx`、`*.engine`、`*.safetensors`
- `TauriFrontend/node_modules/`
- `TauriFrontend/dist/`
- `TauriFrontend/src-tauri/target/`
- `VisitorFrontend/node_modules/`、`VisitorFrontend/dist/`、两端浏览器测试产物
- `tmp/camera-bridge/`（本机生产摄像头代理的凭据、清单和日志）

默认启动脚本不会主动创建日志文件：后端结构化 JSON 日志、Uvicorn 日志、worker 日志和
Tauri/Vite 开发日志都输出到启动它们的终端。Docker 运行时使用
`docker compose logs` 查看。审计日志则是业务数据，分别保存在 core/scenic 数据库的
`audit_logs` 表中。完整位置表、日志重定向示例和测试/安装包产物见
[docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md#9-文档日志与运行产物在哪里)。

冻结的 Qt/QML 与 pre-v1 API 源码、最后兼容版本和支持策略见 [legacy/README.md](legacy/README.md)。

生产与预发布启动、模型配置、双数据库迁移及备份流程见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。
历史 core/scenic 表审计、备份恢复演练和独立迁移 head 操作见 [docs/DATABASE_SPLIT_RUNBOOK.md](docs/DATABASE_SPLIT_RUNBOOK.md)。
