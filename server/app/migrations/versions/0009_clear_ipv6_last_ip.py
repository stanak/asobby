"""clear IPv6 values from users.last_ip

last_ip は th123 echo プローブで得たゲスト IPv4 との照合に使うため、
Web ブラウザ経由で混入した IPv6 は照合不能。以後は保存時に弾くので、
既存の IPv6 値を空にする。

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-14

"""
from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET last_ip = '' WHERE last_ip LIKE '%:%'")


def downgrade() -> None:
    pass
