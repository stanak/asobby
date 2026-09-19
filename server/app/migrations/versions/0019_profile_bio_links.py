"""Optional plain-text biography and ordered, labelled external profile links."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bio", sa.Text(), nullable=False, server_default=""))
    op.add_column("users", sa.Column("profile_links", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("users", "profile_links")
    op.drop_column("users", "bio")
