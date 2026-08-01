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
    """直接接続可能なら AP バッジと同様、クリップボードにも AP を載せない。"""
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": True, "direct_reachable": True},
    )


def test_include_autopunch_when_reachability_uncertain():
    assert should_include_autopunch_in_clipboard(
        {
            "autopunch": True,
            "direct_reachable": False,
            "reachability_uncertain": True,
        },
    )


def test_include_autopunch_when_local_ap_but_server_says_direct():
    """サーバーが direct と誤判定しても、クライアントが AP 利用なら含める。"""
    assert should_include_autopunch_in_clipboard(
        {"autopunch": False, "direct_reachable": True},
        local_autopunch=True,
    )


def test_omit_autopunch_when_ap_exe_loaded_but_not_used_for_post():
    """AP exe が常駐していても、募集で AP 未使用なら含めない。"""
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": False, "direct_reachable": True},
        local_autopunch=False,
    )


def test_old_logic_false_positive_direct():
    post = {"autopunch": False, "direct_reachable": True}
    old = bool(post.get("autopunch")) and not bool(post.get("direct_reachable"))
    new = should_include_autopunch_in_clipboard(post, local_autopunch=True)
    assert old is False
    assert new is True
