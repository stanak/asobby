from __future__ import annotations


def format_host_clipboard_text(
    addr: str,
    *,
    giuroll: bool,
    include_autopunch: bool,
) -> str:
    tools: list[str] = []
    if giuroll:
        tools.append("Giuroll")
    if include_autopunch:
        tools.append("AutoPunch")
    if tools:
        return f"{addr} {', '.join(tools)}"
    return addr


def should_include_autopunch_in_clipboard(post_data: dict) -> bool:
    """クリップボードに AutoPunch を含めるか。

    ロビー REQUIRE 列の AP バッジ (``autopunch && !direct_reachable``) と完全一致。
    """
    return bool(post_data.get("autopunch")) and not bool(
        post_data.get("direct_reachable")
    )


def reachability_flags_for_clipboard(
    *,
    direct_ok: bool,
    uses_autopunch: bool,
) -> dict[str, bool]:
    """ローカル UDP プローブ結果から clipboard 用 reachability フラグを組み立てる。"""
    if direct_ok:
        return {"autopunch": False, "direct_reachable": True}
    return {"autopunch": bool(uses_autopunch), "direct_reachable": False}
