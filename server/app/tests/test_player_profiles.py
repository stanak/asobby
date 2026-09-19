from datetime import date
from pathlib import Path
import json
import re
from unittest.mock import AsyncMock

import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
import db
import main
import player_profiles as profiles


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    engine = db.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    async with db.session() as s:
        s.add_all([db.User(id=uid, name=f"Discord {uid}", discord_username=f"login_{uid}") for uid in ("alice", "bob", "secret")])
        await s.commit()
    main.RECORDS.clear()
    main.LAST_CREATE_AT.clear()
    monkeypatch.setattr(main, "SESSION_SECRET", "profile-tests")
    monkeypatch.setattr(main, "HOSTCHECK_ENABLED", False)
    monkeypatch.setattr(profiles, "today", lambda: date(2026, 9, 19))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="https://test") as c:
        yield c
    main.RECORDS.clear()
    await db.dispose()


def auth(uid="alice"):
    return {"Authorization": "Bearer " + main.make_session_token({"id": uid, "name": uid}, 1)}


def example(**updates):
    return {"player_name": "あそび人", "main_character": 20, "use_player_name": True, "birth_date": "2000-09-20", "birth_visibility": "public",
            "bio": "霊夢を使っています。\n対戦よろしくお願いします！",
            "profile_links": [{"label": "YouTube", "url": "https://www.youtube.com/@example"},
                              {"label": "個人サイト", "url": "https://example.com/"}],
            "country_code": "JP", "device_type": "gamepad", "device_model": "HORI ＲＡＰ-４",
            "favorite_players": ["  Top Player  ", "top player", "Other"],
            "other_games": ["STREET FIGHTER 6", "ぷよぷよ"], "strong_characters": [0, 5],
            "weak_characters": [1, 19], "character_winrates_public": False, **updates}


async def save(client, uid="alice", **updates):
    r = await client.put("/user/profile", headers=auth(uid), json=example(**updates))
    assert r.status_code == 200, r.text
    return r


@pytest.mark.asyncio
async def test_defaults_privacy_roundtrip_clear_and_auth(client):
    for path in ("/user/profile", "/api/players", "/api/players/alice", "/api/players/statistics"):
        assert (await client.get(path)).status_code == 401
    assert (await client.put("/user/profile", json=example())).status_code == 401
    defaults = (await client.get("/user/profile", headers=auth())).json()
    assert defaults["birth_date"] is None and not defaults["character_winrates_public"]
    assert defaults["birth_visibility"] == "secret"
    assert defaults["country_code"] == ""  # Never inferred from last IP.
    assert defaults["strong_characters"] == defaults["weak_characters"] == []
    assert defaults["bio"] == "" and defaults["profile_links"] == []
    await save(client)
    own = await client.get("/user/profile", headers=auth())
    assert own.headers["cache-control"] == "private, no-store"
    assert own.json()["birth_date"] == "2000-09-20"
    assert own.json()["favorite_players"] == ["Other", "Top Player"]
    assert own.json()["strong_characters"] == [0, 5]
    assert own.json()["weak_characters"] == [1, 19]
    assert own.json()["main_character"] == 20
    public = await client.get("/api/players/alice", headers=auth("bob"))
    assert public.json()["age"] == 25
    assert public.json()["character_winrates"] is None
    assert "birth_date" not in public.text and "2000-09-20" not in public.text
    assert "token_version" not in public.text and "last_ip" not in public.text
    assert public.headers["cache-control"] == "private, no-store"
    # Even the owner's public URL doesn't contain the private birth date.
    assert "birth_date" not in (await client.get("/api/players/alice", headers=auth())).text
    await save(client, birth_date=None, birth_visibility="secret", country_code="")
    assert (await client.get("/api/players/alice", headers=auth("bob"))).json()["age"] is None
    async with db.session() as s:
        assert (await s.get(db.User, "alice")).birth_date is None
    assert (await client.get("/api/players/missing", headers=auth())).status_code == 404


