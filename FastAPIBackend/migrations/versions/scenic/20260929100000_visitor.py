"""Independent visitor identity, service configuration and durable production requests."""
from alembic import op
import sqlalchemy as sa

revision = "20260929100000_scenic"
down_revision = "20260914120000_scenic"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade():
    op.create_table("visitor_accounts", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("appid", sa.String(128), nullable=False), sa.Column("openid", sa.String(128), nullable=False),
        *timestamps(), sa.UniqueConstraint("appid", "openid", name="uq_visitor_wechat"))
    op.create_table("visitor_auth_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False), *timestamps())
    op.create_table("visitor_services", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False), sa.Column("consent_version", sa.String(32), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False), *timestamps())
    op.create_table("visitor_visits", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("visitor_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("tourist_sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("request_key", sa.String(64), nullable=False), sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False), sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False), sa.Column("internal_error", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime()),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("scenic_edit_drafts.id", ondelete="SET NULL")),
        sa.Column("vlog_id", sa.Integer(), sa.ForeignKey("scenic_vlog_jobs.id", ondelete="SET NULL")),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("scenic_orders.id", ondelete="SET NULL"), unique=True),
        *timestamps(), sa.UniqueConstraint("account_id", "request_key", name="uq_visitor_registration"))
    op.create_index("ix_visitor_visits_account_id", "visitor_visits", ["account_id"])
    op.create_index("ix_visitor_visits_stage", "visitor_visits", ["stage"])


def downgrade():
    for table in ("visitor_visits", "visitor_services", "visitor_auth_sessions", "visitor_accounts"):
        op.drop_table(table)
