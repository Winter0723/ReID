"""add email login codes

Revision ID: 20260521110000
Revises: 20260521100000
Create Date: 2026-05-21 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260521110000"
down_revision = "20260521100000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_login_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="login"),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_login_codes_id"), "email_login_codes", ["id"], unique=False)
    op.create_index(op.f("ix_email_login_codes_email"), "email_login_codes", ["email"], unique=False)
    op.create_index(op.f("ix_email_login_codes_purpose"), "email_login_codes", ["purpose"], unique=False)
    op.create_index(op.f("ix_email_login_codes_expires_at"), "email_login_codes", ["expires_at"], unique=False)
    op.create_index(op.f("ix_email_login_codes_consumed_at"), "email_login_codes", ["consumed_at"], unique=False)
    dialect = op.get_bind().dialect.name
    is_active_value = "1" if dialect == "sqlite" else "TRUE"
    op.execute("UPDATE users SET avatar_url = '' WHERE avatar_url IS NULL")
    op.execute("UPDATE users SET role = 'operator' WHERE role IS NULL")
    op.execute(f"UPDATE users SET is_active = {is_active_value} WHERE is_active IS NULL")
    op.execute("UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")


def downgrade():
    op.drop_index(op.f("ix_email_login_codes_consumed_at"), table_name="email_login_codes")
    op.drop_index(op.f("ix_email_login_codes_expires_at"), table_name="email_login_codes")
    op.drop_index(op.f("ix_email_login_codes_purpose"), table_name="email_login_codes")
    op.drop_index(op.f("ix_email_login_codes_email"), table_name="email_login_codes")
    op.drop_index(op.f("ix_email_login_codes_id"), table_name="email_login_codes")
    op.drop_table("email_login_codes")