@pytest.mark.parametrize("payload", [
    {"player_name": "あ" * 13}, {"player_name": "a" * 25}, {"player_name": "😀"},
    {"player_name": "abc\n"}, {"player_name": "", "use_player_name": True},
    {"birth_date": "2026-09-20"}, {"birth_date": "2025-02-29"}, {"birth_date": "1899-01-01"},
    {"country_code": "XX"}, {"device_type": "joystick"}, {"device_model": "pad", "device_type": ""},
    {"strong_characters": [20]}, {"weak_characters": [-1]}, {"strong_characters": [True]},
    {"strong_characters": 0}, {"weak_characters": None}, {"strong_characters": ["5"]},
    {"weak_characters": [1.0]}, {"strong_characters": [0] * 21}, {"weak_characters": [0] * 21},
    {"strong_character": 0}, {"weak_character": 1},
    {"favorite_players": ["x"] * 21}, {"other_games": ["x"] * 31}, {"device_model": "x" * 121},
    {"other_games": ["x\x00y"]}, {"rank": "ph"}, {"id": "bob"}, {"character_winrates_public": "false"},
    {"main_character": 21}, {"main_character": -1}, {"main_character": True},
    {"birth_visibility": "invalid"}, {"birth_date": None, "birth_visibility": "public"},
    {"birth_date": None, "birth_visibility": "statistics"},
    {"bio": "あ" * 401}, {"bio": "😀" * 401}, {"bio": "a\x00b"}, {"bio": "a\x7fb"},
    {"bio": None}, {"bio": 123}, {"profile_links": None}, {"profile_links": "https://example.com/"},
    {"profile_links": [{"label": "site", "url": "https://example.com/"}] * 11},
    {"profile_links": [{"label": "site"}]}, {"profile_links": [{"url": "https://example.com/"}]},
    {"profile_links": [{"label": " " * 2, "url": "https://example.com/"}]},
    {"profile_links": [{"label": "a" * 41, "url": "https://example.com/"}]},
    {"profile_links": [{"label": "site\n", "url": "https://example.com/"}]},
    {"profile_links": [{"label": "site", "url": "https://example.com/", "html": "<b>x</b>"}]},
])
@pytest.mark.asyncio
async def test_invalid_values_cannot_mutate_profile(client, payload):
    r = await client.put("/user/profile", headers=auth(), json=example(**payload))
    assert r.status_code == 422, r.text
    assert (await client.get("/user/profile", headers=auth())).json()["player_name"] == ""


@pytest.mark.parametrize("name", ["あ" * 12, "a" * 24, "a" * 12 + "漢" * 6, "ｱ" * 24])
def test_exact_cp932_limit(name):
    assert profiles.ProfileIn(player_name=name).player_name == name


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>alert(1)</script>",
    "file:///C:/test", "ftp://example.com/", "//example.com/", "/profile", "example.com", "https:example.com",
    "https:///example.com", "https://", "https://?x=1", "https://user:pass@example.com/",
    "https://user@example.com/", "https://example.com\\@evil.example/", "https://exam\nple.com/",
    "https://example.com/has space", "https://example.com/\x00", "https://example.com/\u200b",
    "https://example.com:99999/", "https://[invalid]/", "https://example.com/" + "x" * 2048,
])
def test_profile_links_reject_unsafe_or_malformed_urls(url):
    with pytest.raises(ValueError):
        profiles.ProfileLinkIn(label="site", url=url)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/@example", "https://www.twitch.tv/example", "https://x.com/example",
    "http://example.com/path?lang=ja#about", "https://example.com/日本語", "https://例え.jp/",
])
def test_profile_links_allow_generic_http_urls(url):
    link = profiles.ProfileLinkIn(label=" My site ", url=url)
    assert link.label == "My site" and link.url.startswith(("http://", "https://"))


