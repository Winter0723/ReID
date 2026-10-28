# 部署、升级与恢复

本指南覆盖 `allinone` 三端。Windows + Docker Desktop + NVIDIA + USB 相机使用 [本机生产指南](local-production.md)；隔离游客联调使用 [游客手册](VISITOR_MANUAL_TEST.md)。变量含义见 [配置参考](CONFIGURATION.md)。除桌面构建外，命令从仓库根目录执行。

## 服务职责与部署组合

| 服务 | 职责与运行要求 |
| --- | --- |
| `backend` | HTTP、实时 WebSocket、相机入库、录像封存/发布、保留清理；单个 Uvicorn 进程持有相机与会话状态。 |
| `worker` | RQ 离线索引与精剪渲染；队列、双数据库和对象目录与 API 一致。 |
| `visitor-coordinator` | 每秒调度持久化游客请求，推进匹配和制作结果检查；属于 `visitor` profile。 |
| `visitor-web` | Nginx 静态 H5 与游客 API 同源代理；属于 `visitor` profile，默认监听宿主机 `127.0.0.1:5174`。 |
| PostgreSQL / `database-init` | Core、Scenic 两库 / 幂等建库；游客表位于 Scenic 库。 |
| Redis、MinIO/S3、Milvus、etcd | 队列、媒体对象、向量及向量服务元数据。 |

API、worker、协调器共享 `backend_objects`、`backend_gallery` 卷；对象目录也存跨进程锁、缓存和封存录像。当前调度面向单主机/共享文件系统，不能直接拆到没有共享锁目录的主机。基础 Compose 的 Redis 使用 `redis_data` 持久卷和 AOF；恢复后仍须核对未完成任务，数据库、队列和对象需采用一致的备份策略。

| 用途 | Compose 组合 | 说明 |
| --- | --- | --- |
| 开发 | `docker-compose.yml` | PostgreSQL + Redis + MinIO + Milvus 完整栈。 |
| 挂载源码开发 | 基础 + `docker-compose.dev.yml` | API/worker 使用本地对象和 JSON 向量；基础依赖服务仍启动。 |
| 预发 / 通用生产 | 基础 + `docker-compose.staging.yml` / `docker-compose.prod.yml` | 生产校验、支付证书与重启策略。 |
| Windows GPU 生产 | 基础 + `docker-compose.local-production.yml` | `myreid-production` 项目；脚本管理桥接和游客 profile。 |
| 游客联调 | 基础 + `docker-compose.visitor-test.yml` | `myreid-visitor-test` 独立项目/卷；API `22889`、H5 `25174`。 |
| HTTP 冒烟 | 基础 + `docker-compose.e2e.yml` | 对隔离环境执行真实 HTTP 业务脚本。 |

## 模型与依赖

源码和 Docker 镜像均不包含模型权重。按[模型说明](../model/README.md)取得三个兼容模型后，先校验，再由 Compose 只读挂载：

```bash
python scripts/check_models.py
```

| 仓库文件 | 容器位置 |
| --- | --- |
| `FastAPIBackend/yolov8n.pt` | `/models/yolov8n.pt` |
| `model/ViT-MSMT17-60.pth` | `/models/ViT-MSMT17-60.pth` |
| `model/clip_weights/ViT-B-16.pt` | `/workspace/model/clip_weights/ViT-B-16.pt` |

文件非空不等于权重已下载，LFS 指针同样非空。容器入口拒绝缺失文件及以 `version https://git-lfs.github.com/spec/` 开头的指针。替换要求见 [模型说明](../model/NOTICE.md)。两种 Dockerfile 均使用 Python 3.11、带哈希的依赖锁、FFmpeg/FFprobe 和 Noto CJK；GPU 版本另装 CUDA PyTorch，还需主机驱动与容器 GPU 访问能力。基础依赖已含 `lap`，景区人物分析仍需显式开启。

## 通用生产与预发

首次部署复制 `.env.prod.example` 为 `.env.prod`，填写真实数据库、对象存储、SMTP、管理员邮箱、随机签名密钥和 metrics token；已有部署保留现有配置。Core/Scenic 使用不同非 SQLite 数据库，关闭本地任务 fallback、开发验证码和旧 API，并显式配置订单金额和币种。

