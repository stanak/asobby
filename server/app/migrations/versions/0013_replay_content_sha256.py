"""replays.content_sha256 unique

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-03

"""
from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "replays",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, data FROM replays")).fetchall()
    for row_id, data in rows:
        digest = hashlib.sha256(data).hexdigest()
        conn.execute(
            sa.text("UPDATE replays SET content_sha256 = :digest WHERE id = :id"),
            {"digest": digest, "id": row_id},
        )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("replays") as batch_op:
            batch_op.alter_column("content_sha256", nullable=False)
    else:
        op.alter_column("replays", "content_sha256", nullable=False)
    op.create_index(
        "uq_replays_content_sha256",
        "replays",
        ["content_sha256"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_replays_content_sha256", table_name="replays")
    op.drop_column("replays", "content_sha256")
