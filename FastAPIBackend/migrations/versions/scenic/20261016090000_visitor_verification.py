"""Require staff evidence before exposing visitor matches."""
from alembic import op
import sqlalchemy as sa

revision = "20261016090000_scenic"
down_revision = "20261012100000_scenic"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("visitor_visits", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column("visitor_visits", sa.Column("verified_by", sa.String(128), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("visitor_visits") as batch:
        batch.drop_column("verified_by")
        batch.drop_column("verified_at")
