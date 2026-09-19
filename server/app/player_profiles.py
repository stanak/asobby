"""Opt-in profiles with explicit public serializers and bounded SQL search."""
from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Annotated, Literal
import unicodedata
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import case, delete, func, or_, select, union_all

import db
from integrations import RANK_SYMBOLS

# ISO 3166-1 alpha-2, including territories. Display names are localized in the UI.
COUNTRIES = tuple("AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split())
Device = Literal["", "keyboard", "gamepad", "arcade", "other"]
Rank = Literal["easy", "normal", "ex", "hard", "luna", "ph"]
Char = Annotated[int, Field(strict=True, ge=0, le=19)]
TAG_FIELDS = ("favorite_players", "other_games")
CHARACTER_FIELDS = ("strong_characters", "weak_characters")
SCALAR_FIELDS = ("player_name", "bio", "main_character", "use_player_name", "birth_date", "birth_visibility", "country_code",
                 "device_type", "device_model", "character_winrates_public")


def today() -> date:
    return db.utcnow().astimezone(timezone(timedelta(hours=9))).date()


def age(born: date, on: date | None = None) -> int:
    on = on or today()
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def years_ago(on: date, years: int) -> date:
    try:
        return on.replace(year=on.year - years)
    except ValueError:  # Leap day; a Feb 29 birthday ages on March 1 in a non-leap year.
        return on.replace(year=on.year - years, day=28)


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def clean_text(value: str) -> str:
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("制御文字は使用できません / Control characters are not allowed")
    return value.strip()


class ProfileLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("label")
    @classmethod
    def valid_label(cls, value):
        value = clean_text(value)
        if not value:
            raise ValueError("リンクのラベルを入力してください / Enter a link label")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip()
        if (not value.lower().startswith(("https://", "http://")) or "\\" in value
                or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value)):
            raise ValueError("http(s)://で始まるURLを入力してください / Enter an absolute HTTP(S) URL")
        if not urlsplit(value).hostname:
            raise ValueError("URLのホスト名が必要です / URL must include a hostname")
        url = HttpUrl(value)
        if url.username is not None or url.password is not None:
            raise ValueError("認証情報を含むURLは使えません / URL credentials are not allowed")
        result = str(url)
        if len(result) > 2048:
            raise ValueError("URLは2048文字以内 / URL must fit in 2048 characters")
        return result


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    player_name: str = Field(default="", max_length=24)
    bio: str = Field(default="", max_length=400)
    profile_links: list[ProfileLinkIn] = Field(default_factory=list, max_length=10)
    main_character: Annotated[int, Field(strict=True, ge=0, le=20)] | None = None
    use_player_name: bool = False
    birth_date: date | None = None
    birth_visibility: Literal["public", "statistics", "secret"] = "secret"
    country_code: str = ""
    device_type: Device = ""
    device_model: str = Field(default="", max_length=120)
    favorite_players: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=20)
    other_games: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=30)
    strong_characters: list[Char] = Field(default_factory=list, max_length=20)
    weak_characters: list[Char] = Field(default_factory=list, max_length=20)
    character_winrates_public: bool = False

    @field_validator("bio", mode="before")
    @classmethod
    def normalize_bio(cls, value):
        return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value

    @field_validator("bio")
    @classmethod
    def valid_bio(cls, value):
        if any(unicodedata.category(c) in ("Cc", "Cs") and c not in "\n\t" for c in value):
            raise ValueError("自己紹介に制御文字は使用できません / Control characters are not allowed")
        return value

    @field_validator("strong_characters", "weak_characters")
    @classmethod
    def unique_characters(cls, values):
        return sorted(set(values))

    @field_validator("birth_date", mode="before")
    @classmethod
    def parse_birth_date(cls, value):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value

    @field_validator("birth_date")
    @classmethod
    def valid_birth_date(cls, value):
        if value is not None and not date(1900, 1, 1) <= value <= today():
            raise ValueError("生年月日は1900年から今日まで / Birth date must be between 1900 and today")
        return value

    @field_validator("player_name")
    @classmethod
    def valid_player_name(cls, value):
        value = clean_text(value)
        try:
            size = len(value.encode("cp932"))
        except UnicodeEncodeError:
            raise ValueError("天則で使用できる文字（CP932）で入力してください / Use CP932 characters") from None
        if size > 24:
            raise ValueError("プレイヤーネームは24バイト以内 / Player name must fit in 24 CP932 bytes")
        return value

    @field_validator("device_model")
    @classmethod
    def valid_model(cls, value):
        return clean_text(value)

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value):
        value = value.upper()
        if value and value not in COUNTRIES:
            raise ValueError("invalid country code")
        return value

    @field_validator("favorite_players", "other_games")
    @classmethod
    def clean_tags(cls, values):
        result, seen = [], set()
        for raw in values:
            value = clean_text(raw)
            key = normalized(value)
            if value and key not in seen:
                result.append(value)
                seen.add(key)
        return result

    @model_validator(mode="after")
    def valid_combinations(self):
        if self.birth_visibility == "secret":
            self.birth_date = None  # Forget previously stored dates even for stale clients.
        elif self.birth_date is None:
            raise ValueError("公開・統計利用には生年月日を入力してください / Enter a birth date for public or statistical use")
        if self.use_player_name and not self.player_name:
            raise ValueError("ロビー表示にはプレイヤーネームが必要です / Enter a player name first")
        if self.device_model and not self.device_type:
            raise ValueError("型番を入力する場合はデバイス種別を選択してください / Select a device type")
        return self


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(default="", max_length=100)
    main_character: int | None = Field(default=None, ge=0, le=20)
    age_min: int | None = Field(default=None, ge=0, le=150)
    age_max: int | None = Field(default=None, ge=0, le=150)
    country_code: str = ""
    device_type: Device = ""
    device_model: str = Field(default="", max_length=120)
    favorite_player: str = Field(default="", max_length=100)
    game: str = Field(default="", max_length=120)
    rank: list[Rank] = Field(default_factory=list, max_length=6)
    strong_char: int | None = Field(default=None, ge=0, le=19)
    weak_char: int | None = Field(default=None, ge=0, le=19)
    page: int = Field(default=1, ge=1, le=10000)
    limit: int = Field(default=24, ge=1, le=100)

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value):
        return ProfileIn.valid_country(value)

    @model_validator(mode="after")
    def ordered_ranges(self):
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("下限は上限以下にしてください / Minimum must not exceed maximum")
        return self


