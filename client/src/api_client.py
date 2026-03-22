from __future__ import annotations
import httpx

class ApiClient:
    def __init__(self, http: httpx.AsyncClient, base: str) -> None:
        self.http = http
        self.base = base.rstrip("/")

    async def myip(self) -> str:
        r = await self.http.get(f"{self.base}/myip")
        r.raise_for_status()
        return r.json()["ip"]

    async def upsert(self, payload: dict) -> dict:
        r = await self.http.post(f"{self.base}/posts/upsert", json=payload)
        r.raise_for_status()
        return r.json()

    async def close(self, post_id: str, reason: str = "auto") -> dict:
        r = await self.http.post(f"{self.base}/posts/close", json={"id": post_id, "reason": reason})
        r.raise_for_status()
        return r.json()
