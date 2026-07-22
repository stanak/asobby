from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import ctypes
import ctypes.wintypes as wt
import ntpath
import os
import time

from detect_api import DetectionState

import logging
logger = logging.getLogger(__name__)


# ========================
# WinAPI types (env-safe)
# ========================
SIZE_T = getattr(wt, "SIZE_T", ctypes.c_size_t)
ULONG_PTR = getattr(wt, "ULONG_PTR", ctypes.c_size_t)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TH32CS_SNAPPROCESS = 0x00000002

# ========================
# Static addresses (AlwaysRecordable準拠)
# ========================
PNETOBJECT = 0x008986A0
COMMMODE = 0x00898690
SCENEID = 0x008A0044

# COMMMODE の値 (天則観 SWRSAddrDef.h 準拠)
COMM_SERVER = 4
COMM_CLIENT = 5
COMM_WATCH = 6

# SWRSSCENE (SokuLib Scenes.hpp 準拠)。シーン遷移は giuroll が
# 差し替えないため、MOD 有無に依存しない対戦判定に使える。
SCENE_SELECTSV = 8
SCENE_SELECTCL = 9
SCENE_LOADINGSV = 10
SCENE_LOADINGCL = 11
SCENE_LOADINGWATCH = 12
SCENE_BATTLESV = 13
SCENE_BATTLECL = 14
SCENE_BATTLEWATCH = 15

NET_BATTLE_SCENES = {SCENE_BATTLESV, SCENE_BATTLECL, SCENE_BATTLEWATCH}
NET_CHARSEL_SCENES = {SCENE_SELECTSV, SCENE_SELECTCL}
NET_LOADING_SCENES = {SCENE_LOADINGSV, SCENE_LOADINGCL, SCENE_LOADINGWATCH}
NET_RESULT_SCENES = NET_BATTLE_SCENES | NET_CHARSEL_SCENES | NET_LOADING_SCENES
NET_SIDE_HOST = {SCENE_SELECTSV, SCENE_LOADINGSV, SCENE_BATTLESV}
NET_SIDE_CLIENT = {SCENE_SELECTCL, SCENE_LOADINGCL, SCENE_BATTLECL}
NET_SIDE_WATCH = {SCENE_LOADINGWATCH, SCENE_BATTLEWATCH}
# 相手プロフィール名が意味を持つのはネット対戦系シーンのみ
NET_SCENES = {
    SCENE_SELECTSV, SCENE_SELECTCL,
    SCENE_LOADINGSV, SCENE_LOADINGCL, SCENE_LOADINGWATCH,
    SCENE_BATTLESV, SCENE_BATTLECL, SCENE_BATTLEWATCH,
}

# InGameHostlist はバニラのネットメニューを scene 2 で差し替える。
# ホスト待機中も scene 8 / COMM_SERVER にならず、server シグネチャも
# バニラの (513, 2) ではなく (1280, 5) になる (実測)。
SCENE_INGAME_HOSTLIST = 2
IGH_RECRUIT_S08 = 1280
IGH_RECRUIT_S09 = 5

LCHARID = 0x00899D10
RCHARID = 0x00899D30

# PNET profile name offsets
LPROFOFS = 0x04
RPROFOFS = 0x24
PROFSZ = 0x20

# PNET -> SERVER chain
ADRBEGOFS = 0x4C8
SERVEROFS = 0x04

# SERVER
SERVER_PORT_OFF = 0x428  # u16le
SERVER08_OFF = 0x08  # u16le: 513=募集 / 114=client connect mode / それ以外 None等
SERVER09_OFF = 0x09      # u8
SERVER_PHASE_OFF = 0x11A0  # u8

# PBATTLEMGR (KO 後の勝利数読み取り用。検出ロジックには使わない)
PBATTLEMGR = 0x008985E4
PBATTLEMGR_GIUROLL = 0x0047579C
LCHAROFS = 0x0C
RCHAROFS = 0x10
BTLMODEOFS = 0x88
WINCNTOFS = 0x573
CHARIDXOFS = 0x34C

