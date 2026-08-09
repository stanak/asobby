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
    from windows_toasts.wrappers import ToastScenario

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
        import os
        import winreg
        from pathlib import Path

        icon_uri: Optional[str] = None
        icon_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "asobby"
        icon_path = icon_dir / "toast.ico"
        if not icon_path.exists():
            try:
                from icon_art import render_icon

                icon_dir.mkdir(parents=True, exist_ok=True)
                ico_images = [render_icon(s) for s in (32, 48, 64)]
                ico_images[0].save(
                    icon_path,
                    format="ICO",
                    sizes=[(img.width, img.height) for img in ico_images],
                    append_images=ico_images[1:],
                )
            except Exception:
                pass
        if icon_path.exists():
            icon_uri = str(icon_path.resolve())

        key_path = f"SOFTWARE\\Classes\\AppUserModelId\\{APP_AUMID}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "asobby")
            if icon_uri:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_uri)
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
            log(t("toast.error.unavailable", detail=IMPORT_ERROR or ""))
        return False
    try:

        def activated(args: ToastActivatedEventArgs) -> None:
            arg = args.arguments
            if arg in ("accept", "decline"):
                on_reply(arg)

        toast = Toast([text], duration=ToastDuration.Short)
        toast.AddAction(ToastButton(t("toast.accept"), "accept"))
        toast.AddAction(ToastButton(t("toast.decline"), "decline"))
        toast.on_activated = activated
        _get_toaster().show_toast(toast)
        _live_toasts.append(toast)
        del _live_toasts[:-_LIVE_TOASTS_MAX]
        return True
    except Exception as e:
        if log:
            log(t("toast.error.request_failed", detail=repr(e)))
        return False


def show_info_toast(
    text: str,
    *,
    title: str = "asobby",
    important: bool = False,
    log: Optional[Callable[[str], None]] = None,
    on_click: Optional[Callable[[], None]] = None,
) -> bool:
    """Windows トーストを表示する。on_click 指定時はトースト本体クリックで発火。"""
    if not INTERACTIVE_AVAILABLE:
        if log:
            log(
                t("toast.error.info_unavailable", detail=IMPORT_ERROR or "")
            )
        return False
    try:
        if important:
            toast = Toast(
                [title, text],
                duration=ToastDuration.Short,
                scenario=ToastScenario.Important,
            )
        else:
            toast = Toast([title, text], duration=ToastDuration.Short)

        def on_failed(args) -> None:
            if log:
                log(t("toast.error.os_failed", detail=repr(args)))

        toast.on_failed = on_failed
        if on_click is not None:
            def activated(_args) -> None:
                try:
                    on_click()
                except Exception:
                    pass

            toast.on_activated = activated
        _get_toaster().show_toast(toast)
        _live_toasts.append(toast)
        del _live_toasts[:-_LIVE_TOASTS_MAX]
        return True
    except Exception as e:
        if log:
            log(t("toast.error.failed", detail=repr(e)))
        return False
