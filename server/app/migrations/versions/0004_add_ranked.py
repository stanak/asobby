"""add ranked match system

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-13

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("rank", sa.String(8), nullable=False, server_default="easy"),
    )
    op.add_column(
        "users",
        sa.Column("rank_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "ts_mu",
            sa.Float(),
            nullable=False,
            server_default=sa.text("25.0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "ts_sigma",
            sa.Float(),
            nullable=False,
            server_default=sa.text("8.333333333333334"),
        ),
    )
    op.add_column(
        "matches",
        sa.Column("ranked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("matches", "ranked")
    op.drop_column("users", "ts_sigma")
    op.drop_column("users", "ts_mu")
    op.drop_column("users", "rank_changed_at")
    op.drop_column("users", "rank")
