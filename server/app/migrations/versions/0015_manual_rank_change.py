"""one extra manual rank change per account

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL grants the unused allowance to existing as well as new accounts.
    op.add_column("users", sa.Column("rank_change_used_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "rank_change_used_at")
