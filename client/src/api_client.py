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

    async def myip(self) -> str:
        r = await self.http.get(f"{self.base}/myip")
        r.raise_for_status()
        return r.json()["ip"]

    async def create(self, payload: dict) -> dict:
        """投稿を新規作成する。返り値は {"post": {...}, "owner_token": "..."}"""
        r = await self.http.post(f"{self.base}/posts", json=payload)
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
