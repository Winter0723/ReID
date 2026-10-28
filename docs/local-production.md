# MyReID 本机生产模式启动指南

适用环境：Windows + Docker Desktop Linux 容器 + NVIDIA GPU。本指南以 RTX 5060 Ti 部署记录为例，日常操作已按 `allinone` 脚本更新；文末实测仅代表标注日期。以下命令均在项目根目录的 PowerShell 中执行，路径替换为实际检出位置：

```powershell
cd C:\path\to\MyReID
```

## 1. 当前运行模式

目前是“本机前端 + Docker 生产后端”，不是整个桌面应用都运行在 Docker 中。

| 组件 | 运行位置 | 访问方式 |
| --- | --- | --- |
| Vue/Vite 浏览器前端 | Windows 本机 | http://127.0.0.1:5173（以启动终端显示为准） |
| Tauri 桌面前端 | Windows 本机 | 桌面窗口；与浏览器前端二选一即可 |
| FastAPI | Docker backend | http://127.0.0.1:2889，容器内部端口 2889 |
| RQ Worker | Docker worker | 从 Redis 队列取任务，CUDA 推理、NVENC 编码 |
| 游客制作协调器 | Docker visitor-coordinator | 推进持久化匹配与制作请求 |
| 游客 H5 | Docker visitor-web | http://127.0.0.1:5174/visitor/，生产公网入口另配 HTTPS |
| 独立摄像头网关 | 宿主机代理 + myreid-camera Docker 项目 | 多 USB / 虚拟设备 / 网络 RTSP；回环视频端口 18554、控制端口 19997 |
| PostgreSQL | Docker postgres | 基础系统 myreid、景区系统 myreid_scenic 两个数据库 |
| Milvus + etcd | Docker | 向量检索与元数据 |
| MinIO | Docker | 真实对象存储，同时供 Milvus 使用 |
| Redis | Docker | 真实任务队列 |

后端使用 APP_ENV=production，关闭开发验证码、本地任务回退和旧 API。API、游客网页和摄像头桥接端口映射到主机回环地址，数据库等服务通过 Docker 内部网络连接。

首次部署创建新的生产数据库，**不会自动迁移旧 SQLite 中的账号、项目或素材**。旧账号不存在、SMTP 未配置与前端连接失败需分别排查。

## 2. 这台机器日常怎么启动

1. 打开 Docker Desktop，等待引擎启动，使用 Linux 容器模式。
2. 启动生产后端与依赖：

```powershell
.\scripts\run_production.ps1
```

3. 验证服务：

```powershell
Invoke-RestMethod http://127.0.0.1:2889/api/v1/healthz
Invoke-RestMethod http://127.0.0.1:2889/api/v1/readyz
```

`healthz` 预期 `environment=production`；`readyz` 预期 `status=ready`、`device=cuda`、`queue_mode=redis`，数据库、模型、队列、向量库和磁盘就绪字段为 True，`degraded_reasons` 为空。HTTP 200 或容器 healthy 只证明存活，仍要检查这些字段和 worker/协调器日志。

4. 另开一个 PowerShell，在项目根目录启动前端：

```powershell
# 浏览器方式
.\scripts\run_tauri_frontend.ps1 -BrowserOnly

# 或 Tauri 桌面方式（需要已有 Rust/Tauri 编译环境）
.\scripts\run_tauri_frontend.ps1
```

首次安装前端依赖可附加 -Install。使用浏览器方式时打开终端给出的地址。

根目录 `.env.local` 中应保留：

```dotenv
MYREID_API_PORT=2889
```

常规端口固定为后端 `2889`、运营前端 `5173`、游客前端 `5174`。从旧配置升级时，将前端 `.env.local` 的 `MYREID_API_PORT` 恢复为 `2889`，清除终端中旧端口覆盖值并重启前端；若旧桌面包编译了旧地址，需要重新构建。显式配置过 `VITE_MYREID_API_BASE_URL` 或 `MYREID_API_BASE_URL` 时也应清除旧地址。后续不再随部署模式切换端口。

Docker 游客网页与 Vite 游客开发服务器都使用 `5174`，二选一运行。需要热更新或局域网开发服务器时，先停止该 Compose 项目的 `visitor-web`，再启动 Vite；不要改端口来同时运行两份。运营端 Docker `staff` profile 与 Vite `5173` 同理。

生产模式下不要再启动 `run_backend.cmd`：后端已由 Docker 运行，重复启动会争用端口。Windows venv 的 CUDA 配置仅用于独立本地后端，不控制容器中的 PyTorch。

