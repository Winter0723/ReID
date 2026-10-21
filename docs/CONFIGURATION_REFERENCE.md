# 完整配置参考

首次安装请先读 [空白机器部署与必填项](NEW_MACHINE_DEPLOYMENT.md)，其中区分 Docker local、production 和宿主机开发。

所有根目录 `.env*.example` 均包含全部后端 Settings 配置项。示例使用占位符或公开的开发值；本机 `.env.local/.env.prod` 不被修改或打包。`python scripts/check_public_config.py` 检查遗漏与重复键。

## 本地运行

- SQLite / 本地文件 / JSON 向量：复制 `.env.sqlite.example` 为 `.env.local`。
- 本机 PostgreSQL：复制 `.env.local.example` 为 `.env.local`，先启动两个数据库；用户名/口令自行设置。
- 新机器 Docker 全栈 local：`.env.docker-local.example`，优先用配置生成器；生产：`.env.prod.example`。已有开发/游客联调环境仍可参考 `.env.dev.example`、`.env.visitor-test.example`。
- 安装锁定依赖，按 `model/README.md` 放置权重，执行 `python scripts/init_runtime.py --migrate --strict-models`。
- API：使用 `scripts/run_backend.*`；生产另启 RQ worker、`python -m app.workers.maintenance`，游客模式另启 `python -m app.workers.visitor`。
- Settings 自动读取根目录和后端目录的 `.env`、`.env.local`，环境变量优先。直接运行 Python 时，启动器专用变量需显式导出；Compose `env_file` 会注入。测试不读取任何本地 env 文件。
- Compose 插值用 `--env-file .env.prod`，并设置 `MYREID_ENV_FILE=.env.prod` 选择容器 env_file；二者用途不同。

## 必须自行配置的内容

数据库两套 URL、`SECRET_KEY`（至少 32 随机字节）、`METRICS_TOKEN`、管理员邮箱、SMTP、HTTPS 公网域名、S3 桶和凭据、模型位置、摄像头设备映射。生产示例故意不能直接启动：替换全部 replace-* 后再部署。带特殊字符的数据库密码需 URL 编码。S3 桶必须私有，不要配置公开媒体 CDN。

`CAMERA_SOURCES` 为 JSON 对象，例如 `CAMERA_SOURCES={"0":"0","entrance":"rtsp://user:password@192.0.2.10/live"}`。这只是文档示意，真实密码只放私有配置；界面填写 `entrance`。注册表由部署管理员控制，业务用户不能新增连接地址。授权地址仍需防火墙/出站访问控制；不要把不受信任用户可修改的 DNS 名放入允许列表。

生产需要 `VISITOR_COOKIE_SECURE=1`、`VISITOR_ALLOW_LAN_HTTP=0`；LAN HTTP 仅用于隔离开发环境。默认开启现场核验，普通注册默认 viewer。`VISITOR_OPERATIONS_PER_HOUR` 限制账号重型操作，`VISITOR_PROJECT_OPERATIONS_PER_HOUR` 限制项目登记及已有游览的制作、预览等操作；生产 Redis 不可用时限流失败关闭。

`S3_PUBLIC_BASE_URL` 仅为旧配置兼容保留，不再生成公开媒体链接；S3 一律返回短期签名 URL。部署者仍须关闭已有桶的公开读取策略。

`PAYMENT_PROVIDER=manual`、`VISITOR_PAYMENT_PROVIDER=manual` 保留人工核实收款；真实支付当前不可直接启用，见 `WECHAT_PAYMENT_INTEGRATION.md`。`VISITOR_AUTO_PUBLISH` / `VISITOR_AUTO_SIMILARITY` 为旧流程兼容选项，现行流程仍由游客选择和确认制作，不绕过现场核验。

## 后端配置清单

下表是代码默认值；各部署示例会覆盖其中的数据库、存储、邮件和支付选项。空值表示不配置，不表示自动获取真实凭据。相对数据路径以 FastAPIBackend 为基准；模型相对路径按仓库/后端/工作目录解析。

