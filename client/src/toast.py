from __future__ import annotations

from typing import Callable, Optional

from i18n import t

try:
    from windows_toasts import (
        InteractableWindowsToaster,
        Toast,
        ToastActivatedEventArgs,
        ToastButton,
        ToastDuration,
    )

    INTERACTIVE_AVAILABLE = True
    IMPORT_ERROR: Optional[str] = None
except ImportError as _e:
    INTERACTIVE_AVAILABLE = False
    IMPORT_ERROR = repr(_e)

APP_AUMID = "asobby"

_toaster = None
# 表示中トーストへの参照を保持しないと GC され on_activated が発火しない
_live_toasts: list = []
_LIVE_TOASTS_MAX = 20


def _ensure_aumid() -> Optional[str]:
    """HKCU に asobby の AUMID を登録する (管理者権限不要)。

    カスタム AUMID がないと、トーストがアクションセンターに移った後の
    ボタン操作でコールバックが発火しない (windows-toasts の既知の制約)。
    """
    try:
        import winreg

        key_path = f"SOFTWARE\\Classes\\AppUserModelId\\{APP_AUMID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "asobby")
        return APP_AUMID
    except Exception:
        return None


def _get_toaster():
    global _toaster
    if _toaster is None:
        _toaster = InteractableWindowsToaster("asobby", notifierAUMID=_ensure_aumid())
    return _toaster


def show_request_toast(
    text: str,
    on_reply: Callable[[str], None],
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """ボタン付き Windows トーストを表示する。非対応環境では False。"""
    if not INTERACTIVE_AVAILABLE:
        if log:
            log(f"windows-toasts が利用できないため通常通知にフォールバックします: {IMPORT_ERROR}")
        return False
    try:

        def activated(args: ToastActivatedEventArgs) -> None:
            arg = args.arguments
            if arg in ("accept", "decline"):
                on_reply(arg)

        toast = Toast([text], duration=ToastDuration.Long)
        toast.AddAction(ToastButton(t("toast.accept"), "accept"))
        toast.AddAction(ToastButton(t("toast.decline"), "decline"))
        toast.on_activated = activated
        _get_toaster().show_toast(toast)
        _live_toasts.append(toast)
        del _live_toasts[:-_LIVE_TOASTS_MAX]
        return True
    except Exception as e:
        if log:
            log(f"ボタン付きトーストの表示に失敗: {e!r}")
        return False
