"""add match details

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("host_char", sa.SmallInteger(), nullable=True))
    op.add_column("matches", sa.Column("guest_char", sa.SmallInteger(), nullable=True))
    op.add_column(
        "matches",
        sa.Column("host_profile", sa.String(64), nullable=False, server_default=""),
    )
    op.add_column(
        "matches",
        sa.Column("guest_profile", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("matches", "guest_profile")
    op.drop_column("matches", "host_profile")
    op.drop_column("matches", "guest_char")
    op.drop_column("matches", "host_char")
