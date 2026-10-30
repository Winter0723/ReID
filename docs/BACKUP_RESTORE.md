# 一致性备份与恢复

`scripts/backup_restore.sh` 使用**整栈停机、全部 Compose 命名卷快照**，包含 PostgreSQL（Core / Scenic 两库）、gallery、object_storage、Redis AOF、Milvus、etcd 和内置 MinIO。它不接受旧版 SQL gzip / assets 三文件恢复参数，也不会自动停止服务或覆盖非空卷。

需要 Bash 4+、Python 3、Docker Compose 和 `sha256sum`；Windows 使用已接入同一 Docker 引擎的 WSL/Bash。以下 Bash 命令从仓库根目录执行。快照包含个人数据，须限制访问、加密保管，并记录源码提交、镜像版本、配置来源、备份时间和校验结果。

## 1. 选中实际部署

通用 CPU 生产示例使用 `myreid-prod`。已有部署必须沿用原项目名，不能直接照抄一个不同名字，否则会定位到另一组卷。

```bash
export COMPOSE_PROJECT_NAME=myreid-prod
export COMPOSE_PROFILES=staff,visitor
export MYREID_ENV_FILE=.env.prod
export MYREID_COMPOSE_ENV_FILE=.env.prod
export MYREID_COMPOSE_FILES='docker-compose.yml;docker-compose.prod.yml'

# 后面的状态、停止与启动命令复用备份脚本所用的项目和文件组合。
compose_stack() {
  local -a args=(--env-file "$MYREID_COMPOSE_ENV_FILE" -p "$COMPOSE_PROJECT_NAME")
  local -a files
  local file
  IFS=';' read -r -a files <<< "$MYREID_COMPOSE_FILES"
  for file in "${files[@]}"; do args+=(-f "$file"); done
  docker compose "${args[@]}" "$@"
}

compose_stack config --quiet
compose_stack ps -a
```

按实际部署调整上面变量，然后再执行检查：

| 部署 | 项目 / profiles / 文件差异 |
| --- | --- |
| 通用生产 + 仓库 HTTPS 网关 | 在 `MYREID_COMPOSE_FILES` 末尾加 `;docker-compose.https.yml`，并保留原有域名配置；Caddy 的两个卷也会进入快照。 |
| Windows GPU 生产 | 项目为 `myreid-production`，`COMPOSE_PROFILES=visitor`，文件为 `docker-compose.yml;docker-compose.local-production.yml`，env 仍是 `.env.prod`。如另启了 `staff`，也将其纳入 profiles。 |
| Docker local 体验 | 使用实际 local 项目名，两个 env 变量均选 `.env.docker-local`，文件仅 `docker-compose.yml`，profiles 为 `staff,visitor`。 |

模型、私有 env、secret、摄像头配置等 bind mount 不在快照内，须单独加密备份。外部命名卷也不由脚本处理。启用额外 Compose 覆盖文件时应完整列出，不能只备份基础文件中声明的卷。

## 2. 停止写入并备份

先停止接收新操作，等待希望保留的任务完成，并停止外部 S3 的其他写入者。Windows GPU 部署先在 PowerShell 中执行 `scripts/stop_production.ps1 -StopCameraGateway`，让后端封存录像并释放宿主机摄像头；再回到上述 Bash 终端确认同一项目已停止。

```bash
compose_stack stop -t 60
compose_stack ps --status running
# 上一条不得再显示运行中的服务。
snapshot_dir="backups/snapshot-$(date +%Y%m%d-%H%M%S)"
bash scripts/backup_restore.sh backup "$snapshot_dir"
(cd "$snapshot_dir" && sha256sum -c SHA256SUMS)
```

输出目录必须不存在。脚本保存每个卷的 tar、`volumes.tsv` 和 `SHA256SUMS`；任何一步失败都视为不完整快照，保留失败现场并另选新目录重做。不要在备份中途启动 API、worker、maintenance、游客协调器或其他写入者。

