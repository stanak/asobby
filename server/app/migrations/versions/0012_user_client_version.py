"""users.client_version column

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-25

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("client_version", sa.String(32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("users", "client_version")
