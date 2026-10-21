# MyReID 从零理解、运行与产物定位指南

本文面向第一次接触仓库的开发者，回答四个问题：这个系统解决什么问题、代码怎样组成、前后端怎样运行、文档/日志/数据/构建产物在哪里。接口逐项定义、生产部署和数据库拆分细节不在此重复，分别链接到专项文档。

## 1. 先建立全局认识

MyReID 是一套行人重识别（Person Re-identification, ReID）与景区视频运营系统。用户可以把图片、视频或实时摄像头画面送入系统，后端检测其中的行人、生成特征向量并建立索引，再用文本或图片查找相似目标。景区工作台在这条能力之上增加游客登记、游玩片段匹配、Vlog 合成、订单支付和成片交付。

`allinone` 维护三个端：`FastAPIBackend/` 后端、`TauriFrontend/` 运营工作台、`VisitorFrontend/` 游客 H5。运营工作台的 Vite 浏览器模式用于开发和 E2E，Tauri 提供桌面包；游客 H5 则独立部署为静态网页。旧 Qt/QML 客户端和 pre-v1 API 已冻结在 `legacy/`，不进入当前构建、路由和 CI。

系统包含两个持久业务域和三类身份边界：

| 域 | API 前缀 | 数据库 | token scope | 用途 |
| --- | --- | --- | --- | --- |
| Core | `/api/v1` | `DATABASE_URL` | 空 scope 或 `core` | 项目、媒体、相机、索引、文本/图片检索。 |
| Scenic | `/api/v1/scenic` | `SCENIC_DATABASE_URL` | `scenic` | 景区点位、游客、片段、Vlog、订单和交付。 |
| Visitor | `/api/v1/scenic/visitor` | 同一 Scenic 库的 `visitor_*` 表 | 独立 Cookie 会话 | 游客自己的登记、选片、制作、购买与领取。 |

Core 与 Scenic 的工作人员账号、令牌和项目互相独立；游客身份与工作人员 JWT 也不互通。开发环境未配置 `BOOTSTRAP_ADMIN_EMAIL` 时，每个工作人员域的第一个注册用户成为该域管理员；预发/生产必须明确指定管理员邮箱。游客不会因为是首个登记用户而成为工作人员。

## 2. 推荐阅读顺序

1. 本文：先理解结构、启动方式、数据流和所有产物位置。
2. [`../README.md`](../README.md)：查看日常最短命令与功能摘要。
3. [`API.md`](API.md)：查看当前 method/path 操作、请求/响应、权限及景区精剪扩展。
4. [`DEVELOPMENT.md`](DEVELOPMENT.md)：查看环境变量、测试和开发维护命令。
5. [`DEPLOYMENT.md`](DEPLOYMENT.md)：查看 Compose、模型挂载、预发/生产部署和备份。
6. [`DATABASE_SPLIT_RUNBOOK.md`](DATABASE_SPLIT_RUNBOOK.md)：处理 core/scenic 历史表审计、迁移和恢复。
7. [`../TauriFrontend/README.md`](../TauriFrontend/README.md)：只关注桌面前端时阅读。
8. [`../legacy/README.md`](../legacy/README.md)：仅在追溯旧实现时阅读。

游客开发与现场联调从 [`VISITOR_H5.md`](VISITOR_H5.md) 和 [`VISITOR_MANUAL_TEST.md`](VISITOR_MANUAL_TEST.md) 开始；全部文档入口见 [文档导航](README.md)，环境变量的默认值与覆盖关系见 [配置参考](CONFIGURATION.md)。

在线 Swagger、ReDoc 和 OpenAPI JSON 由正在运行的后端动态生成，不会自动写入仓库文件：

```text
http://127.0.0.1:2889/docs
http://127.0.0.1:2889/redoc
http://127.0.0.1:2889/openapi.json
```

`python scripts/check_openapi_docs.py` 只检查 [`API.md`](API.md) 与运行时代码是否一致，不会另行生成 Markdown。

## 3. 系统架构与一次请求的路径

```text
Vue view
  -> Pinia domain store / workbench orchestration
  -> src/lib/api/*.ts
  -> browser fetch (Vite proxy) or Tauri HTTP plugin
  -> FastAPI middleware and router
  -> auth/RBAC + service layer
  -> core DB or scenic DB
  -> local/S3 object storage
  -> Redis/RQ worker or local fallback
  -> YOLO + CLIP-ReID
  -> JSON vector store or Milvus
```

后端采用单个 API 进程管理相机、实时会话和录像维护，RQ worker 执行离线索引与渲染。轻量开发模式没有 Redis 时，`ALLOW_LOCAL_JOB_FALLBACK=1` 允许进程内线程执行任务；Redis 可用时任务仍进入 RQ，需有 worker 消费。生产环境禁止本地 fallback。不要直接用多个 Uvicorn workers 扩容相机服务：会话、票据和设备所有权包含进程内状态。

