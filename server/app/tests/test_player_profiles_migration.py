from pathlib import Path
import sqlite3

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
