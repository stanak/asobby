"""add user avatar

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("avatar", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("users", "avatar")
