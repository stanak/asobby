"""Opt-in player profiles; do not infer birthdays/countries from other data."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, length in (("discord_username", 100), ("player_name", 24),
                         ("country_code", 2), ("device_type", 16),
                         ("device_model", 120)):
        op.add_column("users", sa.Column(name, sa.String(length), nullable=False, server_default=""))
    op.add_column("users", sa.Column("device_model_search", sa.Text(), nullable=False, server_default=""))
    for name in ("use_player_name", "character_winrates_public"):
        op.add_column("users", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column("users", sa.Column("birth_visibility", sa.String(16), nullable=False, server_default="secret"))
    for name in ("main_character", "strong_character", "weak_character"):
        op.add_column("users", sa.Column(name, sa.SmallInteger(), nullable=True))
    for name in ("birth_date", "country_code", "device_type", "main_character", "strong_character", "weak_character"):
        op.create_index(f"ix_users_{name}", "users", [name])
    op.create_table(
        "player_profile_tags",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(24), primary_key=True),
        sa.Column("value", sa.String(120), primary_key=True),
        sa.Column("search_value", sa.Text(), nullable=False),
    )
    op.create_index("ix_profile_tag_kind_user", "player_profile_tags", ["kind", "user_id"])


def downgrade() -> None:
    op.drop_table("player_profile_tags")
    for name in ("birth_date", "country_code", "device_type", "main_character", "strong_character", "weak_character"):
        op.drop_index(f"ix_users_{name}", table_name="users")
    for name in ("discord_username", "player_name", "use_player_name", "birth_date",
                 "country_code", "device_type", "device_model", "device_model_search",
                 "character_winrates_public", "main_character", "strong_character", "weak_character", "birth_visibility"):
        op.drop_column("users", name)
