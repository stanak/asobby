"""Persist shared battle IDs and unconfirmed result reports without rewriting history."""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "battle_tickets",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("client_id", sa.String(32), primary_key=True),
        sa.Column("match_id", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("post_id", sa.String(32), nullable=False),
        sa.Column("host_user_id", sa.String(32)),
        sa.Column("guest_user_id", sa.String(32)),
        sa.Column("host_profile", sa.String(64), nullable=False),
        sa.Column("guest_profile", sa.String(64), nullable=False),
        sa.Column("match_rank", sa.String(8)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("match_id", "side", name="uq_battle_ticket_side"),
    )
    op.create_index("ix_battle_tickets_match_id", "battle_tickets", ["match_id"])
    op.create_index("ix_battle_ticket_host_start", "battle_tickets", ["host_user_id", "started_at"])
    op.create_index("ix_battle_ticket_guest_start", "battle_tickets", ["guest_user_id", "started_at"])
    op.create_table(
        "match_reports",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("match_id", sa.String(32)),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("conflict_payload", sa.JSON()),
        sa.Column("client_played_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
    )
    op.create_index("ix_match_reports_match_id", "match_reports", ["match_id"])
    op.create_index("ix_match_reports_status", "match_reports", ["status"])
    op.create_index("ix_match_report_client_time", "match_reports", ["user_id", "client_played_at"])
    op.create_index("ix_match_report_received", "match_reports", ["user_id", "received_at"])


def downgrade() -> None:
    op.drop_table("match_reports")
    op.drop_table("battle_tickets")
