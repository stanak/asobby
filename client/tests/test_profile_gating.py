from profile_gating import (
    NET_SCENES,
    SCENE_BATTLESV,
    SCENE_INGAME_HOSTLIST,
    gate_profiles_for_scene,
)


def test_gate_keeps_both_profiles_in_net_scenes():
    lprof, rprof = gate_profiles_for_scene(
        "HostP",
        "GuestP",
        scene_id=SCENE_BATTLESV,
        mode="battle",
    )
    assert lprof == "HostP"
    assert rprof == "GuestP"


def test_gate_keeps_host_profile_during_recruiting():
    lprof, rprof = gate_profiles_for_scene(
        "HostP",
        "garbage",
        scene_id=SCENE_INGAME_HOSTLIST,
        mode="host_wait",
    )
    assert lprof == "HostP"
    assert rprof == ""


def test_gate_clears_profiles_outside_net_flow():
    lprof, rprof = gate_profiles_for_scene(
        "stale",
        "garbage",
        scene_id=SCENE_INGAME_HOSTLIST,
        mode="idle",
    )
    assert lprof == ""
    assert rprof == ""


def test_net_scenes_cover_charsel_loading_battle():
    assert SCENE_BATTLESV in NET_SCENES
