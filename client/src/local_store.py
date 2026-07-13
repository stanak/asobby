from __future__ import annotations

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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

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
    ) -> str:
        """ローカル対戦を記録する。戻り値は生成した id。"""
        local_id = uuid.uuid4().hex
        played_at = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO matches(
                  id, server_id, played_at, my_side, winner,
                  host_char, guest_char, host_profile, guest_profile,
                  ranked, source, pushed
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'local', 0)
                """,
                (
                    local_id,
                    played_at,
                    my_side,
                    winner,
                    host_char,
                    guest_char,
                    host_profile or "",
                    guest_profile or "",
                    ranked,
                ),
            )
        return local_id

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
                ranked = int(row.get("ranked", 0) or 0)
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
                          ranked = ?, source = ?
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
                            source,
                            server_id,
                        ),
                    )
                    continue

                cur = conn.execute(
                    """
                    SELECT * FROM matches
                    WHERE server_id IS NULL
                      AND ABS(played_at - ?) <= 60
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
                          ranked = ?
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
                            local_match["id"],
                        ),
                    )
                    continue

                conn.execute(
                    """
                    INSERT INTO matches(
                      id, server_id, played_at, my_side, winner,
                      host_char, guest_char, host_profile, guest_profile,
                      ranked, source, pushed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'server', 1)
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
                    ),
                )
                inserted += 1
        return inserted

    def fetch_unpushed(self, older_than_sec: float = 300) -> list[dict]:
        """未送信のローカル戦績を返す。"""
        cutoff = time.time() - older_than_sec
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM matches
                WHERE server_id IS NULL AND pushed = 0 AND played_at < ?
                ORDER BY played_at
                """,
                (cutoff,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

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

    def max_server_played_at(self) -> float:
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT MAX(played_at) FROM matches
                WHERE source = 'server' OR server_id IS NOT NULL
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
                "((my_side = 'host' AND host_char = ?) OR (my_side = 'guest' AND guest_char = ?))"
            )
            params.extend([my_char, my_char])

        if opp_char is not None:
            clauses.append(
                "((my_side = 'host' AND guest_char = ?) OR (my_side = 'guest' AND host_char = ?))"
            )
            params.extend([opp_char, opp_char])

        if opp_profile_like:
            like = f"%{opp_profile_like}%"
            clauses.append(
                "((my_side = 'host' AND guest_profile LIKE ?) OR (my_side = 'guest' AND host_profile LIKE ?))"
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
            my_side == "guest" and winner == "guest"
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