@pytest.mark.asyncio
async def test_bio_links_roundtrip_limits_clear_and_atomic_validation(client):
    bio = "こんにちは👩‍💻\r\n対戦歓迎\r<img src=x onerror=alert(1)>\n\tよろしく！"
    links = [{"label": " YouTube ", "url": "HTTPS://WWW.YOUTUBE.COM/@example"},
             {"label": "個人サイト", "url": "http://example.com/"}]
    await save(client, bio=bio, profile_links=links)
    expected_bio = bio.replace("\r\n", "\n").replace("\r", "\n")
    expected_links = [{"label": "YouTube", "url": "https://www.youtube.com/@example"}, links[1]]
    for path, uid in [("/user/profile", "alice"), ("/api/players/alice", "bob"), ("/api/players?name=login_alice", "bob")]:
        data = (await client.get(path, headers=auth(uid))).json()
        if "players" in data:
            data = data["players"][0]
        assert data["bio"] == expected_bio and data["profile_links"] == expected_links
    for updates in ({"bio": "x" * 401}, {"profile_links": [{"label": "bad", "url": "javascript:alert(1)"}]}):
        result = await client.put("/user/profile", headers=auth(), json=example(**updates))
        assert result.status_code == 422
        own = (await client.get("/user/profile", headers=auth())).json()
        assert own["bio"] == expected_bio and own["profile_links"] == expected_links
    links = [{"label": "😀" * 40, "url": f"https://example.com/{n}"} for n in range(10)]
    for bio in ("あ" * 400, "😀" * 400, "x" * 398 + "\r\n\t"):
        await save(client, bio=bio, profile_links=links)
        own = (await client.get("/user/profile", headers=auth())).json()
        assert len(own["bio"]) == 400 and own["profile_links"] == links
    assert (await client.get("/user/profile", headers=auth("bob"))).json()["profile_links"] == []
    await save(client, bio="", profile_links=[])
    own = (await client.get("/user/profile", headers=auth())).json()
    assert own["bio"] == "" and own["profile_links"] == []
    await save(client)
    assert (await client.put("/user/profile", headers=auth(), json={})).status_code == 200
    own = (await client.get("/user/profile", headers=auth())).json()
    assert own["bio"] == "" and own["profile_links"] == []


def test_edit_documentation_covers_entire_input_schema():
    document = (Path(__file__).resolve().parents[2] / "docs/player-profiles.md").read_text()
    example = json.loads(re.search(r"<!-- player-profile-edit -->\s*```json\s*(.*?)\s*```", document, re.S)[1])
    assert set(example) == set(profiles.ProfileIn.model_fields)
    assert profiles.ProfileIn.model_validate(example).main_character == 20


@pytest.mark.asyncio
async def test_all_search_fields_and_inclusive_age_boundaries(client):
    await save(client)
    await save(client, "bob", player_name="Bob", main_character=0, birth_date="2000-09-19", country_code="US", device_type="keyboard",
               device_model="Kinesis", favorite_players=[], other_games=[], strong_characters=[6], weak_characters=[])
    queries = ["name=あそ", "name=DISCORD+ALICE", "name=LOGIN_ALICE", "main_character=20", "age_min=25&age_max=25",
               "country_code=JP", "device_type=gamepad", "device_model=rap-4", "favorite_player=top",
               "game=street+fighter", "strong_char=0", "strong_char=5", "weak_char=1", "weak_char=19",
               "strong_char=5&weak_char=19",
               "rank=normal&device_model=rap", "country_code=jp&age_max=25&game=ぷよ"]
    for query in queries:
        r = await client.get(f"/api/players?{query}", headers=auth())
        assert r.status_code == 200, r.text
        assert [p["id"] for p in r.json()["players"]] == ["alice"], query
        assert "birth_date" not in r.text and "2000-09-20" not in r.text
        assert "total_matches" not in r.text and "character_winrates\"" not in r.text
    for query in ("country_code=JP&device_type=keyboard", "age_min=0&age_max=10", "name=%25", "game=%25", "device_model=%25"):
        assert (await client.get(f"/api/players?{query}", headers=auth())).json()["total"] == 0
    assert (await client.get("/api/players?age_min=26&age_max=26", headers=auth())).json()["players"][0]["id"] == "bob"
    assert (await client.get("/api/players?main_character=0", headers=auth())).json()["players"][0]["id"] == "bob"
    # Secret/unset birthdays cannot match an age filter, even min=0.
    assert (await client.get("/api/players?age_min=0", headers=auth())).json()["total"] == 2
    pages = [(await client.get(f"/api/players?limit=1&page={n}", headers=auth())).json() for n in (1, 2, 3)]
    assert [p["players"][0]["id"] for p in pages] == ["alice", "bob", "secret"]


