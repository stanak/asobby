from datetime import date
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ASOBBY_HOSTCHECK", "off")
import db
import main
import player_profiles as profiles


def auth(uid="a0"):
    return {"Authorization": "Bearer " + main.make_session_token({"id": uid, "name": "Viewer"}, 1)}


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    engine = db.init_engine(f"sqlite+aiosqlite:///{tmp_path / 'analytics.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    monkeypatch.setattr(main, "SESSION_SECRET", "statistics-tests")
    monkeypatch.setattr(profiles, "today", lambda: date(2026, 9, 19))
    async with db.session() as s, s.begin():
        for i in range(10):
            s.add(db.User(id=f"a{i}", name=f"Private name {i}", birth_date=date(2000, 9, 20),
                birth_visibility="statistics" if i < 6 else "public", rank="normal" if i < 5 else "hard",
                country_code="JP" if i < 5 else "US", device_type="keyboard" if i < 5 else "gamepad",
                character_winrates_public=True))
        for i in range(5):
            s.add(db.User(id=f"b{i}", birth_date=date(1990, 1, 1), birth_visibility="public", rank="ex",
                          country_code="JP", device_type="gamepad", character_winrates_public=True))
            # Even an old/corrupt saved date with Secret visibility must not be used.
            s.add(db.User(id=f"c{i}", birth_date=date(2000, 9, 20), birth_visibility="secret", rank="easy"))
        await s.flush()
        for i in range(5):
            s.add(db.Match(id=f"ranked{i}", host_user_id=f"a{i}", guest_user_id=f"a{i+5}",
                          host_char=0, guest_char=5, winner="draw" if i == 4 else "host", ranked=True))
            s.add(db.Match(id=f"casual{i}", host_user_id=f"b{i}", guest_user_id=None,
                          host_char=20, guest_char=0, winner="host", ranked=False))
        s.add(db.Match(id="unsettled", host_user_id="a0", guest_user_id="b0", host_char=0, guest_char=20, winner=""))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="https://test") as c:
        yield c
    await db.dispose()


async def get(client, query=""):
    result = await client.get(f"/api/players/analytics?{query}", headers=auth())
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "private, no-store"
    assert "Private name" not in result.text and "2000-09-20" not in result.text
    assert "user_id" not in result.text and "birth_date" not in result.text
    return result.json()


@pytest.mark.asyncio
async def test_random_selection_is_counted_only_in_random_cohort(client):
    async with db.session() as s, s.begin():
        for i in range(5):
            match = await s.get(db.Match, f"ranked{i}")
            match.host_char, match.host_actual_char, match.host_random = 20, 0, True
    data = await get(client, "character=20&match_type=ranked")
    assert data["total_players"] == data["characters"][20]["games"] == 5
    assert data["characters"][20]["wins"] == 4 and data["characters"][20]["draws"] == 1
    assert data["characters"][0]["games"] == 0
    assert (await get(client, "character=0&match_type=ranked"))["suppressed"] is True


def counts(data, field):
    return {row["key"]: row["count"] for row in data["distributions"][field]}


@pytest.mark.asyncio
async def test_all_distributions_consented_ages_and_match_math(client):
    assert (await client.get("/api/players/analytics")).status_code == 401
    page = await client.get("/players/statistics")
    assert page.status_code == 200 and "/static/players.js" in page.text
    data = await get(client)
    assert data["total_players"] == 20 and data["suppressed"] is False
    assert data["age_scope"] == "consented" and data["min_players"] == 5
    assert data["match_scope"] == "all_confirmed"
    assert counts(data, "age_band")["20"] == 10  # Includes six privately consented dates.
    assert counts(data, "age_band")["30"] == counts(data, "age_band")["unknown"] == 5
    assert counts(data, "rank")["normal"] == counts(data, "rank")["hard"] == 5
    assert counts(data, "country") == {"JP": 10, "US": 5, "unknown": 5}
    assert counts(data, "device") == {"keyboard": 5, "gamepad": 10, "arcade": 0, "other": 0, "unknown": 5}
    assert (data["match_players"], data["unique_matches"], data["participations"]) == (15, 10, 15)
    assert data["characters"][0] == {"char": 0, "games": 5, "wins": 4, "losses": 0, "draws": 1, "win_rate": .8, "suppressed": False}
    assert data["characters"][5]["win_rate"] == 0
    assert data["characters"][20]["win_rate"] == 1
    assert data["characters"][1]["games"] == 0 and data["characters"][1]["win_rate"] is None
    # Aggregate consent never makes the private date individually searchable/public.
    private = (await client.get("/api/players/a0", headers=auth())).json()
    assert private["age"] is None and "birth_date" not in private
    search = (await client.get("/api/players?age_min=20&age_max=29", headers=auth())).json()
    assert search["total"] == 4


@pytest.mark.parametrize("query, total", [
    ("age_band=20", 10), ("age_band=30", 5), ("age_band=unknown", 5), ("rank=normal", 5),
    ("country_code=jp", 10), ("country_code=US", 5), ("country_code=unknown", 5),
    ("device_type=gamepad", 10), ("device_type=unknown", 5), ("character=0", 5),
    ("character=20", 5), ("match_type=ranked", 10), ("match_type=casual", 5),
    ("age_band=20&rank=normal&country_code=JP&device_type=keyboard&character=0&match_type=ranked", 5),
])
@pytest.mark.asyncio
async def test_filters_and_combinations(client, query, total):
    data = await get(client, query)
    assert data["total_players"] == total
    assert data["suppressed"] is False
    for field in data["distributions"]:
        assert sum(row["count"] for row in data["distributions"][field]) == total


