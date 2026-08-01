from __future__ import annotations


def should_include_autopunch_in_clipboard(post_data: dict) -> bool:
    """クリップボードに AutoPunch を含めるか。

    ロビー REQUIRE 列の AP バッジ (``autopunch && !direct_reachable``) と完全一致。
    """
    return bool(post_data.get("autopunch")) and not bool(
        post_data.get("direct_reachable")
    )
