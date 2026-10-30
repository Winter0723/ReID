# 配置参考

适用 `allinone`。下表区分代码默认值与模板示例值；复制模板后以实际运行进程的配置为准。启动步骤见 [开发指南](DEVELOPMENT.md) 和 [部署指南](DEPLOYMENT.md)。

## 选择运行模式

| 模式 | 模板 / 配置 | 启动入口 | 默认宿主机端口 |
| --- | --- | --- | --- |
| 本机轻量开发 | `.env.sqlite.example` → `.env.local` | `scripts/run_backend.*` | API `2889` |
| 本机 API + PostgreSQL/Redis | `.env.local.example` → `.env.local` | `docker-compose.postgres.yml` + 本机 API/worker | API `2889`，数据库 `5432`，Redis `6379` |
| 完整 Compose 开发栈 | `.env.example` | `docker-compose.yml` | API `2889` |
| Compose 源码挂载开发 | `.env.dev.example` → `.env.dev` | 基础文件 + `docker-compose.dev.yml` | API `2889` |
| 预发 / 通用生产 | `.env.staging.example` / `.env.prod.example` | 基础文件 + 对应 staging/prod 覆盖文件 | API `2889`；应放在 TLS 代理之后 |
| Windows 本机 GPU 生产 | `.env.prod` | `scripts/run_production.ps1` | API `2889`，游客 H5 `5174` |
| 隔离游客联调 | `.env.visitor-test.example` → `.env.visitor-test` | `scripts/start-visitor-test.ps1` | API `22889`，游客 H5 `25174` |
| 运营 / 游客前端开发 | 根目录 `MYREID_API_PORT` | 各前端的 `npm run dev` | 运营 `5173`，游客 `5174` |

常规开发与生产统一使用 API `2889`、运营 `5173`、游客 `5174`，基础 Compose 直接固定映射，不受旧 `MYREID_API_PORT`、`STAFF_WEB_PORT`、`VISITOR_WEB_PORT` 端口覆盖影响。前端代理的 `MYREID_API_PORT` 保持 `2889`；只有明确启动隔离游客联调环境时才使用该环境专用配置。端口冲突时处理占用或保留原因，不自动换端口；开发前端与对应 Docker 前端不能同时启动。

`visitor-web` 和 `visitor-coordinator` 属于 Compose 的 `visitor` profile。基础 Compose 默认不启动它们；本机生产和游客联调脚本已经包含 `--profile visitor`。本机生产和游客联调覆盖文件使用 `!override`，Compose 必须支持该语法；GPU 覆盖还使用 `gpus: all`。

## 配置如何生效

后端 `Settings` 按以下顺序读取文件，后面的覆盖前面的，进程环境变量优先于文件：

1. 仓库根目录 `.env`
2. `FastAPIBackend/.env`
3. 仓库根目录 `.env.local`
4. `FastAPIBackend/.env.local`

`.env.prod`、`.env.staging`、`.env.visitor-test` 不在这个自动读取列表中，由 Compose 的 `env_file` 注入。配置缓存于进程内，改文件后需重启本机进程；容器要执行原 Compose 组合的 `up -d`，单独 `restart` 不会重新注入变量。

- `run_backend.ps1` 把上述四个文件导入进程，并保护调用前已有的变量；`run_backend.sh` 使用 dotenv 解析同样四个文件，不执行 Shell 内容，并保留调用前已有的环境变量。
- `run_backend_postgres.sh` / `run_worker.sh` 显式导出本机数据库等默认值。使用自定义地址时先设置 shell 变量；它们不会把全部 `.env.local` 项目导出。
- `SCENIC_FONT_FILE`、`SCENIC_FONT_FAMILY`、`SCENIC_VIDEO_ENCODER`、`CLIP_CACHE_DIR`、`MYREID_CAMERA_BRIDGE_DIR`、`LOCAL_JOB_*` 等由模块直接读进程环境。直接运行 Python/worker 时，仅放进 Pydantic 的 dotenv 文件不一定生效，应显式导出或通过 Compose 注入。
- Compose 有两层配置：`--env-file` 提供 `${...}` 插值，服务级 `env_file` 提供容器变量，`environment` 覆盖同名容器变量。`MYREID_ENV_FILE` 选择基础服务的 `env_file`；它不会替代 `--env-file`。启用游客 profile 时也要让协调器读取同一份配置。
- SQLite URL、`GALLERY_DIR`、`OBJECT_STORAGE_DIR` 的相对路径以 `FastAPIBackend/` 为基准。模型相对路径依次在仓库根、后端目录、当前工作目录查找。前端 `VITE_*` 变量会进入客户端构建，不可放密钥。

