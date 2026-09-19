"""Opt-in profiles with explicit public serializers and bounded SQL search."""
from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Annotated, Literal
import unicodedata

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
SCALAR_FIELDS = ("player_name", "main_character", "use_player_name", "birth_date", "birth_visibility", "country_code",
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


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    player_name: str = Field(default="", max_length=24)
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
            countries = (await s.execute(select(db.User.country_code, func.count()).where(
                db.User.country_code != "").group_by(db.User.country_code).order_by(db.User.country_code))).all()
            bands = [(0, 19), (20, 29), (30, 39), (40, 49), (50, 59), (60, None)]
            conditions = []
            for lo, hi in bands:
                condition = (db.User.birth_date <= years_ago(today(), lo)) & db.User.birth_visibility.in_(("public", "statistics"))
                if hi is not None:
                    condition &= db.User.birth_date > years_ago(today(), hi + 1)
                conditions.append(func.count(case((condition, 1))))
            counts = (await s.execute(select(*conditions).select_from(db.User))).one()
            return {"countries": [{"country_code": code, "count": n} for code, n in countries],
                    "age_bands": [{"min": lo, "max": hi, "count": n} for (lo, hi), n in zip(bands, counts)]}

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