AGE_BANDS = tuple((lo, lo + 9) for lo in range(0, 100, 10)) + ((100, None),)
STATISTICS_MIN_PLAYERS = 5
AgeBand = Literal["", "0", "10", "20", "30", "40", "50", "60", "70", "80", "90", "100", "unknown"]


class StatisticsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    age_band: AgeBand = ""
    rank: Rank | Literal[""] = ""
    country_code: str = ""
    device_type: Device | Literal["unknown"] = ""
    character: int | None = Field(default=None, ge=0, le=20)
    match_type: Literal["all", "ranked", "casual"] = "all"

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value):
        return value if value == "unknown" else ProfileIn.valid_country(value)


async def filtered_statistics(s, filters: StatisticsIn) -> dict:
    """Consented aggregate ages, all confirmed results, small-cell suppression."""
    if s.bind.dialect.name == "postgresql":
        # Suppression and displayed totals must use one consistent snapshot.
        await s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    u, m = db.User, db.Match
    on = today()
    age_cases = []
    for lo, hi in AGE_BANDS:
        condition = u.birth_visibility.in_(("public", "statistics")) & (u.birth_date <= years_ago(on, lo))
        if hi is not None:
            condition &= u.birth_date > years_ago(on, hi + 1)
        age_cases.append((condition, str(lo)))
    age_group = case(*age_cases, else_="unknown")
    rank_group = case((u.rank.in_(RANK_SYMBOLS), u.rank), else_="unknown")
    country_group = case((u.country_code.in_(COUNTRIES), u.country_code), else_="unknown")
    device_group = case((u.device_type.in_(("keyboard", "gamepad", "arcade", "other")), u.device_type), else_="unknown")

    # One record per participant, never per report/replay. A selected-player mirror
    # match contributes twice to participation counts but once to unique matches.
    settled = m.winner.in_(("host", "guest", "draw"))
    sides = []
    for side, user_id, char in (("host", m.host_user_id, m.host_char), ("guest", m.guest_user_id, m.guest_char)):
        query = select(m.id.label("match_id"), user_id.label("user_id"), char.label("character"),
                       case((m.winner == side, 1), else_=0).label("win"),
                       case((m.winner == "draw", 1), else_=0).label("draw")).where(settled, user_id.is_not(None))
        if side == "guest":
            query = query.where(or_(m.host_user_id.is_(None), m.host_user_id != m.guest_user_id))
        if filters.character is not None:
            query = query.where(char == filters.character)
        if filters.match_type != "all":
            query = query.where(m.ranked.is_(filters.match_type == "ranked"))
        sides.append(query)
    games = union_all(*sides).cte("statistics_games")
    cohort_query = select(u.id, age_group.label("age_band"), rank_group.label("rank"),
                          country_group.label("country"), device_group.label("device"))
    for value, column in ((filters.age_band, age_group), (filters.rank, rank_group),
                          (filters.country_code, country_group), (filters.device_type, device_group)):
        if value:
            cohort_query = cohort_query.where(column == value)
    if filters.character is not None or filters.match_type != "all":
        cohort_query = cohort_query.where(select(games.c.user_id).where(games.c.user_id == u.id).exists())
    cohort = cohort_query.cte("statistics_cohort")
    total = await s.scalar(select(func.count()).select_from(cohort))
    result = {"filters": filters.model_dump(), "as_of": on.isoformat(), "total_players": None,
              "match_players": None, "unique_matches": None, "participations": None,
              "unknown_character_games": None, "distributions": {key: [] for key in ("age_band", "rank", "country", "device")},
              "characters": [], "suppressed": total < STATISTICS_MIN_PLAYERS,
              "min_players": STATISTICS_MIN_PLAYERS, "age_scope": "consented", "match_scope": "all_confirmed"}
    # Do not distinguish zero people from 1–4, or expose any totals for that cohort.
    if result["suppressed"]:
        return result
    distributions = {}
    for field, keys in (("age_band", [str(lo) for lo, _ in AGE_BANDS] + ["unknown"]),
                        ("rank", [*RANK_SYMBOLS, "unknown"]), ("country", None),
                        ("device", ["keyboard", "gamepad", "arcade", "other", "unknown"])):
        column = cohort.c[field]
        counts = dict((await s.execute(select(column, func.count()).group_by(column))).all())
        if keys is None:
            keys = sorted(key for key in counts if key != "unknown") + ["unknown"]
        hidden = {key for key, count in counts.items() if 0 < count < STATISTICS_MIN_PLAYERS}
        # One hidden bucket is reconstructable from total minus visible buckets.
        if len(hidden) == 1:
            candidates = [key for key, count in counts.items() if count and key not in hidden]
            if candidates:
                hidden.add(min(candidates, key=lambda key: (counts[key], key)))
        distributions[field] = [{"key": key, "count": None if key in hidden else counts.get(key, 0),
                                  "suppressed": key in hidden} for key in keys]

    selected_games = select(games).join(cohort, cohort.c.id == games.c.user_id).cte("statistics_selected_games")
    participants, matches, participations = (await s.execute(select(
        func.count(func.distinct(selected_games.c.user_id)), func.count(func.distinct(selected_games.c.match_id)),
        func.count()).select_from(selected_games))).one()
    rows = (await s.execute(select(selected_games.c.character, func.count(), func.sum(selected_games.c.win), func.sum(selected_games.c.draw),
                                  func.count(func.distinct(selected_games.c.user_id)))
        .where(selected_games.c.character.between(0, 20)).group_by(selected_games.c.character))).all()
    by_character = {char: (n, w, d, players) for char, n, w, d, players in rows}
    characters = []
    for char in range(21):
        n, w, d, players = by_character.get(char, (0, 0, 0, 0))
        hidden = 0 < players < STATISTICS_MIN_PLAYERS
        characters.append({"char": char, "games": None if hidden else n, "wins": None if hidden else w,
                           "losses": None if hidden else n - w - d, "draws": None if hidden else d,
                           "win_rate": round(w / n, 4) if n and not hidden else None, "suppressed": hidden})
    unknown = participations - sum(n for n, _, _, _ in by_character.values())
    # No side totals from which a suppressed character row could be subtracted.
    hide_matches = any(row["suppressed"] for row in characters) or 0 < participants < STATISTICS_MIN_PLAYERS or unknown > 0
    result.update(total_players=total, distributions=distributions, characters=characters,
                  match_players=None if hide_matches else participants,
                  unique_matches=None if hide_matches else matches, participations=None if hide_matches else participations,
                  unknown_character_games=None if hide_matches else unknown)
    return result


