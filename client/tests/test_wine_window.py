"""Exercise the Wine UI with real Windows Tk, without network/game/user files."""
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows client UI")


@pytest.fixture(scope="module")
def tk_root():
    # Use one Tcl interpreter, as the real app does; repeated Tk initialization
    # can fail with the Microsoft Store Python's virtualized Tcl library path.
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def window(monkeypatch, tk_root):
    import tkinter as tk
    import i18n
    from compat_window import CompatWindow
    from main import TrayApp

    monkeypatch.setattr(i18n, "_lang", "ja")
    root = tk.Toplevel(tk_root)
    root.withdraw()  # Never present a test window to the user.
    monkeypatch.setattr(root, "after", Mock())  # Ticks are deterministic/manual.
    app = TrayApp.__new__(TrayApp)
    app.icon = None
    app._quit = Mock()
    app._append_log = Mock()
    app._status_text = Mock(return_value="idle")
    app.controller = Mock()
    app.controller.discord_user = "Tester"
    app.controller.pending_requests = []
    app.controller.update_available = None
    app.controller.my_post = SimpleNamespace(comment="", stream_url="", post_type="casual")
    for method in ("is_logged_in", "is_detect_paused", "is_replay_refusal_active", "hotkeys_enabled"):
        getattr(app.controller, method).return_value = False
    for method in ("comment_presets", "stream_presets"):
        getattr(app.controller, method).return_value = []
    app.controller.startup_notify_enabled.return_value = True
    app.compat_window = CompatWindow(root, app)
    try:
        yield app.compat_window, app
    finally:
        root.destroy()


def labels(menu):
    return [menu.entrycget(index, "label") for index in range(menu.index("end") + 1)
            if menu.type(index) != "separator"]


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_real_app_menu_settings_submenus_and_locale_without_tray(window, monkeypatch, lang):
    import i18n
    view, app = window
    monkeypatch.setattr(i18n, "_lang", lang)
    view._tick()
    assert view.menu_button.cget("text") == i18n.t("wine.menu")
    assert view.status.cget("text") == "idle"
    assert view.root.protocol("WM_DELETE_WINDOW")
    menu = view._make_menu(view.root, app._build_menu())
    texts = labels(menu)
    assert i18n.t("tray.discord_login") in texts
    assert i18n.t("tray.reply_requests") not in texts
    assert i18n.t("tray.stats") in texts
    assert i18n.t("tray.quit") in texts
    setting = next(i for i in range(menu.index("end") + 1)
                   if menu.type(i) == "command" and menu.entrycget(i, "label") == "✓ " + i18n.t("tray.startup_notify"))
    menu.invoke(setting)
    app.controller.set_startup_notify_enabled.assert_called_once_with(False)
    app._append_log.assert_not_called()
    assert app.icon is None


def test_notifications_from_worker_and_click_actions(window, monkeypatch):
    import main
    from i18n import t
    view, app = window
    toast = Mock()
    monkeypatch.setattr(main.toast, "show_info_toast", toast)
    callback = Mock()
    thread = threading.Thread(target=lambda: app.emit_notify("first\nsecond", important=True, on_click=callback))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not view.notices  # Worker must not touch the widgets.
    view._tick()
    assert view.listbox.get(0) == "! first second"
    assert view.detail.get("1.0", "end-1c") == "first\nsecond"
    view.open_button.invoke()
    callback.assert_called_once_with()
    toast.assert_not_called()
    app.emit_request(SimpleNamespace(message_id="test"), "Challenge")
    view._tick()
    assert view.notices[0] == ("Challenge\n" + t("wine.request_hint"), True, None)
    assert str(view.open_button.cget("state")) == "disabled"


def test_notification_history_and_callback_failure(window):
    view, app = window
    for i in range(70):
        view.notify(str(i))
    view._tick()
    view._tick()
    assert len(view.notices) == 50
    assert view.listbox.size() == 50
    assert view.notices[0][0] == "69" and view.notices[-1][0] == "20"
    view._invoke(Mock(side_effect=RuntimeError("test error")))
    app._append_log.assert_called_once()
    view._tick()
    assert view.notices[0][1] is True


@pytest.mark.parametrize("reply", ["accept", "decline"])
def test_real_request_reply_menu_without_icon(window, monkeypatch, reply):
    import main
    from i18n import t
    view, app = window
    app.controller.pending_requests = [SimpleNamespace(message_id="req-1", req_type="battle", from_name="Alice")]
    app.controller._request_type_label.return_value = "Battle"
    app.loop = object()
    dispatch = Mock()
    monkeypatch.setattr(main.asyncio, "run_coroutine_threadsafe", dispatch)
    menu = view._make_menu(view.root, app._build_menu())
    index = next(i for i in range(menu.index("end") + 1)
                 if menu.type(i) == "cascade" and menu.entrycget(i, "label") == t("tray.reply_requests"))
    submenu = view.root.nametowidget(menu.entrycget(index, "menu"))
    assert submenu.entrycget(0, "state") == "disabled"
    submenu.invoke(1 if reply == "accept" else 2)
    app.controller.reply_request.assert_called_once_with("req-1", reply)
    dispatch.assert_called_once_with(app.controller.reply_request.return_value, app.loop)
    dispatch.return_value.add_done_callback.call_args.args[0](None)
    app._append_log.assert_not_called()


@pytest.mark.parametrize("notice", [False, True])
def test_wine_startup_uses_window_not_tray_and_respects_notice_setting(monkeypatch, notice):
    import compat_window
    import i18n
    import main
    import tkinter

    root, view = Mock(), Mock()
    monkeypatch.setattr(tkinter, "Tk", Mock(return_value=root))
    window_factory = Mock(return_value=view)
    monkeypatch.setattr(compat_window, "CompatWindow", window_factory)
    monkeypatch.setattr(main, "is_wine", lambda: True)
    monkeypatch.setattr(main, "wine_version", lambda: "test")
    monkeypatch.setattr(main.threading, "Thread", Mock())
    tray_factory = Mock()
    monkeypatch.setattr(main, "TrayIcon", tray_factory)
    app = main.TrayApp.__new__(main.TrayApp)
    app.icon = None
    app._append_log = Mock()
    app.controller = Mock()
    app.controller.hotkeys_enabled.return_value = False
    app.controller.startup_notify_enabled.return_value = notice
    app.run()
    root.withdraw.assert_not_called()
    tray_factory.assert_not_called()
    window_factory.assert_called_once_with(root, app)
    root.after.assert_called_once_with(1500, app._show_startup_notice)
    root.after.call_args.args[1]()
    if notice:
        view.notify.assert_called_once_with(i18n.t("wine.startup_notice"), important=False, on_click=None)
    else:
        view.notify.assert_not_called()
    root.mainloop.assert_called_once()
