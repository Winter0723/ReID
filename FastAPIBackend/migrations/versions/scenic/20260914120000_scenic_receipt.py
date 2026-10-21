"""Record explicit receipt separately from payment and delivery eligibility."""

from alembic import op
import sqlalchemy as sa

revision = "20260914120000_scenic"
down_revision = "20260907120000_scenic"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scenic_orders", sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.create_index("ix_scenic_orders_claimed_at", "scenic_orders", ["claimed_at"])


def downgrade():
    with op.batch_alter_table("scenic_orders") as batch:
        batch.drop_index("ix_scenic_orders_claimed_at")
        batch.drop_column("claimed_at")
