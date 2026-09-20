"""Policy access from the shared Windows/Wine menu must not require login."""
import sys
from unittest.mock import Mock

import pytest

import i18n


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("base", ["https://asobby.com/", "http://localhost:8000/custom/"])
@pytest.mark.parametrize("logged_in", [False, True])
def test_policy_menu_uses_configured_server_without_auth(monkeypatch, lang, base, logged_in):
    if sys.platform != "win32":
        pytest.skip("The client imports Win32 bindings")
    import main

    monkeypatch.setattr(i18n, "_lang", lang)
    open_browser = Mock()
    monkeypatch.setattr(main.webbrowser, "open", open_browser)
    app = main.TrayApp.__new__(main.TrayApp)
    app.controller = Mock()
    app.controller.discord_user = "Viewer" if logged_in else None
    app.controller.is_logged_in.return_value = logged_in
    app.controller.config_mgr.get_api_base.return_value = base
    app._status_text = Mock(return_value="idle")
    item = next(item for item in app._build_menu().items if item.text == i18n.t("tray.privacy"))
    assert item.visible and item.enabled
    item(None)
    open_browser.assert_called_once_with(f"{base.rstrip('/')}/privacy?lang={lang}")
    app.controller.lobby_url.assert_not_called()
    app.controller.login.assert_not_called()
