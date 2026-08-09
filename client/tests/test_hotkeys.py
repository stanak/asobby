"""グローバルホットキーの組み合わせ解析テスト (Windows API 非依存)。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    HotkeyManager,
    parse_combo,
)


def test_parse_ctrl_alt_letter():
    assert parse_combo("ctrl+alt+t") == (MOD_CONTROL | MOD_ALT, ord("T"))
    assert parse_combo("Ctrl+Alt+L") == (MOD_CONTROL | MOD_ALT, ord("L"))
    assert parse_combo("ctrl+alt+r") == (MOD_CONTROL | MOD_ALT, ord("R"))


def test_parse_other_modifiers_and_keys():
    assert parse_combo("ctrl+shift+1") == (MOD_CONTROL | MOD_SHIFT, ord("1"))
    assert parse_combo("alt+f5") == (MOD_ALT, 0x74)


def test_parse_rejects_invalid():
    assert parse_combo("") is None
    assert parse_combo("t") is None  # 修飾キーなし
    assert parse_combo("ctrl+") is None
    assert parse_combo("foo+t") is None
    assert parse_combo("ctrl+alt+enter") is None  # 未対応キー
    assert parse_combo("ctrl+alt+f99") is None


def test_manager_noop_on_non_windows():
    """Windows 以外では start しても何も起きない (テスト環境で安全)。"""
    mgr = HotkeyManager()
    mgr.add("post_type", "ctrl+alt+t", lambda: None)
    mgr.start()
    if sys.platform != "win32":
        assert not mgr.running
    mgr.stop()
