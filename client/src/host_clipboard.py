from __future__ import annotations


def should_include_autopunch_in_clipboard(
    post_data: dict,
    *,
    local_autopunch: bool = False,
) -> bool:
    """クリップボードに AutoPunch を含めるか。

    ロビーの AP バッジ表示に加え、reachability_uncertain やクライアント側の
    AP 利用 (サーバーが direct_reachable と誤判定した場合) も反映する。
    """
    if bool(post_data.get("reachability_uncertain")):
        return True
    if bool(post_data.get("autopunch")):
        return True
    return bool(local_autopunch)
