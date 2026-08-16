"""match set score columns

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-16

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("host_wins", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("guest_wins", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("matches", "guest_wins")
    op.drop_column("matches", "host_wins")