如果媒体使用当前栈的内置 MinIO，`minio_data` 已在卷快照中；若使用远端 S3/MinIO，须在同一个停止写入窗口补做对象备份。在安装了后端依赖、可访问该对象服务的宿主机环境中，显式注入正确的 `OBJECT_STORAGE_MODE`、`S3_*` 配置，然后执行：

```bash
python scripts/object_store_backup.py backup "${snapshot_dir}-objects" --writers-stopped
```

该 Python 工具不会自动读取 `.env.prod` 或 `MYREID_ENV_FILE`。不要直接 `source` dotenv 文件；使用部署环境的安全凭据注入方式，确认目标 endpoint 和 bucket 后运行。工具保留当前对象内容、key、大小、SHA256 和部分对象元数据；版本历史、删除标记、桶策略、生命周期与 KMS 密钥须使用供应商备份方案。将卷快照和对象快照作为同一备份集保管。

备份完成后，可用 `compose_stack up -d` 恢复原通用栈；Windows GPU 原栈使用 PowerShell 的 `scripts/run_production.ps1` 恢复摄像头与服务。完成恢复演练前，不把“生成了备份文件”视为已验证可恢复。

## 3. 恢复到新项目的空卷

使用与备份时相同的源码、配置结构及 PostgreSQL/Milvus/etcd/MinIO 镜像版本，恢复验证后再单独升级。优先在隔离主机演练；同机演练必须先停止原栈并释放固定端口，保留原卷作为回滚目标。

在恢复目录安全还原私有配置和模型。数据库、MinIO 的账号密码必须匹配快照内已有账号，不能用生成器新建随机密码替换；同时保留原签名密钥、bucket/collection 名和模型描述。外部 S3 恢复使用独立空桶，并将恢复环境指向它，避免写回原桶。域名、邮件等外部集成也应按演练环境隔离。

复用第 1 节的 env、profiles、完整文件组合和 `compose_stack` 函数，仅把项目名换成一个未使用的名字；`snapshot_dir` 指向第 2 节已校验的实际快照目录。新终端中需重新设置这些变量。

```bash
export COMPOSE_PROJECT_NAME=myreid-restore
compose_stack config --quiet
compose_stack ps -a
# 不要先 up：新项目的服务初始化会向空卷写入数据。
bash scripts/backup_restore.sh restore "$snapshot_dir"
```

脚本先验证全部校验和及全部目标卷为空，再按逻辑卷名恢复；目标卷不存在时自动创建。恢复期间不需要运行 PostgreSQL 或 backend。如果 Compose 使用自定义固定卷名，须先改为独立恢复卷名；只改项目名无法隔离固定命名的卷。不要删除原卷来绕过“非空卷”检查。

外部对象存储须预先创建独立空桶、设置私有权限，在第 2 节所述宿主机凭据环境中执行：

```bash
python scripts/object_store_backup.py restore "${snapshot_dir}-objects" --writers-stopped
```

工具拒绝非空桶，并在上传前验证全部对象文件；内置 MinIO 卷恢复不需要重复导入对象。

## 4. 分阶段启动与验收

先只启动恢复项目的数据库和存储：

```bash
compose_stack up -d --wait postgres redis minio etcd milvus
compose_stack ps -a
```

核对两库关键表行数、对象清单、Milvus collection 数量和队列状态。随后启动同一恢复项目的业务服务，并检查日志与就绪信息：

```bash
compose_stack up -d
compose_stack ps -a
compose_stack logs --tail 100 backend worker maintenance visitor-coordinator
```

检查登录、原图/录像访问、样本检索排序、精剪成片、人工付款确认和已付领取。`healthz` 或容器 healthy 不等于业务恢复成功，还须检查 `readyz` 与实际任务消费。Windows GPU 的恢复项目不要使用固定指向 `myreid-production` 的 `run_production.ps1` 启动；先按[网关说明](camera-gateway.md)准备其外部网络/网关，再使用这里选定的恢复项目命令。

恢复中断时不开放新栈，在另一组空卷/空桶重试；不要向正在服务的卷覆盖解压，不要执行 `down -v` 清理原栈。回滚时先停止恢复项目，切回未修改的原项目及其原配置。上线前记录演练耗时、数据丢失窗口和业务校验结果。