@pytest.mark.asyncio
async def test_clear_tags_and_unicode_expansion_are_safe(client):
    await save(client, device_model="ﬃ" * 120, other_games=["ﬃ" * 120])
    assert (await client.get("/api/players?device_model=ffi&game=ffi", headers=auth())).json()["total"] == 1
    await save(client, main_character=None, favorite_players=[], other_games=[], strong_characters=[], weak_characters=[])
    saved = (await client.get("/user/profile", headers=auth())).json()
    assert saved["main_character"] is None
    assert saved["strong_characters"] == saved["weak_characters"] == []
    assert all(saved[key] == [] for key in profiles.TAG_FIELDS)
    assert (await client.get("/api/players?game=ffi", headers=auth())).json()["total"] == 0
    for query in ("strong_char=0", "strong_char=5", "weak_char=1", "weak_char=19"):
        assert (await client.get(f"/api/players?{query}", headers=auth())).json()["total"] == 0


@pytest.mark.asyncio
async def test_multiple_characters_roundtrip_dedup_replace_and_search(client):
    await save(client, strong_characters=[5, 0, 5], weak_characters=[19, 5, 19])
    await save(client, "bob", strong_characters=[5, 6], weak_characters=[1])
    for path in ("/user/profile", "/api/players/alice", "/api/players?name=login_alice"):
        data = (await client.get(path, headers=auth())).json()
        if "players" in data:
            data = data["players"][0]
        assert data["strong_characters"] == [0, 5]
        assert data["weak_characters"] == [5, 19]  # Same ID may be selected in both fields.
        assert "strong_character" not in data and "weak_character" not in data
    for query, ids in [
        ("strong_char=5", ["alice", "bob"]), ("weak_char=5", ["alice"]),
        ("strong_char=5&weak_char=19", ["alice"]), ("strong_char=0&weak_char=1", []),
        ("strong_char=19", []), ("weak_char=0", []),
        ("strong_char=5&limit=1&page=2", ["bob"]),
    ]:
        data = (await client.get(f"/api/players?{query}", headers=auth())).json()
        assert [p["id"] for p in data["players"]] == ids
        assert data["total"] == (2 if "strong_char=5" == query or "page=2" in query else len(ids))
    await save(client, strong_characters=[6], weak_characters=[])
    assert (await client.get("/api/players?strong_char=0", headers=auth())).json()["total"] == 0
    assert (await client.get("/api/players?weak_char=19", headers=auth())).json()["total"] == 0
    bob = (await client.get("/user/profile", headers=auth("bob"))).json()
    assert bob["strong_characters"] == [5, 6] and bob["weak_characters"] == [1]
    async with db.session() as s:
        user = await s.get(db.User, "alice")
        assert user.strong_character == 6 and user.weak_character is None
    # All 20 are accepted, and invalid changes must not erase an existing selection.
    await save(client, strong_characters=list(range(20)), weak_characters=list(range(20)))
    r = await client.put("/user/profile", headers=auth(), json=example(strong_characters=[0, 20]))
    assert r.status_code == 422
    own = (await client.get("/user/profile", headers=auth())).json()
    assert own["strong_characters"] == own["weak_characters"] == list(range(20))
    # Omitted fields follow full-replacement semantics, restoring empty lists.
    assert (await client.put("/user/profile", headers=auth(), json={})).status_code == 200
    own = (await client.get("/user/profile", headers=auth())).json()
    assert own["strong_characters"] == own["weak_characters"] == []


