"""add user profile fields

Revision ID: 20260520090000
Revises: 20260519190000
Create Date: 2026-05-20 09:00:00.000000
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260520090000"
down_revision = "20260519190000"
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("public_id", sa.String(length=36), nullable=True))
            batch_op.add_column(sa.Column("email", sa.String(length=254), nullable=True))
            batch_op.add_column(sa.Column("avatar_url", sa.String(length=1024), nullable=False, server_default=""))
    else:
        op.add_column("users", sa.Column("public_id", sa.String(length=36), nullable=True))
        op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
        op.add_column("users", sa.Column("avatar_url", sa.String(length=1024), nullable=False, server_default=""))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM users WHERE public_id IS NULL OR public_id = ''")).fetchall()
    for row in rows:
        connection.execute(
            sa.text("UPDATE users SET public_id = :public_id WHERE id = :id"),
            {"public_id": str(uuid.uuid4()), "id": row[0]},
        )

    if dialect == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("public_id", existing_type=sa.String(length=36), nullable=False)
    else:
        op.alter_column("users", "public_id", nullable=False)
    op.create_index(op.f("ix_users_public_id"), "users", ["public_id"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_public_id"), table_name="users")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("avatar_url")
            batch_op.drop_column("email")
            batch_op.drop_column("public_id")
    else:
        op.drop_column("users", "avatar_url")
        op.drop_column("users", "email")
        op.drop_column("users", "public_id")
