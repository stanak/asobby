from typing import Optional

# SWRSSCENE (SokuLib Scenes.hpp 準拠)
SCENE_SELECTSV = 8
SCENE_SELECTCL = 9
SCENE_LOADINGSV = 10
SCENE_LOADINGCL = 11
SCENE_LOADINGWATCH = 12
SCENE_BATTLESV = 13
SCENE_BATTLECL = 14
SCENE_BATTLEWATCH = 15
SCENE_INGAME_HOSTLIST = 2

NET_SCENES = {
    SCENE_SELECTSV,
    SCENE_SELECTCL,
    SCENE_LOADINGSV,
    SCENE_LOADINGCL,
    SCENE_LOADINGWATCH,
    SCENE_BATTLESV,
    SCENE_BATTLECL,
    SCENE_BATTLEWATCH,
}


def gate_profiles_for_scene(
    lprof: str,
    rprof: str,
    *,
    scene_id: Optional[int],
    mode: str,
) -> tuple[str, str]:
    """ネット対戦系シーン以外のプロフィール名を整理する。

    相手プロフィール (rprof) は giuroll 環境で未初期化ゴミが残るため、
    ネット対戦系シーン以外では常に空にする。ホスト側 (lprof) は募集中
    (host_wait) だけ Match 列表示用に残す。
    """
    if scene_id in NET_SCENES:
        return lprof, rprof
    rprof = ""
    if mode == "host_wait":
        return lprof, rprof
    return "", rprof