## 身份、数据库与生产校验

| 变量 | 代码默认值 | 用途 / 约束 |
| --- | --- | --- |
| `APP_ENV` | `development` | 主要生产校验识别 `production`、`prod`、`staging`；部署使用模板的标准名称。 |
| `DATABASE_URL` | `sqlite:///./reid_system.db` | Core 连接；PostgreSQL 驱动形式为 `postgresql+psycopg://...`。 |
| `SCENIC_DATABASE_URL` | 空；开发推导独立 `scenic_system.db` | Scenic 与游客共用的业务库；预发/生产必须显式设置为非 SQLite。 |
| `SECRET_KEY` | 开发占位值 | JWT、媒体签名等使用；预发/生产要求至少 32 字节、非内置占位值、无首尾空白。 |
| `BOOTSTRAP_ADMIN_EMAIL` | 空 | 预发/生产必填；匹配邮箱在注册时成为管理员，不自动建号或提升已有账号。 |
| `METRICS_TOKEN` | 空 | `/metrics` 的独立 Bearer 密钥；预发/生产至少 32 字节，不能以 `replace-` 开头。 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | 工作人员 access token，有效范围 1–10080 分钟。 |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` | 可撤销、可轮换 refresh token，有效范围 1–129600 分钟。 |
| `ENABLE_LEGACY_API` | `0` | 任何环境设为 `1` 都拒绝启动。 |

Core 与 Scenic 必须使用不同数据库；这是部署约束，须结合 `scripts/audit_database_split.py --strict` 检查，不能仅凭应用能启动就认定隔离成功。游客账号和 Cookie 会话保存在 Scenic 的 `visitor_*` 表中，不需要第三个数据库。Core token 接受空 scope 或 `core`，Scenic 要求 `scenic`，游客使用独立 HttpOnly Cookie。

## 模型、存储与任务

| 变量 | 默认值 / 模板差异 | 说明 |
| --- | --- | --- |
| `MODEL_SERVICE_BACKEND` / `EMBEDDING_BACKEND` / `DETECTION_BACKEND` | `baseline` / `clipreid` / `yolo` | 当前 Settings 只接受这一组合；源码中的其他适配器不等于已开放配置。 |
| `MODEL_DEVICE` | `auto` | 自动按 CUDA → MPS → CPU 选择；可明确设为 `cpu`、`cuda`、`mps` 等 PyTorch 设备。指定设备前需验证可用性。 |
| `WORKER_MODEL_DEVICE` | Compose / worker 脚本默认 `cpu` | 进程资源配置；本机 GPU 生产覆盖文件将 API、worker、协调器均设为 `cuda`。 |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | 本机解析到后端目录；Compose 模板使用 `/models/yolov8n.pt`。 |
| `CLIPREID_MODEL_PATH` | `model/ViT-MSMT17-60.pth` | Compose 使用 `/models/reid/ViT-MSMT17-60.pth`。 |
| `CLIP_CACHE_DIR` | 未设置时使用 `model/clip_weights/` | CLIP 基础权重目录；容器挂载 `ViT-B-16.pt` 到该目录。 |
| `EMBEDDING_DIMENSION` | `512` | 必须匹配实际模型和向量集合；换模型/预处理需按 [模型说明](../model/NOTICE.md) 重建并验证索引。 |
| `OBJECT_STORAGE_MODE` | `local` | 可选 `local`、`minio`、`s3`。 |
| `GALLERY_DIR` / `OBJECT_STORAGE_DIR` | `./gallery` / `./object_storage` | 预览、本地对象、缓存、锁与录像目录；API/worker/协调器必须共享对象目录。 |
| `S3_ENDPOINT_URL` / `S3_BUCKET` | 空 / `myreid-media` | MinIO 填容器可达的 `http://minio:9000`；托管 S3 按供应方配置。 |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_REGION` | 空 / 空 / `us-east-1` | 对象存储凭据；模板中的 MinIO 账号必须与服务配置一致。 |
| `ASSET_URL_TTL_SECONDS` | `300` | 业务媒体 API 签名有效期，30–3600 秒；过期重新请求业务数据。 |
| `VECTOR_BACKEND` | 代码 `milvus`；本机模板 `memory` | `memory` 实际持久化为 core/scenic 两个 JSON 文件。 |
| `MILVUS_URI` / `MILVUS_COLLECTION` | `http://localhost:19530` / `reid_clip_embeddings` | Compose URI 使用服务名 `milvus`；Scenic 使用独立集合命名空间。 |
| `REDIS_URL` / `RQ_QUEUE_NAME` | `redis://localhost:6379/0` / `media` | API 与 worker 必须一致。Redis 可连接不代表已有 worker 消费。 |
| `RQ_JOB_TIMEOUT_SECONDS` | `21600` | 单个 RQ 任务超时，单位秒。 |
| `ALLOW_LOCAL_JOB_FALLBACK` | `1` | Redis 不可用时进程内执行；预发/生产必须为 `0`。Redis 可用时仍会入队，必须运行 worker。 |
| `LOCAL_JOB_WORKERS` / `LOCAL_JOB_QUEUE_LIMIT` | `4` / `128` | 本地线程数 / 排队容量；进程重启不保证恢复这类本地任务。 |
| `MAX_UPLOAD_MB` | `1024` | 通用上传限制；游客登记照片另限 10 MiB，实时参考照片 20 MiB、临时视频 1 GiB。 |