| 变量 | 代码默认值 |
| --- | --- |
| `APP_NAME` | `行人重识别系统` |
| `APP_ENV` | `development` |
| `API_V1_PREFIX` | `/api/v1` |
| `DATABASE_URL` | `sqlite:///./reid_system.db` |
| `SCENIC_DATABASE_URL` | `(空)` |
| `SECRET_KEY` | `your-super-secret-key-for-interview-demo` |
| `BOOTSTRAP_ADMIN_EMAIL` | `(空)` |
| `PUBLIC_REGISTRATION_ROLE` | `viewer` |
| `CAMERA_SOURCES` | `{"0":"0"}` |
| `VISITOR_REQUIRE_STAFF_VERIFICATION` | `1` |
| `VISITOR_OPERATIONS_PER_HOUR` | `30` |
| `VISITOR_PROJECT_OPERATIONS_PER_HOUR` | `300` |
| `VISITOR_RETENTION_DAYS` | `30` |
| `METRICS_TOKEN` | `(空)` |
| `ENABLE_LEGACY_API` | `0` |
| `ALLOW_LOCAL_JOB_FALLBACK` | `1` |
| `CAMERA_AUTO_START` | `0` |
| `CORE_CAMERA_AUTO_START` | `1` |
| `SCENIC_CAMERA_AUTO_START` | `1` |
| `CAMERA_CAPTURE_WIDTH` | `640` |
| `CAMERA_CAPTURE_HEIGHT` | `480` |
| `CAMERA_PREVIEW_FPS` | `30` |
| `CAMERA_PREVIEW_WIDTH` | `640` |
| `CAMERA_JPEG_QUALITY` | `75` |
| `CAMERA_RECORDING_RETENTION_HOURS` | `12` |
| `CAMERA_RECORDING_SEGMENT_SECONDS` | `10` |
| `CAMERA_RECORDING_SEGMENT_TARGETS` | `0` |
| `CAMERA_RECORDING_SEGMENT_MAX_BYTES` | `536870912` |
| `SCENIC_EDITING_ENABLED` | `1` |
| `SCENIC_TRACKING_ENABLED` | `0` |
| `VISITOR_AUTH_PROVIDER` | `wechat` |
| `VISITOR_PAYMENT_PROVIDER` | `wechat` |
| `VISITOR_TEST_INVITE_CODE` | `(空)` |
| `VISITOR_WECHAT_APPID` | `(空)` |
| `VISITOR_WECHAT_SECRET` | `(空)` |
| `VISITOR_PUBLIC_BASE_URL` | `(空)` |
| `VISITOR_COOKIE_SECURE` | `1` |
| `VISITOR_ALLOW_LAN_HTTP` | `0` |
| `VISITOR_AUTO_PUBLISH` | `0` |
| `VISITOR_AUTO_SIMILARITY` | `0.9` |
| `VISITOR_INDEX_WAIT_SECONDS` | `600` |
| `SCENIC_PUBLIC_BASE_URL` | `(空)` |
| `ALGORITHM` | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` |
| `GALLERY_DIR` | `./gallery` |
| `OBJECT_STORAGE_MODE` | `local` |
| `OBJECT_STORAGE_DIR` | `./object_storage` |
| `S3_ENDPOINT_URL` | `(空)` |
| `S3_BUCKET` | `myreid-media` |
| `S3_ACCESS_KEY_ID` | `(空)` |
| `S3_SECRET_ACCESS_KEY` | `(空)` |
| `S3_REGION` | `us-east-1` |
| `S3_PUBLIC_BASE_URL` | `(空)` |
| `S3_PRESIGNED_URL_SECONDS` | `3600` |
| `ASSET_URL_TTL_SECONDS` | `300` |
| `VECTOR_BACKEND` | `milvus` |
| `MILVUS_URI` | `http://localhost:19530` |
| `MILVUS_COLLECTION` | `reid_clip_embeddings` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `REDIS_CONNECT_TIMEOUT_SECONDS` | `1.0` |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `2.0` |
| `RQ_QUEUE_NAME` | `media` |
| `RQ_JOB_TIMEOUT_SECONDS` | `21600` |
| `CLIP_MODEL_ID` | `openai/clip-vit-base-patch32` |
| `YOLO_MODEL_PATH` | `yolov8n.pt` |
| `MODEL_SERVICE_BACKEND` | `baseline` |
| `EMBEDDING_BACKEND` | `clipreid` |
| `DETECTION_BACKEND` | `yolo` |
| `EMBEDDING_DIMENSION` | `512` |
| `MODEL_DEVICE` | `auto` |
| `YOLO_PRECISION` | `auto` |
| `YOLO_IMAGE_SIZE` | `640` |
| `CLIPREID_PRECISION` | `auto` |
| `REALTIME_CAMERA_INFERENCE_FPS` | `30` |
| `REALTIME_ENTER_THRESHOLD` | `0.65` |
| `REALTIME_EXIT_THRESHOLD` | `0.55` |
| `REALTIME_MARGIN` | `0.08` |
| `REALTIME_ORIGINS` | `http://localhost:35173,http://127.0.0.1:35173,http://localhost:5173,http://127.0.0.1:5173,http://tauri.localhost,https://tauri.localhost,tauri://localhost` |
| `CUSTOM_REID_MODEL_PATH` | `(空)` |
| `CLIPREID_MODEL_PATH` | `model/ViT-MSMT17-60.pth` |
| `CLIPREID_MODEL_NAME` | `ViT-B-16` |
| `CLIPREID_DATASET_NAME` | `MSMT17` |
| `CLIPREID_NUM_CLASSES` | `1041` |
| `CLIPREID_CAMERA_NUM` | `15` |
| `CLIPREID_VIEW_NUM` | `1` |
| `CLIPREID_IMAGE_HEIGHT` | `336` |
| `CLIPREID_IMAGE_WIDTH` | `160` |
| `CLIPREID_STRIDE_SIZE` | `16` |
| `CUSTOM_DETECTION_MODEL_PATH` | `(空)` |
| `ONNX_MODEL_PATH` | `(空)` |
| `ONNX_DETECTION_MODEL_PATH` | `(空)` |
| `TENSORRT_ENGINE_PATH` | `(空)` |
| `TENSORRT_DETECTION_ENGINE_PATH` | `(空)` |
| `GPU_INFERENCE_URL` | `(空)` |
| `MAX_UPLOAD_MB` | `1024` |
| `SMTP_HOST` | `(空)` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | `(空)` |
| `SMTP_PASSWORD` | `(空)` |
| `SMTP_FROM` | `(空)` |
| `SMTP_USE_TLS` | `1` |
| `SMTP_USE_SSL` | `0` |
| `EMAIL_CODE_TTL_MINUTES` | `10` |
| `EMAIL_CODE_DEV_MODE` | `0` |
| `SCENIC_ORDER_PRICE_CENTS` | `990` |
| `SCENIC_ORDER_CURRENCY` | `CNY` |
| `SCENIC_DELIVERY_TOKEN_TTL_HOURS` | `720` |
| `PAYMENT_PROVIDER` | `(空)` |
| `PAYMENT_PUBLIC_BASE_URL` | `(空)` |
| `WECHAT_PAY_APPID` | `(空)` |
| `WECHAT_PAY_MCHID` | `(空)` |
| `WECHAT_PAY_API_V3_KEY` | `(空)` |
| `WECHAT_PAY_MERCHANT_SERIAL_NO` | `(空)` |
| `WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH` | `(空)` |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_ID` | `(空)` |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH` | `(空)` |
| `LOG_LEVEL` | `INFO` |
| `DISK_ALERT_FREE_PERCENT` | `10.0` |
| `DISK_ALERT_FREE_BYTES` | `1073741824` |

## 启动器、工具和 Compose 选项

示例末尾同时列出 `CLIP_CACHE_DIR`、`YOLO_CONFIG_DIR`、`MYREID_CAMERA_SOURCE`、`MYREID_CAMERA_BRIDGE_DIR`、字体/编码器、本地队列容量和 TTL、API/游客端口等。空路径表示使用程序内默认路径。`VITE_MYREID_API_BASE_URL` 是前端构建时变量，不能存放任何密钥。`SCENIC_FONT_FILE` 应指向具有许可的 CJK 字体；`SCENIC_VIDEO_ENCODER` 默认 libx264，GPU 编码需部署者自行验证。