游客业务还需要 `python -m app.workers.visitor` 协调器。它每秒扫描数据库中的待处理游览，以最多 4 个并发任务推进等待、匹配和渲染结果检查；游客选片、编辑和提交由游客 API 完成。API 内独立的 2 线程封存池和 2 线程录像发布池处理录像，避免长时间渲染阻塞这些工作。三个进程共享对象目录及文件锁，现有部署面向单主机/共享文件系统。

前端没有另一个 Node 服务端。Vite 只负责开发服务器和静态构建；实际业务全部调用 FastAPI。Tauri 只是桌面壳，并通过 HTTP、文件保存和对话框插件提供系统能力。

## 4. 仓库组成

```text
MyReID/
├── FastAPIBackend/              # FastAPI、SQLAlchemy、模型、任务和迁移
│   ├── main.py                  # `app` 兼容入口
│   ├── database.py              # core SQLAlchemy 模型与 session
│   ├── scenic_database.py       # scenic SQLAlchemy 模型与 session
│   ├── app/
│   │   ├── api/                 # 应用工厂、鉴权依赖、core/scenic 路由
│   │   ├── core/                # 配置、安全、结构化日志和运行指标
│   │   ├── db/                  # 数据库初始化、迁移和 ORM 公共逻辑
│   │   ├── services/            # 存储、模型、向量、相机、支付等服务
│   │   └── workers/            # tasks.py、scenic_editing.py、visitor.py
│   ├── migrations/              # Alembic 历史、core/scenic 独立 head
│   └── tests/                   # 后端单元、集成、架构和安全回归
├── TauriFrontend/               # 工作人员运营与 ReID 工作台
│   ├── src/App.vue              # Core 工作台壳
│   ├── src/components/          # 通用、Core 和 Scenic 视图
│   ├── src/lib/api/             # 类型化 HTTP 客户端
│   ├── src/stores/              # auth/core/scenic/system/realtime 状态
│   ├── src-tauri/               # Rust 桌面壳、权限、打包配置与图标
│   └── e2e/                     # Playwright 最小窗口回归
├── VisitorFrontend/             # 游客 H5，独立 Cookie 身份与同源代理
│   ├── src/app/                 # 根组件与 hash 路由
│   ├── src/features/            # 登记、游览、视频编辑、订单和领取
│   ├── src/shared/              # HTTP 请求、类型和公共样式
│   └── nginx.conf              # /visitor/ 静态页及游客 API 代理
├── model/                       # CLIP-ReID 代码、词表、许可和权重校验清单
├── scripts/                     # 启动、测试、冒烟、备份和数据库脚本
├── docs/                        # 受版本控制的项目文档
├── legacy/                      # 冻结的 Qt/QML 与 pre-v1 API
├── docker-compose*.yml          # 开发、E2E、预发、生产服务拓扑
├── .env*.example               # 不同运行模式的配置模板
└── .github/workflows/ci.yml     # Windows 客户端与 Compose 契约 CI
```

### 4.1 后端关键文件

| 文件/目录 | 职责 |
| --- | --- |
| `app/api/app_factory.py` | lifespan、请求观测中间件、路由注册和静态目录挂载。 |
| `app/api/routes/` | Core 认证、管理员、项目/媒体/相机、检索、健康与观测路由。 |
| `app/api/scenic/` | `__init__.py` 注册认证、项目、相机、游客、Vlog、订单、精剪、游客 H5 八组路由；`system_routes.py` 保留共享辅助函数，不是路由入口。 |
| `app/core/config.py` | 环境变量读取、路径解析和生产安全校验；配置事实来源。 |
| `app/core/security.py` | 密码哈希、JWT 签发与解析。 |
| `app/core/observability.py` | JSON 日志、request ID、进程内错误与指标聚合。 |
| `app/services/model_runtime.py` | YOLO 与 CLIP-ReID 的加载、检测和 embedding 接口。 |
| `app/services/storage.py` | 本地文件或 S3 兼容对象存储。 |
| `app/services/vector_store.py` | 本地持久化 JSON 向量库或 Milvus。 |
| `app/services/job_manager.py` | Redis/RQ 状态与本地任务 fallback。 |
| `app/services/camera_stream.py` | 摄像头发现、采集、预览、录像和采样索引。 |
| `app/services/camera_lifecycle.py` | 景区点位自动启动、录像引用保护与过期清理。 |
| `app/services/realtime.py` / `person_tracking.py` | 单目标实时会话与 ByteTrack/特征确认。 |
| `app/services/payments.py` | 模拟支付与微信 Native 支付适配。 |
| `app/workers/tasks.py` | 媒体索引、索引重建、相机媒体和 Scenic Vlog 后台任务。 |
| `app/services/scenic_editing.py` / `scenic_snapshot.py` | 素材预检、双版草稿、修订、不可变制作快照。 |
| `app/services/scenic_render.py` / `app/workers/scenic_editing.py` | FFmpeg 渲染、画幅独立状态、锁和重试。 |
| `app/services/visitor_auth.py` / `visitor_production.py` | 游客会话、归属检查和持久化制作状态推进。 |
| `app/db/visitor_models.py` | 游客账号、登录会话、景区服务配置、游览制作请求四张附加表。 |