通用 prod/staging 使用人工收款确认，不要求微信支付证书。`docker-compose.wechat.yml` 单独声明微信支付 PEM secret；真实微信支付仍需完成[支付接入与验收](WECHAT_PAYMENT_INTEGRATION.md)，不得仅凭配置文件判断已经可用。

生产启动示例（Bash）：

```bash
export MYREID_ENV_FILE=.env.prod
docker compose --env-file .env.prod -p myreid-production \
  -f docker-compose.yml -f docker-compose.prod.yml config --quiet
docker compose --env-file .env.prod -p myreid-production \
  -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

`--env-file` 提供数据库密码、MinIO、secret 路径等 `${...}` 插值，`MYREID_ENV_FILE` 为基础服务选择同一份容器环境文件，也可写进 `.env.prod`。只设置服务级 `env_file` 不会更新 Compose 插值。预发使用独立配置与项目：

```bash
export MYREID_ENV_FILE=.env.staging
docker compose --env-file .env.staging -p myreid-staging \
  -f docker-compose.yml -f docker-compose.staging.yml up --build -d
```

基础 API 默认绑定 `127.0.0.1:2889`，对外访问应通过 HTTPS 代理。同机运行多套环境需分配不同宿主机端口。Prometheus 抓取 `/metrics` 必须发送 `Authorization: Bearer <METRICS_TOKEN>`。

## 游客 H5 与桌面包

在同一环境文件配置 `VISITOR_PUBLIC_BASE_URL`、登录/支付 provider、公众号信息与 Secure Cookie，然后增加 profile：

```bash
export MYREID_ENV_FILE=.env.prod
docker compose --env-file .env.prod -p myreid-production \
  -f docker-compose.yml -f docker-compose.prod.yml \
  --profile visitor up --build -d
```

游客 HTTPS 域名转发到 `127.0.0.1:5174`。附带 Nginx 只提供 `/visitor/` 与 `/api/v1/scenic/visitor/`，运营 API、`/docs` 和相机预览不经游客入口开放。页面、API、OAuth 回调同源。游客 JSAPI 通知路径为 `/api/v1/scenic/visitor/payments/wechat/notify`；人工支付无需商户配置，真实微信登录仍需公众号配置，见 [游客说明](VISITOR_H5.md)。

Windows 本机生产直接运行 `scripts/run_production.ps1 -Build`，已包含游客 profile、CUDA 镜像和宿主机桥接初始化。不要用裸 `docker compose up` 替代该生命周期入口。

运营桌面包：

```bash
cd TauriFrontend
npm ci
VITE_MYREID_API_BASE_URL=https://api.example.com npm run tauri:build
```

`tauri:build` 包装命令校验非本机 HTTPS origin，并生成精确 HTTP capability/CSP。安装包位于 `src-tauri/target/release/bundle/`。Windows CI 的示例域名仅用于编译验证，不能实际发行；`npm run build` 只生成网页静态产物。

## 升级与验证

1. 备份双库、对象、向量和必要配置，记录当前版本；安排暂停写入，避免迁移、录像清理与备份交错。
2. 更新源码和模型，用原项目名与文件组合重建 API、worker、协调器及游客网页；修改 env 后执行 `up -d`，`restart` 不重新注入变量。
3. `database-init` 幂等创建 Scenic 库，包括已有 PostgreSQL 卷；API entrypoint 默认升级 `core@head`、`scenic@head`。worker 的 `RUN_MIGRATIONS_ON_START=0` 仅关闭入口脚本迁移，应用初始化仍有域迁移/锁保护。
4. 核对模型设备、双库、队列消费、相机及业务流程。最新 head 和手动升级见 [数据库手册](DATABASE_SPLIT_RUNBOOK.md)。

```bash
docker compose --env-file .env.prod -p myreid-production \
  -f docker-compose.yml -f docker-compose.prod.yml --profile visitor ps -a
