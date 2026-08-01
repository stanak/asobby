from __future__ import annotations


def should_include_autopunch_in_clipboard(
    post_data: dict,
    *,
    local_autopunch: bool = False,
) -> bool:
    """クリップボードに AutoPunch を含めるか。

    基本はロビーの AP バッジと同じ ``autopunch && !direct_reachable``。
    追加で reachability_uncertain、およびサーバーが direct と誤判定した
    (autopunch=False なのにクライアントは AP 利用) 場合のみ含める。
    """
    autopunch = bool(post_data.get("autopunch"))
    direct_reachable = bool(post_data.get("direct_reachable"))

    if bool(post_data.get("reachability_uncertain")):
        return True
    if autopunch and not direct_reachable:
        return True
    if local_autopunch and direct_reachable and not autopunch:
        return True
    return False
