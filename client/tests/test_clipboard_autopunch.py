import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from host_clipboard import should_include_autopunch_in_clipboard


def test_include_autopunch_for_ap_only_post():
    assert should_include_autopunch_in_clipboard(
        {"autopunch": True, "direct_reachable": False},
    )


def test_omit_autopunch_when_direct_without_ap():
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": False, "direct_reachable": True},
    )


def test_omit_autopunch_when_direct_even_if_autopunch_flag_set():
    """直接接続可能なら REQUIRE と同様、クリップボードにも AP を載せない。"""
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": True, "direct_reachable": True},
    )


def test_omit_autopunch_when_server_says_direct_despite_client_payload():
    """クライアントが AP 常駐でも、サーバー判定が direct なら REQUIRE に AP なし。"""
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": False, "direct_reachable": True},
    )


def test_matches_require_column_with_uncertain_badge():
    post = {
        "autopunch": True,
        "direct_reachable": False,
        "reachability_uncertain": True,
    }
    show_ap_in_require = post["autopunch"] and not post["direct_reachable"]
    assert show_ap_in_require
    assert should_include_autopunch_in_clipboard(post) == show_ap_in_require


def test_matches_require_column_when_no_ap():
    post = {"autopunch": False, "direct_reachable": True}
    show_ap_in_require = post["autopunch"] and not post["direct_reachable"]
    assert not show_ap_in_require
    assert should_include_autopunch_in_clipboard(post) == show_ap_in_require
