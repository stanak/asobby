from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from controller import (  # noqa: E402
    _ko_recordable,
    _match_char_ids,
    _valid_char_id,
)
from detect_api import DetectionState  # noqa: E402


def _state(**overrides) -> DetectionState:
    base = dict(
        alive=True,
        mode="charsel",
        port=None,
        giuroll=False,
        autopunch=False,
        lprof="host",
        rprof="guest",
        lchar_id=7,
        rchar_id=12,
        lchar_name="?",
        rchar_name="?",
        net_side="host",
        btl_mode=5,
        lwin=2,
        rwin=0,
        battle_lchar_id=None,
        battle_rchar_id=None,
    )
    base.update(overrides)
    return DetectionState(**base)


def test_ko_recordable_requires_engaged_battle():
    assert not _ko_recordable(
        is_battle=True, mode="battle", round_battle_engaged=False
    )
    assert not _ko_recordable(
        is_battle=False, mode="charsel", round_battle_engaged=False
    )
    assert _ko_recordable(
        is_battle=True, mode="battle", round_battle_engaged=True
    )
    assert _ko_recordable(
        is_battle=False, mode="charsel", round_battle_engaged=True
    )


def test_match_char_ids_prefers_globals_during_battle():
    st = _state(
        mode="battle",
        lchar_id=0,
        rchar_id=8,
        battle_lchar_id=0,
        battle_rchar_id=0,
    )
    assert _match_char_ids(st) == (0, 8)


def test_match_char_ids_prefers_globals_during_loading():
    st = _state(
        mode="loading",
        lchar_id=0,
        rchar_id=8,
        battle_lchar_id=3,
        battle_rchar_id=3,
    )
    assert _match_char_ids(st) == (0, 8)


def test_match_char_ids_uses_battle_objects_in_charsel():
    st = _state(
        mode="charsel",
        lchar_id=7,
        rchar_id=12,
        battle_lchar_id=0,
        battle_rchar_id=8,
    )
    assert _match_char_ids(st) == (0, 8)


def test_match_char_ids_ignores_charsel_cursor_without_battle():
    st = _state(mode="charsel", battle_lchar_id=None, battle_rchar_id=None)
    assert _match_char_ids(st) == (None, None)


def test_match_char_ids_prefers_battle_objects():
    st = _state(
        mode="charsel",
        lchar_id=7,
        rchar_id=12,
        battle_lchar_id=3,
        battle_rchar_id=5,
    )
    assert _match_char_ids(st) == (3, 5)


def test_valid_char_id():
    assert _valid_char_id(0)
    assert _valid_char_id(19)
    assert not _valid_char_id(20)
    assert not _valid_char_id(None)
