from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path("asobby_config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "server": {
        "api_base": "http://133.130.100.128:8000",
    },
    "tools": {
        "giuroll_path": "",
        "autopunch_path": "",
        "soku_path": "",
    },
    "post_defaults": {
        "comment": "",
        "stream_url": "",
        "rank": "any",
    },
    "sounds": {
        "enabled": True,
        "mode": "beep",
        "on_recruit": {
            "kind": "beep",
            "freq": 880,
            "duration_ms": 180,
        },
        "on_recruit_giuroll": {
            "kind": "beep",
            "freq": 1046,
            "duration_ms": 180,
        },
        "on_recruit_host_unavailable": {
            "kind": "beep",
            "freq": 440,
            "duration_ms": 400,
        },
    },
}


def _merge_defaults(base: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for k, v in loaded.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge_defaults(out[k], v)
        else:
            out[k] = v
    return out


class ConfigManager:
    def __init__(
        self,
        path: str | Path = CONFIG_PATH,
        autosave_delay_sec: float = 3.0,
    ) -> None:
        self.path = Path(path)
        self.autosave_delay_sec = autosave_delay_sec

        self.config: dict[str, Any] = {}
        self._dirty = False
        self._save_task: asyncio.Task | None = None

        self.load()

    # -----------------
    # load / save
    # -----------------
    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.config = deepcopy(DEFAULT_CONFIG)
            self.save_now()
            return self.config

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root must be object")
            self.config = _merge_defaults(DEFAULT_CONFIG, raw)
        except Exception:
            self.config = deepcopy(DEFAULT_CONFIG)
            self.save_now()

        return self.config

    def save_now(self) -> None:
        self.path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False

    async def flush(self) -> None:
        """終了時などに pending autosave を待って即保存する。"""
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        if self._dirty:
            self.save_now()

    # -----------------
    # autosave
    # -----------------
    def mark_dirty(self) -> None:
        self._dirty = True

        # Textual / asyncio 環境前提
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save_now()
            return

        if self._save_task and not self._save_task.done():
            self._save_task.cancel()

        self._save_task = loop.create_task(self._delayed_save())

    async def _delayed_save(self) -> None:
        try:
            await asyncio.sleep(self.autosave_delay_sec)
            if self._dirty:
                self.save_now()
        except asyncio.CancelledError:
            pass

    # -----------------
    # getters
    # -----------------
    def get(self) -> dict[str, Any]:
        return self.config

    def get_section(self, section: str) -> dict[str, Any]:
        value = self.config.get(section, {})
        return value if isinstance(value, dict) else {}

    def get_value(self, section: str, key: str, default: Any = None) -> Any:
        return self.get_section(section).get(key, default)

    # -----------------
    # setters
    # -----------------
    def set_value(self, section: str, key: str, value: Any) -> None:
        self.config.setdefault(section, {})
        if not isinstance(self.config[section], dict):
            self.config[section] = {}
        self.config[section][key] = value
        self.mark_dirty()

    def set_values(self, section: str, **kwargs: Any) -> None:
        self.config.setdefault(section, {})
        if not isinstance(self.config[section], dict):
            self.config[section] = {}
        self.config[section].update(kwargs)
        self.mark_dirty()

    # -----------------
    # convenience helpers
    # -----------------
    def get_post_defaults(self) -> dict[str, Any]:
        return self.get_section("post_defaults")

    def set_post_default(self, key: str, value: Any) -> None:
        self.set_value("post_defaults", key, value)

    def get_tool_paths(self) -> dict[str, str]:
        sec = self.get_section("tools")
        return {
            "giuroll_path": str(sec.get("giuroll_path", "")),
            "autopunch_path": str(sec.get("autopunch_path", "")),
            "soku_path": str(sec.get("soku_path", "")),
        }

    def set_tool_path(self, tool_name: str, path: str) -> None:
        self.set_value("tools", tool_name, path)

    def get_api_base(self) -> str:
        return str(self.get_value("server", "api_base", "http://127.0.0.1:8000"))

    def set_api_base(self, value: str) -> None:
        self.set_value("server", "api_base", value)