# ========================
# Character enum (SWRSSCHAR from SWRSAddrDef.h)
# ========================
CHAR_NAME = {
    0: "Reimu",
    1: "Marisa",
    2: "Sakuya",
    3: "Alice",
    4: "Patchouli",
    5: "Youmu",
    6: "Remilia",
    7: "Yuyuko",
    8: "Yukari",
    9: "Suica",
    10: "Reisen",
    11: "Aya",
    12: "Komachi",
    13: "Iku",
    14: "Tenshi",
    15: "Sanae",
    16: "Cirno",
    17: "Meiling",
    18: "Utsuho",
    19: "Suwako",
    20: "Random",
}


def _char_name(cid: Optional[int]) -> str:
    if cid is None:
        return "?"
    return CHAR_NAME.get(cid, f"CHAR_{cid}")


MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wt.LPVOID),
        ("AllocationBase", wt.LPVOID),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]

kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, wt.LPCVOID, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t

def is_readable_ptr(h: wt.HANDLE, addr: int) -> bool:
    mbi = MEMORY_BASIC_INFORMATION()
    res = kernel32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if not res:
        return False
    if mbi.State != MEM_COMMIT:
        return False
    if mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD):
        return False
    return True

# ========================
# process enumeration
# ========================
class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.CHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32First.restype = wt.BOOL
kernel32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32Next.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


DEFAULT_EXE_NAMES: Tuple[str, ...] = ("th123.exe", "th123_110a.exe")

# 設定済みの soku exe パス (set soku path)。空でなければ、その exe 名も
# プロセス探索の候補に加え、フルパス一致するプロセスを最優先する。
# リネームされた exe や th123.exe が複数いる環境で正しいプロセスを掴むため。
_soku_path_hint: str = ""


def set_soku_path_hint(path: str) -> None:
    global _soku_path_hint
    p = (path or "").strip()
    if p != _soku_path_hint:
        _soku_path_hint = p
        _invalidate_pid_cache()


def _enum_pids_by_names(want: set[str]) -> list[int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wt.HANDLE(-1).value:
        return []

    try:
        pids: list[int] = []
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(pe))
        while ok:
            name = bytes(pe.szExeFile).split(b"\x00", 1)[0].decode("cp932", "ignore")
            if name.lower() in want:
                pids.append(int(pe.th32ProcessID))
            ok = kernel32.Process32Next(snap, ctypes.byref(pe))
        return pids
    finally:
        kernel32.CloseHandle(snap)


def get_hisoutensoku_pid_by_process_name(
    exe_names: Tuple[str, ...] = DEFAULT_EXE_NAMES,
) -> Optional[int]:
    want = {n.lower() for n in exe_names}
    hint = _soku_path_hint
    if hint:
        base = ntpath.basename(hint).lower()
        if base:
            want.add(base)
    pids = _enum_pids_by_names(want)
    if not pids:
        return None
    if hint:
        hint_lower = hint.lower()
        for pid in pids:
            path = _get_exe_path_for_pid(pid)
            if path and path.lower() == hint_lower:
                return pid
    return pids[0]


# ========================
# memory read helpers
# ========================
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE

kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    wt.LPCVOID,
    wt.LPVOID,
    SIZE_T,
    ctypes.POINTER(SIZE_T),
]
kernel32.ReadProcessMemory.restype = wt.BOOL

kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL


def _get_process_exe_path(h: wt.HANDLE) -> str:
    """プロセスハンドルから exe フルパスを取得する。"""
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    if not ok:
        return ""
    return buf.value


def _get_exe_path_for_pid(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        return _get_process_exe_path(h)
    finally:
        kernel32.CloseHandle(h)


def _open_process(pid: int) -> wt.HANDLE:
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def _read_bytes(h: wt.HANDLE, addr: int, size: int) -> Optional[bytes]:
    buf = (ctypes.c_ubyte * size)()
    read = SIZE_T(0)
    ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(read))
    if not ok and int(read.value) <= 0:
        return None
    n = int(read.value)
    if n <= 0:
        return None
    return bytes(buf[:n])