业务图片/视频经授权 API 交付；即使存储为 S3/MinIO，也会先读取到后端缓存再响应，并支持 Range。`S3_PUBLIC_BASE_URL` / `S3_PRESIGNED_URL_SECONDS` 属于存储适配器能力，不替代业务资源权限或 `ASSET_URL_TTL_SECONDS`。

## 相机、实时识别与精剪

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CAMERA_AUTO_START` | `0` | 是否启动旧中央默认相机常驻服务。 |
| `SCENIC_CAMERA_AUTO_START` | `1` | 随 API 启动景区点位采集与索引；与前项独立。 |
| `CAMERA_RECORDING_SEGMENT_SECONDS` | `10` | 定时封存分段，允许 1–3600 秒。 |
| `CAMERA_RECORDING_SEGMENT_TARGETS` | `0` | 0 表示不按目标数量分段。 |
| `CAMERA_RECORDING_SEGMENT_MAX_BYTES` | `536870912` | 512 MiB 分段保护上限。 |
| `CAMERA_RECORDING_RETENTION_HOURS` | `12` | 景区未引用录像保留时长，1–8760 小时；不是总容量上限。 |
| `MYREID_CAMERA_BRIDGE_DIR` | 空 | Windows 桥接共享清单与凭据目录；本机生产 API 挂载 `/run/camera-bridge`。 |
| `REALTIME_ENTER_THRESHOLD` / `REALTIME_EXIT_THRESHOLD` / `REALTIME_MARGIN` | `0.65` / `0.55` / `0.08` | 实时识别进入、保留和分差阈值，不能视作身份概率。 |
| `REALTIME_ORIGINS` | 本机 5173 与 Tauri 默认来源 | WebSocket 的 Origin 白名单，逗号分隔；自定义页面域名需显式加入。 |
| `SCENIC_EDITING_ENABLED` / `SCENIC_TRACKING_ENABLED` | `1` / `0` | 精剪和可选人物分析开关；`lap` 已是基础依赖，关闭景区分析不取消实时识别依赖。 |
| `SCENIC_VIDEO_ENCODER` | `libx264` | 可设 `h264_nvenc`；硬件编码失败回退软件编码。 |
| `SCENIC_FONT_FILE` / `SCENIC_FONT_FAMILY` | 自动查找系统字体 | 完整精剪需要 FFmpeg、FFprobe、subtitles/libass 与中文字体。 |
| `SCENIC_PUBLIC_BASE_URL` | 空 | 工作人员创建的领取页/二维码所用后端地址，必须可由领取设备访问。 |

相机、实时会话和一次性流票据由单个 API 进程管理，当前部署使用一个 Uvicorn worker。详细行为见 [录像保留](camera-retention.md)、[实时识别](realtime-recognition.md) 和 [精剪](SCENIC_EDITING.md)。

## 邮件、支付与游客

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SMTP_HOST` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` | 空 | 邮箱验证码投递配置。 |
| `SMTP_PORT` / `SMTP_USE_TLS` / `SMTP_USE_SSL` | `587` / `1` / `0` | TLS 指 STARTTLS；端口 465 且未显式配置时自动使用 SSL，建议明确填写。 |
| `EMAIL_CODE_TTL_MINUTES` / `EMAIL_CODE_DEV_MODE` | `10` / `0` | 本机模板开启调试验证码；预发/生产必须关闭。 |
| `SCENIC_ORDER_PRICE_CENTS` / `SCENIC_ORDER_CURRENCY` | `990` / `CNY` | 工作人员订单使用服务端定价；生产模板示例为 `19900`，预发/生产要求显式设置。 |
| `SCENIC_DELIVERY_TOKEN_TTL_HOURS` | `720` | 领取链接有效期，按订单创建时间计算。 |
| `PAYMENT_PROVIDER` | 空 | 工作人员支付模式；通用 Docker local/prod/staging 模板均为 `manual`。自动微信支付尚未完成接入验收，见 [支付说明](WECHAT_PAYMENT_INTEGRATION.md)。 |
| `PAYMENT_PUBLIC_BASE_URL` | 空 | 工作人员微信通知的后端公网地址。 |
| `VISITOR_AUTH_PROVIDER` | `wechat` | `wechat` / `browser` / `test`。前两者均支持独立浏览器游客；微信配置就绪后可同时微信登录，独立于工作人员登录。 |
| `VISITOR_PAYMENT_PROVIDER` | `wechat` | `wechat`（JSAPI）、`manual`（人工确认）、`test`；不继承 `PAYMENT_PROVIDER`。 |
| `VISITOR_WECHAT_APPID` / `VISITOR_WECHAT_SECRET` | 空 | 公众号网页授权；仅存服务端。 |
| `VISITOR_PUBLIC_BASE_URL` | 空 | 游客静态页、API、OAuth 回调的同源 origin，不带 `/visitor/`。 |
| `VISITOR_COOKIE_SECURE` | `1` | 公网 HTTPS 保留 Secure Cookie。 |
| `VISITOR_ALLOW_LAN_HTTP` | `0` | 显式开启且 Cookie Secure 为 0 时，允许 RFC1918 私有 IPv4 的 HTTP OAuth 联调。 |
| `VISITOR_TEST_INVITE_CODE` | 空 | 任一游客 provider 为 `test` 时，要求开发/测试环境及至少 16 位邀请码。 |
| `VISITOR_INDEX_WAIT_SECONDS` | `600` | 等待相关索引任务的超时，30–3600 秒；超时进入可处理的异常状态。 |
| `VISITOR_AUTO_PUBLISH` / `VISITOR_AUTO_SIMILARITY` | `0` / `0.9` | 保留的旧流程配置；当前自助链路由游客选片、确认制作，成功双版自动进入 `ready`。 |

真实支付使用 `WECHAT_PAY_APPID`、`WECHAT_PAY_MCHID`、`WECHAT_PAY_API_V3_KEY`、`WECHAT_PAY_MERCHANT_SERIAL_NO`、`WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH`、`WECHAT_PAY_PLATFORM_PUBLIC_KEY_ID`、`WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH`。仅额外叠加 `docker-compose.wechat.yml` 时，`*_FILE` 才是宿主机 secret 来源，`*_PATH` 是容器内路径；本机 GPU 覆盖文件未配置这些 secret 挂载。游客 JSAPI 还需公众号身份与支付 appid 匹配，回调路径见 [游客说明](VISITOR_H5.md)。

游客售价来自景区 `visitor-service` 配置，登记时冻结；已有订单绑定原支付模式及成片版本，修改 provider 不转换旧订单。人工确认不需要商户支付配置，但真实微信登录仍需公众号配置。

## 观测与前端地址

`LOG_LEVEL=INFO` 控制后端日志。`DISK_ALERT_FREE_PERCENT=10`、`DISK_ALERT_FREE_BYTES=1073741824` 是代码默认磁盘告警阈值；生产模板分别为 15 和 2 GiB。`healthz` 是存活检查，`readyz` 返回就绪字段及 `degraded_reasons`，详细运行摘要使用工作人员管理接口。

本机两个前端的代理目标由根目录 Vite 环境配置中的 `MYREID_API_PORT` 决定，进程变量优先，未设置为 2889。运营前端的 `VITE_MYREID_API_BASE_URL` 会改为直接请求；开发浏览器优先用同源代理，后端当前没有通用 CORS 中间件。Tauri 启动脚本会将 `MYREID_API_BASE_URL` 映射到该构建变量。游客前端始终使用相对游客 API 路径。

生产桌面包通过 `npm run tauri:build` 生成精确 HTTP capability 和 CSP，必须设置非本机 HTTPS API origin；普通 `npm run build` 只生成网页静态产物。改地址后需重新构建桌面包或重启 Vite。
