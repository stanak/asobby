"""Tests for set score storage and double-KO-safe validation."""
from __future__ import annotations

import pytest

from main import _normalize_set_score, _score_kwargs


@pytest.mark.parametrize(
    ("host", "guest", "expected"),
    [
        (2, 0, (2, 0)),
        (2, 1, (2, 1)),
        (3, 2, (3, 2)),
        (2, 2, (None, None)),
        (1, 1, (None, None)),
        (1, 0, (None, None)),
        (None, 2, (None, None)),
    ],
)
def test_normalize_set_score(host, guest, expected):
    assert _normalize_set_score(host, guest) == expected


def test_score_kwargs():
    assert _score_kwargs(2, 1) == {"host_wins": 2, "guest_wins": 1}
    assert _score_kwargs(2, 2) == {}