def _read_u32le(h: wt.HANDLE, addr: int) -> Optional[int]:
    b = _read_bytes(h, addr, 4)
    if not b or len(b) < 4:
        return None
    return int.from_bytes(b[:4], "little", signed=False)


def _read_u16le(h: wt.HANDLE, addr: int) -> Optional[int]:
    b = _read_bytes(h, addr, 2)
    if not b or len(b) < 2:
        return None
    return int.from_bytes(b[:2], "little", signed=False)


def _read_u8(h: wt.HANDLE, addr: int) -> Optional[int]:
    b = _read_bytes(h, addr, 1)
    if not b or len(b) < 1:
        return None
    return b[0]


def _read_cpsz_cp932(h: wt.HANDLE, addr: int, size: int) -> str:
    b = _read_bytes(h, addr, size)
    if not b:
        return ""
    b = b.split(b"\x00", 1)[0]
    try:
        return b.decode("cp932", errors="replace")
    except Exception:
        return ""


# ========================
# dll detection (giuroll/autopunch)
# ========================
TH32CS_SNAPMODULE   = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.CHAR * 256),
        ("szExePath", wt.CHAR * 260),
    ]

kernel32.Module32First.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32)]
kernel32.Module32First.restype  = wt.BOOL
kernel32.Module32Next.argtypes  = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32)]
kernel32.Module32Next.restype   = wt.BOOL