### 4.2 前端关键文件

| 文件/目录 | 职责 |
| --- | --- |
| `src/main.ts` | 创建 Vue + Pinia 应用。 |
| `src/App.vue` | Core 工作台和 Core/Scenic 模式入口。 |
| `src/components/ScenicApp.vue` | Scenic 工作台导航与页面编排。 |
| `src/components/core/` | Core 账号、项目、历史和审计界面。 |
| `src/components/scenic/` | 登记、监控、匹配、Vlog、历史和审计界面。 |
| `src/stores/auth.ts` | 两个认证域的会话与登录表单状态。 |
| `src/stores/core.ts` / `scenic.ts` | 各业务域自己的项目、媒体、任务和结果状态。 |
| `src/stores/system.ts` | 模式、偏好、ready/metrics/observability 和全局提示。 |
| `src/stores/realtime.ts` | 实时识别会话、WebSocket 帧解码、确认与释放。 |
| `src/stores/workbench.ts` | 兼容视图 API 的跨域编排门面、轮询和竞态保护。 |
| `src/lib/api/http.ts` | base URL、token、自动 refresh、错误、资源 URL 和下载。 |
| `src-tauri/tauri.conf.json` | 窗口、CSP、构建目录和安装包目标。 |

Core 与 Scenic token 分别保存在 WebView/browser 的 `localStorage`：

```text
myreid.core.access
myreid.core.refresh
myreid.scenic.access
myreid.scenic.refresh
```

上述路径属于 `TauriFrontend/`。游客端使用 `VisitorFrontend/src/app/router.ts` 的 `/sites/:id`、`/sites/:id/register`、`/visits`、`/works`、`/visits/:id` hash 路由；`src/shared/api.ts` 固定请求游客 API，并发送同源 Cookie 与 `X-Visitor-Request: 1`。`Visit.vue` 编排进度、支付和领取，`VisitorEditor.vue` 负责横竖草稿。游客会话不写入工作人员 token 键。

## 5. 启动前准备

推荐使用项目 CI 和后端容器采用的版本基线：

- Git。
- Python 3.11；其它 Python 版本应先完成完整测试和真实模型加载验证。
- Node.js 22 与 npm；依赖版本由 `package-lock.json` 锁定。
- Tauri 桌面开发需要 Rust stable、Windows C++ Build Tools 和 WebView2。
- Docker Desktop/Compose 只在 PostgreSQL/Redis 或完整服务栈模式需要。
- 视频基础处理可回退到 `imageio-ffmpeg`；精剪仍需系统 FFmpeg/FFprobe、subtitles/libass 和中文字体，容器已经安装。
- 使用实时采集时需要摄像头权限和可用设备。

源码不附带三个主链路模型。按[模型说明](../model/README.md)取得文件并放置到指定路径，再执行校验：

```powershell
python scripts/check_models.py
```

如果文件内容以 `version https://git-lfs.github.com/spec/` 开头，它仍是 LFS 指针，后端不能启动。三份权重合计接近 0.9 GB，CPU 冷启动和模型驻留还需要额外内存；日志出现“Loading pretrained model”后应继续等待最终的 `startup.ready`。

## 6. 最简单的本地运行：SQLite 模式

这是第一次了解工程时推荐的模式：core/scenic 使用两个 SQLite 文件，对象存储使用本地目录，向量索引持久化为 JSON；Redis 不可用时任务在 API 进程内执行。

### 6.1 Windows

在仓库根目录打开第一个 PowerShell：

```powershell
Copy-Item .env.sqlite.example .env.local
py -3.11 -m venv FastAPIBackend\venv
.\FastAPIBackend\venv\Scripts\python.exe -m pip install --require-hashes -r FastAPIBackend\requirements.txt
.\scripts\run_backend.cmd
```

脚本也支持自动创建环境；指定 Python 3.11 解释器路径时应分两步，第二步使用新 venv：

```powershell
& 'C:\path\to\python311\python.exe' -m venv FastAPIBackend\venv
.\scripts\run_backend.ps1 -Install
```

