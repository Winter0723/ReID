"""add scenic vlog business tables

Revision ID: 20260525100000
Revises: 20260521123000
Create Date: 2026-05-25 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525100000"
down_revision = "20260521123000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scenic_camera_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("camera_source", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("camera_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="play"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_scenic_camera_points_project_name"),
    )
    op.create_index(op.f("ix_scenic_camera_points_id"), "scenic_camera_points", ["id"], unique=False)
    op.create_index(op.f("ix_scenic_camera_points_project_id"), "scenic_camera_points", ["project_id"], unique=False)
    op.create_index(op.f("ix_scenic_camera_points_camera_source"), "scenic_camera_points", ["camera_source"], unique=False)
    op.create_index(op.f("ix_scenic_camera_points_camera_id"), "scenic_camera_points", ["camera_id"], unique=False)
    op.create_index(op.f("ix_scenic_camera_points_role"), "scenic_camera_points", ["role"], unique=False)
    op.create_index(op.f("ix_scenic_camera_points_is_active"), "scenic_camera_points", ["is_active"], unique=False)

    op.create_table(
        "tourist_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("ticket_code", sa.String(length=128), nullable=False),
        sa.Column("visitor_label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("contact_hint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("consent_granted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("entry_image_object_key", sa.String(length=1024), nullable=False),
        sa.Column("entry_image_filename", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="registered"),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_by_username", sa.String(length=128), nullable=False),
        sa.Column("checked_out_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_username"], ["users.username"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "ticket_code", name="uq_tourist_sessions_project_ticket"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(op.f("ix_tourist_sessions_id"), "tourist_sessions", ["id"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_public_id"), "tourist_sessions", ["public_id"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_project_id"), "tourist_sessions", ["project_id"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_ticket_code"), "tourist_sessions", ["ticket_code"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_status"), "tourist_sessions", ["status"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_created_by_username"), "tourist_sessions", ["created_by_username"], unique=False)
    op.create_index(op.f("ix_tourist_sessions_checked_out_at"), "tourist_sessions", ["checked_out_at"], unique=False)

    op.create_table(
        "tourist_visit_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("camera_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("camera_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("timestamp", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("start_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("end_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("thumbnail_object_key", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("original_media_object_key", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["tourist_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["detection_targets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tourist_visit_segments_id"), "tourist_visit_segments", ["id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_session_id"), "tourist_visit_segments", ["session_id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_project_id"), "tourist_visit_segments", ["project_id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_target_id"), "tourist_visit_segments", ["target_id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_media_id"), "tourist_visit_segments", ["media_id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_camera_id"), "tourist_visit_segments", ["camera_id"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_timestamp"), "tourist_visit_segments", ["timestamp"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_similarity"), "tourist_visit_segments", ["similarity"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_included"), "tourist_visit_segments", ["included"], unique=False)
    op.create_index(op.f("ix_tourist_visit_segments_sort_order"), "tourist_visit_segments", ["sort_order"], unique=False)

    op.create_table(
        "scenic_vlog_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("processing_job_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("preview_object_key", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("full_object_key", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("preview_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("full_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_username", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_username"], ["users.username"]),
        sa.ForeignKeyConstraint(["processing_job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["tourist_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scenic_vlog_jobs_id"), "scenic_vlog_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_scenic_vlog_jobs_session_id"), "scenic_vlog_jobs", ["session_id"], unique=False)
    op.create_index(op.f("ix_scenic_vlog_jobs_processing_job_id"), "scenic_vlog_jobs", ["processing_job_id"], unique=False)
    op.create_index(op.f("ix_scenic_vlog_jobs_status"), "scenic_vlog_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_scenic_vlog_jobs_created_by_username"), "scenic_vlog_jobs", ["created_by_username"], unique=False)

    op.create_table(
        "scenic_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("vlog_job_id", sa.Integer(), nullable=True),
        sa.Column("order_no", sa.String(length=64), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="1999"),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("payment_method", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("delivery_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["tourist_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vlog_job_id"], ["scenic_vlog_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_token"),
        sa.UniqueConstraint("order_no"),
    )
    op.create_index(op.f("ix_scenic_orders_id"), "scenic_orders", ["id"], unique=False)
    op.create_index(op.f("ix_scenic_orders_session_id"), "scenic_orders", ["session_id"], unique=False)
    op.create_index(op.f("ix_scenic_orders_vlog_job_id"), "scenic_orders", ["vlog_job_id"], unique=False)
    op.create_index(op.f("ix_scenic_orders_order_no"), "scenic_orders", ["order_no"], unique=False)
    op.create_index(op.f("ix_scenic_orders_status"), "scenic_orders", ["status"], unique=False)
    op.create_index(op.f("ix_scenic_orders_delivery_token"), "scenic_orders", ["delivery_token"], unique=False)
    op.create_index(op.f("ix_scenic_orders_paid_at"), "scenic_orders", ["paid_at"], unique=False)
    op.create_index(op.f("ix_scenic_orders_delivered_at"), "scenic_orders", ["delivered_at"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_scenic_orders_delivered_at"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_paid_at"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_delivery_token"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_status"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_order_no"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_vlog_job_id"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_session_id"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_id"), table_name="scenic_orders")
    op.drop_table("scenic_orders")

    op.drop_index(op.f("ix_scenic_vlog_jobs_created_by_username"), table_name="scenic_vlog_jobs")
    op.drop_index(op.f("ix_scenic_vlog_jobs_status"), table_name="scenic_vlog_jobs")
    op.drop_index(op.f("ix_scenic_vlog_jobs_processing_job_id"), table_name="scenic_vlog_jobs")
    op.drop_index(op.f("ix_scenic_vlog_jobs_session_id"), table_name="scenic_vlog_jobs")
    op.drop_index(op.f("ix_scenic_vlog_jobs_id"), table_name="scenic_vlog_jobs")
    op.drop_table("scenic_vlog_jobs")

    op.drop_index(op.f("ix_tourist_visit_segments_sort_order"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_included"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_similarity"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_timestamp"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_camera_id"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_media_id"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_target_id"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_project_id"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_session_id"), table_name="tourist_visit_segments")
    op.drop_index(op.f("ix_tourist_visit_segments_id"), table_name="tourist_visit_segments")
    op.drop_table("tourist_visit_segments")

    op.drop_index(op.f("ix_tourist_sessions_checked_out_at"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_created_by_username"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_status"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_ticket_code"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_project_id"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_public_id"), table_name="tourist_sessions")
    op.drop_index(op.f("ix_tourist_sessions_id"), table_name="tourist_sessions")
    op.drop_table("tourist_sessions")

    op.drop_index(op.f("ix_scenic_camera_points_is_active"), table_name="scenic_camera_points")
    op.drop_index(op.f("ix_scenic_camera_points_role"), table_name="scenic_camera_points")
    op.drop_index(op.f("ix_scenic_camera_points_camera_id"), table_name="scenic_camera_points")
    op.drop_index(op.f("ix_scenic_camera_points_camera_source"), table_name="scenic_camera_points")
    op.drop_index(op.f("ix_scenic_camera_points_project_id"), table_name="scenic_camera_points")
    op.drop_index(op.f("ix_scenic_camera_points_id"), table_name="scenic_camera_points")
    op.drop_table("scenic_camera_points")
