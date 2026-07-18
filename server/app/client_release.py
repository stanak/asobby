"""GitHub Releases から asobby クライアント最新版情報を取得する (キャッシュ付き)。"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

import httpx

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/stanak/asobby/releases/latest"
)
CACHE_SEC = 600

_cache: Optional[dict[str, Any]] = None
_cache_at: float = 0.0


def parse_version(value: str) -> tuple[int, ...]:
    s = re.sub(r"^[^0-9]*", "", value.strip())
    if not s:
        return (0,)
    parts: list[int] = []
    for piece in s.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts) or (0,)


def is_older(current: str, latest: str) -> bool:
    return parse_version(current) < parse_version(latest)


def _release_from_env() -> Optional[dict[str, Any]]:
    raw = (os.environ.get("ASOBBY_CLIENT_LATEST_VERSION") or "").strip()
    if not raw:
        return None
    tag = raw if raw.startswith("v") else f"v{raw}"
    version = tag.lstrip("vV")
    base = "https://github.com/stanak/asobby/releases"
    return {
        "tag": tag,
        "version": version,
        "html_url": f"{base}/tag/{tag}",
        "download_url": f"{base}/download/{tag}/asobby-{tag}-windows.exe",
        "published_at": "",
    }


def cached_latest() -> Optional[dict[str, Any]]:
    return _cache


async def get_latest_release(*, force: bool = False) -> Optional[dict[str, Any]]:
    """最新クライアントリリース情報。失敗時は stale キャッシュを返す。"""
    global _cache, _cache_at

    env_info = _release_from_env()
    if env_info is not None:
        _cache = env_info
        _cache_at = time.time()
        return env_info

    now = time.time()
    if not force and _cache is not None and (now - _cache_at) < CACHE_SEC:
        return _cache

    try:
        headers = {"Accept": "application/vnd.github+json"}
        token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("ASOBBY_GITHUB_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                GITHUB_LATEST_RELEASE_URL,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        print(f"client_release fetch error: {e}")
        if env_info is not None:
            return env_info
        return _cache

    tag = str(data.get("tag_name") or "")
    if not tag:
        return _cache

    assets = data.get("assets") or []
    download_url = ""
    if assets:
        download_url = str(assets[0].get("browser_download_url") or "")
    html_url = str(data.get("html_url") or "")
    if not download_url:
        download_url = html_url

    info = {
        "tag": tag,
        "version": tag.lstrip("vV"),
        "html_url": html_url,
        "download_url": download_url,
        "published_at": str(data.get("published_at") or ""),
    }
    _cache = info
    _cache_at = now
    return info
