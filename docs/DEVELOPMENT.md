# MyReID Development and Operations Guide / 开发与运维维护指南

本文档补充 README 中不适合展开的开发、运行、验证、部署和数据维护细节。

维护范围为 `allinone` 的后端、运营工作台和游客 H5。全局结构见 [项目总指南](PROJECT_GUIDE.md)，配置默认值、生产约束及三端端口见 [配置参考](CONFIGURATION.md)。以下相对命令默认从仓库根目录执行；标出 `cd` 的命令块从该目录继续。

## 1. 本地环境

无 PostgreSQL/Redis 依赖的轻量模式复制：

```powershell
if (!(Test-Path .env.local)) { Copy-Item .env.sqlite.example .env.local }
```

需要本机 PostgreSQL + Redis 时改用 `Copy-Item .env.local.example .env.local`。

Windows 后端脚本会读取以下文件，已有进程环境变量优先生效：

```text
.env
FastAPIBackend/.env
.env.local
FastAPIBackend/.env.local
```

轻量开发推荐：

```text
APP_ENV=development
DATABASE_URL=sqlite:///./reid_system.db
SCENIC_DATABASE_URL=sqlite:///./scenic_system.db
OBJECT_STORAGE_MODE=local
OBJECT_STORAGE_DIR=./object_storage
VECTOR_BACKEND=memory
ALLOW_LOCAL_JOB_FALLBACK=1
EMAIL_CODE_DEV_MODE=1
```

接近部署拓扑的本机开发：

```text
DATABASE_URL=postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid
SCENIC_DATABASE_URL=postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid_scenic
REDIS_URL=redis://127.0.0.1:6379/0
VECTOR_BACKEND=milvus
OBJECT_STORAGE_MODE=minio
```

代码和 CI 以 Python 3.11、Node.js 22 为基线。先按 [模型说明](../model/README.md) 获取三个模型并运行 `python scripts/check_models.py`；视频精剪另需 FFmpeg/FFprobe、H.264/AAC、subtitles/libass 和中文字体。`requirements.txt` 已包含实时 ByteTrack 所需的 `lap==0.5.13`。

## 2. 后端运行

Windows：

```powershell
py -3.11 -m venv FastAPIBackend\venv
.\FastAPIBackend\venv\Scripts\python.exe -m pip install --require-hashes -r FastAPIBackend\requirements.txt
.\scripts\run_backend.cmd
```

启用热重载：

```powershell
$env:MYREID_RELOAD="1"
.\scripts\run_backend.cmd
```

Linux/macOS：

```bash
python3.11 -m venv FastAPIBackend/venv
FastAPIBackend/venv/bin/python -m pip install --require-hashes -r FastAPIBackend/requirements.txt
./scripts/run_backend.sh
# 需要热重载时改用：MYREID_RELOAD=1 ./scripts/run_backend.sh
```

PostgreSQL + Redis 本机模式：

```bash
./scripts/start_postgres.sh
./scripts/run_backend_postgres.sh
# 另开终端：./scripts/run_worker.sh
```

Windows 本机 worker 在另一个终端运行，使用与 API 相同的环境文件/变量，选择支持 Windows 的 `SpawnWorker`：

```powershell
Set-Location FastAPIBackend
.\venv\Scripts\python.exe -m rq worker -w rq.worker.SpawnWorker media --url redis://127.0.0.1:6379/0
```

`media` 和 Redis 地址应与 `RQ_QUEUE_NAME` / `REDIS_URL` 一致。Redis 可连接时，任务会入队，API 不因缺少 worker 自动切换本地执行。API 进程拥有摄像头和实时会话，当前应只启动一个 Uvicorn worker；识别过程中避免热重载。

停止本机 PostgreSQL：

```bash
./scripts/stop_postgres.sh
```

## 3. 前端运行

Windows Tauri：

```powershell
.\scripts\run_tauri_frontend.ps1 -Install
.\scripts\run_tauri_frontend.cmd
```

浏览器版：

```powershell
.\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

手动运行：

```powershell
cd TauriFrontend
npm ci
npm run dev
# 桌面模式用 npm run tauri:dev，和上面的 Vite 二选一
```

覆盖后端：

```powershell
$env:MYREID_API_BASE_URL="http://127.0.0.1:2889"
.\scripts\run_tauri_frontend.cmd
```

浏览器模式通过端口变量配置同源代理（以下命令在仓库根目录的新终端执行）：

```powershell
$env:MYREID_API_PORT="12889"
Remove-Item Env:MYREID_API_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:VITE_MYREID_API_BASE_URL -ErrorAction SilentlyContinue
.\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

