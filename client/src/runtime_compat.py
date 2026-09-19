"""Detect Wine from the Windows runtime, not from an inherited WINEPREFIX."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import os
import sys


@lru_cache(maxsize=1)
def wine_version() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        # Wine exports this cdecl function; native Windows does not.
        get_version = ctypes.CDLL("ntdll.dll").wine_get_version
        get_version.argtypes = []
        get_version.restype = ctypes.c_char_p
        value = get_version()
        return value.decode("utf-8", errors="replace") if value else None
    except (AttributeError, OSError):
        return None


def is_wine() -> bool:
    # Explicit opt-in is also useful for launchers and Windows compatibility tests.
    return os.environ.get("ASOBBY_WINE", "").strip() == "1" or wine_version() is not None