将示例路径替换成实际解释器。`-Python` 是启动时使用的解释器，不只是 venv 创建工具；不要用 `-Python py` 误把依赖安装到系统 Python。

后端最终地址：

```text
http://127.0.0.1:2889
```

在第二个 PowerShell 启动桌面前端：

```powershell
.\scripts\run_tauri_frontend.ps1 -Install
```

以后依赖不变时：

```powershell
.\scripts\run_backend.cmd
.\scripts\run_tauri_frontend.cmd
```

只需要浏览器界面时，在第二个终端运行：

```powershell
.\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

然后访问 `http://127.0.0.1:5173`。5173 端口是严格固定的，被占用时 Vite 会直接失败。

### 6.2 Linux/macOS

```bash
cp .env.sqlite.example .env.local
python3.11 -m venv FastAPIBackend/venv
FastAPIBackend/venv/bin/python -m pip install --require-hashes -r FastAPIBackend/requirements.txt
./scripts/run_backend.sh
```

另开终端：

```bash
cd TauriFrontend
npm ci
npm run dev
```

需要桌面壳时运行 `npm run tauri:dev`，并先安装对应平台的 Tauri 系统依赖。

### 6.3 如何判断真正启动完成

看到 Uvicorn 进程不代表模型已经 ready。后端 lifespan 顺序是：

1. 检查 `lap`，升级 core/scenic 数据库、补齐默认角色/项目 owner，并修复异常退出遗留的相机任务状态。
2. 按配置启动可选中央相机服务。
3. 加载 CLIP-ReID 与 YOLO。
4. 初始化 core/scenic 向量库。
5. 创建 gallery/object storage 运行目录。
6. 输出 `startup.ready`，启动实时会话维护、录像分段/封存/发布、景区自动采集和过期清理线程，再由 Uvicorn 报告 application startup complete。

另开终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:2889/api/v1/healthz
Invoke-RestMethod http://127.0.0.1:2889/api/v1/readyz
```

`healthz` 只表示进程可响应；`readyz` 报告模型、数据库、队列、向量库与磁盘就绪字段，详细输出契约见 [API.md](API.md)。开发模式没有 Redis 时，队列可能显示 `local`/降级，但因为允许 fallback，核心功能仍可使用。`queue_mode=redis` 只确认 Redis 链路，不证明 worker 正在消费。

## 7. 其它运行模式

### 7.1 本机 API + PostgreSQL/Redis

`.env.local.example` 面向该模式，不是 SQLite 模板。Linux/macOS：

```bash
if [ ! -f .env.local ]; then cp .env.local.example .env.local; fi
./scripts/start_postgres.sh
./scripts/run_backend_postgres.sh
# 另开终端运行 ./scripts/run_worker.sh
```

Windows 可以用 `docker-compose.postgres.yml` 启动 PostgreSQL/Redis，再分别运行后端和 worker：

```powershell
Copy-Item .env.local.example .env.local
docker compose -f docker-compose.postgres.yml up -d postgres redis
.\scripts\run_backend.cmd
```

worker 在另一个 PowerShell 中从 `FastAPIBackend/` 运行：

```powershell
.\venv\Scripts\python.exe -m rq worker -w rq.worker.SpawnWorker media --url redis://127.0.0.1:6379/0
```

### 7.2 完整 Compose 后端栈

该模式运行 API、worker、PostgreSQL、Redis、MinIO、Milvus 和 etcd；运营前端在另一终端单独运行。游客容器需另外开启 `visitor` profile，并配置同一份 `MYREID_ENV_FILE`，见 [部署指南](DEPLOYMENT.md)。

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
.\scripts\run_tauri_frontend.cmd
```

停止服务但保留命名卷：

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

不要随意加 `-v`，因为它会删除 PostgreSQL、MinIO 和 Milvus 数据卷。

### 7.3 覆盖前端 API 地址

本地默认值是 `http://127.0.0.1:2889`。启动脚本支持：

```powershell
$env:MYREID_API_BASE_URL = "http://127.0.0.1:2889"
.\scripts\run_tauri_frontend.cmd
```

也可直接设置构建变量 `VITE_MYREID_API_BASE_URL`。浏览器开发模式未设置该变量时使用 Vite proxy；Tauri 运行时直接请求该 origin。生产桌面构建要求明确的 HTTPS origin，脚本会生成与 origin 精确匹配的 Tauri capability 和 CSP。

## 8. 从界面理解三条主业务链

### 8.1 Core ReID

