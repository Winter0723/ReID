"""Support camera recordings and media files larger than 2 GiB."""

from alembic import op
import sqlalchemy as sa

revision = "20261012100000_scenic"
down_revision = "20260929100000_scenic"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("media_files") as batch:
        batch.alter_column("size_bytes", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)


def downgrade():
    # Refuse a lossy rollback, including on SQLite where INTEGER accepts 64 bits.
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM media_files WHERE size_bytes > 2147483647 OR size_bytes < -2147483648 LIMIT 1"
    )).first():
        raise RuntimeError("Cannot downgrade media_files.size_bytes: values exceed the INTEGER range")
    with op.batch_alter_table("media_files") as batch:
        batch.alter_column("size_bytes", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
