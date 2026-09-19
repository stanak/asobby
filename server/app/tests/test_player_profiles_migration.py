from pathlib import Path
import sqlite3
import pytest

from alembic import command
from alembic.config import Config


def test_profiles_migration_preserves_existing_names_ranks_and_privacy(tmp_path):
    app = Path(__file__).resolve().parents[1]
    path = tmp_path / "upgrade.db"
    config = Config(str(app / "alembic.ini"))
    config.set_main_option("script_location", str(app / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0016")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users (id, name, created_at, rank) VALUES ('u', 'Discord Name', '2026-09-01', 'ph')")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name, rank, player_name, use_player_name, birth_date, country_code, character_winrates_public, birth_visibility FROM users").fetchone() == ("Discord Name", "ph", "", 0, None, "", 0, "secret")
        assert conn.execute("SELECT COUNT(*) FROM player_profile_tags").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM player_profile_characters").fetchone()[0] == 0


def test_multiple_characters_migration_preserves_choices_and_safe_downgrade(tmp_path):
    app = Path(__file__).resolve().parents[1]
    path = tmp_path / "characters.db"
    config = Config(str(app / "alembic.ini"))
    config.set_main_option("script_location", str(app / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0017")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users (id, name, created_at, rank, main_character, strong_character, weak_character) VALUES ('u', 'Name', '2026-09-01', 'ph', 20, 0, 19)")
        conn.execute("INSERT INTO users (id, name, created_at) VALUES ('unset', 'Unset', '2026-09-01')")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT user_id, kind, character_id FROM player_profile_characters ORDER BY kind").fetchall() == [
            ("u", "strong_characters", 0), ("u", "weak_characters", 19)]
        assert conn.execute("SELECT name, rank, main_character FROM users WHERE id = 'u'").fetchone() == ("Name", "ph", 20)
        for kind, char in [("strong_characters", 0), ("strong_characters", 20), ("weak_characters", -1), ("invalid", 1)]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO player_profile_characters VALUES ('u', ?, ?)", (kind, char))
        conn.execute("INSERT INTO player_profile_characters VALUES ('u', 'strong_characters', 5)")
    with pytest.raises(RuntimeError, match="without losing data"):
        command.downgrade(config, "0017")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM player_profile_characters").fetchone()[0] == 3
        conn.execute("DELETE FROM player_profile_characters WHERE character_id IN (0, 19)")
    command.downgrade(config, "0017")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT strong_character, weak_character FROM users WHERE id = 'u'").fetchone() == (5, None)
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM player_profile_characters").fetchall() == [("u", "strong_characters", 5)]