1. 在 Core 域注册/登录。
2. 创建 `media` 项目，或创建 `camera` 项目并选择摄像头源。
3. 上传图片/视频。API 计算 SHA-256、写对象、写 `MediaFile`/`ProcessingJob`，再派发索引任务。
4. worker/fallback 用 YOLO 检测行人，用 CLIP-ReID 生成特征，保存裁剪图，写 `DetectionTarget` 和向量库。
5. 使用文本或图片发起检索。API 生成查询向量、按 project/filter 搜索、写 `SearchTask`/`SearchResult`。
6. 在结果、历史、媒体预览或 CSV 报告中查看/下载输出。

实时相机项目会持续更新 `gallery/.../latest.jpg` 供低延迟预览，并把录像、裁剪和索引写入对象存储与数据库。基础系统通过轻量状态接口刷新预览，帧轮询独立于项目统计和“自动刷新”开关，慢统计请求不会冻结画面。

景区相机默认随后端自动入库，不必打开工作台。自动启动失败会重试，成功启动后手动停止的点位不会被该自动循环再次启动；API 重启会重新评估。工作台进入项目时也可发现和初始化点位。`SCENIC_CAMERA_AUTO_START=0` 只关闭后端自动策略，手动入口仍可用。引用保护与清理见 [录像保留](camera-retention.md)。

### 8.2 Scenic 景区业务

1. 切换到 Scenic 域并单独注册/登录。
2. 创建景区项目，发现或配置摄像头点位。
3. 登记游客门票、授权和入园图片。
4. 用入园图在景区向量库匹配游玩片段，人工选择/排序片段。
5. 创建 Vlog 任务，worker 用 FFmpeg 输出试看版与完整版。
6. 创建订单，通过人工/开发模拟/微信 Native 支付更新状态。
7. 交付 token 页面在付款前提供受限预览，付款后允许完整版交付。

### 8.3 Visitor 自助业务

1. 工作人员启用景区游客服务，设置价格、授权文案及版本，生成入园入口。
2. 游客以微信或隔离测试身份登录，上传照片、框选本人并确认授权；服务端冻结本次价格与授权。
3. 结束游览保存出园时间，立即返回 `sealing`，API 后台封存录像，协调器等待素材并匹配。
4. 游客在 `selecting` 挑选本人片段，进入 `editing` 保存双版草稿，显式提交后进入 `rendering`。
5. 两版成功后为 `ready`，可试看、申请人工确认或使用配置的微信/测试支付；付款后下载购买版本并主动确认领取。

| 状态 | 推进者 / 处理方式 |
| --- | --- |
| `visiting` | 等游客结束游览。 |
| `sealing` | API 的录像维护线程；重启后读取持久化请求继续封存。 |
| `waiting` / `matching` | 游客协调器等待相关入库任务并匹配；无素材、等待超时进入 `review`。 |
| `selecting` / `editing` | 等游客确认片段、保存并提交；普通自助流程不需要工作人员发布。 |
| `rendering` | RQ/fallback 渲染，协调器检查双版产物；失败或部分成功进入 `review`。 |
| `ready` | 可以购买和领取；实际下载仍检查订单状态、有效期和固定版本。 |
| `review` | 显示可处理原因；无订单时可重匹配或重试失败画幅，工作人员也可辅助恢复。 |

`planning` 是兼容旧请求的阶段，协调器会将其恢复到 `selecting`。关闭页面不取消制作。已经创建订单便不能通过重剪、重匹配替换交付版本；付款不等于领取，`claimed_at` 只在主动确认时写入。

## 9. 文档、日志与运行产物在哪里

### 9.1 受版本控制的文档

| 内容 | 位置 |
| --- | --- |
| 项目入口 | `README.md` |
| 本指南 | `docs/PROJECT_GUIDE.md` |
| API 合同 | `docs/API.md` |
| 开发/运维 | `docs/DEVELOPMENT.md` |
| 部署 | `docs/DEPLOYMENT.md` |
| 配置与端口 | `docs/CONFIGURATION.md` |
| 游客端 / 现场联调 | `docs/VISITOR_H5.md`、`docs/VISITOR_MANUAL_TEST.md` |
| 数据库拆分 | `docs/DATABASE_SPLIT_RUNBOOK.md` |
| 前端说明 | `TauriFrontend/README.md` |
| 旧实现说明 | `legacy/README.md`、`legacy/*/README.md` |
| 模型来源与许可 | `model/NOTICE.md`、`model/LICENSE.CLIP-REID` |

Swagger、ReDoc、OpenAPI JSON 只存在于运行中的 HTTP 服务。若确实需要保存 schema，可显式把 `/openapi.json` 的响应重定向到一个自选文件；项目当前没有固定的生成文件路径。

### 9.2 进程日志

API、worker、Vite 默认只向终端输出；Windows 摄像头桥接是例外，会写 `tmp/camera-bridge/agent.log` 和 `supervisor.log`。

