"""Exercise the real detector state machine without a game, network or user files."""
import sys
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

if sys.platform != "win32":
    pytest.skip("controller imports Win32 memory bindings", allow_module_level=True)

import controller
from detect_api import DetectionState


@pytest.mark.asyncio
async def test_detector_keeps_id_through_rollback_and_rotates_only_for_next_game(monkeypatch):
    clocks = [1000.0, 50.0]
    monkeypatch.setattr(controller, "time", SimpleNamespace(time=lambda: clocks[0], monotonic=lambda: clocks[1]))
    ctrl = controller.Controller.__new__(controller.Controller)
    ctrl._stable_counts = defaultdict(int)
    for name in ("_detect_pause_until", "_battle_start_ts", "_last_ko_played_at", "_last_keepalive_ts", "_next_guest_presence_ts"):
        setattr(ctrl, name, 0.0)
    for name in ("_round_battle_engaged", "_close_pending", "_result_reported", "_replay_pending", "_battle_presence_announced"):
        setattr(ctrl, name, False)
    ctrl._round_char_ids = (None, None)
    ctrl._last_ko_fingerprint = ""
    ctrl._pending_local_match = None
    ctrl._battle_identity = None
    ctrl._last_recorded_battle = ""
    ctrl._battle_identity_profiles = ("", "", "")
    ctrl.has_active_post = lambda: False
    ctrl.is_logged_in = lambda: False
    ctrl._reset_session_score = lambda: None
    ctrl._build_match_status = lambda *a, **kw: ""
    ctrl._update_tray_ui_state = lambda **kw: None
    ctrl._handle_session_score = lambda payload: None
    ctrl._schedule_replay_upload = AsyncMock()
    events = []

    def tick(*, mode="battle", left=1, right=1):
        st = DetectionState(alive=True, mode=mode, port=None, giuroll=True, autopunch=False,
                            lprof="hp", rprof="gp", lchar_id=0, rchar_id=5,
                            lchar_name="Reimu", rchar_name="Youmu", net_side="host",
                            btl_mode=5 if max(left, right) >= 2 else 2, lwin=left, rwin=right)
        ctrl.on_detect(st, my_ip="")
        if ctrl._pending_local_match:
            events.append(ctrl._pending_local_match)
            ctrl._pending_local_match = None
        clocks[0] += .05
        clocks[1] += .05

    # Attaching to an already ended game cannot allocate a second identity.
    for _ in range(15):
        tick(left=2)
    assert ctrl._battle_identity is None and not events
    for _ in range(20):
        tick()
    original = ctrl._battle_identity.client_id
    tick(left=2)
    tick(mode="charsel")  # one sample of scene flicker
    for _ in range(160):
        tick()
    clocks[0] -= 382  # Wall-clock correction must not change the identity/duration.
    tick(left=1, right=2)
    assert len(events) == 1 and events[0]["client_id"] == original
    assert ctrl._battle_identity.client_id == original
    for _ in range(12):
        tick(mode="charsel")
    assert ctrl._battle_identity is None
    for _ in range(20):
        tick()
    tick(left=2)
    assert len(events) == 2 and events[1]["client_id"] != original
    assert events[1]["duration_sec"] < 2
