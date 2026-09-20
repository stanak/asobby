from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
  id TEXT PRIMARY KEY,
  server_id TEXT,
  played_at REAL NOT NULL,
  my_side TEXT NOT NULL,
  winner TEXT NOT NULL,
  host_char INTEGER,
  guest_char INTEGER,
  host_profile TEXT NOT NULL DEFAULT '',
  guest_profile TEXT NOT NULL DEFAULT '',
  ranked INTEGER NOT NULL DEFAULT 0,
  match_rank TEXT,
  host_wins INTEGER,
  guest_wins INTEGER,
  source TEXT NOT NULL DEFAULT 'local',
  pushed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS matches_played_at ON matches(played_at);
CREATE UNIQUE INDEX IF NOT EXISTS matches_server_id ON matches(server_id) WHERE server_id IS NOT NULL;
"""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class LocalStore:
    """ローカル戦績 SQLite ストア。接続は都度 open/close。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    @classmethod
    def open_with_fallback(cls, preferred: str | Path) -> "LocalStore":
        """preferred に開けない場合 (UNC/WSL 共有パス等、SQLite のロックが
        効かないファイルシステム) は LOCALAPPDATA 配下にフォールバックする。"""
        candidates = [Path(preferred)]
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidates.append(Path(local_app) / "asobby" / "matches.db")
        else:
            candidates.append(Path.home() / ".asobby" / "matches.db")

        last_err: Exception | None = None
        for p in candidates:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                return cls(p)
            except (sqlite3.Error, OSError) as e:
                last_err = e
        raise last_err  # type: ignore[misc]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_columns(conn)

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        """既存 DB への後方互換: 不足列を ALTER TABLE で追加する。"""
        cur = conn.execute("PRAGMA table_info(matches)")
        columns = {row[1] for row in cur.fetchall()}
        if "match_rank" not in columns:
            conn.execute("ALTER TABLE matches ADD COLUMN match_rank TEXT")
        if "host_wins" not in columns:
            conn.execute("ALTER TABLE matches ADD COLUMN host_wins INTEGER")
        if "guest_wins" not in columns:
            conn.execute("ALTER TABLE matches ADD COLUMN guest_wins INTEGER")
        for name, kind in (("battle_id", "TEXT"), ("duration_sec", "REAL"), ("report_version", "INTEGER NOT NULL DEFAULT 1"), ("report_status", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                conn.execute(f"ALTER TABLE matches ADD COLUMN {name} {kind}")
        for side in ("host", "guest"):
            for field in ("actual_char", "random"):
                name = f"{side}_{field}"
                if name not in columns:
                    conn.execute(f"ALTER TABLE matches ADD COLUMN {name} INTEGER")
        conn.execute("UPDATE matches SET my_side = 'client' WHERE my_side = 'guest'")

    @staticmethod
    def is_client_side(my_side: str | None) -> bool:
        return my_side in ("guest", "client")

    def record_local(
        self,
        my_side: str,
        winner: str,
        host_char: int | None,
        guest_char: int | None,
        host_profile: str,
        guest_profile: str,
        *,
        ranked: int = 0,
        match_rank: str | None = None,
        host_wins: int | None = None,
        guest_wins: int | None = None,
        played_at: float | None = None,
        client_id: str = "",
        match_id: str = "",
        duration_sec: float | None = None,
        report_version: int = 1,
        host_random: bool | None = None,
        guest_random: bool | None = None,
    ) -> str:
        """ローカル対戦を記録する。戻り値は生成した id (重複時は既存 id)。"""
        if played_at is None:
            played_at = time.time()
        existing_id = None if client_id else self._find_recent_duplicate(
            played_at=played_at,
            my_side=my_side,
            winner=winner,
            host_profile=host_profile,
            guest_profile=guest_profile,
        )
        if existing_id is not None:
            return existing_id

        local_id = client_id or uuid.uuid4().hex
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM matches WHERE id = ?", (local_id,)).fetchone():
                return local_id
            conn.execute(
                """
                INSERT INTO matches(
                  id, server_id, played_at, my_side, winner,
                  host_char, guest_char, host_profile, guest_profile,
                  ranked, match_rank, host_wins, guest_wins, source, pushed
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'local', 0)
                """,
                (
                    local_id,
                    played_at,
                    my_side,
                    winner,
                    20 if host_random is True else host_char,
                    20 if guest_random is True else guest_char,
                    host_profile or "",
                    guest_profile or "",
                    0 if report_version == 2 else ranked,
                    match_rank,
                    host_wins,
                    guest_wins,
                ),
            )
            conn.execute("UPDATE matches SET battle_id = ?, duration_sec = ?, report_version = ? WHERE id = ?",
                         (match_id or None, duration_sec, report_version, local_id))
            conn.execute("UPDATE matches SET host_actual_char = ?, guest_actual_char = ?, host_random = ?, guest_random = ? WHERE id = ?",
                         (host_char, guest_char, host_random, guest_random, local_id))
        return local_id

    def _find_recent_duplicate(
        self,
        *,
        played_at: float,
        my_side: str,
        winner: str,
        host_profile: str,
        guest_profile: str,
        window_sec: float = 30,
    ) -> str | None:
        host_profile = host_profile or ""
        guest_profile = guest_profile or ""
        with self._connect() as conn:
            if host_profile and guest_profile:
                cur = conn.execute(
                    """
                    SELECT id FROM matches
                    WHERE ABS(played_at - ?) <= ?
                      AND winner = ?
                      AND host_profile = ?
                      AND guest_profile = ?
                    ORDER BY ABS(played_at - ?)
                    LIMIT 1
                    """,
                    (
                        played_at,
                        window_sec,
                        winner,
                        host_profile,
                        guest_profile,
                        played_at,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id FROM matches
                    WHERE ABS(played_at - ?) <= ?
                      AND winner = ?
                      AND my_side = ?
                    ORDER BY ABS(played_at - ?)
                    LIMIT 1
                    """,
                    (played_at, window_sec, winner, my_side, played_at),
                )
            row = cur.fetchone()
            return str(row["id"]) if row is not None else None

    def merge_server_rows(self, rows: list[dict]) -> int:
        """サーバー戦績を取り込む。戻り値は新規 insert 数。"""
        inserted = 0
        with self._connect() as conn:
            for row in rows:
                server_id = str(row["id"])
                played_at = float(row["played_at"])
                winner = str(row["winner"])
                host_char = row.get("host_char")
                guest_char = row.get("guest_char")
                host_profile = str(row.get("host_profile", "") or "")
                guest_profile = str(row.get("guest_profile", "") or "")
                my_side = str(row.get("my_side", ""))
                if my_side == "guest":
                    my_side = "client"
                ranked = int(row.get("ranked", 0) or 0)
                match_rank = row.get("match_rank")
                host_wins = row.get("host_wins")
                guest_wins = row.get("guest_wins")
                source = str(row.get("source", "server") or "server")

                cur = conn.execute(
                    "SELECT * FROM matches WHERE server_id = ?",
                    (server_id,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE matches SET
                          played_at = ?, my_side = ?, winner = ?,
                          host_char = ?, guest_char = ?,
                          host_profile = ?, guest_profile = ?,
                          ranked = ?, match_rank = ?,
                          host_wins = ?, guest_wins = ?, source = ?, report_status = 'confirmed'
                        WHERE server_id = ?
                        """,
                        (
                            played_at,
                            my_side,
                            winner,
                            host_char,
                            guest_char,
                            host_profile,
                            guest_profile,
                            ranked,
                            match_rank,
                            host_wins,
                            guest_wins,
                            source,
                            server_id,
                        ),
                    )
                    self._merge_character_metadata(conn, row, existing)
                    continue

                if row.get("report_version") == 2:
                    # Never use a time-window heuristic for ID-based matches.
                    cur = conn.execute("SELECT * FROM matches WHERE battle_id = ? OR id = ?",
                                       (server_id, row.get("client_id", "")))
                elif host_profile and guest_profile:
                    cur = conn.execute(
                        """
                        SELECT * FROM matches
                        WHERE server_id IS NULL
                          AND report_version = 1
                          AND ABS(played_at - ?) <= 30
                          AND winner = ?
                          AND host_profile = ?
                          AND guest_profile = ?
                        ORDER BY ABS(played_at - ?)
                        LIMIT 1
                        """,
                        (
                            played_at,
                            winner,
                            host_profile,
                            guest_profile,
                            played_at,
                        ),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT * FROM matches
                        WHERE server_id IS NULL
                          AND report_version = 1
                          AND ABS(played_at - ?) <= 30
                          AND winner = ?
                        ORDER BY ABS(played_at - ?)
                        LIMIT 1
                        """,
                        (played_at, winner, played_at),
                    )
                local_match = cur.fetchone()
                if local_match is not None:
                    conn.execute(
                        """
                        UPDATE matches SET
                          server_id = ?, pushed = 1,
                          played_at = ?, my_side = ?, winner = ?,
                          host_char = ?, guest_char = ?,
                          host_profile = ?, guest_profile = ?,
                          ranked = ?, match_rank = ?,
                          host_wins = ?, guest_wins = ?, report_status = 'confirmed'
                        WHERE id = ?
                        """,
                        (
                            server_id,
                            played_at,
                            my_side,
                            winner,
                            host_char,
                            guest_char,
                            host_profile,
                            guest_profile,
                            ranked,
                            match_rank,
                            host_wins,
                            guest_wins,
                            local_match["id"],
                        ),
                    )
                    self._merge_character_metadata(conn, row, local_match)
                    continue

                if row.get("report_version") == 2:
                    cur = conn.execute("SELECT id FROM matches WHERE server_id = ?", (server_id,))
                    if cur.fetchone() is not None:
                        continue
                elif host_profile and guest_profile:
                    cur = conn.execute(
                        """
                        SELECT id FROM matches
                        WHERE report_version = 1 AND ABS(played_at - ?) <= 30
                          AND winner = ?
                          AND host_profile = ?
                          AND guest_profile = ?
                        LIMIT 1
                        """,
                        (played_at, winner, host_profile, guest_profile),
                    )
                    if cur.fetchone() is not None:
                        continue
                else:
                    cur = conn.execute(
                        """
                        SELECT id FROM matches
                        WHERE report_version = 1 AND ABS(played_at - ?) <= 30
                          AND winner = ?
                          AND my_side = ?
                        LIMIT 1
                        """,
                        (played_at, winner, my_side),
                    )
                    if cur.fetchone() is not None:
                        continue

                conn.execute(
                    """
                    INSERT INTO matches(
                      id, server_id, played_at, my_side, winner,
                      host_char, guest_char, host_profile, guest_profile,
                      ranked, match_rank, host_wins, guest_wins, source, pushed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'server', 1)
                    """,
                    (
                        server_id,
                        server_id,
                        played_at,
                        my_side,
                        winner,
                        host_char,
                        guest_char,
                        host_profile,
                        guest_profile,
                        ranked,
                        match_rank,
                        host_wins,
                        guest_wins,
                    ),
                )
                if row.get("report_version") == 2:
                    conn.execute("UPDATE matches SET report_version = 2, battle_id = ?, report_status = 'confirmed' WHERE server_id = ?", (server_id, server_id))
                self._merge_character_metadata(conn, row, None)
                inserted += 1
        return inserted

    @staticmethod
    def _merge_character_metadata(conn: sqlite3.Connection, row: dict, existing: sqlite3.Row | None) -> None:
        for side in ("host", "guest"):
            selected = row.get(f"{side}_char")
            actual_key, random_key = f"{side}_actual_char", f"{side}_random"
            actual = row.get(actual_key, existing[actual_key] if existing is not None else None)
            random = row.get(random_key, existing[random_key] if existing is not None else None)
            # A server predating this feature omits metadata. Do not erase local
            # evidence on a pull of the same resolved fighter. A feature-aware
            # server's explicit null/false, however, is authoritative.
            if random_key not in row and random and selected == actual:
                selected = 20
            conn.execute(f"UPDATE matches SET {side}_char = ?, {actual_key} = ?, {random_key} = ? WHERE server_id = ?",
                         (selected, actual, random, str(row["id"])))

    def fetch_unpushed(self, min_age_sec: float = 0) -> list[dict]:
        """未送信のローカル戦績を返す。

        min_age_sec > 0 のときは played_at がその秒数より前の行だけ返す
        (即時 sync 後の API 報告との競合を避ける用途)。
        """
        clauses = ["server_id IS NULL", "pushed = 0"]
        params: list[Any] = []
        if min_age_sec > 0:
            clauses.append("played_at < ?")
            params.append(time.time() - min_age_sec)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                SELECT * FROM matches
                WHERE {where}
                ORDER BY played_at
                """,
                params,
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    def fetch_unpushed_by_id(self, local_id: str) -> list[dict]:
        """指定 id の未送信ローカル戦績を返す (即時 sync 用)。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM matches
                WHERE id = ? AND server_id IS NULL AND pushed = 0
                """,
                (local_id,),
            )
            row = cur.fetchone()
            return [_row_to_dict(row)] if row is not None else []

    def mark_pushed(self, local_id: str, server_id_or_none: str | None) -> None:
        with self._connect() as conn:
            if server_id_or_none:
                try:
                    conn.execute(
                        "UPDATE matches SET pushed = 1, server_id = ? WHERE id = ?",
                        (server_id_or_none, local_id),
                    )
                    return
                except sqlite3.IntegrityError:
                    # 同じ server_id が別行に紐付いている (pull 済み) 場合は
                    # pushed だけ立てて同期の無限リトライを防ぐ
                    pass
            conn.execute(
                "UPDATE matches SET pushed = 1 WHERE id = ?",
                (local_id,),
            )

    def accept_report(self, local_id: str, server_id: str | None, status: str) -> None:
        """Pending/conflict is a durable acknowledgement, not a confirmed win."""
        self.mark_pushed(local_id, server_id)
        # Only pulling the canonical row marks it confirmed. Until then its
        # local wall-clock timestamp must not advance the incremental cursor.
        if status in ("confirmed", "imported", "duplicate"):
            status = "pending"
        with self._connect() as conn:
            conn.execute("UPDATE matches SET report_status = ?, battle_id = COALESCE(?, battle_id), ranked = CASE WHEN ? IN ('pending', 'conflict') THEN 0 ELSE ranked END WHERE id = ?",
                         (status, server_id, status, local_id))

    def pending_report_ids(self) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT id FROM matches WHERE pushed = 1 AND report_status IN ('pending', 'conflict')")]

    def bind_battle(self, local_id: str, match_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE matches SET battle_id = ? WHERE id = ?", (match_id, local_id))

    def mark_pushed_for_report(
        self,
        played_at: float,
        winner: str,
        host_profile: str,
        guest_profile: str,
        server_id: str,
        *,
        window_sec: float = 45,
    ) -> None:
        """API 報告成功後、同一対戦の未 push 行を server_id 付きで済みにする。"""
        if played_at <= 0 or not server_id:
            return
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id FROM matches
                WHERE pushed = 0
                  AND ABS(played_at - ?) <= ?
                  AND winner = ?
                  AND host_profile = ?
                  AND guest_profile = ?
                ORDER BY ABS(played_at - ?)
                LIMIT 1
                """,
                (
                    played_at,
                    window_sec,
                    winner,
                    host_profile,
                    guest_profile,
                    played_at,
                ),
            )
            row = cur.fetchone()
            if row is None:
                return
        self.mark_pushed(str(row[0]), server_id)

    def max_server_played_at(self) -> float:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT MAX(played_at) FROM matches
                WHERE source = 'server' OR (server_id IS NOT NULL AND (report_version = 1 OR report_status = 'confirmed'))
                """
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return 0.0
            return float(row[0])

    def query(
        self,
        my_char: int | None = None,
        opp_char: int | None = None,
        opp_profile_like: str = "",
        ranked_only: bool = False,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []

        if my_char is not None:
            clauses.append(
                "((my_side = 'host' AND host_char = ?) OR "
                "(my_side IN ('guest', 'client') AND guest_char = ?))"
            )
            params.extend([my_char, my_char])

        if opp_char is not None:
            clauses.append(
                "((my_side = 'host' AND guest_char = ?) OR "
                "(my_side IN ('guest', 'client') AND host_char = ?))"
            )
            params.extend([opp_char, opp_char])

        if opp_profile_like:
            like = f"%{opp_profile_like}%"
            clauses.append(
                "((my_side = 'host' AND guest_profile LIKE ?) OR "
                "(my_side IN ('guest', 'client') AND host_profile LIKE ?))"
            )
            params.extend([like, like])

        if ranked_only:
            clauses.append("ranked = 1")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM matches {where} ORDER BY played_at DESC"

        with self._connect() as conn:
            cur = conn.execute(sql, params)
            return [_row_to_dict(r) for r in cur.fetchall()]

    @staticmethod
    def is_my_win(row: dict) -> bool:
        my_side = row.get("my_side")
        winner = row.get("winner")
        return (my_side == "host" and winner == "host") or (
            LocalStore.is_client_side(my_side) and winner == "guest"
        )

    @staticmethod
    def is_draw(row: dict) -> bool:
        return row.get("winner") == "draw"

    @staticmethod
    def my_char_id(row: dict) -> int | None:
        if row.get("my_side") == "host":
            return row.get("host_char")
        return row.get("guest_char")

    @staticmethod
    def opp_char_id(row: dict) -> int | None:
        if row.get("my_side") == "host":
            return row.get("guest_char")
        return row.get("host_char")

    @staticmethod
    def opp_profile(row: dict) -> str:
        if row.get("my_side") == "host":
            return str(row.get("guest_profile", "") or "")
        return str(row.get("host_profile", "") or "")

    def fetch_all(self) -> list[dict]:
        """全戦績を played_at 降順で返す（ビューアの一括読込用）。"""
        return self.query()