## 3. 首次部署与配置文件

已有部署应保留 `.env.prod` 和现有数据，**不要用模板覆盖配置文件**。首次部署需自行准备配置与镜像，不能假定这台机器已有可用环境。

新机器可先复制模板，再填写下面参数：

```powershell
if (!(Test-Path .env.prod)) { Copy-Item .env.prod.example .env.prod }
```

模板含支付、邮件等占位项，不能直接原样启动。所有 replace-* 值必须替换；未使用的支付参数按后文处理。

配置完成后首次构建并启动：

```powershell
.\scripts\run_production.ps1 -Build
```

已有镜像时不加 -Build 即可。修改后端代码或依赖后需重新构建；仅改 `.env.prod`，重新执行普通启动命令即可更新容器环境。单独 `docker restart` 不会读取新的 env 文件。

### 手机局域网 HTTP 联调

如果游客入口使用 `http://192.168.x.x:5174` 等局域网 HTTP 地址，应在现有 `.env.prod` 中显式设置 `APP_ENV=local`、`VISITOR_COOKIE_SECURE=0`、`VISITOR_ALLOW_LAN_HTTP=1`。Compose 会为 API、worker、游客协调器和维护服务统一读取 `APP_ENV`；未配置时仍默认 production。其余数据库、队列、账号和项目数据保持原配置，启动命令不变。该模式用于本机联调，不是 HTTPS 生产部署，`healthz` 中的 `environment` 应为 `local`。

浏览器游客入口可设置 `VISITOR_AUTH_PROVIDER=browser`，无需微信、邀请码或个人资料，点击“开始登记”即可使用。每个浏览器通过 HttpOnly Cookie 保存独立游客身份（有效期 30 天），再次进入同一或其他景区仍沿用该身份。清除 Cookie、使用无痕窗口或更换设备后无法找回原记录。登录与付款分别配置，`VISITOR_PAYMENT_PROVIDER=manual` 仍需工作人员确认收款。修改后运行 `scripts/run_production.ps1 -Build`。

正式 HTTPS 部署使用 `APP_ENV=production`、`VISITOR_AUTH_PROVIDER=wechat`、`VISITOR_COOKIE_SECURE=1`、`VISITOR_ALLOW_LAN_HTTP=0`，并设置对应的 HTTPS 游客入口。`wechat` 和 `browser` 均支持正式环境的独立浏览器游客，配置公众号后同时提供微信登录入口。`VISITOR_REQUIRE_STAFF_VERIFICATION=0` 为自助流程，设为 `1` 可启用现场核验。启动脚本会在构建和启动网关之前检查模式与 Cookie 配置是否冲突；不要仅把 HTTP 环境的 Cookie 改为 Secure，否则手机不能发送登录 Cookie。

部署组合固定为 `docker-compose.yml` + `docker-compose.local-production.yml`，项目名 `myreid-production`，脚本已启用 `visitor` profile，并先启动独立的 `myreid-camera` 摄像头网关。启动时始终用 Docker 构建缓存核对 backend 与 visitor-web 的源码，worker 和协调器复用后端镜像，避免新前端连接旧后端；源码未变化时复用缓存层。`-Build` 参数保留兼容旧命令。不要只运行裸的 `docker compose up`，它使用的配置、端口和项目名可能不同。`docker-compose.prod.yml` 是另一套包含支付证书的部署覆盖文件，不是本指南使用的组合。

Docker Compose 必须支持覆盖文件中的 `!override` 和 `gpus`。宿主机还需 PATH 中的 FFmpeg、Python（含 pythonw.exe）及桌面应用摄像头权限，详见 [Windows 摄像头桥接](windows-camera-bridge.md)。

### 必需参数（写入根目录 .env.prod）

