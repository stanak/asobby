from __future__ import annotations


def should_include_autopunch_in_clipboard(post_data: dict) -> bool:
    """クリップボードに AutoPunch を含めるか (クライアント申告ベース)。"""
    return bool(post_data.get("autopunch"))
