import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from host_clipboard import should_include_autopunch_in_clipboard


def test_include_autopunch_when_client_reports_ap():
    assert should_include_autopunch_in_clipboard({"autopunch": True})


def test_omit_autopunch_when_client_reports_none():
    assert not should_include_autopunch_in_clipboard({"autopunch": False})


def test_ignores_direct_reachable_flag():
    assert should_include_autopunch_in_clipboard(
        {"autopunch": True, "direct_reachable": True},
    )
    assert not should_include_autopunch_in_clipboard(
        {"autopunch": False, "direct_reachable": False},
    )
