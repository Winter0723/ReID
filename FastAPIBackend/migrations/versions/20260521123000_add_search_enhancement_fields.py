"""add search enhancement fields

Revision ID: 20260521123000
Revises: 20260521110000
Create Date: 2026-05-21 12:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260521123000"
down_revision = "20260521110000"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("search_tasks", sa.Column("filters_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("search_results", sa.Column("rank", sa.Integer(), nullable=False, server_default="0"))
    op.create_index(op.f("ix_search_results_rank"), "search_results", ["rank"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_search_results_rank"), table_name="search_results")
    op.drop_column("search_results", "rank")
    op.drop_column("search_tasks", "filters_json")
