"""match rank column

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-14

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("match_rank", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matches", "match_rank")
