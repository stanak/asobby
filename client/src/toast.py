from __future__ import annotations

from typing import Callable

try:
    from windows_toasts import (
        InteractableWindowsToaster,
        Toast,
        ToastActivatedEventArgs,
        ToastButton,
    )

    INTERACTIVE_AVAILABLE = True
except ImportError:
    INTERACTIVE_AVAILABLE = False

_toaster = None


def _get_toaster():
    global _toaster
    if _toaster is None:
        _toaster = InteractableWindowsToaster("asobby")
    return _toaster


def show_request_toast(text: str, on_reply: Callable[[str], None]) -> bool:
    """ボタン付き Windows トーストを表示する。非対応環境では False。"""
    if not INTERACTIVE_AVAILABLE:
        return False
    try:

        def activated(args: ToastActivatedEventArgs) -> None:
            arg = args.arguments
            if arg in ("accept", "decline"):
                on_reply(arg)

        toast = Toast([text])
        toast.AddAction(ToastButton("承諾する", "accept"))
        toast.AddAction(ToastButton("ごめんなさい", "decline"))
        toast.on_activated = activated
        _get_toaster().show_toast(toast)
        return True
    except Exception:
        return False