`VITE_MYREID_API_BASE_URL` 会让浏览器绕过代理；后端未配置通用 CORS。Tauri 需要直连时使用前一组地址变量及 `tauri:dev` 包装入口，自动生成权限。生产安装包使用 `npm run tauri:build` 并显式设置 HTTPS origin。

### 游客 H5

```powershell
# 新终端，从仓库根目录执行
$env:MYREID_API_PORT="2889"
Set-Location VisitorFrontend
npm ci
npm run dev
```

入口为 `http://127.0.0.1:5174/visitor/#/sites/<景区ID>`。还需在后端环境启动 `python -m app.workers.visitor`，并按队列配置运行渲染 worker；仅有前端页面不能推进素材匹配。无微信配置时使用隔离测试栈 `scripts/start-visitor-test.ps1`，详见 [游客手册](VISITOR_MANUAL_TEST.md)。

## 4. 模型与运行时文件

主链路固定使用以下 3 个模型文件：

```text
FastAPIBackend/yolov8n.pt
model/ViT-MSMT17-60.pth
model/clip_weights/ViT-B-16.pt
```

首次 clone 后需另行获取权重，模型更新后执行 `python scripts/check_models.py` 校验；Compose 会分别挂载为 `/models/yolov8n.pt`、`/models/ViT-MSMT17-60.pth` 和 `/workspace/model/clip_weights/ViT-B-16.pt`。

相关变量：

| Variable | 说明 |
| --- | --- |
| `YOLO_MODEL_PATH` | YOLO 行人检测模型路径。 |
| `EMBEDDING_BACKEND` | 当前配置只允许 `clipreid`；源码里的旧 CLIP/预留适配器不属于可切换的受支持配置。 |
| `CLIPREID_MODEL_PATH` | 本地 CLIP-ReID checkpoint。 |
| `MODEL_DEVICE` | `auto`, `cpu`, `cuda`, `mps`。 |
| `EMBEDDING_DIMENSION` | 向量维度，必须与 Milvus collection 一致。 |

本地运行产物：

```text
FastAPIBackend/gallery/
FastAPIBackend/object_storage/
FastAPIBackend/reid_system.db
FastAPIBackend/scenic_system.db
TauriFrontend/dist/
TauriFrontend/src-tauri/target/
VisitorFrontend/dist/
```

## 5. 支付配置

默认不开启真实支付。开发/演示环境可使用：

- `/api/v1/scenic/tourists/{session_id}/orders/simulate-payment`
- `/api/v1/scenic/orders/{order_id}/simulate-payment-result`

这两个接口在 `APP_ENV=production`、`prod` 或 `staging` 时拒绝；真实支付适配器也拒绝生产环境的 fake provider。

微信 Native 支付变量：

| Variable | 说明 |
| --- | --- |
| `PAYMENT_PROVIDER` | 设置为 `wechat_native` 启用真实微信 Native 支付。 |
| `PAYMENT_PUBLIC_BASE_URL` | 后端公网 HTTPS 地址，用于生成 `notify_url`。 |
| `WECHAT_PAY_APPID` | 微信支付 appid。 |
| `WECHAT_PAY_MCHID` | 商户号。 |
| `WECHAT_PAY_API_V3_KEY` | API v3 密钥。 |
| `WECHAT_PAY_MERCHANT_SERIAL_NO` | 商户证书序列号。 |
| `WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH` | 商户私钥 PEM 路径。 |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_ID` | 微信平台公钥 ID 或证书序列。 |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH` | 平台公钥 PEM 路径。 |

`*_PATH` 是应用进程内可读取的路径。通用 prod/staging Compose 将证书挂到 API 的 `/run/secrets/`，`*_FILE` 只供 Compose 选择宿主机来源；本机 GPU 覆盖未配置这两项挂载。不要把宿主机路径写成容器路径。

生产必须使用 HTTPS，并确保支付回调路径可公网访问：

```text
POST /api/v1/scenic/payments/wechat/notify
```

游客登录与支付单独配置：`VISITOR_AUTH_PROVIDER=wechat|test`、`VISITOR_PAYMENT_PROVIDER=wechat|manual|test`。JSAPI 通知入口为 `/api/v1/scenic/visitor/payments/wechat/notify`。人工模式由景区负责人或全局管理员确认游客待付款订单；无关项目、普通操作员和游客无此权限。工作人员创建的普通订单仍要求 Scenic admin 人工确认。

