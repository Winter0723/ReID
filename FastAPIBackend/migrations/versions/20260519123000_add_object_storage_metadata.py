"""add object storage metadata

Revision ID: 20260519123000
Revises: 20260519110003
Create Date: 2026-05-19 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260519123000"
down_revision = "20260519110003"
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("media_files") as batch_op:
            batch_op.add_column(sa.Column("content_type", sa.String(length=128), nullable=False, server_default=""))
            batch_op.add_column(sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
            batch_op.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
            batch_op.add_column(sa.Column("duplicate_of_media_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_media_files_duplicate_of_media_id",
                "media_files",
                ["duplicate_of_media_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column("media_files", sa.Column("content_type", sa.String(length=128), nullable=False, server_default=""))
        op.add_column("media_files", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        op.add_column("media_files", sa.Column("sha256", sa.String(length=64), nullable=True))
        op.add_column("media_files", sa.Column("duplicate_of_media_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_media_files_duplicate_of_media_id",
            "media_files",
            "media_files",
            ["duplicate_of_media_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(op.f("ix_media_files_sha256"), "media_files", ["sha256"], unique=False)
    op.create_index(op.f("ix_media_files_duplicate_of_media_id"), "media_files", ["duplicate_of_media_id"], unique=False)

    op.add_column("detection_targets", sa.Column("crop_object_key", sa.String(length=1024), nullable=True))
    op.create_index(op.f("ix_detection_targets_crop_object_key"), "detection_targets", ["crop_object_key"], unique=False)

    op.add_column("search_tasks", sa.Column("query_object_key", sa.String(length=1024), nullable=True))
    op.create_index(op.f("ix_search_tasks_query_object_key"), "search_tasks", ["query_object_key"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_search_tasks_query_object_key"), table_name="search_tasks")
    op.drop_column("search_tasks", "query_object_key")

    op.drop_index(op.f("ix_detection_targets_crop_object_key"), table_name="detection_targets")
    op.drop_column("detection_targets", "crop_object_key")

    op.drop_index(op.f("ix_media_files_duplicate_of_media_id"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_sha256"), table_name="media_files")
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("media_files") as batch_op:
            batch_op.drop_constraint("fk_media_files_duplicate_of_media_id", type_="foreignkey")
            batch_op.drop_column("duplicate_of_media_id")
            batch_op.drop_column("sha256")
            batch_op.drop_column("size_bytes")
            batch_op.drop_column("content_type")
    else:
        op.drop_constraint("fk_media_files_duplicate_of_media_id", "media_files", type_="foreignkey")
        op.drop_column("media_files", "duplicate_of_media_id")
        op.drop_column("media_files", "sha256")
        op.drop_column("media_files", "size_bytes")
        op.drop_column("media_files", "content_type")
