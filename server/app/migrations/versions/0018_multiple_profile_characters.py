"""Allow multiple declared strong/difficult characters; preserve existing choices."""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_profile_characters",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kind", sa.String(24), primary_key=True),
        sa.Column("character_id", sa.SmallInteger(), primary_key=True),
        sa.CheckConstraint("kind IN ('strong_characters', 'weak_characters')", name="ck_profile_character_kind"),
        sa.CheckConstraint("character_id BETWEEN 0 AND 19", name="ck_profile_character_id"),
    )
    op.create_index("ix_profile_character_lookup", "player_profile_characters", ["kind", "character_id", "user_id"])
    for column in ("strong_character", "weak_character"):
        op.execute(sa.text(
            f"INSERT INTO player_profile_characters (user_id, kind, character_id) "
            f"SELECT id, '{column}s', {column} FROM users WHERE {column} IS NOT NULL"
        ))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text(
        "SELECT 1 FROM player_profile_characters GROUP BY user_id, kind HAVING COUNT(*) > 1 LIMIT 1"
    )).first():
        raise RuntimeError("Cannot downgrade profiles with multiple characters without losing data; reduce each selection to at most one first.")
    for column in ("strong_character", "weak_character"):
        op.execute(sa.text(
            f"UPDATE users SET {column} = (SELECT character_id FROM player_profile_characters "
            f"WHERE user_id = users.id AND kind = '{column}s')"
        ))
    op.drop_table("player_profile_characters")
