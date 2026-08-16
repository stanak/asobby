from __future__ import annotations

CREATE_HOSTCHECK_NOTIFY_AFTER = 2


def should_notify_create_hostcheck_failure(failures: int) -> bool:
    return failures >= CREATE_HOSTCHECK_NOTIFY_AFTER


def test_notify_only_after_second_create_failure():
    assert not should_notify_create_hostcheck_failure(1)
    assert should_notify_create_hostcheck_failure(2)
