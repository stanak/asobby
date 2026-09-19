from battle_identity import BattleIdentity
from local_store import LocalStore


def test_result_identity_and_duration_survive_retries():
    battle = BattleIdentity(100.0)
    original = battle.result_fields(220.0)
    battle.match_id = "a" * 32
    retried = battle.result_fields(230.0)
    assert original["client_id"] == retried["client_id"]
    assert original["duration_sec"] == retried["duration_sec"] == 120
    assert retried["match_id"] == "a" * 32
    assert BattleIdentity(230.0).client_id != battle.client_id


def test_local_store_does_not_merge_short_games_or_create_second_ko(tmp_path):
    store = LocalStore(tmp_path / "matches.db")
    common = dict(my_side="host", winner="host", host_char=0, guest_char=5, host_profile="hp", guest_profile="gp", report_version=2, duration_sec=8)
    first = store.record_local(**common, client_id="a"*32, played_at=100)
    second = store.record_local(**common, client_id="b"*32, played_at=108)
    assert first != second
    assert store.record_local(**{**common, "winner": "guest"}, client_id="a"*32, played_at=109) == first
    assert len(store.fetch_all()) == 2


def test_server_identity_links_clock_skewed_local_row(tmp_path):
    store = LocalStore(tmp_path / "matches.db")
    common = dict(my_side="host", winner="host", host_char=0, guest_char=5, host_profile="hp", guest_profile="gp")
    store.record_local(**common, client_id="a"*32, match_id="b"*32, played_at=100, report_version=2)
    store.accept_report("a"*32, "b"*32, "pending")
    assert store.max_server_played_at() == 0
    store.merge_server_rows([{**common, "id": "b"*32, "client_id": "a"*32, "report_version": 2, "played_at": 482, "ranked": True}])
    rows = store.fetch_all()
    assert len(rows) == 1 and rows[0]["played_at"] == 482
    assert rows[0]["report_status"] == "confirmed"


def test_two_server_ids_remain_distinct_even_with_same_time_and_winner(tmp_path):
    store = LocalStore(tmp_path / "matches.db")
    common = dict(my_side="host", winner="host", host_char=0, guest_char=5, host_profile="hp", guest_profile="gp", report_version=2, played_at=100)
    assert store.merge_server_rows([{**common, "id": "a"*32}, {**common, "id": "b"*32}]) == 2