async def profile_lists_for(s, user_ids: list[str]) -> dict:
    values = {uid: {field: [] for field in (*TAG_FIELDS, *CHARACTER_FIELDS)} for uid in user_ids}
    if not user_ids:
        return values
    rows = (await s.scalars(select(db.PlayerProfileTag).where(
        db.PlayerProfileTag.user_id.in_(user_ids)
    ).order_by(db.PlayerProfileTag.value))).all()
    for row in rows:
        values[row.user_id][row.kind].append(row.value)
    characters = (await s.scalars(select(db.PlayerProfileCharacter).where(
        db.PlayerProfileCharacter.user_id.in_(user_ids)
    ).order_by(db.PlayerProfileCharacter.character_id))).all()
    for row in characters:
        values[row.user_id][row.kind].append(row.character_id)
    return values


def public_profile(user: db.User, tags: dict) -> dict:
    # Whitelist, never model_dump()/__dict__: raw birthdays are owner-only.
    return {
        "id": user.id, "player_name": user.player_name, "discord_name": user.name,
        "bio": user.bio, "profile_links": user.profile_links,
        "main_character": user.main_character,
        "discord_username": user.discord_username, "display_name": user.player_name or user.name,
        "lobby_name": db.lobby_display_name(user), "use_player_name": user.use_player_name,
        "avatar": f"https://cdn.discordapp.com/avatars/{user.id}/{user.avatar}.png?size=128" if user.avatar else "",
        "age": age(user.birth_date) if user.birth_visibility == "public" and user.birth_date else None,
        "country_code": user.country_code, "device_type": user.device_type, "device_model": user.device_model,
        "rank": user.rank, "rank_symbol": RANK_SYMBOLS.get(user.rank, user.rank),
        "rating": round(user.ts_mu - 3 * user.ts_sigma, 1) if user.rank == "ph" else None,
        "character_winrates_public": user.character_winrates_public,
        **tags,
    }


