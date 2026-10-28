"""Persist immutable project model bindings; legacy rows remain unverified."""
from alembic import op
import sqlalchemy as sa
revision = "20261026090000_core"
down_revision = "20261012100000_core"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("reid_model_id", sa.String(64), nullable=False, server_default=""))
    op.add_column("projects", sa.Column("embedding_space_id", sa.String(64), nullable=False, server_default=""))
    op.add_column("projects", sa.Column("model_snapshot_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    with op.batch_alter_table("projects") as batch:
        for name in ("model_snapshot_json", "embedding_space_id", "reid_model_id"):
            batch.drop_column(name)
