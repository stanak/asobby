"""Keep Random selection separate from the resolved fighter; no historical inference."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for side in ("host", "guest"):
        op.add_column("matches", sa.Column(f"{side}_actual_char", sa.SmallInteger(), nullable=True))
        op.add_column("matches", sa.Column(f"{side}_random", sa.Boolean(), nullable=True))


def downgrade() -> None:
    # Preserve the actual fighter if the old schema is restored.
    op.execute("UPDATE matches SET host_char = COALESCE(host_actual_char, host_char), "
               "guest_char = COALESCE(guest_actual_char, guest_char)")
    for side in ("guest", "host"):
        op.drop_column("matches", f"{side}_random")
        op.drop_column("matches", f"{side}_actual_char")
