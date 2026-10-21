"""add camera project fields

Revision ID: 20260521100000
Revises: 20260520090000
Create Date: 2026-05-21 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260521100000"
down_revision = "20260520090000"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("project_type", sa.String(length=32), nullable=False, server_default="media"))
    op.add_column("projects", sa.Column("camera_source", sa.String(length=128), nullable=False, server_default=""))
    op.create_index(op.f("ix_projects_project_type"), "projects", ["project_type"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_projects_project_type"), table_name="projects")
    op.drop_column("projects", "camera_source")
    op.drop_column("projects", "project_type")