工作人员订单金额来自 `SCENIC_ORDER_PRICE_CENTS` / `SCENIC_ORDER_CURRENCY`；游客订单金额在登记时从景区服务配置冻结。客户端传金额不能改价。详见 [游客业务](VISITOR_H5.md) 和 [API](API.md)。

## 6. 验证命令

测试使用项目虚拟环境。后端测试会重建其测试库并清理测试对象目录；运行前确保终端没有导出业务 `DATABASE_URL`、`SCENIC_DATABASE_URL`、`OBJECT_STORAGE_DIR` 或 `MYREID_TEST_ROOT`。`conftest.py` 对部分变量使用 `setdefault`，不会替换已有业务连接。使用新的测试终端，并将 gallery 指向临时目录：

```powershell
$env:APP_ENV="test"
$env:MODEL_DEVICE="cpu"
$env:GALLERY_DIR=Join-Path $env:TEMP ("myreid-test-gallery-" + [Guid]::NewGuid().ToString("N"))
```

Python 编译检查：

```powershell
.\FastAPIBackend\venv\Scripts\python.exe -m compileall -q FastAPIBackend\app FastAPIBackend\database.py FastAPIBackend\scenic_database.py
```

后端测试：

```powershell
.\FastAPIBackend\venv\Scripts\python.exe scripts\check_explicit_common_imports.py
.\FastAPIBackend\venv\Scripts\python.exe scripts\check_openapi_docs.py
.\FastAPIBackend\venv\Scripts\python.exe -m pytest FastAPIBackend\tests -W error
.\FastAPIBackend\venv\Scripts\python.exe -m unittest discover -s scripts/tests -p test_camera_bridge.py -v
```

前端类型检查：

```powershell
cd TauriFrontend
npm run typecheck
```

前端组件与域状态单测：

```powershell
cd TauriFrontend
npm run test
```

前端构建：

```powershell
cd TauriFrontend
npm run build
```

1120×720 最小窗口与未登录作用域切换回归：

```powershell
cd TauriFrontend
npx playwright install chromium
npm run test:e2e
```

游客端验证（从仓库根目录进入）：

```powershell
cd VisitorFrontend
npm ci
npm run test
npm run build
npx playwright install chromium
npm run test:e2e
```

两端 `build` 都包含 `vue-tsc --noEmit`。浏览器 E2E 主要使用 API 夹具，验证界面交互，不等于真实模型、相机、微信和跨设备下载已验收。

Linux/macOS 后端汇总脚本，不含 npm/Rust 检查：

```bash
scripts/test_all.sh
```

已运行后端上的 HTTP 冒烟：

```bash
MYREID_RUN_HTTP_SMOKE=1 scripts/test_all.sh
python scripts/smoke_business_http.py
```

HTTP 冒烟会创建测试账号、项目和素材，应对隔离环境执行。目标地址通过 `MYREID_BASE_URL` 指定，默认 `http://127.0.0.1:2889/api/v1`。相机硬件验收脚本也会产生业务记录，执行范围见 [摄像头桥接说明](windows-camera-bridge.md)。

Compose e2e：

```bash
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up --build --abort-on-container-exit --exit-code-from e2e e2e
```

### 按变更选择回归

| 修改范围 | 重点检查 |
| --- | --- |
| 路由与 API 文档 | `test_api_docs_contract.py`、`check_openapi_docs.py`、`test_frontend_contract.py` |
| 数据库与迁移 | `test_migrations.py`、`test_migration_logging.py`、数据库拆分审计 |
| 相机 / 录像 | `test_camera_lifecycle.py`、`test_camera_bridge.py`、核心/景区 camera preview 测试 |
| 实时识别 | `test_realtime.py`、运营端 `realtime.test.ts`、`e2e/realtime.spec.ts` |
| 精剪 / 渲染 | `test_scenic_editing*`、`test_scenic_render*`、`test_scenic_worker_reliability.py` |
| 游客闭环 | `test_visitor.py`、游客 Vitest 与 `e2e/visitor.spec.ts`、`e2e/selfservice.spec.ts` |
| 安全 / 资源 / 观测 | `test_security_regressions.py`、`test_asset_delivery.py`、`test_observability_regressions.py` |

CI 包含 Linux 依赖锁校验、Windows 后端/运营前端/Rust/安装包、游客 Linux 构建与浏览器测试、Compose 配置校验。CI 当前的 Compose 作业只检查配置可解析，没有自动执行真实服务栈 E2E，也未覆盖 local-production/visitor-test 两个覆盖组合。

## 7. 部署拓扑

推荐生产拓扑：

