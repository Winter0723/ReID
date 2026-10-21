"""add active media deduplication index

Revision ID: 20260710170000
Revises: 20260604120000
Create Date: 2026-07-10 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710170000"
down_revision = "20260604120000"
branch_labels = None
depends_on = None
ACTIVE_MEDIA_INDEX = "uq_media_files_project_sha_active"
POSITIVE_AMOUNT_CONSTRAINT = "ck_scenic_orders_amount_positive"


def _reject_invalid_order_amounts() -> None:
    connection = op.get_bind()
    scenic_orders = sa.table(
        "scenic_orders",
        sa.column("id", sa.Integer()),
        sa.column("amount_cents", sa.Integer()),
    )
    invalid_count = connection.scalar(
        sa.select(sa.func.count(scenic_orders.c.id)).where(scenic_orders.c.amount_cents <= 0)
    )
    if invalid_count:
        raise RuntimeError(
            f"Cannot add {POSITIVE_AMOUNT_CONSTRAINT}: {invalid_count} scenic order(s) have amount_cents <= 0"
        )


def _create_positive_amount_constraint() -> None:
    connection = op.get_bind()
    existing = {item["name"] for item in sa.inspect(connection).get_check_constraints("scenic_orders")}
    if POSITIVE_AMOUNT_CONSTRAINT in existing:
        return
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("scenic_orders") as batch_op:
            batch_op.create_check_constraint(POSITIVE_AMOUNT_CONSTRAINT, "amount_cents > 0")
    else:
        op.create_check_constraint(
            POSITIVE_AMOUNT_CONSTRAINT,
            "scenic_orders",
            "amount_cents > 0",
        )


def _mark_existing_duplicates() -> None:
    connection = op.get_bind()
    media_files = sa.table(
        "media_files",
        sa.column("id", sa.Integer()),
        sa.column("project_id", sa.Integer()),
        sa.column("sha256", sa.String(length=64)),
        sa.column("status", sa.String(length=32)),
        sa.column("duplicate_of_media_id", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
    )
    duplicate_groups = connection.execute(
        sa.select(media_files.c.project_id, media_files.c.sha256)
        .where(
            media_files.c.sha256.is_not(None),
            media_files.c.status.not_in(("failed", "duplicate")),
        )
        .group_by(media_files.c.project_id, media_files.c.sha256)
        .having(sa.func.count(media_files.c.id) > 1)
    ).all()
    for project_id, sha256 in duplicate_groups:
        ids = connection.execute(
            sa.select(media_files.c.id)
            .where(
                media_files.c.project_id == project_id,
                media_files.c.sha256 == sha256,
                media_files.c.status.not_in(("failed", "duplicate")),
            )
            .order_by(media_files.c.created_at.asc(), media_files.c.id.asc())
        ).scalars().all()
        canonical_id, *duplicate_ids = ids
        if duplicate_ids:
            connection.execute(
                media_files.update()
                .where(media_files.c.id.in_(duplicate_ids))
                .values(status="duplicate", duplicate_of_media_id=canonical_id)
            )


def upgrade():
    _reject_invalid_order_amounts()
    _create_positive_amount_constraint()
    _mark_existing_duplicates()
    predicate = sa.text("sha256 IS NOT NULL AND status NOT IN ('failed', 'duplicate')")
    existing_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("media_files")}
    if ACTIVE_MEDIA_INDEX not in existing_indexes:
        op.create_index(
            ACTIVE_MEDIA_INDEX,
            "media_files",
            ["project_id", "sha256"],
            unique=True,
            sqlite_where=predicate,
            postgresql_where=predicate,
        )


def downgrade():
    op.drop_index(ACTIVE_MEDIA_INDEX, table_name="media_files")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("scenic_orders") as batch_op:
            batch_op.drop_constraint(POSITIVE_AMOUNT_CONSTRAINT, type_="check")
    else:
        op.drop_constraint(POSITIVE_AMOUNT_CONSTRAINT, "scenic_orders", type_="check")
