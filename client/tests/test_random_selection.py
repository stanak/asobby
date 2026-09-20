from dataclasses import replace

import pytest

from detect_api import CharacterSelection, DetectionState
from random_selection import RandomSelectionTracker, UNKNOWN


def state(mode="charsel", *, chars=(20, 5), stages=(3, 3), scene=100, **kw):
    return DetectionState(alive=True, mode=mode, port=None, giuroll=True, autopunch=False,
        lprof="hp", rprof="gp", lchar_id=chars[0], rchar_id=chars[1],
        lchar_name="?", rchar_name="?", net_side="host", process_id=1,
        character_selection=CharacterSelection(scene, *stages, *chars) if mode == "charsel" else None,
        **kw)


@pytest.mark.parametrize("chars, expected", [((20, 5), (True, False)), ((0, 20), (False, True)), ((20, 20), (True, True)), ((0, 5), (False, False))])
def test_latches_ready_choice_before_resolution(chars, expected):
    tracker = RandomSelectionTracker()
    tracker.observe(state(chars=chars, stages=(1, 1)))
    tracker.observe(state(chars=chars))
    for _ in range(30):
        tracker.observe(state(chars=(0, 5)))  # Random has resolved, still Select.
    tracker.observe(state("loading"))
    tracker.observe(state("battle"))
    assert tracker.battle_choices == expected


@pytest.mark.parametrize("stages", [(0, 0), (1, 1), (3, 1), (1, 3)])
def test_hover_or_one_ready_player_is_not_random_evidence(stages):
    tracker = RandomSelectionTracker()
    tracker.observe(state(stages=stages))
    tracker.observe(state("battle"))
    assert tracker.battle_choices == UNKNOWN


def test_cancel_random_and_confirm_normal():
    tracker = RandomSelectionTracker()
    tracker.observe(state())
    tracker.observe(state(stages=(0, 3)))
    tracker.observe(state(chars=(0, 5), stages=(1, 3)))
    tracker.observe(state(chars=(0, 5)))
    tracker.observe(state("loading"))
    tracker.observe(state("battle"))
    assert tracker.battle_choices == (False, False)


@pytest.mark.parametrize("interruption", [
    state("idle"), state("host_wait"), replace(state(), alive=False),
    replace(state(), net_side="watch"), replace(state(), character_selection=None),
])
def test_interruption_discards_evidence(interruption):
    tracker = RandomSelectionTracker()
    tracker.observe(state())
    tracker.observe(interruption)
    tracker.observe(state("battle"))
    assert tracker.battle_choices == UNKNOWN


@pytest.mark.parametrize("change", [{"process_id": 2}, {"rprof": "new opponent"}, {"net_side": "client"}])
def test_new_connection_cannot_inherit_previous_choice(change):
    tracker = RandomSelectionTracker()
    tracker.observe(state())
    tracker.observe(replace(state("battle"), **change))
    assert tracker.battle_choices == UNKNOWN


def test_start_midbattle_and_rematch_without_selection_evidence():
    tracker = RandomSelectionTracker()
    tracker.observe(state("battle"))
    assert tracker.battle_choices == UNKNOWN
    tracker.observe(state(stages=(1, 1)))
    tracker.observe(state())
    tracker.observe(state("battle"))
    assert tracker.battle_choices == (True, False)
    tracker.observe(state(stages=(0, 0), scene=101))
    tracker.observe(state("loading"))
    tracker.observe(state("battle"))
    assert tracker.battle_choices == UNKNOWN


def test_attaching_after_random_resolves_does_not_assert_direct_selection():
    tracker = RandomSelectionTracker()
    tracker.observe(state(chars=(0, 5)))
    tracker.observe(state("battle"))
    assert tracker.battle_choices == UNKNOWN
