"""Startup-only notification preference; never start real UI/network/game polling."""
import json
import sys
from unittest.mock import Mock

import pytest

from config_manager import ConfigManager
import i18n


def test_new_config_enables_startup_notification(tmp_path):
    path = tmp_path / "asobby_config.json"
    config = ConfigManager(path)
    assert config.get_value("options", "startup_notify_enabled") is True
    assert json.loads(path.read_text(encoding="utf-8"))["options"]["startup_notify_enabled"] is True


def test_old_config_defaults_to_enabled_without_losing_preferences(tmp_path):
    path = tmp_path / "asobby_config.json"
    path.write_text(json.dumps({"options": {"locale": "en", "ping_warn_enabled": False}}), encoding="utf-8")
    config = ConfigManager(path)
    assert config.get_value("options", "startup_notify_enabled") is True
    assert config.get_value("options", "locale") == "en"
    assert config.get_value("options", "ping_warn_enabled") is False


@pytest.mark.parametrize("enabled", [False, True])
def test_preference_survives_reload(tmp_path, enabled):
    path = tmp_path / "asobby_config.json"
    ConfigManager(path).set_value("options", "startup_notify_enabled", enabled)
    assert ConfigManager(path).get_value("options", "startup_notify_enabled") is enabled


@pytest.fixture
def app(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("tray/controller import Win32 bindings")
    from controller import Controller
    from main import TrayApp

    monkeypatch.setattr(i18n, "_lang", "ja")
    # Bypass constructors: they would open user files and detect the game.
    ctrl = Controller.__new__(Controller)
    ctrl.config_mgr = ConfigManager(tmp_path / "asobby_config.json")
    ctrl.log_sink = Mock()
    ctrl.discord_user = "Viewer"
    instance = TrayApp.__new__(TrayApp)
    instance.controller = ctrl
    instance.icon = Mock()
    instance.emit_notify = Mock()
    instance._status_text = Mock(return_value="idle")
    instance._pause_menu_label = Mock(return_value="Pause")
    return instance


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_menu_toggles_and_persists_without_emitting_notifications(app, monkeypatch, lang):
    monkeypatch.setattr(i18n, "_lang", lang)
    item = next(item for item in app._build_menu().items if item.text == i18n.t("tray.startup_notify"))
    assert item.checked is True
    for expected in (False, True):
        item(app.icon)
        assert item.checked is expected
        assert ConfigManager(app.controller.config_mgr.path).get_value("options", "startup_notify_enabled") is expected
    assert app.icon.update_menu.call_count == 2
    app.emit_notify.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_run_schedules_only_the_optional_startup_notice(app, monkeypatch, enabled):
    import main
    import tkinter

    root = Mock()
    monkeypatch.setattr(tkinter, "Tk", Mock(return_value=root))
    monkeypatch.setattr(main.threading, "Thread", Mock())
    monkeypatch.setattr(main, "TrayIcon", Mock(return_value=app.icon))
    app._icon_for = Mock()
    app._status_text = Mock(return_value="idle")
    app._append_log = Mock()
    app.controller.hotkeys_enabled = lambda: False
    app.controller.lobby_url = lambda: "https://example.invalid"
    app.controller.set_startup_notify_enabled(enabled)
    app.run()
    root.after.assert_called_once_with(1500, app._show_startup_notice)
    root.after.call_args.args[1]()
    if enabled:
        app.emit_notify.assert_called_once_with(i18n.t("tray.startup_notice"))
    else:
        app.emit_notify.assert_not_called()
    app.icon.run_detached.assert_called_once()
    root.mainloop.assert_called_once()


def test_disabling_while_startup_callback_is_pending(app):
    callback = app._show_startup_notice
    app._toggle_startup_notify()
    callback()
    app.emit_notify.assert_not_called()


@pytest.mark.parametrize("raw", [None, 0, "false", ""])
def test_malformed_preference_keeps_default(app, raw):
    app.controller.config_mgr.set_value("options", "startup_notify_enabled", raw)
    assert app.controller.startup_notify_enabled() is True


@pytest.mark.parametrize("shown", [True, False])
def test_other_notifications_and_tray_fallback_remain_enabled(app, monkeypatch, shown):
    import main

    app.controller.set_startup_notify_enabled(False)
    app._append_log = Mock()
    app._notify = Mock()
    show = Mock(return_value=shown)
    monkeypatch.setattr(main.toast, "show_info_toast", show)
    click = Mock()
    main.TrayApp.emit_notify(app, "Other notification", important=True, on_click=click)
    assert show.call_args.args == ("Other notification",)
    assert show.call_args.kwargs["important"] is True
    assert show.call_args.kwargs["on_click"] is click
    if shown:
        app._notify.assert_not_called()
    else:
        app._notify.assert_called_once_with("Other notification")