| 参数 | 本机生产值或填写规则 |
| --- | --- |
| APP_ENV | production |
| MYREID_ENV_FILE | .env.prod |
| MYREID_API_PORT | 2889，与前端 .env.local 一致 |
| POSTGRES_USER / POSTGRES_DB / SCENIC_POSTGRES_DB | myreid / myreid / myreid_scenic |
| POSTGRES_PASSWORD | 自行生成强随机密码；已有数据库不能仅修改 env 就完成数据库密码轮换 |
| DATABASE_URL | postgresql+psycopg://myreid:数据库密码@postgres:5432/myreid |
| SCENIC_DATABASE_URL | postgresql+psycopg://myreid:数据库密码@postgres:5432/myreid_scenic |
| SECRET_KEY | 至少 32 字节非占位随机值；更换后旧登录凭据失效 |
| METRICS_TOKEN | 至少 32 字节非占位随机值 |
| BOOTSTRAP_ADMIN_EMAIL | 你可以收验证码的真实管理员邮箱，不要保留 admin@example.com 或模板邮箱 |
| EMAIL_CODE_DEV_MODE / ALLOW_LOCAL_JOB_FALLBACK / ENABLE_LEGACY_API | 均为 0；覆盖文件也会强制关闭 |
| CAMERA_AUTO_START | 0 |
| SCENIC_CAMERA_AUTO_START | 默认 1；独立控制景区点位自动采集，CAMERA_AUTO_START=0 不会关闭它 |
| CAMERA_RECORDING_SEGMENT_SECONDS / CAMERA_RECORDING_RETENTION_HOURS | 默认 10 秒 / 12 小时；只回收无引用的景区源录像，见 [保留策略](camera-retention.md) |
| SCENIC_ORDER_PRICE_CENTS / SCENIC_ORDER_CURRENCY | 模板示例 19900 / CNY；生产校验要求显式设置，按实际业务定价调整 |
| OBJECT_STORAGE_MODE | s3 |
| MINIO_ROOT_USER / MINIO_ROOT_PASSWORD | MinIO 账号和强随机密码 |
| S3_ENDPOINT_URL | http://minio:9000（容器内部地址） |
| S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY | 本机配置分别与 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD 一致 |
| S3_BUCKET / S3_REGION | myreid-media-prod / us-east-1 |
| VECTOR_BACKEND | milvus |
| MILVUS_URI / MILVUS_COLLECTION | http://milvus:19530 / reid_clip_embeddings_prod |
| REDIS_URL / RQ_QUEUE_NAME | redis://redis:6379/0 / media |
| MODEL_SERVICE_BACKEND / EMBEDDING_BACKEND / DETECTION_BACKEND | baseline / clipreid / yolo |
| MODEL_DEVICE | cuda；本机覆盖文件为 API、worker 和协调器指定 cuda |
| EMBEDDING_DIMENSION | 512 |
| YOLO_MODEL_PATH | /models/yolov8n.pt |
| CLIPREID_MODEL_PATH | /models/ViT-MSMT17-60.pth |
| CLIPREID_IMAGE_HEIGHT / CLIPREID_IMAGE_WIDTH | 336 / 160 |
| GALLERY_DIR / OBJECT_STORAGE_DIR | /workspace/FastAPIBackend/gallery / /workspace/FastAPIBackend/object_storage |
| SCENIC_VIDEO_ENCODER | h264_nvenc，Worker 覆盖文件已配置 |

URL 中的数据库密码若包含特殊字符，需要 URL 编码；首次生成使用随机十六进制值可以避免这个问题。不要重新生成已有部署的数据库密码或 SECRET_KEY 来“修复登录”。

确认以下真实权重文件存在（不能只是 Git LFS 指针）：

- FastAPIBackend/yolov8n.pt
- model/ViT-MSMT17-60.pth
- model/clip_weights/ViT-B-16.pt

GPU 需要 Docker Desktop 能访问 NVIDIA 驱动。历史 CUDA/NVENC 实测见文末；换机器或重建依赖后重新验证。8GB 显存部署保持单 Worker、单路实时识别；同时运行多个推理任务时仍需留意显存。

启用游客业务还需设置公众号授权、游客同源地址和付款方式；配置 `VISITOR_PAYMENT_PROVIDER=manual` 时由景区负责人或管理员确认付款，无需支付商户证书。真实登录仍需公众号配置，游客售价在运营端的景区服务面板设置，详见 [游客 H5](VISITOR_H5.md)。仅启动容器不会自动开放景区服务。

## 4. 原来的邮件能否继续使用

**可以复用原来的有效 SMTP 配置，无需因为切换 Docker 就更换邮箱。** 本机 Python 与 Docker 读取的环境文件不同：开发 `.env.local` 中已有邮件参数，不代表生产 `.env.prod` 已配置。注册发码失败时，先检查容器实际使用的邮件主机、端口和凭据是否齐备。

