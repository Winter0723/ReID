<a id="top"></a>

# ReID · 行人重识别与景区视频运营系统

[项目介绍](#overview) · [功能概览](#features) · [源码结构](#structure) · [Docker 快速开始](#quick-start) · [邮箱与正式部署](#production) · [第一次使用](#usage) · [GPU 与相机](#hardware) · [更新与维护](#maintenance) · [常见问题](#faq) · [开发文档](#docs) · [支持项目](#support)

<a id="overview"></a>
## 项目介绍

MyReID 将行人重识别检索、实时相机采集和景区游览视频制作放在同一套系统中。你可以上传图片或视频查找人物，也可以接入相机持续采集，再通过景区工作台完成游客登记、片段匹配、视频精剪和人工收款交付。

项目包含 **FastAPI 后端、Vue/Tauri 运营工作台、Vue 游客 H5**。Docker 可以同时启动后端和两个网页；使用网页无需安装或编译桌面客户端。没有相机时，也可以先用上传素材体验检索和剪辑。

> 许可为 [AGPL-3.0-only](LICENSE)，第三方说明见 [NOTICE](NOTICE.md)。源码不包含模型权重、真实账号数据或部署密钥。本仓库是独立的纯净源码发行仓库，默认分支为 `main`；发布范围见[源码发布说明](docs/OPEN_SOURCE_RELEASE.md)。

<a id="features"></a>
## 功能概览

| 模块 | 可以做什么 |
| --- | --- |
| 人物检索 | 上传图片/视频、自动检测行人、素材去重、图片检索、结果预览、历史和报告导出 |
| 项目多模型 | 创建项目时选择 DSCL-ReID 或 MGD-DPM；各模型使用独立向量空间，DSCL-ReID 另支持文本检索 |
| 实时识别 | 框选参考人物，对 USB、RTSP 或视频进行单目标跟踪与实时预览 |
| 相机入库 | 连续录像、分段封存、采样索引、相机墙；未被引用的景区录像默认保留 12 小时 |
| 景区运营 | 独立账号与项目、游客登记、片段匹配、横竖版剪辑、配乐、版本冻结和失败重试 |
| 游客自助 | 浏览器登记、可选微信登录、结束游览、选片、制作、预览、申请付款和领取；关闭页面后后台继续处理 |
| 收款交付 | 当前使用工作人员核实收款并人工确认；订单固定购买版本，付款和领取分别记录 |
| 运行管理 | 角色与项目权限、审计日志、双数据库、Redis/RQ 队列、MinIO/S3、Milvus、健康检查和运行观测 |

模型在项目创建后固定，切换模型请新建项目。浏览器游客依赖当前浏览器 Cookie 保存身份，清除 Cookie 或更换设备后无法找回原记录；微信登录与浏览器登录是不同身份。当前游客流程默认不要求现场核验，用户在登记时提交照片并确认授权。

真实微信自动支付尚未接通验收，保留代码的状态见[支付说明](docs/WECHAT_PAYMENT_INTEGRATION.md)。以下教程统一使用人工确认收款。

<a id="structure"></a>
## 源码结构

```text
ReID/
├── FastAPIBackend/       # API、认证、模型推理、任务、数据库迁移与视频制作
├── TauriFrontend/        # Vue 运营工作台与 Tauri 桌面壳
├── VisitorFrontend/      # 游客 H5：登记、选片、制作与领取
├── model/                # 模型适配代码、描述文件；权重另行放置
├── camera_gateway/       # 独立摄像头网关
├── scripts/              # 配置生成、预检、启动、备份与维护工具
├── docs/                 # 配置、功能、开发与部署参考
├── legacy/               # 不再维护的 Qt/QML 与旧 API 归档
├── docker-compose*.yml   # 通用、生产、GPU、测试等部署组合
└── .env*.example         # 可以提交的配置模板
```

核心 API 为 `/api/v1`，景区 API 为 `/api/v1/scenic`，游客 API 为 `/api/v1/scenic/visitor`。核心与景区使用独立账号域和数据库，需要分别注册。

<a id="quick-start"></a>
## Docker 快速开始

**第一次体验建议完整走完本节。** 这条路线使用 CPU、本机网页、开发验证码和游客测试邀请码，不需要 SMTP、微信账号或显卡。准备好权重后，Docker 会启动 PostgreSQL 两库、Redis、MinIO、etcd、Milvus、API、索引/渲染 worker、维护服务、游客协调器和两个网页。

### 第一步：准备环境并获取源码

| 准备项 | 要求 |
| --- | --- |
| 系统 | Linux x86-64，或 Windows Docker Desktop 的 Linux 容器模式 |
| 软件 | Git、Docker、Docker Compose v2.30+、Python 3.11+；Python 只用于配置与校验脚本，无需额外安装 Python 包 |
| 资源 | 建议从 16 GB 内存、40 GB 可用磁盘起步；视频和录像空间另算，CPU 推理速度取决于硬件 |
| 网络 | 首次构建需要访问容器仓库、Debian、PyPI 和 npm；CPU 镜像当前也会安装锁文件中的 CUDA 运行库，下载量较大 |

Docker 会安装应用运行所需的 Python 依赖、Node 构建环境、FFmpeg 和中文字体。ARM/macOS 原生镜像与跨架构模拟不在本教程已确认的运行范围内。

先确认 Docker 引擎已经启动：

```sh
git --version
docker version
docker compose version
python --version
```

获取纯净开源版本：

```sh
git clone https://github.com/Winter0723/ReID.git
cd ReID
```

已有工作目录请跳到[更新与维护](#maintenance)，不要重新生成或覆盖已有配置。后续命令均在仓库根目录执行；Windows 若只有 `py -3`，将命令中的 `python` 替换为 `py -3`。下面的单行 Docker 命令可直接在 PowerShell 或 Bash 中执行。

### 第二步：放置并校验模型权重

选择下方任一网盘获取模型包。下载后按本节目录放置并校验；网盘包如果带有外层文件夹，请将里面的权重放到指定位置。

| 下载方式 | 地址 | 提取码 |
| --- | --- | --- |
| 夸克网盘 | [下载 ReID 模型](https://pan.quark.cn/s/d5cef630d149) | 无 |
| 百度网盘 | [下载 ReID 模型](https://pan.baidu.com/s/1aL_Idla5IHhdmNG5Q7hgbw) | `y26r` |

模型用途、来源与兼容性见 [model/README.md](model/README.md)。下载入口不改变模型原有许可；源码仓库不直接存放权重。

将基础权重放成以下结构，保留源码自带的模型描述文件：

```text
ReID/
├── FastAPIBackend/
│   └── yolov8n.pt
└── model/
    ├── ViT-MSMT17-60.pth
    ├── clipreid-msmt17.model.json
    ├── mgddpm-market2msmt.model.json
    └── clip_weights/
        └── ViT-B-16.pt
```

```sh
python scripts/check_models.py
```

**成功现象：**三份基础权重都显示 `Verified`。报缺失、大小错误或 SHA256 不符时先修正文件；压缩包不要多套一层目录，也不要用改名方式替代兼容权重。

MGD-DPM 是可选模型，需要额外放置 `model/Market2MSMT.pth`，配套描述文件已在源码中。基础校验脚本只校验上述三份文件；MGD-DPM 的可用性由应用模型目录检查，在创建项目时查看模型列表。未放置 MGD-DPM 权重不影响使用默认 DSCL-ReID。

Docker 模板已经设置好容器内路径：

| 配置 | 保持的值 |
| --- | --- |
| `YOLO_MODEL_PATH` | `/models/yolov8n.pt` |
| `REID_MODEL_DIR` | `/models/reid`，对应宿主机整个 `model/` 目录 |
| `CLIPREID_MODEL_PATH` | `/models/reid/ViT-MSMT17-60.pth` |
| `REID_DEFAULT_MODEL_ID` | `clipreid-msmt17`，界面显示 DSCL-ReID |
| `MODEL_DEVICE` / `WORKER_MODEL_DEVICE` | `cpu` / `cpu` |
| `CLIP_CACHE_DIR` | 留空，沿用已有基础权重挂载 |

这些是容器路径，不要改成宿主机盘符。Linux 权重文件需允许容器 UID 10001 读取。模型目录、描述文件和基础权重的关系见[模型说明](model/README.md)。

### 第三步：生成本机配置

```sh
python scripts/prepare_deployment.py local
python scripts/check_deployment.py --env-file .env.docker-local
```

生成器会创建 `.env.docker-local`，填入独立的随机密钥、数据库密码、存储密码和游客测试邀请码；**已有文件会被拒绝覆盖**。预检应显示 `Deployment preflight passed`。

首次体验保留默认值即可：

| 配置 | 本机行为 |
| --- | --- |
| `EMAIL_CODE_DEV_MODE=1` | 注册/登录验证码显示在管理界面，不发送邮件 |
| `BOOTSTRAP_ADMIN_EMAIL=` | 空白数据库中，每个账号域的第一个注册用户成为管理员 |
| `VISITOR_AUTH_PROVIDER=test` | 游客使用测试邀请码和游客 A/B 身份登录 |
| `VISITOR_TEST_INVITE_CODE` | 已随机生成，在自己的 `.env.docker-local` 中查看并用于游客登录 |
| `PAYMENT_PROVIDER=manual` / `VISITOR_PAYMENT_PROVIDER=manual` | 工作人员人工确认收款 |
| `DATABASE_URL`、`SCENIC_DATABASE_URL`、`S3_*`、`REDIS_URL`、`MILVUS_URI` | 已连接 Compose 内置服务，不需购买外部数据库或对象存储 |

`.env.docker-local` 是私有配置，不提交到 Git。不要将给宿主机开发使用的 `.env.local` 直接套进 Docker；容器中的 `127.0.0.1` 只代表当前容器。

### 第四步：构建并启动整套服务

```sh
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor config --quiet
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor up -d --build --wait --wait-timeout 900
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor ps -a
```

第一条没有输出并正常退出表示配置解析通过；第二条首次会下载并构建镜像。`--wait-timeout 900` 是启动等待时间，不代表整个镜像构建一定能在 15 分钟内完成。

**成功现象：**常驻容器运行，带健康检查的服务显示 healthy；`database-init` 显示 `Exited (0)` 正常，它只执行一次建库任务。

| 入口 | 地址 |
| --- | --- |
| 运营工作台 | [http://127.0.0.1:5173/](http://127.0.0.1:5173/) |
| 游客 H5 | [http://127.0.0.1:5174/visitor/](http://127.0.0.1:5174/visitor/)；实际登记需使用景区专属入口 |
| 就绪检查 | [http://127.0.0.1:2889/api/v1/readyz](http://127.0.0.1:2889/api/v1/readyz) |
| API 文档 | [http://127.0.0.1:2889/docs](http://127.0.0.1:2889/docs) |

在浏览器打开就绪检查，确认 `status` 为 `ready`，`database_ready`、`scenic_database_ready`、`models_ready`、`queue_ready`、`vector_store_ready`、`disk_ready` 为 `true`，`queue_mode` 为 `redis`。只有首页能打开或 `/healthz` 返回成功，还不足以确认任务能运行。

端口固定为 `2889`、`5173`、`5174`，默认只绑定本机回环地址。手机上的 `127.0.0.1` 是手机自身，不能用它访问电脑；先在电脑浏览器验证游客页面。跨设备访问另按[正式部署](#production)配置域名与 HTTPS。已有服务占用这些端口时，先确认并停止重复实例，不要同时运行 Docker 网页和同端口的 Vite 网页。

### 第五步：注册并完成第一条业务流程

打开运营工作台，选择核心 ReID 工作台，点击注册，填写用户名、邮箱、密码，获取界面显示的开发验证码并完成注册。默认本机配置下，第一个核心账号是管理员；切换景区工作台后需要另行注册，第一个景区账号也是管理员。

先创建一个默认 DSCL-ReID 项目，上传一张有人物的图片或一段短视频，等待入库任务完成，再上传参考照片进行图片检索。能看到对应结果后，再进入[第一次使用](#usage)体验完整景区流程。

<a id="production"></a>
## 邮箱与正式部署

正式部署复用前面的源码和模型步骤，使用独立的 `.env.prod` 与 `myreid-prod` 数据卷。**本机体验数据不会自动迁入正式环境。** 如果已经在使用 Windows GPU 专用部署，请继续使用原 `myreid-production` 项目和[专用启动脚本](docs/local-production.md)，不要用这里的项目名替换它。

### 1. 生成生产配置并填写邮箱

```sh
python scripts/prepare_deployment.py production
```

已有 `.env.prod` 时保留原文件，对照 `.env.prod.example` 补齐缺项。编辑 `.env.prod`，随机密码和 `SECRET_KEY`、`METRICS_TOKEN` 已由生成器设置；部署者还需填写：

| 参数 | 如何填写 |
| --- | --- |
| `BOOTSTRAP_ADMIN_EMAIL` | 自己能收信的管理员邮箱；之后用此邮箱分别注册核心/景区账号，没有默认管理员密码 |
| `SMTP_HOST` | 邮件服务商提供的 SMTP 服务器地址 |
| `SMTP_PORT` | 服务商要求的端口，常见为 587 或 465 |
| `SMTP_USERNAME` | 发件账号，通常是完整邮箱地址 |
| `SMTP_PASSWORD` | 服务商提供的 SMTP 授权码或应用密码；按服务商要求开启 SMTP 服务 |
| `SMTP_FROM` | 该账号被允许使用的发件地址，通常与 `SMTP_USERNAME` 相同 |
| `SMTP_USE_TLS` / `SMTP_USE_SSL` | 587 的 STARTTLS 通常为 `1/0`；465 的隐式 TLS 通常为 `0/1`，以服务商要求为准 |
| `SCENIC_ORDER_PRICE_CENTS` / `SCENIC_ORDER_CURRENCY` | 明确工作人员订单售价与币种；金额单位为分，`990` 表示人民币 9.90 元 |

例如使用服务商的 465 端口时，在文件中修改原有各行，不要重复追加同名键：

```dotenv
BOOTSTRAP_ADMIN_EMAIL=your-admin@example.com
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=465
SMTP_USERNAME=your-sender@example.com
SMTP_PASSWORD=replace-with-your-smtp-authorization-code
SMTP_FROM=your-sender@example.com
SMTP_USE_TLS=0
SMTP_USE_SSL=1
EMAIL_CODE_DEV_MODE=0
SCENIC_ORDER_PRICE_CENTS=990
SCENIC_ORDER_CURRENCY=CNY
PAYMENT_PROVIDER=manual
VISITOR_PAYMENT_PROVIDER=manual
```

以上邮箱、主机和授权码都是占位示例，需替换为真实值。`BOOTSTRAP_ADMIN_EMAIL` 是收验证码的管理员地址，`SMTP_FROM` 是系统发送邮件的地址，两者可以不同。

保留 `APP_ENV=production`、`ALLOW_LOCAL_JOB_FALLBACK=0`、`VISITOR_COOKIE_SECURE=1`、`VISITOR_ALLOW_LAN_HTTP=0`。内置 PostgreSQL、Redis、MinIO 和 Milvus 的连接配置继续保留模板值；不要将已有数据库密码随意重新生成，修改配置文件不会同步修改数据卷里的数据库用户密码。

### 2. 先启动管理端并验证真实邮件

没有公网域名和微信配置时，可以先在服务器本机完成管理端验收：

```sh
python scripts/check_deployment.py --env-file .env.prod --staff-only
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml --profile staff config --quiet
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml --profile staff up -d --build --wait --wait-timeout 900
```

管理端仍为 `http://127.0.0.1:5173/`。远程服务器可通过 SSH 隧道访问；使用 `BOOTSTRAP_ADMIN_EMAIL` 获取验证码，确认收到邮件并分别注册两个工作台的管理员。`--staff-only` 只跳过游客 OAuth 配置检查，不会关闭生产安全约束，也不证明邮件已经投递成功。

本机体验与生产栈使用相同固定端口，切换前请用原参数 `stop` 停止正在运行的另一套环境。

### 3. 配置游客入口与 HTTPS

当前游客端支持浏览器会话；配置好公众号网页授权后也可微信登录。`wechat` 模式下“开始登记”同样可以创建浏览器身份，“使用微信登录登记”是另一个入口。**浏览器身份不等于微信身份。**

下面走完整生产预检支持的“公众号配置 + HTTPS + 人工收款”路线：当前 `check_deployment.py` 在未加 `--staff-only` 时会要求微信 AppID/Secret，即使应用本身支持浏览器登录。没有这些条件时，先完成本机体验和管理端测试；不要用虚假参数通过预检。

| 配置或外部设置 | 内容 |
| --- | --- |
| `VISITOR_AUTH_PROVIDER` | 保留 `wechat`；生产禁用 `test` |
| `VISITOR_WECHAT_APPID` / `VISITOR_WECHAT_SECRET` | 具有相应网页授权能力的微信应用配置；这是登录配置，人工收款不需要支付商户证书 |
| `VISITOR_PUBLIC_BASE_URL` | 真实 HTTPS 游客站点根地址，如 `https://visit.example.com`，不带 `/visitor/` |
| 微信网页授权域名 | 配置对应游客域名；回调路径为 `/api/v1/scenic/visitor/auth/callback` |
| `STAFF_DOMAIN` / `VISITOR_DOMAIN` | 使用内置 HTTPS 网关时填写两个不同域名，只填主机名，例如 `staff.example.com` / `visit.example.com` |
| `ACME_EMAIL` | 自动签发证书的通知邮箱 |
| `REALTIME_ORIGINS` | 在原逗号分隔列表中追加管理端来源，例如 `https://staff.example.com` |
| `SCENIC_PUBLIC_BASE_URL` | 工作人员交付链接使用的管理端基址，例如 `https://staff.example.com` |

将两个域名的 DNS 指向服务器，开放 TCP 80/443（HTTP/3 可选 UDP 443）。示例域名必须换成自己的域名，然后执行：

```sh
python scripts/check_deployment.py --env-file .env.prod
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.https.yml --profile staff --profile visitor config --quiet
docker compose --env-file .env.prod -p myreid-prod -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.https.yml --profile staff --profile visitor up -d --build --wait --wait-timeout 900
```

Caddy 负责 HTTPS，管理域名转向运营网页，游客域名转向游客网页及游客 API。不要额外公开数据库、Redis、MinIO 和 Milvus 端口。已有 HTTPS 代理时可省略 HTTPS 覆盖文件，按[新机器部署指南](docs/NEW_MACHINE_DEPLOYMENT.md)转发至两个本机端口。

`WECHAT_PAY_*` 在人工模式下保持空值，不叠加 `docker-compose.wechat.yml`。真实邮件、证书签发、微信登录和手机领取需要在具备相应条件的目标环境实测。

<a id="usage"></a>
## 第一次使用

### 核心 ReID：从素材到检索结果

1. 登录核心工作台，创建项目并选择可用模型。默认选择 DSCL-ReID；只有安装对应权重后才能选择 MGD-DPM。
2. 上传图片或短视频，等待后台任务完成入库，确认素材与检测结果出现。
3. 上传参考人物照片，框选目标后搜索；DSCL-ReID 项目也可尝试文本搜索。
4. 查看相似度、来源素材和对应时间，预览结果，按需导出报告或查看历史。
5. 需要持续采集时，再配置相机项目。实时单目标跟踪与持续录像入库是不同任务，按使用目的选择入口。

### 景区与游客：从登记到短片领取

1. 登录景区工作台，创建景区项目。在游客服务设置中确认服务已启用，填写展示说明、拍摄授权文本和价格并保存，获取景区专属入口链接或二维码。
2. 在电脑浏览器打开该入口。本机体验输入 `.env.docker-local` 中的测试邀请码，并选择游客 A 或 B；正式环境可使用浏览器身份或已配置的微信登录。
3. 登记照片、框选人物并确认授权。当前默认 `VISITOR_REQUIRE_STAFF_VERIFICATION=0`，无需现场核验步骤。
4. 在本次游览时间窗口内采集或准备包含参考人物的素材，等待入库，再结束游览。没有对应人物、时间窗口内素材或可用索引时，不会凭空生成匹配片段。
5. 游客选择匹配片段，调整横竖版、字幕/配乐等可用编辑项并确认制作；等待横版和竖版完成，检查预览。后台协调器与渲染 worker 共同推进任务。
6. 游客发起人工付款请求，工作人员在系统外核实到账后，由有权限的管理员/景区负责人确认，游客随后下载并确认领取。

工作人员也可以从景区后台登记、匹配并制作短片。更完整的编辑操作见[景区精剪指南](docs/SCENIC_EDITING.md)，游客流程见[游客手册](docs/VISITOR_H5.md)。

<a id="hardware"></a>
## GPU 与相机配置

CPU 路线可以先跑通业务。需要 NVIDIA GPU 或 Windows USB 相机时，使用 [Windows 本机生产指南](docs/local-production.md)和 `scripts/run_production.ps1`；它使用 CUDA 镜像、GPU 设备配置及相机网关。仅修改 `MODEL_DEVICE=cuda` 不能让通用 CPU 栈自动获得 GPU。

网络相机在私有环境文件中配置 ID 与视频源映射，例如：

```dotenv
CAMERA_SOURCES={"0":"0","entrance":"rtsp://USER:PASSWORD@CAMERA_HOST/live"}
MYREID_CAMERA_SOURCE=entrance
CAMERA_AUTO_START=0
SCENIC_CAMERA_AUTO_START=1
```

按真实相机替换地址和凭据，在界面选择 `entrance`。修改后用原 Compose 参数重新执行 `up -d`。示例中的 `0` 不代表 Docker 已连接 USB；容器无法自动使用浏览器所在设备的摄像头。Linux USB 需要设备映射和 video 组权限，Windows 可使用[独立摄像头网关](docs/camera-gateway.md)。

`CAMERA_AUTO_START` 控制旧中央默认相机，`CORE_CAMERA_AUTO_START` 和 `SCENIC_CAMERA_AUTO_START` 分别控制项目相机自动采集；仅做离线素材测试时可以关闭相应项目开关。录像留存规则见[相机保留策略](docs/camera-retention.md)。

<a id="maintenance"></a>
## 更新与维护

以下命令对应本教程的 `myreid-local`。生产环境请始终使用启动时相同的环境文件、项目名、Compose 覆盖文件和 profiles；Windows GPU 专用环境继续使用其启动/停止脚本。

查看状态与最近日志：

```sh
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor ps -a
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor logs --tail 100 backend worker maintenance visitor-coordinator
```

停止与恢复服务（保留数据）：

```sh
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor stop
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor up -d --wait --wait-timeout 900
```

常驻服务使用 `restart: unless-stopped`，一次性 `database-init` 保持 `restart: "no"`。Docker 引擎启动后，未被手动停止的常驻容器会按策略恢复；手动 `stop` 后需要显式 `up -d`。`unhealthy` 状态本身不触发重启，进程仍在运行但不就绪时需要查看日志。这个策略不代替 Docker Desktop 开机启动，也不保证外接相机网关和进行中的制作任务已恢复，应重新检查就绪状态及任务结果。

更新已有 `main` 部署前先按[备份恢复指南](docs/BACKUP_RESTORE.md)备份数据与私有配置，记录当前提交；随后在工作区无待保留的未提交修改时执行：

```sh
git status --short
git switch main
git pull --ff-only origin main
python scripts/check_models.py
python scripts/check_deployment.py --env-file .env.docker-local
docker compose --env-file .env.docker-local -p myreid-local -f docker-compose.yml --profile staff --profile visitor up -d --build --wait --wait-timeout 900
```

已有配置不重新生成，新增变量对照模板补齐。修改环境变量或镜像后需要 `up -d` 重建相应容器，单独 `restart` 不会重新注入配置。更换 Compose 项目名会使用另一组卷，看起来像全新系统；不要用删除数据卷解决账号、密码或配置问题。

备份工具使用**整栈停机后的命名卷快照**，要求明确快照目录。它覆盖内置数据库、对象、队列和向量相关命名卷；模型、私有配置、外部 S3 与其他 bind mount 需要另外备份。准确的停止、备份、空卷恢复命令统一见 [BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)，不要在日常停止命令中添加 `down -v`。个人照片与视频留存另见[数据清理说明](docs/DATA_RETENTION.md)。

<a id="faq"></a>
## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `prepare_deployment` 提示文件已存在 | 保留已有私有配置，对照模板补缺项；生成器不会覆盖密码 |
| `Missing or wrong-size model` / `Configured model file is not mounted` | 检查解压层级、文件名和 SHA256，容器 ReID 路径为 `/models/reid/...` |
| 模型列表中 MGD-DPM 不可用 | 补齐兼容的 `Market2MSMT.pth` 和描述文件，刷新模型列表；默认 DSCL-ReID 可以独立使用 |
| 首次构建耗时很长 | 查看镜像与依赖下载进度、网络和 Docker 磁盘；镜像较大，尚在下载时不是服务启动失败 |
| `address already in use` / 端口绑定失败 | 检查旧栈、Vite、已有进程或 Windows 保留端口；释放本教程固定端口后重试 |
| 页面能开，`readyz` 不是 `ready` | 查看模型、双库、Redis、向量库和磁盘字段及日志，给 CPU 模型加载留出时间 |
| 注册收不到邮件 | 本机体验查看页面开发验证码；生产检查 SMTP 授权码、端口/TLS、发件地址权限、网络和垃圾邮件 |
| 登录/验证码提示请求过于频繁 | 等待限流窗口结束，避免反复点击；当前代理下可能共享 IP 额度，公网代理与多用户限流仍需目标环境验证 |
| 后注册的账号不能创建项目 | 默认公开注册角色为 viewer；由管理员配置角色/项目权限，正式环境用引导管理员邮箱注册 |
| 游客页面没有具体景区 | 使用后台生成的项目链接或二维码，游客根页面不会自动创建景区 |
| 任务一直排队或制作无进展 | 检查 worker、visitor-coordinator、maintenance 是否运行，以及索引/渲染日志；Redis 可连接不代表有人消费队列 |
| 手机打不开电脑上的页面 | 本机端口只绑定回环地址；跨设备使用已配置的 HTTPS 入口，不能在手机上用电脑的 `127.0.0.1` 地址 |
| 支付没有自动扣款 | 当前为人工确认，核实收款后由有权限的工作人员确认订单 |

提交问题时附上版本、系统、部署方式、脱敏日志和复现步骤。不要公开完整 `.env`、含密钥的 Compose 展开结果、账号数据或游客素材。安全问题按 [SECURITY.md](SECURITY.md)处理。

<a id="docs"></a>
## 开发与更多文档

只修改 Python/Vue 代码、希望不用完整 Docker 栈时，可使用 SQLite 本机开发；Python 3.11、Node.js 22、FFmpeg/中文字体和可选 Tauri/Rust 环境的完整步骤见[开发指南](docs/DEVELOPMENT.md)。旧版 Qt/QML 和 pre-v1 API 位于 `legacy/`，不在当前维护范围。

| 文档 | 内容 |
| --- | --- |
| [文档导航](docs/README.md) | 按使用场景查找文档 |
| [空白机器部署](docs/NEW_MACHINE_DEPLOYMENT.md) | 必填参数、环境区别与部署验收细节 |
| [配置说明](docs/CONFIGURATION.md) / [完整变量表](docs/CONFIGURATION_REFERENCE.md) | 加载优先级、模板与设置参考 |
| [部署与升级](docs/DEPLOYMENT.md) | 服务组合、迁移与升级 |
| [模型说明](model/README.md) | 权重来源、目录、兼容性与新增模型 |
| [API 文档](docs/API.md) | 三端接口契约 |
| [运营桌面端](TauriFrontend/README.md) / [游客端](VisitorFrontend/README.md) | 各端开发与构建 |
| [项目总指南](docs/PROJECT_GUIDE.md) | 数据、日志、运行产物与模块定位 |
| [备份恢复](docs/BACKUP_RESTORE.md) / [数据留存](docs/DATA_RETENTION.md) | 一致性快照、恢复演练与清理 |
| [贡献说明](CONTRIBUTING.md) / [源码发布](docs/OPEN_SOURCE_RELEASE.md) | 参与开发、测试与发布边界 |

<a id="support"></a>
## 支持项目

如果这个项目对你有帮助，欢迎 Star、反馈问题、完善文档或提交修复。也可以扫描下方收款码，自愿赞助项目维护。

<table>
  <tr><th>支付宝赞助</th><th>微信赞助</th></tr>
  <tr>
    <td><img src="docs/assets/sponsorship/alipay.jpg" alt="支付宝赞助收款码" width="260" /></td>
    <td><img src="docs/assets/sponsorship/wechat.png" alt="微信赞助收款码" width="260" /></td>
  </tr>
</table>

[查看支付宝原图](docs/assets/sponsorship/alipay.jpg) · [查看微信原图](docs/assets/sponsorship/wechat.png)

赞助完全自愿，不影响功能使用，也不代表购买服务或获得额外授权。这些收款码用于支持项目维护，与部署者的景区订单收款流程分开。感谢你的支持。

[返回顶部](#top)
