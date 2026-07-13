"""replay unique per match

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-13

"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_replays_match_id", "replays", ["match_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_replays_match_id", table_name="replays")