async def profile_stats(s, user_id: str, *, include_winrates: bool) -> dict:
    m = db.Match
    # Each canonical match counted once, independent of number of reports/replays.
    settled = m.winner.in_(("host", "guest", "draw"))
    host = select(m.host_char.label("char"), m.guest_user_id.label("opponent"),
                  case((m.winner == "host", 1), else_=0).label("win"),
                  case((m.winner == "draw", 1), else_=0).label("draw")).where(m.host_user_id == user_id, settled)
    guest = select(m.guest_char.label("char"), m.host_user_id.label("opponent"),
                   case((m.winner == "guest", 1), else_=0).label("win"),
                   case((m.winner == "draw", 1), else_=0).label("draw")).where(
        m.guest_user_id == user_id, or_(m.host_user_id.is_(None), m.host_user_id != user_id), settled)
    games = union_all(host, guest).subquery()
    total, opponents, unknown = (await s.execute(select(
        func.count(), func.count(func.distinct(case((~games.c.opponent.in_((user_id, "")), games.c.opponent)))),
        func.coalesce(func.sum(case((or_(games.c.opponent.is_(None), games.c.opponent == ""), 1), else_=0)), 0),
    ).select_from(games))).one()
    result = {"total_matches": total, "unique_opponents": opponents, "unidentified_opponent_matches": unknown,
              "character_winrates": None}
    if include_winrates:
        rows = (await s.execute(select(games.c.char, func.count(), func.sum(games.c.win), func.sum(games.c.draw))
                               .where(games.c.char.between(0, 20)).group_by(games.c.char).order_by(games.c.char))).all()
        result["character_winrates"] = [{"char": char, "games": n, "wins": w, "losses": n - w - d,
                                         "draws": d, "win_rate": round(w / n, 4)} for char, n, w, d in rows]
    return result


def search_query(filters: SearchIn):
    u, tag = db.User, db.PlayerProfileTag
    query = select(u)
    if filters.main_character is not None:
        query = query.where(u.main_character == filters.main_character)
    character = db.PlayerProfileCharacter
    for field, kind in (("strong_char", "strong_characters"), ("weak_char", "weak_characters")):
        if (value := getattr(filters, field)) is not None:
            query = query.where(select(character.user_id).where(
                character.user_id == u.id, character.kind == kind, character.character_id == value
            ).exists())
    if filters.name.strip():
        needle = filters.name.strip().lower()
        query = query.where(or_(*(func.lower(field).contains(needle, autoescape=True)
                                  for field in (u.player_name, u.name, u.discord_username))))
    if filters.age_min is not None:
        query = query.where(u.birth_date <= years_ago(today(), filters.age_min))
    if filters.age_max is not None:
        query = query.where(u.birth_date > years_ago(today(), filters.age_max + 1))
    if filters.age_min is not None or filters.age_max is not None:
        query = query.where(u.birth_visibility == "public")
    for field in ("country_code", "device_type"):
        if value := getattr(filters, field):
            query = query.where(getattr(u, field) == value)
    if filters.device_model.strip():
        query = query.where(u.device_model_search.contains(normalized(filters.device_model.strip()), autoescape=True))
    if filters.rank:
        query = query.where(u.rank.in_(filters.rank))
    for kind, value in (("favorite_players", filters.favorite_player), ("other_games", filters.game)):
        if value.strip():
            query = query.where(select(tag.user_id).where(tag.user_id == u.id, tag.kind == kind,
                tag.search_value.contains(normalized(value.strip()), autoescape=True)).exists())
    return query


