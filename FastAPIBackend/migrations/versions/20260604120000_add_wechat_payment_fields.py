"""add wechat payment fields

Revision ID: 20260604120000
Revises: 20260525100000
Create Date: 2026-06-04 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260604120000"
down_revision = "20260525100000"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scenic_orders", sa.Column("payment_provider", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("provider_trade_no", sa.String(length=128), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("provider_trade_state", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("payment_code_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("payment_qr_object_key", sa.String(length=1024), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("payment_expires_at", sa.DateTime(), nullable=True))
    op.add_column("scenic_orders", sa.Column("payment_error", sa.Text(), nullable=False, server_default=""))
    op.add_column("scenic_orders", sa.Column("last_payment_sync_at", sa.DateTime(), nullable=True))
    op.add_column("scenic_orders", sa.Column("notify_event_id", sa.String(length=128), nullable=False, server_default=""))

    op.create_index(op.f("ix_scenic_orders_payment_provider"), "scenic_orders", ["payment_provider"], unique=False)
    op.create_index(op.f("ix_scenic_orders_provider_trade_no"), "scenic_orders", ["provider_trade_no"], unique=False)
    op.create_index(op.f("ix_scenic_orders_provider_trade_state"), "scenic_orders", ["provider_trade_state"], unique=False)
    op.create_index(op.f("ix_scenic_orders_payment_expires_at"), "scenic_orders", ["payment_expires_at"], unique=False)
    op.create_index(op.f("ix_scenic_orders_last_payment_sync_at"), "scenic_orders", ["last_payment_sync_at"], unique=False)
    op.create_index(op.f("ix_scenic_orders_notify_event_id"), "scenic_orders", ["notify_event_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_scenic_orders_notify_event_id"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_last_payment_sync_at"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_payment_expires_at"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_provider_trade_state"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_provider_trade_no"), table_name="scenic_orders")
    op.drop_index(op.f("ix_scenic_orders_payment_provider"), table_name="scenic_orders")

    op.drop_column("scenic_orders", "notify_event_id")
    op.drop_column("scenic_orders", "last_payment_sync_at")
    op.drop_column("scenic_orders", "payment_error")
    op.drop_column("scenic_orders", "payment_expires_at")
    op.drop_column("scenic_orders", "payment_qr_object_key")
    op.drop_column("scenic_orders", "payment_code_url")
    op.drop_column("scenic_orders", "provider_trade_state")
    op.drop_column("scenic_orders", "provider_trade_no")
    op.drop_column("scenic_orders", "payment_provider")
