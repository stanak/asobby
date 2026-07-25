"""ローカル戦績マージの重複排除テスト。"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from local_store import LocalStore


def test_merge_server_rows_skips_duplicate_after_sync_link():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(Path(tmp) / "matches.db")
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
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(Path(tmp) / "matches.db")
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
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStore(Path(tmp) / "matches.db")
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
