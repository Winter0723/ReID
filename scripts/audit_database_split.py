"""Read-only audit for the core/scenic database split.

The script reports schema revisions and aggregate row counts only. It never
selects application payloads and never mutates either database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings, redact_database_url


SCENIC_DOMAIN_TABLES = (
    "scenic_camera_points",
    "tourist_sessions",
    "tourist_visit_segments",
    "scenic_vlog_jobs",
    "scenic_orders",
)


def _scalar(connection, statement: str):
    return connection.execute(text(statement)).scalar()


def _inspect_database(name: str, database_url: str) -> dict:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        quote = engine.dialect.identifier_preparer.quote
        counts: dict[str, int] = {}
        with engine.connect() as connection:
            revision = None
            if "alembic_version" in tables:
                revision = _scalar(connection, "SELECT version_num FROM alembic_version")
            for table_name in SCENIC_DOMAIN_TABLES:
                if table_name in tables:
                    counts[table_name] = int(
                        _scalar(connection, f"SELECT COUNT(*) FROM {quote(table_name)}") or 0
                    )

            order_totals = None
            if "scenic_orders" in tables:
                order_totals = {
                    "orders": counts.get("scenic_orders", 0),
                    "amount_cents": int(
                        _scalar(
                            connection,
                            "SELECT COALESCE(SUM(amount_cents), 0) FROM scenic_orders",
                        )
                        or 0
                    ),
                    "paid_amount_cents": int(
                        _scalar(
                            connection,
                            "SELECT COALESCE(SUM(amount_cents), 0) "
                            "FROM scenic_orders WHERE status IN ('paid', 'delivered')",
                        )
                        or 0
                    ),
                }

        return {
            "domain": name,
            "database_url": redact_database_url(database_url),
            "dialect": engine.dialect.name,
            "revision": revision,
            "table_count": len(tables),
            "scenic_domain_rows": counts,
            "order_totals": order_totals,
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        "--fail-on-core-scenic-data",
        dest="strict",
        action="store_true",
        help="Exit 2 when URLs, revisions, or core scenic rows are unsafe.",
    )
    args = parser.parse_args()

    settings = get_settings()
    core = _inspect_database("core", settings.database_url)
    scenic = _inspect_database("scenic", settings.scenic_database_url)
    core_scenic_rows = sum(core["scenic_domain_rows"].values())
    urls_are_distinct = settings.database_url != settings.scenic_database_url
    revisions_are_current = bool(
        str(core["revision"] or "").endswith("_core")
        and str(scenic["revision"] or "").endswith("_scenic")
    )
    ready = not core_scenic_rows and urls_are_distinct and revisions_are_current
    report = {
        "status": "ready" if ready else "review_required",
        "database_urls_are_distinct": urls_are_distinct,
        "revisions_are_current": revisions_are_current,
        "core_contains_scenic_rows": core_scenic_rows > 0,
        "core_scenic_row_count": core_scenic_rows,
        "cleanup_authorized": False,
        "domains": {"core": core, "scenic": scenic},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and not ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
