import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_random_migration_preserves_history_and_has_reversible_schema(tmp_path):
    app = Path(__file__).resolve().parents[1]
    path = tmp_path / "random.db"
    config = Config(str(app / "alembic.ini"))
    config.set_main_option("script_location", str(app / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0019")
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO matches (id, host_char, guest_char, created_at) VALUES ('m', 0, 5, '2026-09-21')")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT host_char, guest_char, host_actual_char, guest_actual_char, host_random, guest_random FROM matches").fetchone() == (0, 5, None, None, None, None)
        c.execute("UPDATE matches SET host_char = 20, host_actual_char = 0, host_random = 1")
    command.downgrade(config, "0019")
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT host_char, guest_char FROM matches").fetchone() == (0, 5)
        assert "host_random" not in {r[1] for r in c.execute("PRAGMA table_info(matches)")}
