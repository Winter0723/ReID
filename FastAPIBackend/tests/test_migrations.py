from pathlib import Path

import sqlalchemy as sa
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

from app.services.database_migrations import upgrade_database


def test_core_and_scenic_configs_have_isolated_heads():
    backend_dir = Path(__file__).resolve().parents[1]
    core_script = ScriptDirectory.from_config(Config(str(backend_dir / "alembic.ini")))
    scenic_script = ScriptDirectory.from_config(Config(str(backend_dir / "alembic.scenic.ini")))

    assert core_script.get_current_head() == "20261012100000_core"
    assert scenic_script.get_current_head() == "20261016090000_scenic"
    with pytest.raises(CommandError):
        core_script.get_revision("20260829100000_scenic")
    with pytest.raises(CommandError):
        scenic_script.get_revision("20260829100000_core")


@pytest.mark.parametrize(
    ("migration_scope", "database_filename"),
    [("core", "migration.db"), ("scenic", "scenic_migration.db")],
)
def test_alembic_upgrade_and_downgrade_on_clean_sqlite(
    tmp_path,
    monkeypatch,
    migration_scope,
    database_filename,
):
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / database_filename
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("MYREID_MIGRATION_SCOPE", migration_scope)
    monkeypatch.setenv(
        "SCENIC_DATABASE_URL" if migration_scope == "scenic" else "DATABASE_URL",
        database_url,
    )

    config_filename = "alembic.ini" if migration_scope == "core" else "alembic.scenic.ini"
    config = Config(str(backend_dir / config_filename))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, f"{migration_scope}@head")

    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        with engine.connect() as connection:
            current_revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert current_revision.endswith(f"_{migration_scope}")
        assert "users" in inspector.get_table_names()
        assert "processing_jobs" in inspector.get_table_names()
        assert "email_login_codes" in inspector.get_table_names()

        media_columns = {column["name"] for column in inspector.get_columns("media_files")}
        assert {"content_type", "size_bytes", "sha256", "duplicate_of_media_id"} <= media_columns
        media_indexes = {index["name"] for index in inspector.get_indexes("media_files")}
        assert "uq_media_files_project_sha_active" in media_indexes

        order_constraints = {
            constraint["name"] for constraint in inspector.get_check_constraints("scenic_orders")
        }
        assert "ck_scenic_orders_amount_positive" in order_constraints

        search_columns = {column["name"] for column in inspector.get_columns("search_tasks")}
        assert {"query_object_key", "filters_json"} <= search_columns

        command.downgrade(config, "base")
        assert sa.inspect(engine).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()


def test_incomplete_pre_alembic_schema_is_rejected_without_repair(tmp_path):
    import database

    database_url = f"sqlite:///{tmp_path / 'incomplete.db'}"
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))

        with pytest.raises(RuntimeError, match="cannot be bootstrapped safely"):
            upgrade_database(
                database_url=database_url,
                engine=engine,
                metadata=database.Base.metadata,
                scope="core",
            )

        inspector = sa.inspect(engine)
        assert inspector.get_table_names() == ["users"]
        assert {column["name"] for column in inspector.get_columns("users")} == {"id"}
    finally:
        engine.dispose()


def test_scenic_receipt_upgrade_preserves_existing_order_and_downgrade(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'receipt_upgrade.db'}"
    monkeypatch.setenv("SCENIC_DATABASE_URL", url)
    monkeypatch.setenv("MYREID_MIGRATION_SCOPE", "scenic")
    config = Config(str(backend_dir / "alembic.scenic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260907120000_scenic")
    engine = sa.create_engine(url)
    try:
        # This migration touches only orders; a minimal historical row is enough
        # to prove the nullable addition neither rewrites nor infers receipt.
        with engine.begin() as connection:
            connection.execute(sa.text("""INSERT INTO scenic_orders
                (id, session_id, order_no, amount_cents, currency, status, payment_method, delivery_token,
                 paid_at, delivered_at, created_at)
                VALUES (1, 1, 'historic', 1999, 'CNY', 'paid', 'manual', 'historic-token',
                        '2026-09-01 12:00:00', '2026-09-01 12:00:00', '2026-09-01 11:00:00')"""))
        command.upgrade(config, "scenic@head")
        with engine.connect() as connection:
            row = connection.execute(sa.text("SELECT status, delivery_token, delivered_at, claimed_at FROM scenic_orders")).one()
        assert tuple(row) == ("paid", "historic-token", "2026-09-01 12:00:00", None)
        command.downgrade(config, "20260907120000_scenic")
        assert "claimed_at" not in {column["name"] for column in sa.inspect(engine).get_columns("scenic_orders")}
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT delivery_token FROM scenic_orders")).scalar_one() == "historic-token"
    finally:
        engine.dispose()


@pytest.mark.parametrize("scope,previous,module_name", [
    ("core", "20260829100000_core", "database"),
    ("scenic", "20260929100000_scenic", "scenic_database"),
])
@pytest.mark.parametrize("legacy_unversioned", [False, True])
def test_media_size_bigint_upgrade_preserves_data(tmp_path, monkeypatch, scope, previous, module_name, legacy_unversioned):
    import importlib
    from sqlalchemy.dialects.postgresql import psycopg

    module = importlib.import_module(module_name)
    url = f"sqlite:///{tmp_path / 'media_size.db'}"
    monkeypatch.setenv("SCENIC_DATABASE_URL" if scope == "scenic" else "DATABASE_URL", url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / ("alembic.scenic.ini" if scope == "scenic" else "alembic.ini")))
    command.upgrade(config, previous)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("""INSERT INTO media_files
                (id, project_id, uploader_username, filename, object_key, media_type, status, size_bytes, created_at)
                VALUES (6, 1, 'camera', 'recording.avi', 'camera/recording.avi', 'camera', 'streaming', 123, '2026-10-08 08:53:19')"""))
            if legacy_unversioned:
                connection.execute(sa.text("DROP TABLE alembic_version"))
        upgrade_database(database_url=url, engine=engine, metadata=module.Base.metadata, scope=scope)
        columns = {c["name"]: c for c in sa.inspect(engine).get_columns("media_files")}
        assert isinstance(columns["size_bytes"]["type"], sa.BigInteger)
        assert not columns["size_bytes"]["nullable"]
        with engine.connect() as connection:
            assert tuple(connection.execute(sa.text("SELECT id, size_bytes, object_key FROM media_files")).one()) == (6, 123, "camera/recording.avi")
        update = sa.update(module.MediaFile).where(module.MediaFile.id == 6).values(size_bytes=3981207810)
        # SQLite alone would hide this bug: psycopg must also bind as BIGINT.
        assert "::BIGINT" in str(update.compile(dialect=psycopg.dialect()))
        with engine.begin() as connection:
            connection.execute(update)
        upgrade_database(database_url=url, engine=engine, metadata=module.Base.metadata, scope=scope)
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT size_bytes FROM media_files WHERE id=6")).scalar_one() == 3981207810
        with pytest.raises(RuntimeError, match="values exceed the INTEGER range"):
            command.downgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(sa.text("UPDATE media_files SET size_bytes=123 WHERE id=6"))
        command.downgrade(config, previous)
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT size_bytes FROM media_files WHERE id=6")).scalar_one() == 123
    finally:
        engine.dispose()