| 进程 | 默认去向 | 查看方式 |
| --- | --- | --- |
| FastAPI 应用日志 | 启动终端的 stderr，单行 JSON | 运行 `run_backend.*` 的终端。 |
| Uvicorn/Alembic 日志 | 启动终端 | 同一后端终端。 |
| RQ worker | worker 启动终端 | 运行 `run_worker.sh` 或 `rq worker` 的终端。 |
| 游客协调器 | 独立终端或容器日志 | `docker compose logs -f visitor-coordinator`（使用原部署参数）。 |
| Vite/Tauri/Rust | 前端启动终端 | 运行 `run_tauri_frontend.*` 或 npm 的终端。 |
| Vue/WebView 运行错误 | browser/WebView DevTools console | 开发模式 DevTools。 |
| Compose 服务 | Docker logging driver | `docker compose logs -f backend worker`。 |

需要本地文件时由操作者显式重定向，例如：

```powershell
New-Item -ItemType Directory -Force logs
.\scripts\run_backend.cmd *> logs\backend.log
.\scripts\run_tauri_frontend.cmd *> logs\frontend.log
```

`*.log` 已被 Git 忽略。重定向后终端可能不再实时显示内容；需要同时查看可使用 `Tee-Object`。容器日志物理文件由 Docker logging driver 和 Docker Desktop 管理，不属于本仓库，不应依赖其内部路径。

应用日志包含 UTC 时间、级别、logger、message、event，并在请求中带 `request_id`。每个响应返回 `X-Request-ID`；服务端错误还可能返回 `X-Error-ID`，排障时应一并记录。`LOG_LEVEL` 控制根 logger 级别。

### 9.3 审计、观测和指标不是进程日志文件

- Core 和 Scenic 各自数据库的 `audit_logs` 表保存登录、项目、媒体、检索、游客、订单等业务审计事件；可从工作台或项目 audit API 查看。
- `GET /api/v1/observability` 为 Core 管理员提供数据库、任务、模型、队列、磁盘和进程内错误摘要。
- `GET /api/v1/metrics` 返回当前用户可见范围的业务/运行指标。
- `GET /metrics` 返回 Prometheus 文本；配置了 `METRICS_TOKEN` 时需要 bearer token。
- 请求计数、耗时、错误摘要和模型加载时间保存在 API 进程内存中，重启后清零；它们不是持久日志。

### 9.4 本地数据库、媒体和向量

所有相对后端路径都以 `FastAPIBackend/` 为基准解析，而不是调用命令时的任意目录。

| 产物 | 默认位置 | 说明 |
| --- | --- | --- |
| Core SQLite | `FastAPIBackend/reid_system.db` | 账号、项目、媒体、目标、任务、结果、审计。 |
| Scenic SQLite | `FastAPIBackend/scenic_system.db` | 独立 Scenic 账号和景区业务。 |
| SQLite 辅助文件 | 同目录的 `*.db-wal`、`*.db-shm` | 服务运行时可能存在。 |
| 实时预览 | `FastAPIBackend/gallery/` | `latest.jpg`、头像和低延迟预览。 |
| 本地对象存储 | `FastAPIBackend/object_storage/` | 原始媒体、裁剪、预览、录像、Vlog 和支付二维码。 |
| Core 本地向量 | `FastAPIBackend/object_storage/vector_store.json` | `VECTOR_BACKEND=memory` 仍会落盘以供 API/worker 共享。 |
| Scenic 本地向量 | `FastAPIBackend/object_storage/scenic_vector_store.json` | Scenic 独立 namespace。 |
| 文件锁/暂存 | `object_storage/*.lock`、`object_storage/.staging/` | 并发安全和上传暂存。 |
| 精剪分析 / 任务锁 / 合成配乐 | `object_storage/.scenic_analysis/`、`.scenic_jobs/`、`.scenic_music/` | API、worker、协调器须共享，音乐版本与已提交快照相关。 |
| 相机原始录像 | `object_storage/camera_monitor/` | 另有按项目/媒体建立的封存引用，不手工覆盖或截断旧分片。 |
| 实时识别临时输入 | `FastAPIBackend/.tmp/realtime/` | 会话结束清理，不属于普通媒体入库，也不在公开静态目录中。 |
| 摄像头桥接 | `tmp/camera-bridge/` | 主机侧设备清单、凭据、RTSP 配置和日志；不提交 Git。 |

`OBJECT_STORAGE_MODE=minio` 或 `s3` 时，真实对象位于配置的 S3 兼容 bucket；`VECTOR_BACKEND=milvus` 时向量位于 Milvus collection。数据库中主要保存 object key、元数据和 vector ID，而不是大文件本体。

历史迁移曾让 core 数据库包含 Scenic 表结构。当前 Scenic 路由只使用 `SCENIC_DATABASE_URL`，但不要自行删除 core 中的历史重复表；必须按 [`DATABASE_SPLIT_RUNBOOK.md`](DATABASE_SPLIT_RUNBOOK.md) 审计和迁移。