docker compose --env-file .env.prod -p myreid-production \
  -f docker-compose.yml -f docker-compose.prod.yml --profile visitor \
  logs --tail 100 backend worker visitor-coordinator
```

`database-init` 显示 `Exited (0)` 正常。容器 healthcheck 只访问 `healthz`，还要检查 `/api/v1/readyz` 内容；`queue_mode=redis` 不证明 worker 已在消费。再完成一次核心上传/索引/检索和一次游客登记/选片/双版制作/付款/领取，硬件、微信和跨设备下载应记录实际验收结果。

## 备份与恢复

`scripts/backup_restore.sh` 为 Bash 工具。Windows 可在已配置 Docker 访问的 WSL/Bash 使用；必须选择与部署相同的项目和文件：

```bash
export COMPOSE_PROJECT_NAME=myreid-production
export MYREID_ENV_FILE=.env.prod
export MYREID_COMPOSE_ENV_FILE=.env.prod
export MYREID_COMPOSE_FILES='docker-compose.yml;docker-compose.prod.yml'
scripts/backup_restore.sh backup
```

本机 GPU 部署将文件组合改为 `docker-compose.yml;docker-compose.local-production.yml`。输出默认在 `backups/`：两个 SQL gzip dump 加容器内 `gallery`、`object_storage` 归档，实际目录为 `/workspace/FastAPIBackend/`。它不备份远端 S3/MinIO bucket、Milvus/etcd、Redis、secret 或外部模型，也不暂停写入或提供跨双库/文件的原子快照。

恢复先选择隔离目标、准备空库及可写资源卷：脚本用普通 `psql` 导入，不会清库，不是覆盖/合并工具。PostgreSQL 与 backend 容器需可供 `exec` 使用；避免 backend 提前迁移建表后再导入同一库，必要时用维护容器恢复资源。确认目标后执行：

```bash
scripts/backup_restore.sh restore \
  backups/myreid_core_db_TIMESTAMP.sql.gz \
  backups/myreid_scenic_db_TIMESTAMP.sql.gz \
  backups/myreid_assets_TIMESTAMP.tar.gz
```

随后恢复外部对象与向量，核对行数、订单金额和引用，完成检索与领取验收再切换服务。不要对正在服务的库试运行恢复。SQLite 开发备份及 PostgreSQL 原生 dump 方式见数据库手册。

## 停止与保留数据

普通 Compose 使用原参数 `stop` 或 `down`，不加 `-v` 时保留命名卷；包含游客容器时保留 `--profile visitor`。Windows 本机生产使用 `scripts/stop_production.ps1`，先给后端封存时间再释放摄像头并停止容器。关闭前端窗口不会停止后台采集和制作。


## 多模型升级

1. 备份两域数据库、向量库和模型文件，暂停相机入库，处理完旧队列；统一升级 API 和 worker。
2. 迁移为两域项目新增模型绑定字段；旧行保持未核验，不会自动认领当前默认模型。历史媒体仍可浏览。
3. 将权重与 `*.model.json` 一起放入模型目录。Docker 现在挂载整个 `./model` 到 `/models/reid`。真实部署环境须同步新增环境变量，不能只更新示例文件。
4. 核验旧项目所用原权重 SHA256、图像尺寸、标准化、输出和精度。执行下方预览，确认输出描述与历史记录一致后追加 `--apply`。此命令是对来源的显式确认；只允许未绑定项目，保留原 collection/JSON，无需重新提取。来源不明的项目保持未核验，可新建项目重新导入素材。

```sh
python scripts/bind_legacy_models.py --scope core --project-id 1 --model-id clipreid-msmt17 --expected-sha256 <已核验原权重哈希>
# 景区单独使用 --scope scenic。确认预览后追加 --apply。
```

5. 新建两个模型项目验证入库和检索；用已标注正负样本分别校准相似度阈值，不将两个模型的分数直接比较。缓存容量是每进程计算的，模型资源不足会明确失败。

开发和本次功能发布只在 `allinone` 进行。纯净分支/独立源码副本只定期发布用户测试确认的版本，不随开发提交自动同步。
