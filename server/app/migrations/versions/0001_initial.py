"""initial: users, matches, replays

Revision ID: 0001
Revises:
Create Date: 2026-07-12

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, server_default=""),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_last_ip", "users", ["last_ip"])

    op.create_table(
        "matches",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "host_user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "guest_user_id", sa.String(32), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("host_ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("guest_ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("winner", sa.String(8), nullable=False, server_default=""),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_matches_host_user_id", "matches", ["host_user_id"])
    op.create_index("ix_matches_guest_user_id", "matches", ["guest_user_id"])

    op.create_table(
        "replays",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "match_id",
            sa.String(32),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_replays_match_id", "replays", ["match_id"])


def downgrade() -> None:
    op.drop_table("replays")
    op.drop_table("matches")
    op.drop_table("users")
