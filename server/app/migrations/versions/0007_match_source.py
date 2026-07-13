"""match source column

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-13

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("source", sa.String(8), nullable=False, server_default="host"),
    )
    # ゲスト報告では host_user_id が不明 (NULL) になる
    with op.batch_alter_table("matches") as batch_op:
        batch_op.alter_column("host_user_id", nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("matches") as batch_op:
        batch_op.alter_column("host_user_id", nullable=False)
    op.drop_column("matches", "source")