def build_router(resolve_session, refresh_names) -> APIRouter:
    router = APIRouter()

    async def require_user(request: Request, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        sess = await resolve_session(request)
        if sess is None:
            raise HTTPException(401, "Discord login required")
        return sess

    @router.get("/api/players/options")
    async def options():
        return {"countries": COUNTRIES, "ranks": RANK_SYMBOLS, "devices": ["keyboard", "gamepad", "arcade", "other"]}

    @router.get("/user/profile")
    async def own_profile(request: Request, response: Response):
        sess = await require_user(request, response)
        async with db.session() as s:
            user = await s.get(db.User, sess["id"])
            tags = (await profile_lists_for(s, [user.id]))[user.id]
            return {**public_profile(user, tags), "birth_date": user.birth_date, "birth_visibility": user.birth_visibility,
                    "player_name_bytes": len(user.player_name.encode("cp932"))}

    @router.put("/user/profile")
    async def save_profile(body: ProfileIn, request: Request, response: Response):
        sess = await require_user(request, response)
        async with db.session() as s, s.begin():
            user = await s.scalar(select(db.User).where(db.User.id == sess["id"]).with_for_update())
            for field in SCALAR_FIELDS:
                setattr(user, field, getattr(body, field))
            user.profile_links = [link.model_dump() for link in body.profile_links]
            await s.execute(delete(db.PlayerProfileCharacter).where(db.PlayerProfileCharacter.user_id == user.id))
            for kind in CHARACTER_FIELDS:
                values = getattr(body, kind)
                # Keep legacy single-value columns usable for an older server.
                setattr(user, kind[:-1], values[0] if values else None)
                for character_id in values:
                    s.add(db.PlayerProfileCharacter(user_id=user.id, kind=kind, character_id=character_id))
            user.device_model_search = normalized(body.device_model)
            await s.execute(delete(db.PlayerProfileTag).where(db.PlayerProfileTag.user_id == user.id))
            for kind in TAG_FIELDS:
                for value in getattr(body, kind):
                    s.add(db.PlayerProfileTag(user_id=user.id, kind=kind, value=str(value), search_value=normalized(str(value))))
        await refresh_names(user)
        return {"ok": True, "id": user.id}

    @router.get("/api/players")
    async def search_players(request: Request, response: Response, filters: Annotated[SearchIn, Query()]):
        await require_user(request, response)
        async with db.session() as s:
            query = search_query(filters)
            total = await s.scalar(select(func.count()).select_from(query.subquery()))
            rows = (await s.scalars(query.order_by(db.User.id).offset((filters.page - 1) * filters.limit).limit(filters.limit))).all()
            tags = await profile_lists_for(s, [u.id for u in rows])
            # No statistics or hidden birthday fields in searchable listings.
            return {"players": [public_profile(u, tags[u.id]) for u in rows], "total": total,
                    "page": filters.page, "limit": filters.limit}

    @router.get("/api/players/statistics")
    async def population_statistics(request: Request, response: Response):
        await require_user(request, response)
        async with db.session() as s:
            data = await filtered_statistics(s, StatisticsIn())
            return {"suppressed": data["suppressed"], "min_players": data["min_players"],
                    "countries": [{"country_code": row["key"], "count": row["count"]} for row in data["distributions"]["country"] if row["key"] != "unknown"],
                    "age_bands": [{"min": int(row["key"]), "max": int(row["key"]) + 9 if row["key"] != "100" else None,
                                   "count": row["count"]} for row in data["distributions"]["age_band"] if row["key"] != "unknown"]}

    @router.get("/api/players/analytics")
    async def player_analytics(request: Request, response: Response, filters: Annotated[StatisticsIn, Query()]):
        await require_user(request, response)
        async with db.session() as s:
            return await filtered_statistics(s, filters)

    @router.get("/api/players/{user_id}")
    async def get_profile(user_id: str, request: Request, response: Response):
        sess = await require_user(request, response)
        async with db.session() as s:
            user = await s.get(db.User, user_id)
            if user is None:
                raise HTTPException(404, "player not found")
            tags = (await profile_lists_for(s, [user.id]))[user.id]
            own = user.id == sess["id"]
            return {**public_profile(user, tags), "is_owner": own,
                    **await profile_stats(s, user.id, include_winrates=own or user.character_winrates_public)}

    return router