@pytest.mark.asyncio
async def test_twenties_rank_distribution_and_character_filter(client):
    data = await get(client, "age_band=20")
    assert counts(data, "rank")["normal"] == counts(data, "rank")["hard"] == 5
    assert data["unique_matches"] == 5 and data["participations"] == 10
    data = await get(client, "character=0")
    assert data["unique_matches"] == data["participations"] == 5
    assert data["characters"][0]["games"] == 5 and data["characters"][5]["games"] == 0
    assert counts(data, "rank")["normal"] == 5  # Uses played, not main, character.


@pytest.mark.asyncio
async def test_small_cohort_empty_and_complementary_suppression(client):
    async with db.session() as s, s.begin():
        (await s.get(db.User, "a0")).country_code = "FR"
    data = await get(client)
    countries = counts(data, "country")
    assert countries["FR"] is None
    assert sum(row["suppressed"] for row in data["distributions"]["country"]) >= 2
    for query in ("country_code=FR", "country_code=GB", "age_band=20&match_type=casual", "rank=ph", "age_band=10"):
        hidden = await get(client, query)
        assert hidden["suppressed"] is True
        assert all(hidden[key] is None for key in ("total_players", "match_players", "unique_matches", "participations", "unknown_character_games"))
        assert not any(hidden["distributions"].values()) and hidden["characters"] == []
    # The legacy endpoint cannot reveal a suppressed value by subtraction.
    legacy = (await client.get("/api/players/statistics", headers=auth())).json()
    assert next(row for row in legacy["countries"] if row["country_code"] == "FR")["count"] is None


@pytest.mark.asyncio
async def test_private_winrates_are_aggregated_but_individual_rates_stay_private(client):
    async with db.session() as s, s.begin():
        (await s.get(db.User, "a0")).character_winrates_public = False
        # Many games from only one person must not meet the five-player minimum.
        for i in range(12):
            s.add(db.Match(id=f"many{i}", host_user_id="a1", host_char=7, winner="host"))
        for i in range(5):
            s.add(db.Match(id=f"private{i}", host_user_id=f"c{i}", host_char=8, winner="guest"))
    data = await get(client)
    assert data["total_players"] == 20  # Profile distributions don't depend on win-rate permission.
    for char in (7,):
        row = data["characters"][char]
        assert row["suppressed"] is True
        assert all(row[key] is None for key in ("games", "wins", "losses", "draws", "win_rate"))
    assert data["characters"][0]["games"] == data["characters"][5]["games"] == 5
    assert data["characters"][8]["games"] == 5 and data["characters"][8]["win_rate"] == 0
    assert data["unique_matches"] is None and data["participations"] is None
    assert (await get(client, "character=0"))["total_players"] == 5
    assert (await get(client, "character=8"))["total_players"] == 5
    assert (await get(client, "character=7"))["suppressed"] is True
    private = (await client.get("/api/players/a0", headers=auth("a1"))).json()
    assert private["character_winrates"] is None
    private = (await client.get("/api/players/c0", headers=auth())).json()
    assert private["character_winrates"] is None


@pytest.mark.asyncio
async def test_guest_only_unknown_char_and_self_match_counting(client):
    async with db.session() as s, s.begin():
        for i in range(5):
            s.add(db.Match(id=f"guest{i}", guest_user_id=f"a{i}", guest_char=10, winner="guest"))
            s.add(db.Match(id=f"self{i}", host_user_id=f"a{i}", guest_user_id=f"a{i}", host_char=11, guest_char=11, winner="host"))
        s.add(db.Match(id="nochar", host_user_id="a0", host_char=None, winner="host"))
    data = await get(client)
    assert data["characters"][10]["games"] == data["characters"][11]["games"] == 5
    assert data["characters"][11]["win_rate"] == 1  # Self-match doesn't create a second perspective.
    assert data["participations"] is None and data["unknown_character_games"] is None


@pytest.mark.parametrize("query", ["age_band=25", "age_band=-10", "rank=bogus", "country_code=XX", "device_type=joystick",
    "character=-1", "character=21", "character=nan", "match_type=invalid", "name=a0", "user_id=a0", "age_min=20", "birth_date=2000-01-01", "win_rate=0.5"])
@pytest.mark.asyncio
async def test_invalid_and_identifying_filters_rejected(client, query):
    assert (await client.get(f"/api/players/analytics?{query}", headers=auth())).status_code == 422


@pytest.mark.asyncio
async def test_decades_birthday_boundaries(client):
    async with db.session() as s, s.begin():
        for i in range(5):
            (await s.get(db.User, f"a{i}")).birth_date = date(2006, 9, 20)  # Still 19.
            (await s.get(db.User, f"a{i+5}")).birth_date = date(2006, 9, 19)  # Exactly 20.
    data = await get(client)
    assert counts(data, "age_band")["10"] == counts(data, "age_band")["20"] == 5
    assert (await get(client, "age_band=10"))["total_players"] == 5