若已有可用配置，将邮件相关值复制到 `.env.prod`；下面以 465 SSL 为例，不要把真实凭据写入文档或提交 Git：

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=原来的邮箱账号
SMTP_PASSWORD=原来的SMTP授权码
SMTP_FROM=原来的发件邮箱
SMTP_USE_SSL=1
SMTP_USE_TLS=0
EMAIL_CODE_DEV_MODE=0
```

这里的中文内容是填写说明，不是有效配置值。使用原来的 SMTP 授权码，不要替换成网站登录密码。当前代码会为未显式指定 SSL/TLS 的 465 端口自动启用 SSL，但生产配置中明确写出更易检查。SMTP_USE_TLS 指 STARTTLS；465 的 SSL 模式下设为 0。若原服务使用 587 STARTTLS，应改为 SMTP_USE_SSL=0、SMTP_USE_TLS=1，并沿用邮件服务要求的地址与端口。

只迁移邮件相关字段，**不要用 .env.local 整体覆盖 .env.prod**，否则可能把数据库、向量库和任务队列改回开发配置。

然后执行：

```powershell
.\scripts\run_production.ps1
```

检查有效配置时，只输出主机、端口和是否填写密码，不打印密码：

```powershell
docker exec myreid-production-backend-1 python -c "from app.core.config import get_settings; s=get_settings(); print('host=',s.smtp_host,'port=',s.smtp_port,'ssl=',s.smtp_use_ssl,'starttls=',s.smtp_use_tls,'password_set=',bool(s.smtp_password),'dev=',s.email_code_dev_mode)"
```

最后在前端注册页用自己的真实邮箱请求验证码，收到邮件后完成注册。配置存在不等于投递成功；若停用了 SMTP、撤销了授权码或服务商拒绝连接，需要恢复 SMTP 或更新授权码。检查垃圾邮件目录，并避免连续重复请求验证码。

## 5. 注册、管理员与登录

1. 将 `.env.prod` 的 BOOTSTRAP_ADMIN_EMAIL 设置为你实际控制、能收到邮件的邮箱。
2. 按第 4 节配置 SMTP，再重新运行生产启动脚本。
3. 在前端退出旧登录状态，进入注册页面，使用该邮箱接收验证码并注册。
4. 当前代码在注册时将与 BOOTSTRAP_ADMIN_EMAIL 匹配的邮箱分配为 admin；其他邮箱注册为 operator。该参数不会自动创建账号，也不会自动提升已有账号权限。
5. 后续可使用注册时的账号/邮箱和密码登录，密码登录不依赖每次发送验证码。

基础系统与景区系统使用不同数据库、认证入口及会话，账号不会因为在另一模块注册过就自动存在。旧 SQLite 用户也不会自动出现于 PostgreSQL。需要保留旧数据时应另做迁移，不能通过切换开发模式代替迁移。

## 6. 查看状态、日志与停止

在当前 PowerShell 中定义一次命令参数：

```powershell
$ComposeArgs = @('compose', '--env-file', '.env.prod', '-p', 'myreid-production', '-f', 'docker-compose.yml', '-f', 'docker-compose.local-production.yml', '--profile', 'visitor')
docker @ComposeArgs ps
docker @ComposeArgs logs --tail 100 backend worker visitor-coordinator visitor-web
```

停止后端、游客服务及业务依赖（保留数据，独立摄像头网关继续运行）：

```powershell
.\scripts\stop_production.ps1
```

前端终端使用 Ctrl+C 停止。下次启动使用 `scripts/run_production.ps1`。

数据保存在 myreid-production_* 命名卷，删除容器和删除数据卷不是同一件事。不要使用 `down -v` 或清理生产数据卷。配置文件 `.env.prod`、`.env.local` 被 Git 忽略，仍需自行安全保存；不要共享包含密钥的完整 `docker compose config` 输出。

## 7. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 用户名或密码错误 / 401 | 先确认是否在新的生产数据库注册过；旧 SQLite 账号不可直接登录 |
| 旧会话刷新 401 | 退出旧账号再登录，生产 SECRET_KEY 与旧环境不同 |
| 注册发码 500，提示 SMTP_HOST 未配置 | 把真实邮件参数写入 .env.prod，重新执行启动脚本 |
| 发码 502 | 检查容器网络、465/587 配置及 SMTP 授权码，查看后端日志 |
| 前端连接失败 | 先检查 2889 的 healthz，再检查 5173/api/v1/healthz，核对前端端口配置并重启前端 |
| 修改 env 后没生效 | 用生产启动脚本重新创建有配置变化的容器，不是仅 docker restart |
| 找不到 myreid:production-gpu | 首次运行附加 -Build |
| 模型列表 404 或旧路径权重加载失败 | 旧镜像没有多模型接口。更新代码后运行启动脚本，会先构建同步镜像。实际 `.env.prod` 应设置 `REID_MODEL_DIR=/models/reid`、`CLIPREID_MODEL_PATH=/models/reid/ViT-MSMT17-60.pth`。模型文件与 `.model.json` 一起挂载。 |
| 镜像 Built，但 backend unhealthy，日志提示 Production requires VISITOR_COOKIE_SECURE | 生产模式与 HTTP 联调配置冲突，按上文选择完整的一组环境、Cookie 和入口配置 |
| 摄像头接入后报 object_storage 或 gallery PermissionError | 检查旧命名卷所有者；当前镜像用户为 10001:10001，旧 root 所有目录需一次性修正为该用户。不要删除数据卷或使用 chmod 777 |
| 启动脚本端口冲突 / access permissions | 启动前检查 backend `2889` 和 visitor-web `5174`；已运行的同项目同端口可重复启动。排查监听进程及 Windows TCP 保留范围，释放固定端口后重试。先停止同端口的开发服务器，不自动换端口，也不修改系统保留范围。 |
| readyz 队列或向量库未就绪 | 查看 worker、redis、milvus 日志，等待依赖启动；不启用模拟回退 |
| USB 摄像头不可用 | 生产启动会启动 Windows 采集代理与 MediaMTX；检查 tmp/camera-bridge/agent.log，详见 [Windows 摄像头接入](windows-camera-bridge.md) |

本机生产配置不等于已完成公网部署。公开访问还需域名、HTTPS、代理与备份等部署工作，见 [部署指南](DEPLOYMENT.md)。仅测试 ReID 时可将工作人员 `PAYMENT_PROVIDER` 留空，并清空未使用的 `WECHAT_PAY_*` 占位项；游客登录和支付单独配置。真实支付需另配商户参数、回调地址和证书挂载，不能使用模拟成功返回。

## 本机实测（2026-09-22）

以下保留当时的硬件、依赖和验证结果；不代表后续改动、当前主机或重新构建镜像已重新验收。

- Windows Python：PyTorch 2.12.0+cu130；本机 `.env.local` 为 `MODEL_DEVICE=cuda`。可用 `scripts/install_gpu.ps1` 重装并校验。
- 当时使用复用依赖层的 Docker 镜像，未验证完整冷构建。当前依赖以 `requirements.in`、`Dockerfile.cuda` 和 `scripts/install_gpu.ps1` 为准，不能将历史记录作为当前构建的验收结果。
- GPU：NVIDIA GeForce RTX 5060 Ti；CUDA 运算及模型推理已通过。
- 用户提供的 PedestrianTrackingVideo.avi：169 帧全部完成，单帧处理耗时 P50=46.6 ms、P95=65.74 ms；之前 CPU 为约 358/844 ms。输入视频为 1 FPS，播放按源时间输出，显示约 0.97 FPS，不代表 GPU 只能处理 1 FPS。
- readyz：ready，device=cuda，数据库/景区数据库/模型/队列/向量库均就绪，queue_mode=redis，无降级。
- 两个 PostgreSQL 数据库临时表读写、MinIO 上传/下载/删除、Milvus 写入/检索/项目隔离/删除、RQ 真 Worker 执行排队任务均通过；另实际排队运行 DSCL-ReID，输出为 `(1,512)`、设备 `cuda:0`，烟测数据已清理。
- NVENC 已实际编码 2 秒 1280×720 H.264 视频，FFprobe 校验通过。
- 已删除三个无容器使用的 myreid_codex_e2e 旧测试镜像；保留全部数据卷与当前生产镜像。

实时页面选择临时上传或项目视频后会显示首帧，不触发离线入库；切换来源或项目会丢弃过期预览响应。


Windows 本机 USB 摄像头现通过宿主机采集代理 → Docker MediaMTX → 后端 RTSP 接入。
启动、停止、故障恢复及真实验收方式见 [Windows 摄像头生产接入](windows-camera-bridge.md)。相机网关使用独立 Compose 项目，不随后端停止；不安装 Windows 登录自启动项。详见 [独立网关](camera-gateway.md)。


## 统一关闭与摄像头释放

如需同时关闭独立网关，在项目根目录运行：

```powershell
.\scripts\stop_production.ps1 -StopCameraGateway
```

此命令正常停止后端/worker、释放 Windows 摄像头、停止剩余 Docker 服务，保留所有数据卷。
下次使用 `.\scripts\run_production.ps1` 恢复。
停止 backend 不影响其他 RTSP 使用方。单独停止摄像头用 `run_camera_gateway.ps1 -Stop`；停止独立 MediaMTX 时代理会释放本地采集，服务恢复后重新发布。
关闭浏览器或桌面窗口本身不停止后台 Docker 服务；前端开发终端使用 Ctrl+C 退出。