def list_modules_toolhelp(pid: int) -> list[tuple[str, str]]:
    """プロセスのロード済みモジュール (名前, フルパス) を列挙する。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wt.HANDLE(-1).value:
        return []
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        ok = kernel32.Module32First(snap, ctypes.byref(me))
        mods: list[tuple[str, str]] = []
        while ok:
            name = bytes(me.szModule).split(b"\x00", 1)[0].decode("cp932", "ignore")
            path = bytes(me.szExePath).split(b"\x00", 1)[0].decode("cp932", "ignore")
            mods.append((name, path))
            ok = kernel32.Module32Next(snap, ctypes.byref(me))
        return mods
    finally:
        kernel32.CloseHandle(snap)


def detect_tools_from_loaded_modules(pid: int) -> tuple[bool, bool]:
    mods = [n.lower() for n, _ in list_modules_toolhelp(pid)]
    giu = any("giuroll" in m for m in mods)
    ap  = any("autopunch" in m for m in mods)
    return giu, ap


def _has_ingame_hostlist(modules: str) -> bool:
    return "ingamehostlist" in modules.lower()


def _valid_udp_port(port: Optional[int]) -> bool:
    return port is not None and 0 < port < 65536


def _looks_like_igh_host_wait(
    *,
    has_igh: bool,
    scene_id: Optional[int],
    pnet: Optional[int],
    server: Optional[int],
    server08: Optional[int],
    server09: Optional[int],
    port: Optional[int],
) -> bool:
    if not has_igh:
        return False
    if scene_id != SCENE_INGAME_HOSTLIST:
        return False
    if not pnet or not server:
        return False
    if not _valid_udp_port(port):
        return False
    if server08 is None or server09 is None:
        return False
    return server08 == IGH_RECRUIT_S08 and server09 == IGH_RECRUIT_S09


def nonsystem_modules(mods: list[tuple[str, str]]) -> str:
    """システムフォルダ以外から読まれているモジュール名を列挙する。

    ゲームフォルダ同梱の DLL (ラッパー・翻訳パッチ・Mod ローダー等) を
    洗い出す診断用。想定外の DLL がゲーム内部構造を書き換えている
    ケースの調査に使う。
    """
    sysroot = os.environ.get("SystemRoot", r"C:\Windows").lower()
    out = []
    for name, path in mods:
        if path and path.lower().startswith(sysroot):
            continue
        out.append(name)
    return ",".join(out)


# ========================
# pid / module cache
# ========================
# 50ms ポーリングでプロセス列挙・モジュール列挙 (Toolhelp スナップショット) を
# 毎回行うと重いため、結果を短時間キャッシュする。
_PID_CACHE_TTL_SEC = 2.0
_pid_cache: Optional[int] = None
_tools_cache: Tuple[bool, bool] = (False, False)
_exe_path_cache: str = ""
_modules_cache: str = ""
_pid_cache_ts: float = 0.0


def _get_pid_and_tools() -> Tuple[Optional[int], bool, bool, str, str]:
    global _pid_cache, _tools_cache, _exe_path_cache, _modules_cache, _pid_cache_ts
    now = time.monotonic()
    if (now - _pid_cache_ts) < _PID_CACHE_TTL_SEC:
        return _pid_cache, _tools_cache[0], _tools_cache[1], _exe_path_cache, _modules_cache
    pid = get_hisoutensoku_pid_by_process_name()
    mods = list_modules_toolhelp(pid) if pid else []
    lower = [n.lower() for n, _ in mods]
    tools = (
        any("giuroll" in m for m in lower),
        any("autopunch" in m for m in lower),
    )
    modules = nonsystem_modules(mods)
    exe_path = _get_exe_path_for_pid(pid) if pid else ""
    _pid_cache, _tools_cache, _exe_path_cache, _modules_cache, _pid_cache_ts = (
        pid, tools, exe_path, modules, now
    )
    return pid, tools[0], tools[1], exe_path, modules


def _invalidate_pid_cache() -> None:
    global _pid_cache_ts
    _pid_cache_ts = 0.0


def _sanitize_profile(name: str) -> str:
    """プロフィール名らしくない文字列 (未初期化メモリ等) を弾く。

    giuroll はネットワークオブジェクトを差し替えるため、相手不在時の
    rprof 領域に未初期化のゴミが残ることがある。制御文字や cp932 の
    デコード失敗 (置換文字) を含むものはプロフィール名として扱わない。
    """
    if not name:
        return ""
    for ch in name:
        if ord(ch) < 0x20 or ch == "\ufffd":
            return ""
    return name


def _resolve_battle_mgr(h: wt.HANDLE, giuroll: bool) -> Optional[int]:
    """PBATTLEMGR ポインタを解決する。

    giuroll 環境ではバニラの PBATTLEMGR がロールバック中に確定前の値を
    見せることがある (Tsk が同一対戦を多重記録するのと同じ問題) ため、
    giuroll リポジトリの指示どおり 0x0047579C を優先する。
    """
    if giuroll:
        ptr = _read_u32le(h, PBATTLEMGR_GIUROLL)
        if ptr and is_readable_ptr(h, ptr):
            return ptr
    ptr = _read_u32le(h, PBATTLEMGR)
    if ptr and is_readable_ptr(h, ptr):
        return ptr
    return None


def _read_battle_result(
    h: wt.HANDLE, giuroll: bool
) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int], Optional[int]]:
    """(btl_mode, lwin, rwin, battle_lchar_id, battle_rchar_id) を返す。"""
    btl = _resolve_battle_mgr(h, giuroll)
    if not btl:
        return (None, None, None, None, None)

    btl_mode = _read_u32le(h, btl + BTLMODEOFS)

    lchar = _read_u32le(h, btl + LCHAROFS)
    lwin: Optional[int] = None
    lcid: Optional[int] = None
    if lchar and is_readable_ptr(h, lchar):
        lwin = _read_u8(h, lchar + WINCNTOFS)
        lcid = _read_u8(h, lchar + CHARIDXOFS)

    rchar = _read_u32le(h, btl + RCHAROFS)
    rwin: Optional[int] = None
    rcid: Optional[int] = None
    if rchar and is_readable_ptr(h, rchar):
        rwin = _read_u8(h, rchar + WINCNTOFS)
        rcid = _read_u8(h, rchar + CHARIDXOFS)

    return (btl_mode, lwin, rwin, lcid, rcid)


def _decide_mode(
    server08: Optional[int],
    server09: Optional[int],
    scene_id: Optional[int],
    *,
    has_igh: bool = False,
    pnet: Optional[int] = None,
    server: Optional[int] = None,
    port: Optional[int] = None,
) -> str:
    # 対戦 (シーン ID で確定。giuroll はシーン遷移を差し替えないため
    # MOD 有無に関わらず安定して判定できる)
    if scene_id in NET_BATTLE_SCENES:
        return "battle"

    if scene_id in NET_CHARSEL_SCENES:
        return "charsel"

    if scene_id in NET_LOADING_SCENES:
        return "loading"

    if server08 is None or server09 is None:
        return "idle"

    # 募集（確定）
    if server08 == 513 and server09 == 2:
        return "host_wait"

    if _looks_like_igh_host_wait(
        has_igh=has_igh,
        scene_id=scene_id,
        pnet=pnet,
        server=server,
        server08=server08,
        server09=server09,
        port=port,
    ):
        return "host_wait"

    # キャラセレ（確定）
    if server08 == 65281 and server09 == 255:
        return "charsel"

    return "other"


# ========================
# server object scan fallback
# ========================
# pnet+0x4C8 のコンテナ (0x200 バイト) 内での server オブジェクトの位置は
# 環境によってずれることがある (+0x04 決め打ちが通用しない環境が実在する)。
# その場合はコンテナ全域を走査し、募集シグネチャ (+0x08 == 01 02 = s08:513 /
# s09:2) とポートの妥当性で server を特定する。
_VECTOR_SIZE = 0x200
_SCAN_INTERVAL_SEC = 1.0
_scan_last_ts: float = 0.0
_scan_hit: Optional[int] = None


def _looks_like_recruiting_server(h: wt.HANDLE, ptr: int) -> Optional[int]:
    """ptr が募集中の server オブジェクトならポートを返す。"""
    head = _read_bytes(h, ptr, 16)
    if not head or len(head) < 10:
        return None
    if head[8] != 0x01 or head[9] != 0x02:  # s08=513 (0x0201), s09=2
        return None
    pb = _read_bytes(h, ptr + SERVER_PORT_OFF, 2)
    if not pb or len(pb) < 2:
        return None
    port = int.from_bytes(pb, "little")
    if not (0 < port < 65536):
        return None
    return port


def _scan_server_in_vector(h: wt.HANDLE, adrbeg: int) -> Optional[tuple[int, int]]:
    """コンテナ内の各 dword を候補ポインタとして走査する。

    戻り値: (server ポインタ, ポート)。見つからなければ None。
    """
    blob = _read_bytes(h, adrbeg, _VECTOR_SIZE)
    if not blob:
        return None
    seen: set[int] = set()
    for i in range(0, len(blob) - 3, 4):
        c = int.from_bytes(blob[i:i + 4], "little")
        if c in seen:
            continue
        seen.add(c)
        if c % 4 or not (0x10000 <= c <= 0x7FFF0000):
            continue
        port = _looks_like_recruiting_server(h, c)
        if port is not None:
            return c, port
    return None


# ========================
# main public API
# ========================


def _derive_net_side(
    scene_id: Optional[int],
    comm_mode: Optional[int],
    *,
    mode: Optional[str] = None,
) -> Optional[str]:
    """ネット対戦での自分の役割を返す。

    COMMMODE (天則観と同じ判定) を優先する。ホスト同士が凸り合った場合など、
    シーン ID の SV/CL が実際のネットワーク上の役割と食い違うことがあり、
    シーンだけに頼ると my_side が反転して「自分対自分」の戦績になる。
    """
    if scene_id in NET_SCENES:
        if comm_mode == COMM_SERVER:
            return "host"
        if comm_mode == COMM_CLIENT:
            return "client"
        if comm_mode == COMM_WATCH:
            return "watch"
    if scene_id in NET_SIDE_HOST:
        return "host"
    if scene_id in NET_SIDE_CLIENT:
        return "client"
    if scene_id in NET_SIDE_WATCH:
        return "watch"
    # InGameHostlist ホスト待機は scene 2 のまま COMM_SERVER にならない。
    if mode == "host_wait" and scene_id == SCENE_INGAME_HOSTLIST:
        return "host"
    return None


def read_detection_state() -> DetectionState:
    pid, giu, ap, exe_path, modules = _get_pid_and_tools()
    if not pid:
        return DetectionState(
            alive=False,
            mode="idle",
            port=None,
            giuroll=False,
            autopunch=False,
            lprof="",
            rprof="",
            lchar_id=None,
            rchar_id=None,
            lchar_name="?",
            rchar_name="?",
            net_side=None,
            exe_path="",
        )

    try:
        h = _open_process(pid)
    except OSError as e:
        # ERROR_ACCESS_DENIED (5): ゲームが管理者権限で動いていて読めない。
        # プロセス自体は生きているのでキャッシュは維持し、異常として報告する
        if getattr(e, "winerror", None) == 5:
            return DetectionState(
                alive=True,
                mode="idle",
                port=None,
                giuroll=False,
                autopunch=False,
                lprof="",
                rprof="",
                lchar_id=None,
                rchar_id=None,
                lchar_name="?",
                rchar_name="?",
                net_side=None,
                exe_path=exe_path,
                detect_error="access_denied",
            )
        # それ以外はプロセスが終了した直後の可能性が高い。キャッシュを
        # 破棄して次の周期で再列挙させる。
        _invalidate_pid_cache()
        return DetectionState(
            alive=True,
            mode="idle",
            port=None,
            giuroll=False,
            autopunch=False,
            lprof="",
            rprof="",
            lchar_id=None,
            rchar_id=None,
            lchar_name="?",
            rchar_name="?",
            net_side=None,
            exe_path="",
        )

    try:
        has_igh = _has_ingame_hostlist(modules)
        scene_id = _read_u32le(h, SCENEID)
        comm_mode = _read_u32le(h, COMMMODE)

        pnet = _read_u32le(h, PNETOBJECT)
        lprof = rprof = ""
        adrbeg = None
        server = None
        if pnet:
            lprof = _sanitize_profile(_read_cpsz_cp932(h, pnet + LPROFOFS, PROFSZ))
            rprof = _sanitize_profile(_read_cpsz_cp932(h, pnet + RPROFOFS, PROFSZ))
            # プロフィール名はネット対戦系シーン以外では意味を持たない
            # (giuroll 環境では未初期化のゴミが残ることがある)
            if scene_id not in NET_SCENES:
                lprof = ""
                rprof = ""
            adrbeg = _read_u32le(h, pnet + ADRBEGOFS)
            if adrbeg:
                server = _read_u32le(h, adrbeg + SERVEROFS)

        if server:
            port = _read_u16le(h, server + SERVER_PORT_OFF)
            server09 = _read_u8(h, server + SERVER09_OFF)
            server08 = _read_u16le(h, server + SERVER08_OFF)
            # phase = _read_u8(h, server + SERVER_PHASE_OFF) # 何らかにまだ使えるかも
        else:
            port = None
            server09 = None
            server08 = None
            # phase = None

        # +0x04 決め打ちで募集シグネチャが取れない場合のフォールバック走査
        global _scan_last_ts, _scan_hit
        in_host_context = (
            comm_mode == COMM_SERVER
            or scene_id in NET_SIDE_HOST
            or (
                has_igh
                and scene_id == SCENE_INGAME_HOSTLIST
                and pnet
                and adrbeg
            )
        )
        if not in_host_context:
            _scan_hit = None
        elif pnet and adrbeg and not (server08 == 513 and server09 == 2):
            hit_port: Optional[int] = None
            if _scan_hit is not None:
                # 前回ヒットの再検証 (走査より遥かに安い)
                hit_port = _looks_like_recruiting_server(h, _scan_hit)
                if hit_port is None:
                    _scan_hit = None
            if _scan_hit is None:
                now_mono = time.monotonic()
                if now_mono - _scan_last_ts >= _SCAN_INTERVAL_SEC:
                    _scan_last_ts = now_mono
                    found = _scan_server_in_vector(h, adrbeg)
                    if found is not None:
                        _scan_hit, hit_port = found
            if _scan_hit is not None and hit_port is not None:
                server = _scan_hit
                server08, server09, port = 513, 2, hit_port
        elif not pnet:
            _scan_hit = None

        mode = _decide_mode(
            server08,
            server09,
            scene_id,
            has_igh=has_igh,
            pnet=pnet,
            server=server,
            port=port,
        )

        lcid = _read_u32le(h, LCHARID)
        rcid = _read_u32le(h, RCHARID)

        btl_mode = lwin = rwin = battle_lcid = battle_rcid = None
        # KO 確定 (btl_mode==5) は対戦→ロード→キャラセレ遷移の短い間だけ観測できる。
        if scene_id in NET_RESULT_SCENES:
            btl_mode, lwin, rwin, battle_lcid, battle_rcid = _read_battle_result(h, giu)

        raw = (
            f"scene={scene_id} comm={comm_mode} pnet={'y' if pnet else 'n'} "
            f"adr={adrbeg or 0:x} srv={server or 0:x} scan={_scan_hit or 0:x} "
            f"s08={server08} s09={server09} port={port} "
            f"giu={'y' if giu else 'n'} ap={'y' if ap else 'n'} "
            f"igh={'y' if has_igh else 'n'} mode={mode}"
        )

        # 構造診断ダンプ: server チェーンが読めない異常時と、正常な
        # host_wait 時 (比較用ベースライン) に周辺メモリを添える
        dump = ""
        if pnet and adrbeg and (server08 is None or mode == "host_wait"):
            around = _read_bytes(h, pnet + ADRBEGOFS - 0x10, 0x20)
            head = _read_bytes(h, adrbeg, 0x40)
            dump = (
                f"pnet+{ADRBEGOFS - 0x10:x}={(around.hex() if around else '?')} "
                f"adr={(head.hex() if head else '?')}"
            )

        # 将来、対戦募集のランクをどこかから読めたらここに入れる。
        return DetectionState(
            alive=True,
            mode=mode,  # type: ignore
            port=port,
            giuroll=giu,
            autopunch=ap,
            lprof=lprof,
            rprof=rprof,
            lchar_id=lcid,
            rchar_id=rcid,
            lchar_name=_char_name(lcid),
            rchar_name=_char_name(rcid),
            net_side=_derive_net_side(scene_id, comm_mode, mode=mode),
            btl_mode=btl_mode,
            lwin=lwin,
            rwin=rwin,
            battle_lchar_id=battle_lcid,
            battle_rchar_id=battle_rcid,
            exe_path=exe_path,
            raw=raw,
            modules=modules,
            dump=dump,
        )
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    pid = get_hisoutensoku_pid_by_process_name()
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    pnet = _read_u32le(h, PNETOBJECT)
    adrbeg = _read_u32le(h, pnet + ADRBEGOFS) if pnet else None
    server = _read_u32le(h, adrbeg + SERVEROFS) if adrbeg else None

    port  = _read_u16le(h, server + SERVER_PORT_OFF) if server else None
    s09   = _read_u8(h, server + SERVER09_OFF) if server else None
    phase = _read_u8(h, server + SERVER_PHASE_OFF) if server else None

    server08 = _read_u16le(h, server + SERVER08_OFF) if server else None
    server09 = _read_u8(h, server + SERVER09_OFF) if server else None
    print(f"port={port} s09={server09} s08={server08} phase={phase} scene={_read_u32le(h, SCENEID)}")

    print(list_modules_toolhelp(pid))