@pytest.mark.parametrize("query", ["age_min=30&age_max=20", "age_min=-1", "strong_char=20",
    "rank=bogus", "country_code=XX", "page=0", "limit=101", "rating_min=nan", "rating_max=inf",
    "rating_min=2&rating_max=1", "total_matches=1", "unique_opponents=10", "win_rate=0.5", "birth_date=2000-01-01", "main_character=21"])
@pytest.mark.asyncio
async def test_invalid_or_prohibited_search_fields(client, query):
    r = await client.get(f"/api/players?{query}", headers=auth())
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_ph_rank_rating_and_multiple_rank_search(client):
    async with db.session() as s:
        user = await s.get(db.User, "alice")
        user.rank, user.ts_mu, user.ts_sigma = "ph", 42.34, 1.0
        await s.commit()
    r = (await client.get("/api/players/alice", headers=auth())).json()
    assert (r["rank_symbol"], r["rating"]) == ("Ph", 39.3)
    found = (await client.get("/api/players?rank=ph", headers=auth())).json()
    assert [p["id"] for p in found["players"]] == ["alice"]
    assert (await client.get("/api/players?rank=ph&rank=normal", headers=auth())).json()["total"] == 3
    assert (await client.get("/api/players?rating_min=39.3&rating_max=39.3", headers=auth())).status_code == 422


@pytest.mark.asyncio
async def test_stats_deduplicate_ids_and_enforce_permission(client):
    async with db.session() as s:
        for index, (host, guest, winner) in enumerate([
            ("alice", "bob", "host"), ("bob", "alice", "host"), ("alice", "bob", "draw"),
            ("alice", "secret", "host"), ("alice", None, "guest"), ("alice", "bob", ""),
        ]):
            s.add(db.Match(id=str(index), host_user_id=host, guest_user_id=guest, winner=winner,
                           host_char=0 if host == "alice" else 5, guest_char=0 if guest == "alice" else 5))
        s.add(db.MatchReport(user_id="alice", client_id="a"*32, side="host", payload={}, status="pending", reason="waiting"))
        await s.commit()
    public = (await client.get("/api/players/alice", headers=auth("bob"))).json()
    assert (public["total_matches"], public["unique_opponents"], public["unidentified_opponent_matches"]) == (5, 2, 1)
    assert public["character_winrates"] is None
    own = (await client.get("/api/players/alice", headers=auth())).json()
    assert own["character_winrates"] == [{"char": 0, "games": 5, "wins": 2, "losses": 2, "draws": 1, "win_rate": .4}]
    await save(client, character_winrates_public=True)
    assert (await client.get("/api/players/alice", headers=auth("bob"))).json()["character_winrates"] == own["character_winrates"]
    await save(client, character_winrates_public=False)
    assert (await client.get("/api/players/alice", headers=auth("bob"))).json()["character_winrates"] is None


