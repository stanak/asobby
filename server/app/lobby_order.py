"""Shared ordering for native lobby snapshots and the integration API."""
from collections.abc import Mapping
from typing import Any


def post_status(post: Mapping[str, Any]) -> str:
    if post.get("net_status") == 4:
        return "playing"
    if post.get("guest_connected") or post.get("net_status") == 2:
        return "connecting"
    if post.get("net_status") == 3:
        return "waiting"
    return "unknown"


def post_sort_key(post: Mapping[str, Any]) -> tuple[int, int, float, str]:
    # Connecting hosts are occupied too. Unknown hosts must not outrank
    # confirmed waiting hosts; keep both ahead of hosts with an opponent.
    state_order = {"waiting": 0, "unknown": 1, "connecting": 2, "playing": 2}
    return (
        state_order[post_status(post)],
        0 if post.get("post_type") == "ranked" else 1,
        -float(post.get("created_at") or 0),
        str(post.get("id") or ""),
    )
