# Core / Scenic 数据库拆分运行手册

## 当前状态

Core 与 Scenic 继续保持独立账号、token scope 和数据库 URL。既有 Alembic
历史为兼容老版本仍是公共链，但公共历史之后已经建立两个独立 head：

- `core@head`：后续只允许核心域迁移；
- `scenic@head`：后续只允许景区域迁移。

历史公共迁移曾在两个数据库中创建全部表。本次升级不会自动删除任何表，
也不会自动移动历史业务数据。

截至本次源码核对（2026-10-15），两个 head 均包含 `media_files.size_bytes` 扩为 BIGINT 的迁移：`20261012100000_core`、`20261012100000_scenic`。Scenic 分支另包含精剪、领取确认和四张游客表。后续版本以对应 Alembic 配置的 `heads` 输出为准，不把旧日期 revision 当作永久 head。

## 升级前只读审计

在与目标环境相同的环境变量下执行：

```bash
python scripts/audit_database_split.py
```

输出仅包含脱敏连接地址、Alembic revision、表数、景区域聚合行数和订单金额，
不读取或打印用户、游客、媒体等业务内容。旧版本升级前可能返回 `review_required`，应先核对版本及数据归属，再决定迁移路径。以下情况需要处理：

- 两个数据库 URL 相同；
- core 数据库的景区业务表中仍有记录；
- revision 不属于预期域或缺少已知升级路径；
- 订单数量或金额与业务台账不一致。

`--strict` 在 URL 相同、Core 含景区业务数据或 revision 后缀不匹配时返回退出码 2。脚本的 `revisions_are_current` **只检查 `_core` / `_scenic` 后缀**，不验证最新 head，也不自动对账；额外以 `alembic current` 对照 `heads`，人工比较订单汇总与台账。不得把 `ready` 当作允许清表的信号，输出始终为 `cleanup_authorized=false`。

## 备份与恢复演练

1. 同时备份 core PostgreSQL、scenic PostgreSQL、MinIO/S3 对象、Milvus collection。
2. 记录用户、项目、媒体、向量、游客、订单行数及订单金额。
3. 在隔离环境恢复全部备份，启动 API 与 worker，完成一次核心检索和一次景区交付。
4. 只有恢复烟测通过后，才允许进入 staging 迁移。

PostgreSQL 建议对两个数据库分别使用自定义格式备份，并保留校验和：

```bash
# 在能访问数据库的 Bash 环境中，将 SQLAlchemy 驱动 URL 转为 libpq URI。
# DATABASE_URL / SCENIC_DATABASE_URL 需已由受保护的运行配置导出。
CORE_PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
SCENIC_PG_URL="${SCENIC_DATABASE_URL/postgresql+psycopg:/postgresql:}"
pg_dump --format=custom --file=core.dump "$CORE_PG_URL"
pg_dump --format=custom --file=scenic.dump "$SCENIC_PG_URL"
sha256sum core.dump scenic.dump
```

`pg_dump` 不接受 `postgresql+psycopg://`。容器服务名只在相应网络内可解析；在宿主机执行时改用可达地址，或使用 [部署指南](DEPLOYMENT.md) 的 Compose 备份脚本。自定义格式 dump 用 `pg_restore` 恢复，不能直接传给只接受 SQL gzip 的 `backup_restore.sh restore`。

SQLite 仅用于本地开发。复制数据库文件前必须停止 API/worker，并连同 WAL/SHM
状态一起处理；生产环境不得使用 SQLite。

## Staging 迁移

CLI 迁移连接来自进程环境或 Alembic ini；不要只复制 `.env.prod` 就假定 CLI 已读到它。建议在目标 Compose 容器内执行，或先在受控终端明确导出目标 `APP_ENV`、`DATABASE_URL`、`SCENIC_DATABASE_URL` 等配置。

```bash
cd FastAPIBackend
MYREID_MIGRATION_SCOPE=core python -m alembic -c alembic.ini current
MYREID_MIGRATION_SCOPE=core python -m alembic -c alembic.ini heads
MYREID_MIGRATION_SCOPE=scenic python -m alembic -c alembic.scenic.ini current
MYREID_MIGRATION_SCOPE=scenic python -m alembic -c alembic.scenic.ini heads
MYREID_MIGRATION_SCOPE=core python -m alembic -c alembic.ini upgrade core@head
MYREID_MIGRATION_SCOPE=scenic python -m alembic -c alembic.scenic.ini upgrade scenic@head
```

迁移后再次运行只读审计，并核对数据库记录数、对象数、向量数、订单数量与金额。
随后执行 API、worker、MinIO、Milvus 的完整 E2E。

迁移使用 PostgreSQL advisory lock 或 SQLite 文件锁串行化。SQLite 开发同样走 Alembic，不依赖 `create_all()` 修表。不完整的 pre-Alembic 数据库会拒绝自动引导；从备份准备显式迁移，不要手工 stamp 跳过缺失结构。回滚先在恢复副本验证：BIGINT 降回 INTEGER 可能因已有超大文件大小值失败，单纯回退代码也不能恢复被删除的数据或对象。

## 历史重复表清理门槛

Core 中的景区表只有在以下条件全部满足时，才能另开清理版本：

1. 连续两次审计均确认 core 景区表为空，或其数据已经迁移且逐表校验一致；
2. staging 完成清理迁移及 downgrade/恢复演练；
3. 已保存清理前双数据库和对象/向量备份；
4. 业务负责人明确批准清理窗口。

清理必须作为单独 Alembic revision 发布，不允许在应用启动代码中执行 `DROP`
或手工 `ALTER TABLE`。本仓库当前没有加入任何自动删除逻辑。