@pytest.mark.asyncio
async def test_lobby_display_change_existing_new_guest_and_oauth_preservation(client, monkeypatch):
    host = main.PostRecord(main.Post(owner_name="Discord alice", updated_at=5), "token", "", owner_user_id="alice")
    other = main.PostRecord(main.Post(guest_name="Discord alice", guest_user_id="alice"), "other", "", owner_user_id="bob")
    main.RECORDS[host.post.id] = host
    main.RECORDS[other.post.id] = other
    publish = AsyncMock()
    monkeypatch.setattr(main.HUB, "publish", publish)
    await save(client)
    assert host.post.owner_name == other.post.guest_name == "あそび人"
    assert host.post.updated_at == 5  # Editing a profile must not renew an expired host.
    assert host.post.owner_profile_url == "/players/alice"
    assert publish.await_count == 2
    user = await db.upsert_user_on_login("alice", "Renamed Discord", "", discord_username="new_login")
    assert user.player_name == "あそび人" and user.name == "Renamed Discord" and user.discord_username == "new_login"
    # Native listing creation uses the selected name, not the name inside an old token.
    r = await client.post("/posts", headers=auth(), json={"addr":"8.8.8.8:10800", "post_type":"casual"})
    assert r.status_code == 200, r.text
    assert r.json()["post"]["owner_name"] == "あそび人"
    assert r.json()["post"]["owner_profile_url"] == "/players/alice"
    await save(client, use_player_name=False)
    assert host.post.owner_name == "Renamed Discord"
    # Updates target only the authenticated user.
    assert (await client.get("/user/profile", headers=auth("bob"))).json()["player_name"] == ""
    guest = await db.get_user("alice")
    assert main._apply_guest_identity(other, guest)
    assert other.post.guest_name == "Renamed Discord"


@pytest.mark.asyncio
async def test_statistics_only_use_opted_in_fields(client):
    async with db.session() as s, s.begin():
        s.add_all([db.User(id=f"extra{i}") for i in range(5)])
    await save(client)
    stats = (await client.get("/api/players/statistics", headers=auth())).json()
    assert stats["suppressed"] is False
    assert stats["countries"] == [{"country_code": "JP", "count": None}]
    assert stats["age_bands"][2] == {"min": 20, "max": 29, "count": None}
    await save(client, birth_date=None, birth_visibility="secret", country_code="")
    stats = (await client.get("/api/players/statistics", headers=auth())).json()
    assert stats["countries"] == [] and sum(b["count"] for b in stats["age_bands"]) == 0


@pytest.mark.asyncio
async def test_birth_visibility_states_apply_to_all_outputs_and_search(client):
    for visibility, visible_age, searchable in [
        ("public", 25, 1), ("statistics", None, 0), ("secret", None, 0),
    ]:
        # Secret must erase the supplied date too, not just rely on the UI to omit it.
        await save(client, birth_visibility=visibility)
        own = (await client.get("/user/profile", headers=auth())).json()
        assert own["birth_visibility"] == visibility
        assert own["birth_date"] == (None if visibility == "secret" else "2000-09-20")
        public = (await client.get("/api/players/alice", headers=auth("bob"))).json()
        assert public["age"] == visible_age
        assert "birth_date" not in public and "birth_visibility" not in public
        listing = (await client.get("/api/players?name=alice", headers=auth("bob"))).json()["players"][0]
        assert listing["age"] == visible_age and "birth_visibility" not in listing
        for condition in ("age_min=0", "age_max=100", "age_min=25&age_max=25"):
            result = (await client.get(f"/api/players?{condition}", headers=auth("bob"))).json()
            assert result["total"] == searchable
        stats = (await client.get("/api/players/statistics", headers=auth("bob"))).json()
        assert stats["suppressed"] is True and stats["age_bands"] == []  # Only three users: do not expose small counts.
        async with db.session() as s:
            assert (await s.get(db.User, "alice")).birth_date == (None if visibility == "secret" else date(2000, 9, 20))


def test_age_on_leap_day_and_birthday(monkeypatch):
    assert profiles.age(date(2000, 2, 29), date(2025, 2, 28)) == 24
    assert profiles.age(date(2000, 2, 29), date(2025, 3, 1)) == 25
    assert profiles.years_ago(date(2024, 2, 29), 1) == date(2023, 2, 28)


@pytest.mark.asyncio
async def test_routes_and_country_list(client):
    for path in ("/players", "/players/alice", "/profile"):
        r = await client.get(path)
        assert r.status_code == 200 and "/static/players.js" in r.text
    countries = (await client.get("/api/players/options")).json()["countries"]
    assert len(countries) == len(set(countries)) == 249
    assert {"JP", "US", "GB", "TW"} <= set(countries)
