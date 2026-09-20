"""Pre-lock configuration reading never creates, repairs or rewrites files."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from config_manager import read_startup_preferences


def test_missing_config_defaults_without_creating_a_file(tmp_path):
    path = tmp_path / "absent.json"
    assert read_startup_preferences(path) == ("ja", True)
    assert not path.exists()


@pytest.mark.parametrize("contents", [b"{broken", b"null", b"[]", b"{}", b'{"options":null}', b'{"options":[]}', b"\xff"])
def test_unusable_config_defaults_without_repair(tmp_path, contents):
    path = tmp_path / "config.json"
    path.write_bytes(contents)
    assert read_startup_preferences(path) == ("ja", True)
    assert path.read_bytes() == contents


@pytest.mark.parametrize("raw", [None, 0, 1, "false", "", [], {}])
def test_invalid_notification_option_keeps_enabled_default(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"options": {"locale":"en", "startup_notify_enabled":raw}}), encoding="utf-8")
    assert read_startup_preferences(path) == ("en", True)


@pytest.mark.parametrize("enabled", [False, True])
def test_saved_preferences_are_read_without_touching_other_settings(tmp_path, enabled):
    path = tmp_path / "config.json"
    original = json.dumps({"auth":{"session_token":"synthetic-test-value"}, "options":{"locale":"en", "startup_notify_enabled":enabled}})
    path.write_text(original, encoding="utf-8")
    assert read_startup_preferences(path) == ("en", enabled)
    assert path.read_text(encoding="utf-8") == original


def test_unreadable_config_does_not_abort_startup_or_try_to_write(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("blocked")))
    write = Mock(side_effect=AssertionError("must not write"))
    monkeypatch.setattr(Path, "write_text", write)
    assert read_startup_preferences(tmp_path / "config.json") == ("ja", True)
    write.assert_not_called()