### 9.5 前端、测试和安装包产物

| 命令/工具 | 产物位置 |
| --- | --- |
| `npm install` / `npm ci` | `TauriFrontend/node_modules/`；Windows 脚本缓存为 `TauriFrontend/.npm-cache/`。 |
| `npm run build` | `TauriFrontend/dist/`。 |
| `npm run tauri:dev` | `TauriFrontend/src-tauri/target/debug/` 和生成的 capability/config。 |
| `npm run tauri:build` | `TauriFrontend/src-tauri/target/release/`。 |
| Windows 安装包 | `.../target/release/bundle/msi/`、`.../bundle/nsis/`。 |
| `pytest` | 根目录 `.pytest_cache/`；默认不生成 HTML 报告。 |
| Vitest | 默认只输出到终端，不生成持久报告。 |
| Playwright | `TauriFrontend/test-results/`；失败时保留 trace/screenshot，HTML reporter 启用时使用 `playwright-report/`。 |
| 游客 `npm ci` / `npm run build` | `VisitorFrontend/node_modules/` / `VisitorFrontend/dist/`，发布 base 为 `/visitor/`。 |
| 游客 Playwright | `VisitorFrontend/test-results/`；当前默认 list reporter。 |

用户从工作台下载 CSV、图片或视频时，浏览器版使用浏览器下载目录；Tauri 版弹出系统保存对话框，最终位置由用户选择，不固定写入仓库。

### 9.6 Compose 数据与备份

完整栈使用 `postgres_data`、`backend_gallery`、`backend_objects`、`minio_data`、`etcd_data` 和 `milvus_data` 命名卷。用以下命令查看实际卷名与挂载：

```powershell
docker compose ps
docker volume ls
docker compose logs -f backend worker
```

`scripts/backup_restore.sh backup` 默认把 core/scenic 数据库 dump 和本地资源包写入仓库根目录 `backups/`。外部 S3/MinIO 和 Milvus 还需要服务自身的备份方案。

## 10. 配置加载与关键变量

Windows 后端脚本从以下位置读取配置，后面的本地文件覆盖前面的文件，而脚本启动前已经存在的进程环境变量优先级最高：

```text
.env
FastAPIBackend/.env
.env.local
FastAPIBackend/.env.local
```

应用自身也由 `app/core/config.py` 读取这四个位置。不要提交真实 `.env*`、密钥、SMTP 密码或微信支付 PEM。

最常改的变量：

| 变量 | 作用 |
| --- | --- |
| `DATABASE_URL` / `SCENIC_DATABASE_URL` | 两个独立数据库；生产不得相同或使用 SQLite。 |
| `OBJECT_STORAGE_MODE` / `OBJECT_STORAGE_DIR` | `local`、`minio` 或 `s3` 存储模式及本地目录。 |
| `VECTOR_BACKEND` | `memory`（持久 JSON fallback）或 `milvus`。 |
| `REDIS_URL` / `ALLOW_LOCAL_JOB_FALLBACK` | 队列连接与开发 fallback。 |
| `MODEL_DEVICE` | `auto`、`cpu`、`cuda` 或 `mps`。 |
| `YOLO_MODEL_PATH` / `CLIPREID_MODEL_PATH` | 两个推理模型路径。 |
| `CAMERA_AUTO_START` | 是否在 API 启动时立即启动中央相机；默认关闭。 |
| `SCENIC_CAMERA_AUTO_START` | 默认开启，自动启动景区已启用点位的采集和索引。 |
| `CAMERA_RECORDING_RETENTION_HOURS` | 默认 12 小时，仅清理无引用景区录像。 |
| `CAMERA_RECORDING_SEGMENT_SECONDS` | 实时入库录像每段时长，默认 10 秒；封存时继续采集，后台异步上传。 |
| `CAMERA_RECORDING_SEGMENT_TARGETS` | 可选每段目标数量上限；默认 0，不按数量分段。 |
| `CAMERA_RECORDING_SEGMENT_MAX_BYTES` | 每段文件大小保护上限，默认 536870912（512 MiB）。 |
| `EMAIL_CODE_DEV_MODE` | 开发环境是否把验证码返回给 UI/API。 |
| `PAYMENT_PROVIDER` | 开发可用 `fake_wechat`，生产使用真实 provider。 |
| `LOG_LEVEL` | Python 根日志级别。 |

完整变量和覆盖规则见 [`CONFIGURATION.md`](CONFIGURATION.md) 与各 `.env*.example`。

## 11. 验证、测试和构建

后端：

