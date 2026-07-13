"""initial rank choice

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-13

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("rank_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET rank = 'normal' WHERE rank = 'easy'")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("rank", server_default="normal")
    else:
        op.alter_column("users", "rank", server_default="normal")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("rank", server_default="easy")
    else:
        op.alter_column("users", "rank", server_default="easy")
    op.drop_column("users", "rank_locked")