```text
Reverse Proxy / TLS
        |
FastAPI backend
        |
        +-- PostgreSQL
        +-- Redis / RQ
        +-- MinIO or S3
        +-- Milvus
        +-- model files mounted read-only
        |
RQ worker
Visitor coordinator + visitor-web（启用游客业务时）
```

生产变量建议：

```text
APP_ENV=production
ENABLE_LEGACY_API=0
ALLOW_LOCAL_JOB_FALLBACK=0
EMAIL_CODE_DEV_MODE=0
OBJECT_STORAGE_MODE=s3
VECTOR_BACKEND=milvus
```

容器启动脚本 `FastAPIBackend/docker/entrypoint.sh` 会创建运行目录、检查生产密钥、提示模型文件状态，并按需执行：

```bash
MYREID_MIGRATION_SCOPE=core python -m alembic -c alembic.ini upgrade core@head
MYREID_MIGRATION_SCOPE=scenic python -m alembic -c alembic.scenic.ini upgrade scenic@head
```

Python 依赖锁必须覆盖 Windows 和生产 Linux。使用 universal 解析保留平台条件，
同时固定所有间接依赖及发布哈希。仅在 Windows 上运行 pip-compile 会漏掉
Linux 的 milvus-lite、uvloop 和 Torch CUDA 依赖，导致 Docker 的 --require-hashes 安装失败。

```bash
python -m pip install uv==0.12.17
cd FastAPIBackend
uv pip compile requirements.in --constraint requirements.txt --python-version 3.11 --universal --generate-hashes --output-file requirements.txt
pip install --dry-run --require-hashes -r requirements.txt
```

`--constraint requirements.txt` 保留已有版本；确需升级时先更新 requirements.in，
再有针对性地调整旧锁中的版本约束。修改后必须在 Linux Python 3.11 中验证解析并构建生产镜像；
CI 的 linux-dependency-lock 作业执行 Linux 解析检查，本地可运行 `./scripts/run_production.ps1 -Build`。
保留 `--require-hashes`，不要使用 `--no-deps` 掩盖缺失依赖。


## 8. 备份与恢复

脚本：

```bash
scripts/backup_restore.sh backup
scripts/backup_restore.sh restore \
  backups/myreid_core_db_YYYYmmdd_HHMMSS.sql.gz \
  backups/myreid_scenic_db_YYYYmmdd_HHMMSS.sql.gz \
  backups/myreid_assets_YYYYmmdd_HHMMSS.tar.gz
```

备份需要同时覆盖：

- core 与 scenic 两个 PostgreSQL 数据库。
- 容器 `/workspace/FastAPIBackend/gallery`。
- 容器 `/workspace/FastAPIBackend/object_storage` 与 S3/MinIO bucket。
- Milvus 集合及其依赖状态；不能把本地缓存归档当成外部对象/向量完整备份。
- 外部保存的模型文件和生产 `.env` 不应直接进入备份包明文流转。

使用自定义 Compose 项目时还需设置 `COMPOSE_PROJECT_NAME`、`MYREID_COMPOSE_ENV_FILE`、`MYREID_COMPOSE_FILES`。脚本不会停止写入，也不会清空目标数据库；恢复只对隔离空库演练。完整边界见 [部署指南](DEPLOYMENT.md)。

## 9. 数据清理注意事项

不要对生产库执行临时清理脚本。若需要清理演示数据，应先明确保留的管理员账号，然后按模型关系清理：

- projects
- media files
- detection targets
- processing jobs
- search tasks/results
- scenic tourist sessions/segments/vlogs/orders
- audit logs
- email codes
- refresh tokens
- local object/gallery files

删除媒体或项目时优先使用现有 API，因为 API 会同步删除对象文件和向量索引。

## 10. Git 维护约定

`allinone` 包含后端、Tauri/Vue 与游客 H5。Qt/QML 与旧 API 已移入 [`legacy/`](../legacy/README.md)，冻结面不参与活跃 CI 或发布。提交前检查 `git status` 和 `git diff --check`，按文件暂存自己的改动，保留已有本地配置及无关文件；不要提交密钥、模型导出物或运行产物。

清理分支前先确认：

```bash
git branch --merged main
git branch -r --merged origin/main
```

只有确认分支已合并且不再需要时，才删除本地和远端非核心分支。

## 11. 项目总览、日志与产物

从零阅读顺序、前后端启动矩阵、请求链路、数据库/对象/向量数据位置、进程日志、
审计日志、在线 API 文档、测试报告和桌面安装包位置统一记录在
[`PROJECT_GUIDE.md`](PROJECT_GUIDE.md)。API/worker/Vite 默认向终端输出；Windows 摄像头桥接会在 `tmp/camera-bridge/` 写日志。
