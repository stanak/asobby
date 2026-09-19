"""Fail-closed, process-lifetime single-instance guard for the Windows client.

Keep the existing name so other copies and older versions in the same Windows
logon session see the same mutex. Do not use a PID file or executable path.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

MUTEX_NAME = "Local\\asobby_client_mutex"
ERROR_ALREADY_EXISTS = 183


def _load_kernel32():
    if sys.platform != "win32":
        raise OSError("The asobby client requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._kernel32 = _load_kernel32()
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            raise RuntimeError("SingleInstance is already acquired")
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, self._name)
        # Capture ctypes' saved error immediately, before any other Win32 call
        # or lazy lookup of GetLastError can overwrite the thread's error code.
        error = ctypes.get_last_error()
        if not handle:
            # Access denied (e.g. a differently elevated existing instance) is
            # NOT permission to start without a lock. All acquisition failures
            # stop startup; do not claim certainty that another instance exists.
            raise ctypes.WinError(error or 31)
        if error == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is not None:
            if not self._kernel32.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None


def _show_notice(message: str, *, error: bool = False) -> None:
    from tkinter import Tk, messagebox

    root = Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        show = messagebox.showerror if error else messagebox.showinfo
        show("asobby", message, parent=root)
    finally:
        root.destroy()


def run_single_instance(start: Callable[[], None]) -> int:
    """Acquire before constructing the app (DB, local API, detector, hotkeys)."""
    from i18n import t
    from runtime_compat import is_wine

    try:
        guard = SingleInstance()
        acquired = guard.acquire()
    except OSError as exc:
        key = "wine.instance_check_failed" if is_wine() else "tray.instance_check_failed"
        _show_notice(t(key, error=str(exc)), error=True)
        return 1
    if not acquired:
        _show_notice(t("wine.already_running" if is_wine() else "tray.already_running"))
        return 0
    try:
        start()
        return 0
    finally:
        guard.close()
