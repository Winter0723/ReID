from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


COMMON_SCHEMA_HEAD = "20260710170000"
PRE_DEDUP_HEAD = "20260604120000"


def _alembic_config(database_url: str, scope: str) -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config_filename = "alembic.ini" if scope == "core" else "alembic.scenic.ini"
    config = Config(str(backend_dir / config_filename))
    # Embedded migrations must preserve the API/worker logging configuration.
    config.attributes["configure_logger"] = False
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("myreid.migration_scope", scope)
    return config


def _bootstrap_legacy_database(config: Config, engine, metadata, scope: str) -> None:
    """Stamp a complete pre-Alembic database without mutating its schema.

    Incomplete legacy schemas are rejected deliberately. Their upgrade must be
    performed from a backup with an explicit migration instead of startup-time
    ALTER TABLE statements.
    """

    inspector = inspect(engine)
    existing_tables = {
        table_name
        for table_name in inspector.get_table_names()
        if not table_name.startswith("sqlite_")
    }
    if not existing_tables or "alembic_version" in existing_tables:
        return

    required_tables = set(metadata.tables)
    missing_tables = sorted(required_tables - existing_tables)
    missing_columns = {
        table_name: sorted(
            {column.name for column in metadata.tables[table_name].columns}
            - {column["name"] for column in inspector.get_columns(table_name)}
        )
        for table_name in sorted(required_tables & existing_tables)
    }
    # These additive visitor fields are applied after stamping the known legacy head.
    if scope == "scenic" and "visitor_visits" in missing_columns:
        missing_columns["visitor_visits"] = [c for c in missing_columns["visitor_visits"] if c not in {"verified_at", "verified_by"}]
    missing_columns = {name: columns for name, columns in missing_columns.items() if columns}
    if missing_tables or missing_columns:
        raise RuntimeError(
            f"The {scope} database predates Alembic and cannot be bootstrapped safely; "
            f"missing tables={missing_tables}, missing columns={missing_columns}. "
            "Restore a backup and follow docs/DATABASE_SPLIT_RUNBOOK.md."
        )

    media_indexes = {index["name"] for index in inspector.get_indexes("media_files")}
    order_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("scenic_orders")
    }
    if (
        "uq_media_files_project_sha_active" in media_indexes
        and "ck_scenic_orders_amount_positive" in order_constraints
    ):
        # Column-name checks above cannot establish the width of size_bytes.
        # Stamp the last pre-BIGINT revision so the widening migration runs.
        legacy_head = "20260829100000_core" if scope == "core" else "20260929100000_scenic"
        command.stamp(config, legacy_head)
    else:
        command.stamp(config, PRE_DEDUP_HEAD)


def upgrade_database(*, database_url: str, engine, metadata, scope: str) -> None:
    if scope not in {"core", "scenic"}:
        raise ValueError(f"Unsupported migration scope: {scope}")
    config = _alembic_config(database_url, scope)
    _bootstrap_legacy_database(config, engine, metadata, scope)
    command.upgrade(config, f"{scope}@head")
