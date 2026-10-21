# 一致性备份与恢复

`scripts/backup_restore.sh` 现为**停机、全部命名卷快照**，不再把本地缓存误称为完整 S3 备份。
包含 PostgreSQL、gallery、object_storage、Redis AOF、Milvus、etcd 和 MinIO 命名卷。
需 Bash、Python 3、Docker、sha256sum。使用和部署时相同的 Compose 文件、project name 与 env-file。

1. 禁止新的写入，停止整套 Compose 服务（包括 worker、maintenance、visitor-coordinator、
   摄像头桥接和数据库）。停止外部 S3 的其他写入者。保持此窗口直到两个备份全部完成。
2. `scripts/backup_restore.sh backup backups/snapshot-YYYYMMDD`，目录必须不存在。
   检查卷清单和 SHA256SUMS。脚本不备份 bind mount；模型、私有配置、密钥和外部目录
   应分别加密备份，不能加入公开发行包。任何失败都视为不完整快照，不得用于恢复。
3. 如媒体使用外部 S3，在加载相同私有配置的宿主机环境执行
   `python scripts/object_store_backup.py backup backups/objects-YYYYMMDD --writers-stopped`。
   将两个目录作为同一备份集保管。该脚本保留当前对象的 key/大小/SHA256，版本历史、
   删除标记、桶策略、生命周期、KMS 密钥由供应商备份方案管理。
4. 恢复到新 Compose project 的空卷和空桶。保持原栈和原卷完整，作为回滚目标。
   `scripts/backup_restore.sh restore backups/snapshot-YYYYMMDD` 会先验证所有 checksum 和空卷，
   拒绝覆盖已有数据。S3 执行对应 `restore` 命令，非空桶会拒绝操作。
5. PostgreSQL/Milvus/etcd/MinIO 使用与备份时相同版本；快照恢复后再单独升级。
   启动数据库与存储后核对两库表行数、对象清单校验、向量 collection 的数量和样本检索排序，
   再启动 API/worker/maintenance。使用合成账号检查登录、原图/录像、精剪成片、已付领取。
6. 恢复中断时不要开放新栈；保留日志，在另一组空卷/桶重新恢复。不要向正在服务的卷
   覆盖解压，不要用 `docker compose down -v` 清理原栈。回滚为切回未修改的旧栈。

快照内容含个人数据；限制访问、加密保存、异地备份并配置删除期限。源码仓库不会执行
这些备份或清理你的现有数据。首次上线必须在隔离环境演练，记录恢复耗时、数据丢失窗口
和校验结果；未经演练不能宣称有可靠恢复能力。
