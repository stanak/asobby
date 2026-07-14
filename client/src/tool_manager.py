from __future__ import annotations

from hisoutensoku_memory import get_hisoutensoku_pid_by_process_name

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import subprocess
import ctypes
import ctypes.wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_TERMINATE = 0x0001

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


class ToolState(Enum):
    NO_PATH = 0
    READY = 1
    LOADED = 2


@dataclass
class ToolEntry:
    name: str
    path: str = ""
    state: ToolState = ToolState.NO_PATH
    is_active: bool = False

    def button_label(self) -> str:
        if self.name == "soku":
            if self.state != ToolState.NO_PATH and not self.is_active:
                return f"load {self.name}"
            if self.state == ToolState.LOADED and self.is_active:
                return f"restart {self.name}"
            elif self.state == ToolState.NO_PATH and self.is_active:
                return f"stop {self.name}"

        if self.state == ToolState.LOADED and self.is_active:
            return f"{self.name} loaded"
        else:
            if self.state == ToolState.NO_PATH:
                return f"set {self.name} path"
            if self.state == ToolState.READY:
                return f"load {self.name}"

        return f"{self.name} unknown"


class ToolManager:
    def __init__(
        self,
        config_mgr,
    ) -> None:
        self.config_mgr = config_mgr

        paths = self.config_mgr.get_tool_paths()

        self._tools: dict[str, ToolEntry] = {
            "giuroll": self._make_entry("giuroll", paths.get("giuroll_path", "")),
            "autopunch": self._make_entry("autopunch", paths.get("autopunch_path", "")),
            "soku": self._make_entry("soku", paths.get("soku_path", "")),
        }

    def _make_entry(self, name: str, path: str) -> ToolEntry:
        path = (path or "").strip()
        if path:
            return ToolEntry(name=name, path=path, state=ToolState.READY)
        return ToolEntry(name=name, path="", state=ToolState.NO_PATH)

    def launch_tool(self, tool_name: str, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False

        suffix = p.suffix.lower()

        if suffix == ".exe":
            creationflags = 0
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creationflags |= subprocess.DETACHED_PROCESS

            try:
                subprocess.Popen(
                    [str(p)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=creationflags,
                    start_new_session=True,
                    cwd=str(p.parent),
                )
                return True
            except Exception:
                return False
        return False

    def get(self, tool_name: str) -> ToolEntry:
        return self._tools[tool_name]

    def state(self, tool_name: str) -> ToolState:
        return self._tools[tool_name].state

    def path(self, tool_name: str) -> str:
        return self._tools[tool_name].path

    def is_active(self, tool_name: str) -> bool:
        return self._tools[tool_name].is_active

    def button_label(self, tool_name: str) -> str:
        return self._tools[tool_name].button_label()

    def set_active(self, tool_name: str, is_active: bool) -> None:
        self._tools[tool_name].is_active = is_active

    def set_path(self, tool_name: str, path: str) -> None:
        entry = self._tools[tool_name]
        entry.path = path.strip()

        if entry.path:
            entry.state = ToolState.READY
        else:
            entry.state = ToolState.NO_PATH

        config_key = f"{tool_name}_path"
        self.config_mgr.set_tool_path(config_key, entry.path)

    def clear_path(self, tool_name: str) -> None:
        self.set_path(tool_name, "")

    def load(self, tool_name: str) -> bool:
        entry = self._tools[tool_name]
        pid = get_hisoutensoku_pid_by_process_name()

        if not entry.path:
            entry.state = ToolState.NO_PATH
            return False

        if pid or tool_name =="soku":
            ok = self.launch_tool(tool_name, entry.path)
            if ok:
                entry.state = ToolState.LOADED
                return True

        entry.state = ToolState.READY
        return False

    def _kill_pid(self, pid: int) -> bool:
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not h:
            return False
        try:
            return bool(kernel32.TerminateProcess(h, 0))
        finally:
            kernel32.CloseHandle(h)

    def kill_hisoutensoku(self) -> bool:
        pid = get_hisoutensoku_pid_by_process_name()
        if not pid:
            return False
        return self._kill_pid(pid)

    def reset_state(self) -> None:
        for tool_name, entry in self._tools.items():
            if entry.state == ToolState.LOADED:
                if entry.path:
                    self._tools[tool_name].state = ToolState.READY
                else:
                    self._tools[tool_name].state = ToolState.NO_PATH
