from __future__ import annotations
from typing import Optional, Tuple
import httpx

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/stanak/asobby/releases/latest"
)


class ApiClient:
    def __init__(self, http: httpx.AsyncClient, base: str) -> None:
        self.http = http
        self.base = base.rstrip("/")
        self.session_token: str = ""  # Discord ログイン時のセッション（任意）

    def _auth_headers(self) -> dict:
        if self.session_token:
            return {"Authorization": f"Bearer {self.session_token}"}
        return {}

    async def myip(self) -> str:
        r = await self.http.get(f"{self.base}/myip")
        r.raise_for_status()
        return r.json()["ip"]

    async def create(self, payload: dict) -> dict:
        """投稿を新規作成する。返り値は {"post": {...}, "owner_token": "..."}"""
        r = await self.http.post(
            f"{self.base}/posts", json=payload, headers=self._auth_headers()
        )
        r.raise_for_status()
        return r.json()

    async def update(self, post_id: str, owner_token: str, payload: dict) -> dict:
        body = {**payload, "id": post_id, "owner_token": owner_token}
        r = await self.http.post(f"{self.base}/posts/update", json=body)
        r.raise_for_status()
        return r.json()

    async def close(self, post_id: str, owner_token: str, reason: str = "auto") -> dict:
        r = await self.http.post(
            f"{self.base}/posts/close",
            json={"id": post_id, "owner_token": owner_token, "reason": reason},
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
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    async def upload_replay(self, data: bytes) -> dict:
        r = await self.http.post(
            f"{self.base}/replays/upload",
            content=data,
            headers={
                **self._auth_headers(),
                "Content-Type": "application/octet-stream",
            },
        )
        r.raise_for_status()
        return r.json()

    async def auth_device_start(self) -> dict:
        """Discord ログインを開始する。{"device_code", "verify_url", "expires_in", "interval"}"""
        r = await self.http.post(f"{self.base}/auth/device")
        r.raise_for_status()
        return r.json()

    async def auth_device_poll(self, device_code: str) -> dict:
        """ログイン完了をポーリングする。pending なら {"status": "pending"}。"""
        r = await self.http.post(
            f"{self.base}/auth/device/poll", json={"device_code": device_code}
        )
        r.raise_for_status()
        return r.json()

    async def auth_me(self) -> dict:
        """セッション検証。サーバー側で IP の最新化も行われる。"""
        r = await self.http.get(f"{self.base}/auth/me", headers=self._auth_headers())
        r.raise_for_status()
        return r.json()

    async def check_update(self) -> Optional[Tuple[str, str]]:
        """Fetch latest release from GitHub. Returns (tag_name, html_url) or None."""
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
