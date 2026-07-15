from __future__ import annotations

import ctypes
import time

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_OPEN_RETRIES = 5
_OPEN_RETRY_WAIT_SEC = 0.05


def copy_text(text: str) -> bool:
    """Win32 API でクリップボードにテキストを入れる。成功なら True。

    他アプリがクリップボードを掴んでいると OpenClipboard が失敗するため、
    短いリトライを挟む。Windows 以外では常に False。
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False

    import ctypes.wintypes as wt

    # 64bit Python でハンドル/ポインタが 32bit に切り詰められないよう明示する
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wt.LPVOID
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)

    for _ in range(_OPEN_RETRIES):
        if user32.OpenClipboard(None):
            break
        time.sleep(_OPEN_RETRY_WAIT_SEC)
    else:
        return False

    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, buf, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            # SetClipboardData 成功後の所有権は OS 側に移るので、失敗時のみ解放
            kernel32.GlobalFree(handle)
            return False
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()
