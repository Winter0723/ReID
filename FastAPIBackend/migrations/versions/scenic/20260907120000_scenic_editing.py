"""Versioned scenic editing drafts and independent landscape/portrait outputs."""

from alembic import op
import sqlalchemy as sa

revision = "20260907120000_scenic"
down_revision = "20260829100000_scenic"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade():
    op.create_table("scenic_media_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("location", sa.String(255), nullable=False, server_default=""), *timestamps())
    op.create_table("scenic_edit_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"), *timestamps())
    op.create_index("ix_scenic_edit_drafts_session_id", "scenic_edit_drafts", ["session_id"])
    with op.batch_alter_table("tourist_sessions") as batch:
        batch.add_column(sa.Column("consent_version", sa.String(32), nullable=False, server_default=""))
        batch.add_column(sa.Column("consent_granted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("entry_target_box_json", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("scenic_vlog_jobs") as batch:
        batch.add_column(sa.Column("draft_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("draft_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("snapshot_json", sa.Text(), nullable=False, server_default=""))
        batch.create_foreign_key("fk_vlog_draft", "scenic_edit_drafts", ["draft_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_vlog_draft_revision", ["draft_id", "draft_revision"])
    op.create_table("scenic_vlog_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vlog_job_id", sa.Integer(), sa.ForeignKey("scenic_vlog_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_job_id", sa.String(128), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("orientation", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        *[sa.Column(name, sa.String(1024), nullable=False, server_default="") for name in
          ("full_object_key", "preview_object_key", "draft_preview_object_key", "cover_object_key")],
        *timestamps(), sa.UniqueConstraint("vlog_job_id", "orientation", name="uq_vlog_variant_orientation"))
    op.create_index("ix_scenic_vlog_variants_vlog_job_id", "scenic_vlog_variants", ["vlog_job_id"])


def downgrade():
    op.drop_table("scenic_vlog_variants")
    with op.batch_alter_table("scenic_vlog_jobs") as batch:
        batch.drop_constraint("uq_vlog_draft_revision", type_="unique")
        batch.drop_constraint("fk_vlog_draft", type_="foreignkey")
        batch.drop_column("snapshot_json")
        batch.drop_column("draft_revision")
        batch.drop_column("draft_id")
    with op.batch_alter_table("tourist_sessions") as batch:
        batch.drop_column("entry_target_box_json")
        batch.drop_column("consent_granted_at")
        batch.drop_column("consent_version")
    op.drop_table("scenic_edit_drafts")
    op.drop_table("scenic_media_metadata")
