from logging.config import fileConfig
from contextlib import contextmanager
import os
from pathlib import Path

from alembic import context
from filelock import FileLock
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url

from app.core.config import resolve_database_url, resolve_scenic_database_url

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

MIGRATION_SCOPE = (
    config.get_main_option("myreid.migration_scope")
    or os.getenv("MYREID_MIGRATION_SCOPE", "core")
).strip().lower()
if MIGRATION_SCOPE == "scenic":
    from scenic_database import Base
elif MIGRATION_SCOPE == "core":
    from database import Base
else:
    raise RuntimeError(f"Unsupported MYREID_MIGRATION_SCOPE: {MIGRATION_SCOPE}")

target_metadata = Base.metadata
POSTGRES_MIGRATION_LOCK_IDS = {
    "core": 0x4D59524549444301,
    "scenic": 0x4D59524549445301,
}


@contextmanager
def _sqlite_migration_lock(database_url: str):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        yield
        return

    database_path = Path(url.database).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database_path.with_name(f"{database_path.name}.alembic.lock")
    with FileLock(str(lock_path), timeout=60):
        yield


@contextmanager
def _database_migration_lock(connection):
    if connection.dialect.name != "postgresql":
        yield
        return
    lock_id = POSTGRES_MIGRATION_LOCK_IDS[MIGRATION_SCOPE]
    connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
    connection.commit()
    try:
        yield
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
        connection.commit()


def get_url():
    if MIGRATION_SCOPE == "scenic":
        primary_url = resolve_database_url(os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")))
        return resolve_scenic_database_url(
            os.getenv("SCENIC_DATABASE_URL", ""),
            primary_database_url=primary_url,
            app_env=os.getenv("APP_ENV", "development"),
        )
    return resolve_database_url(os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")))


def run_migrations_offline():
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    configuration = config.get_section(config.config_ini_section)
    database_url = get_url()
    configuration["sqlalchemy.url"] = database_url
    engine_options = {}
    if make_url(database_url).get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"timeout": 30}
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        **engine_options,
    )
    try:
        with _sqlite_migration_lock(database_url):
            with connectable.connect() as connection:
                with _database_migration_lock(connection):
                    context.configure(connection=connection, target_metadata=target_metadata)
                    with context.begin_transaction():
                        context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
