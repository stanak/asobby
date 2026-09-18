"""Verify the upgrade against an existing database, never the configured live DB."""
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config


def test_existing_users_get_unused_allowance_without_changing_ranks(tmp_path):
    app_dir = Path(__file__).resolve().parents[1]
    database = tmp_path / "upgrade.db"
    config = Config(str(app_dir / "alembic.ini"))
    config.set_main_option("script_location", str(app_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    command.upgrade(config, "0014")
    with sqlite3.connect(database) as connection:
        for uid, rank, locked in [("new", "normal", 0), ("existing", "hard", 1), ("ph", "ph", 1)]:
            connection.execute(
                "INSERT INTO users (id, created_at, rank, rank_locked, rank_changed_at) VALUES (?, ?, ?, ?, ?)",
                (uid, "2026-08-01 00:00:00", rank, locked, "2026-08-02 00:00:00"),
            )
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        users = connection.execute(
            "SELECT id, rank, rank_locked, rank_changed_at, rank_change_used_at FROM users ORDER BY id"
        ).fetchall()
        assert users == [
            ("existing", "hard", 1, "2026-08-02 00:00:00", None),
            ("new", "normal", 0, "2026-08-02 00:00:00", None),
            ("ph", "ph", 1, "2026-08-02 00:00:00", None),
        ]
        connection.execute("UPDATE users SET rank_change_used_at = '2026-09-18 00:00:00' WHERE id = 'existing'")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT rank_change_used_at FROM users WHERE id = 'existing'").fetchone()[0] == "2026-09-18 00:00:00"