```powershell
.\FastAPIBackend\venv\Scripts\python.exe -m pip check
.\FastAPIBackend\venv\Scripts\python.exe -m compileall -q FastAPIBackend\app FastAPIBackend\database.py FastAPIBackend\scenic_database.py
.\FastAPIBackend\venv\Scripts\python.exe scripts\check_explicit_common_imports.py
.\FastAPIBackend\venv\Scripts\python.exe scripts\check_openapi_docs.py
.\FastAPIBackend\venv\Scripts\python.exe -m pytest FastAPIBackend\tests
```

前端：

```powershell
Set-Location TauriFrontend
npm run test
npm run typecheck
npm run build
npm run test:e2e
Set-Location src-tauri
cargo check --locked
```

生产桌面安装包需要 HTTPS API origin：

```powershell
Set-Location TauriFrontend
$env:VITE_MYREID_API_BASE_URL = "https://api.example.com"
npm run tauri:build
```

## 12. 常见问题定位

### 后端终端停在模型加载，2889 还不能访问

先执行模型校验，再观察 Python 进程的 CPU/内存。CPU 冷启动可能较慢，必须等到 `startup.ready`。优先使用 Python 3.11；如果长时间无进展，用同一 venv 先跑测试，再单独核对 PyTorch、模型权重和设备配置。

### `Connection refused` 或前端一直显示离线

确认后端终端没有停在数据库迁移/模型异常，检查 2889 端口和 `healthz`。浏览器模式还要确认 5173 端口与 Vite proxy；Tauri 模式检查构建时的 API origin 和 capability。

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -In 2889,5173
```

### Redis 不可用

SQLite 模式下预期会降级到本地任务，只要 `ALLOW_LOCAL_JOB_FALLBACK=1`。预发/生产必须启动 Redis + worker；fallback 被安全校验禁止，队列不可用会使新任务返回 503。

### 登录成功但切换工作台后像是未登录

Core 与 Scenic 是两个账号/token scope。需要分别注册/登录；一个域的 token 不会被另一个域接受。

### 找不到媒体或“数据库里有记录但文件打不开”

同时核对数据库、object key 对应的本地目录/S3 bucket，以及向量库。只恢复数据库不足以恢复完整业务。删除数据应优先用 API，避免数据库、对象和向量相互失配。

### 相机不可见或打不开

枚举的是后端可访问的设备；检查权限、占用与容器设备映射。Windows Docker 使用 [摄像头桥接](windows-camera-bridge.md)。`CAMERA_AUTO_START=0` 时仍可能由默认开启的景区自动入库获取设备；要关闭自动采集应检查 `SCENIC_CAMERA_AUTO_START`。本机生产结束使用时运行 `stop_production.ps1`，关闭前端窗口不会停止后台采集。

### 游客停在整理素材或制作中

先区分状态：`sealing` 查看 API 录像维护日志，`waiting/matching` 查看协调器与素材索引，`rendering` 查看 worker/Redis 和画幅错误。确认三类进程使用同一 Scenic 库、对象目录和队列；`selecting/editing` 是等待游客操作。按 [游客联调手册](VISITOR_MANUAL_TEST.md) 使用测试栈复现。

### 前端端口冲突

Vite 配置了 `strictPort: true` 和固定端口 5173。停止占用进程后重启，不要只改 Vite 端口而遗漏 Tauri 的 `devUrl`。

## 13. 修改工程时从哪里下手

- 新增 Core 接口：`app/api/routes/` -> `app/schemas.py`/service -> `src/lib/api/` -> domain store -> Vue view -> `docs/API.md`。
- 新增 Scenic 接口：`app/api/scenic/`，并保持 scenic database/session/token scope。
- 新增游客接口：`visitor_routes.py`、`visitor_auth.py` 与 `VisitorFrontend/src/shared/api.ts`，保持 Cookie、本人归属、来源校验和游客同源代理边界。
- 改数据库：新增 Alembic revision；确认属于 `core@head` 还是 `scenic@head`，不要在启动代码里手写 `DROP/ALTER`。
- 改耗时媒体逻辑：复用 `app/workers/tasks.py`，同时考虑 RQ 与本地 fallback 两种执行方式。
- 改对象或向量：保证数据库记录、object key、vector ID 的创建/回滚/删除是成套的。
- 改前端请求：通过 `src/lib/api/http.ts` 和域 API 封装，不在组件里散落裸 `fetch`。
- 改 API path：同步更新 `docs/API.md`，运行 `scripts/check_openapi_docs.py`。
- 改桌面 API origin：通过 `VITE_MYREID_API_BASE_URL` 和现有生成脚本，不手工放宽所有 CSP/capability。

`legacy/` 是只读历史面。新功能和修复应落在 FastAPI v1、Tauri/Vue 和游客 H5 主线。
