from __future__ import annotations
from typing import Optional, Tuple
import httpx

from services import __version__

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/stanak/asobby/releases/latest"
)


class ApiClient:
    def __init__(self, http: httpx.AsyncClient, base: str) -> None:
        self.http = http
        self.base = base.rstrip("/")
        self.session_token: str = ""  # Discord ログイン時のセッション（任意）

    def _request_headers(self) -> dict:
        headers = {"X-Asobby-Client-Version": __version__}
        headers.update(self._auth_headers())
        return headers

    def _auth_headers(self) -> dict:
        if self.session_token:
            return {"Authorization": f"Bearer {self.session_token}"}
        return {}

    async def myip(self) -> str:
        r = await self.http.get(f"{self.base}/myip", headers=self._request_headers())
        r.raise_for_status()
        return r.json()["ip"]

    async def list_posts(self) -> list[dict]:
        r = await self.http.get(f"{self.base}/posts", headers=self._request_headers())
        r.raise_for_status()
        return r.json()

    async def create(self, payload: dict) -> dict:
        """投稿を新規作成する。返り値は {"post": {...}, "owner_token": "..."}"""
        r = await self.http.post(
            f"{self.base}/posts", json=payload, headers=self._request_headers()
        )
        r.raise_for_status()
        return r.json()

    async def update(self, post_id: str, owner_token: str, payload: dict) -> dict:
        body = {**payload, "id": post_id, "owner_token": owner_token}
        r = await self.http.post(f"{self.base}/posts/update", json=body, headers=self._request_headers())
        r.raise_for_status()
        return r.json()

    async def reply_message(
        self,
        post_id: str,
        owner_token: str,
        message_id: str,
        reply: str,
    ) -> dict:
        r = await self.http.post(
            f"{self.base}/posts/reply",
            json={
                "id": post_id,
                "owner_token": owner_token,
                "message_id": message_id,
                "reply": reply,
            },
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def close(self, post_id: str, owner_token: str, reason: str = "auto") -> dict:
        r = await self.http.post(
            f"{self.base}/posts/close",
            json={"id": post_id, "owner_token": owner_token, "reason": reason},
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def report_result(
        self,
        post_id: str,
        owner_token: str,
        winner: str,
        host_char: int | None = None,
        guest_char: int | None = None,
        host_profile: str = "",
        guest_profile: str = "",
    ) -> dict:
        r = await self.http.post(
            f"{self.base}/posts/result",
            json={
                "id": post_id,
                "owner_token": owner_token,
                "winner": winner,
                "host_char": host_char,
                "guest_char": guest_char,
                "host_profile": host_profile,
                "guest_profile": guest_profile,
            },
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def report_guest_match(
        self,
        winner: str,
        host_char: int | None = None,
        guest_char: int | None = None,
        host_profile: str = "",
        guest_profile: str = "",
    ) -> dict:
        r = await self.http.post(
            f"{self.base}/matches/report",
            json={
                "winner": winner,
                "host_char": host_char,
                "guest_char": guest_char,
                "host_profile": host_profile,
                "guest_profile": guest_profile,
            },
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def upload_replay(
        self,
        data: bytes,
        battle_ts: float = 0,
        *,
        host_profile: str = "",
        guest_profile: str = "",
        winner: str = "",
        my_side: str = "",
    ) -> dict:
        params: dict[str, str | float] = {}
        if battle_ts > 0:
            params["battle_ts"] = battle_ts
        if host_profile:
            params["host_profile"] = host_profile
        if guest_profile:
            params["guest_profile"] = guest_profile
        if winner in ("host", "guest"):
            params["winner"] = winner
        if my_side in ("host", "client"):
            params["my_side"] = my_side
        r = await self.http.post(
            f"{self.base}/replays/upload",
            content=data,
            params=params or None,
            headers={
                **self._request_headers(),
                "Content-Type": "application/octet-stream",
            },
        )
        r.raise_for_status()
        return r.json()

    async def auth_client_exchange(self, code: str) -> dict:
        """ハンドオフのワンタイムコードをセッショントークンに交換する。
        {"status": "ok", "session_token", "user"}"""
        r = await self.http.post(
            f"{self.base}/auth/client/exchange",
            json={"code": code},
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def auth_logout(self) -> None:
        """Bearer セッションをサーバー側で失効する。"""
        r = await self.http.post(
            f"{self.base}/auth/logout",
            headers=self._request_headers(),
        )
        r.raise_for_status()

    async def auth_me(self) -> dict:
        """セッション検証。サーバー側で IP の最新化も行われる。"""
        r = await self.http.get(f"{self.base}/auth/me", headers=self._request_headers())
        r.raise_for_status()
        return r.json()

    async def fetch_my_matches(self, since: float = 0.0, limit: int = 500) -> dict:
        """自分の戦績一覧を取得する。"""
        r = await self.http.get(
            f"{self.base}/stats/me/matches",
            params={"since": since, "limit": limit},
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def sync_matches(self, matches: list[dict]) -> dict:
        """未送信のローカル戦績をサーバーへ同期する。"""
        r = await self.http.post(
            f"{self.base}/matches/sync",
            json={"matches": matches},
            headers=self._request_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def check_update(self) -> Optional[Tuple[str, str]]:
        """最新版を確認する。サーバー (/client/latest) を優先し、失敗時は GitHub。"""
        try:
            r = await self.http.get(
                f"{self.base}/client/latest",
                headers=self._request_headers(),
            )
            r.raise_for_status()
            data = r.json()
            if data.get("ok") and data.get("tag"):
                url = str(data.get("html_url") or data.get("download_url") or "")
                return str(data["tag"]), url
        except Exception as e:
            self._last_update_check_error = e

        try:
            r = await self.http.get(
                GITHUB_LATEST_RELEASE_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
            r.raise_for_status()
            data = r.json()
            return data["tag_name"], data["html_url"]
        except Exception as e:
            self._last_update_check_error = e
            return None
