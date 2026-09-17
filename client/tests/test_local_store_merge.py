"""ローカル戦績マージの重複排除テスト。"""
from __future__ import annotations

import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from local_store import LocalStore


@contextmanager
def temporary_store():
    # SQLite's transaction context does not close the connection. Track the
    # real test connections and close them before Windows deletes the DB file;
    # do not rely on GC timing or change the store's transaction behavior.
    connect = LocalStore._connect
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as cleanup:
        def tracked_connect(store):
            conn = connect(store)
            cleanup.callback(conn.close)
            return conn
        with patch.object(LocalStore, "_connect", tracked_connect):
            yield LocalStore(Path(tmp) / "matches.db")


def test_merge_server_rows_skips_duplicate_after_sync_link():
    with temporary_store() as store:
        played_at = time.time()
        local_id = store.record_local(
            my_side="host",
            winner="host",
            host_char=0,
            guest_char=1,
            host_profile="Alice",
            guest_profile="Bob",
        )
        store.mark_pushed(local_id, "sync-server-id")

        # 同一対戦が host 報告など別 server_id で返っても二重 insert しない
        inserted = store.merge_server_rows(
            [
                {
                    "id": "host-server-id",
                    "played_at": played_at + 2,
                    "winner": "host",
                    "host_char": 0,
                    "guest_char": 1,
                    "host_profile": "Alice",
                    "guest_profile": "Bob",
                    "my_side": "host",
                    "ranked": 0,
                    "source": "host",
                }
            ]
        )
        assert inserted == 0
        rows = store.fetch_all()
        assert len(rows) == 1
        assert rows[0]["host_profile"] == "Alice"
        assert rows[0]["guest_profile"] == "Bob"


def test_record_local_deduplicates_recent_same_match():
    with temporary_store() as store:
        kwargs = dict(
            my_side="host",
            winner="host",
            host_char=0,
            guest_char=1,
            host_profile="Alice",
            guest_profile="Bob",
        )
        first = store.record_local(**kwargs)
        second = store.record_local(**kwargs)
        assert first == second
        assert len(store.fetch_all()) == 1


def test_merge_server_rows_links_local_by_profile_not_winner_only():
    """winner だけ一致する別対戦に誤リンクしない。"""
    with temporary_store() as store:
        t0 = time.time()
        bob_id = store.record_local(
            my_side="host",
            winner="host",
            host_char=0,
            guest_char=1,
            host_profile="Alice",
            guest_profile="Bob",
        )
        store.record_local(
            my_side="host",
            winner="host",
            host_char=0,
            guest_char=2,
            host_profile="Alice",
            guest_profile="Carol",
        )
        inserted = store.merge_server_rows(
            [
                {
                    "id": "server-bob",
                    "played_at": t0 + 5,
                    "winner": "host",
                    "host_char": 0,
                    "guest_char": 1,
                    "host_profile": "Alice",
                    "guest_profile": "Bob",
                    "my_side": "host",
                    "ranked": 0,
                    "source": "host",
                }
            ]
        )
        assert inserted == 0
        rows = {r["guest_profile"]: r for r in store.fetch_all()}
        assert rows["Bob"]["server_id"] == "server-bob"
        assert rows["Bob"]["id"] == bob_id
        assert rows["Carol"]["server_id"] is None
