import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from tools.audit_match_identity import audit


def test_identity_migration_does_not_rewrite_existing_matches(tmp_path, monkeypatch):
    app = Path(__file__).resolve().parents[1]
    path = tmp_path / "upgrade.db"
    config = Config(str(app / "alembic.ini"))
    config.set_main_option("script_location", str(app / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0015")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO users (id, created_at) VALUES ('u', '2026-09-01 00:00:00')")
        conn.execute("INSERT INTO matches (id, host_user_id, winner, played_at, created_at) VALUES ('m', 'u', 'host', '2026-09-19 04:36:29', '2026-09-19 04:42:57')")
        before = conn.execute("SELECT * FROM matches").fetchall()
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    exported = asyncio.run(audit("u", datetime(2026, 9, 19, tzinfo=timezone.utc), datetime(2026, 9, 20, tzinfo=timezone.utc)))
    assert exported["read_only"] and not exported["report_table_present"]
    assert exported["matches"][0]["id"] == "m"
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM matches").fetchall() == before
        assert conn.execute("SELECT COUNT(*) FROM battle_tickets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM match_reports").fetchone()[0] == 0
        indexes = conn.execute("PRAGMA index_list(battle_tickets)").fetchall()
        assert sum(bool(row[2]) for row in indexes) == 2  # PK + per-match side uniqueness
    exported = asyncio.run(audit("u", datetime(2026, 9, 19, tzinfo=timezone.utc), datetime(2026, 9, 20, tzinfo=timezone.utc)))
    assert exported["report_table_present"] and exported["reports"] == []
