"""create enterprise schema

Revision ID: 20260519110003
Revises:
Create Date: 2026-05-19 11:00:03.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260519110003"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_roles_id"), "roles", ["id"], unique=False)
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=False)

    roles = sa.table(
        "roles",
        sa.column("name", sa.String(length=64)),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(
        roles,
        [
            {"name": "admin", "description": "Administrator role"},
            {"name": "operator", "description": "Operator role"},
            {"name": "viewer", "description": "Viewer role"},
        ],
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="operator"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["role"], ["roles.name"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)

    op.create_table(
        "search_histories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_identifier", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("query_type", sa.String(length=64), nullable=False),
        sa.Column("query_content", sa.Text(), nullable=False),
        sa.Column("results_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_histories_id"), "search_histories", ["id"], unique=False)
    op.create_index(op.f("ix_search_histories_session_id"), "search_histories", ["session_id"], unique=False)
    op.create_index(op.f("ix_search_histories_user_identifier"), "search_histories", ["user_identifier"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_username", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_username"], ["users.username"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_id"), "projects", ["id"], unique=False)
    op.create_index(op.f("ix_projects_name"), "projects", ["name"], unique=False)
    op.create_index(op.f("ix_projects_owner_username"), "projects", ["owner_username"], unique=False)

    op.create_table(
        "media_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("uploader_username", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_username"], ["users.username"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(op.f("ix_media_files_id"), "media_files", ["id"], unique=False)
    op.create_index(op.f("ix_media_files_media_type"), "media_files", ["media_type"], unique=False)
    op.create_index(op.f("ix_media_files_object_key"), "media_files", ["object_key"], unique=False)
    op.create_index(op.f("ix_media_files_project_id"), "media_files", ["project_id"], unique=False)
    op.create_index(op.f("ix_media_files_status"), "media_files", ["status"], unique=False)
    op.create_index(op.f("ix_media_files_uploader_username"), "media_files", ["uploader_username"], unique=False)

    op.create_table(
        "detection_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("crop_path", sa.String(length=1024), nullable=False),
        sa.Column("source_info", sa.Text(), nullable=False, server_default=""),
        sa.Column("camera_id", sa.String(), nullable=True),
        sa.Column("timestamp", sa.String(), nullable=True),
        sa.Column("vector_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vector_id"),
    )
    op.create_index(op.f("ix_detection_targets_camera_id"), "detection_targets", ["camera_id"], unique=False)
    op.create_index(op.f("ix_detection_targets_id"), "detection_targets", ["id"], unique=False)
    op.create_index(op.f("ix_detection_targets_media_id"), "detection_targets", ["media_id"], unique=False)
    op.create_index(op.f("ix_detection_targets_project_id"), "detection_targets", ["project_id"], unique=False)
    op.create_index(op.f("ix_detection_targets_timestamp"), "detection_targets", ["timestamp"], unique=False)
    op.create_index(op.f("ix_detection_targets_vector_id"), "detection_targets", ["vector_id"], unique=False)

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["media_id"], ["media_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_processing_jobs_id"), "processing_jobs", ["id"], unique=False)
    op.create_index(op.f("ix_processing_jobs_job_type"), "processing_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_processing_jobs_media_id"), "processing_jobs", ["media_id"], unique=False)
    op.create_index(op.f("ix_processing_jobs_project_id"), "processing_jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_processing_jobs_status"), "processing_jobs", ["status"], unique=False)

    op.create_table(
        "search_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("query_type", sa.String(length=64), nullable=False),
        sa.Column("query_content", sa.Text(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["username"], ["users.username"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_tasks_id"), "search_tasks", ["id"], unique=False)
    op.create_index(op.f("ix_search_tasks_project_id"), "search_tasks", ["project_id"], unique=False)
    op.create_index(op.f("ix_search_tasks_query_type"), "search_tasks", ["query_type"], unique=False)
    op.create_index(op.f("ix_search_tasks_username"), "search_tasks", ["username"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["username"], ["users.username"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_id"), "audit_logs", ["id"], unique=False)
    op.create_index(op.f("ix_audit_logs_resource_type"), "audit_logs", ["resource_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_username"), "audit_logs", ["username"], unique=False)

    op.create_table(
        "search_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_task_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("source_info", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["search_task_id"], ["search_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["detection_targets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_results_id"), "search_results", ["id"], unique=False)
    op.create_index(op.f("ix_search_results_search_task_id"), "search_results", ["search_task_id"], unique=False)
    op.create_index(op.f("ix_search_results_target_id"), "search_results", ["target_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_search_results_target_id"), table_name="search_results")
    op.drop_index(op.f("ix_search_results_search_task_id"), table_name="search_results")
    op.drop_index(op.f("ix_search_results_id"), table_name="search_results")
    op.drop_table("search_results")

    op.drop_index(op.f("ix_audit_logs_username"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_resource_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_search_tasks_username"), table_name="search_tasks")
    op.drop_index(op.f("ix_search_tasks_query_type"), table_name="search_tasks")
    op.drop_index(op.f("ix_search_tasks_project_id"), table_name="search_tasks")
    op.drop_index(op.f("ix_search_tasks_id"), table_name="search_tasks")
    op.drop_table("search_tasks")

    op.drop_index(op.f("ix_processing_jobs_status"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_project_id"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_media_id"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_job_type"), table_name="processing_jobs")
    op.drop_index(op.f("ix_processing_jobs_id"), table_name="processing_jobs")
    op.drop_table("processing_jobs")

    op.drop_index(op.f("ix_detection_targets_vector_id"), table_name="detection_targets")
    op.drop_index(op.f("ix_detection_targets_timestamp"), table_name="detection_targets")
    op.drop_index(op.f("ix_detection_targets_project_id"), table_name="detection_targets")
    op.drop_index(op.f("ix_detection_targets_media_id"), table_name="detection_targets")
    op.drop_index(op.f("ix_detection_targets_id"), table_name="detection_targets")
    op.drop_index(op.f("ix_detection_targets_camera_id"), table_name="detection_targets")
    op.drop_table("detection_targets")

    op.drop_index(op.f("ix_media_files_uploader_username"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_status"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_project_id"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_object_key"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_media_type"), table_name="media_files")
    op.drop_index(op.f("ix_media_files_id"), table_name="media_files")
    op.drop_table("media_files")

    op.drop_index(op.f("ix_projects_owner_username"), table_name="projects")
    op.drop_index(op.f("ix_projects_name"), table_name="projects")
    op.drop_index(op.f("ix_projects_id"), table_name="projects")
    op.drop_table("projects")

    op.drop_index(op.f("ix_search_histories_user_identifier"), table_name="search_histories")
    op.drop_index(op.f("ix_search_histories_session_id"), table_name="search_histories")
    op.drop_index(op.f("ix_search_histories_id"), table_name="search_histories")
    op.drop_table("search_histories")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_index(op.f("ix_roles_id"), table_name="roles")
    op.drop_table("roles")
