"""Windows グローバルホットキー (RegisterHotKey)。

RegisterHotKey の WM_HOTKEY は登録したスレッドのメッセージループにしか
届かないため、登録とメッセージループを専用スレッドで行う。
コールバックはそのスレッド上で呼ばれる (トレイメニューのハンドラと同様、
Controller の同期メソッド呼び出しを想定)。
"""
from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


def parse_combo(combo: str) -> Optional[tuple[int, int]]:
    """"ctrl+alt+t" 形式を (modifiers, virtual_key) に変換する。不正なら None。"""
    parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    key = parts[-1]
    for p in parts[:-1]:
        mod = _MOD_NAMES.get(p)
        if mod is None:
            return None
        mods |= mod
    if not mods:
        return None
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return mods, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return mods, 0x70 + (n - 1)  # VK_F1
    return None


class HotkeyManager:
    """名前付きホットキーの登録とディスパッチ。start()/stop() で開始・停止。"""

    def __init__(self, log: Optional[Callable[[str, str], None]] = None) -> None:
        self._log = log or (lambda level, msg: None)
        self._bindings: list[tuple[str, str, Callable[[], None]]] = []
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._started = threading.Event()
        self.failed_combos: list[tuple[str, str]] = []  # (name, combo)

    def add(self, name: str, combo: str, callback: Callable[[], None]) -> None:
        self._bindings.append((name, combo, callback))

    def start(self) -> None:
        if self._thread is not None or sys.platform != "win32":
            return
        if not self._bindings:
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="hotkeys"
        )
        self._thread.start()
        self._started.wait(timeout=3.0)

    def stop(self) -> None:
        thread = self._thread
        tid = self._thread_id
        if thread is None or tid is None:
            self._thread = None
            self._thread_id = None
            return
        try:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        except Exception:
            pass
        thread.join(timeout=3.0)
        self._thread = None
        self._thread_id = None
        self._started.clear()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        callbacks: dict[int, Callable[[], None]] = {}
        registered_ids: list[int] = []
        self.failed_combos = []
        for i, (name, combo, callback) in enumerate(self._bindings, start=1):
            parsed = parse_combo(combo)
            if parsed is None:
                self.failed_combos.append((name, combo))
                self._log("warn", f"Hotkey parse failed: {name}={combo!r}")
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                callbacks[i] = callback
                registered_ids.append(i)
            else:
                # 他アプリが同じ組み合わせを登録済み
                self.failed_combos.append((name, combo))
                self._log("warn", f"Hotkey register failed: {name}={combo!r}")

        self._started.set()
        if not callbacks:
            self._thread_id = None
            return

        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    cb = callbacks.get(int(msg.wParam))
                    if cb is not None:
                        try:
                            cb()
                        except Exception as e:
                            self._log("error", f"Hotkey handler error: {e}")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for i in registered_ids:
                try:
                    user32.UnregisterHotKey(None, i)
                except Exception:
                    pass
            self._thread_id = None
