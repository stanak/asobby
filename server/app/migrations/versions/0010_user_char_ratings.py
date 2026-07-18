"""per-character TrueSkill ratings for Ph rank

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-18

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_char_ratings",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("char_id", sa.SmallInteger(), nullable=False),
        sa.Column("ts_mu", sa.Float(), nullable=False, server_default="25.0"),
        sa.Column("ts_sigma", sa.Float(), nullable=False, server_default="8.333333333333334"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "char_id"),
    )
    for char_id in range(21):
        op.execute(
            sa.text(
                "INSERT INTO user_char_ratings (user_id, char_id, ts_mu, ts_sigma) "
                "SELECT id, :char_id, ts_mu, ts_sigma FROM users WHERE rank = 'ph'"
            ).bindparams(char_id=char_id)
        )


def downgrade() -> None:
    op.drop_table("user_char_ratings")
