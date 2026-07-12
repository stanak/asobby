from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import ctypes
import ctypes.wintypes as wt

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

TH32CS_SNAPPROCESS = 0x00000002

# ========================
# Static addresses (AlwaysRecordable準拠)
# ========================
PNETOBJECT = 0x008986A0
COMMMODE = 0x00898690
SCENEID = 0x008A0044

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
# 相手プロフィール名が意味を持つのはネット対戦系シーンのみ
NET_SCENES = {
    SCENE_SELECTSV, SCENE_SELECTCL,
    SCENE_LOADINGSV, SCENE_LOADINGCL, SCENE_LOADINGWATCH,
    SCENE_BATTLESV, SCENE_BATTLECL, SCENE_BATTLEWATCH,
}

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


def get_hisoutensoku_pid_by_process_name(
    exe_names: Tuple[str, ...] = ("th123.exe", "th123_110a.exe"),
) -> Optional[int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wt.HANDLE(-1).value:
        return None

    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(pe))
        want = {n.lower() for n in exe_names}
        while ok:
            name = bytes(pe.szExeFile).split(b"\x00", 1)[0].decode("cp932", "ignore")
            if name.lower() in want:
                return int(pe.th32ProcessID)
            ok = kernel32.Process32Next(snap, ctypes.byref(pe))
        return None
    finally:
        kernel32.CloseHandle(snap)


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

def list_modules_toolhelp(pid: int) -> list[str]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wt.HANDLE(-1).value:
        return []
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        ok = kernel32.Module32First(snap, ctypes.byref(me))
        mods: list[str] = []
        while ok:
            name = bytes(me.szModule).split(b"\x00", 1)[0].decode("cp932", "ignore")
            mods.append(name)
            ok = kernel32.Module32Next(snap, ctypes.byref(me))
        return mods
    finally:
        kernel32.CloseHandle(snap)


def detect_tools_from_loaded_modules(pid: int) -> tuple[bool, bool]:
    mods = [m.lower() for m in list_modules_toolhelp(pid)]
    giu = any("giuroll" in m for m in mods)
    ap  = any("autopunch" in m for m in mods)
    return giu, ap


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
    """PBATTLEMGR ポインタを解決する。giuroll 時は代替アドレスも試す。"""
    ptr = _read_u32le(h, PBATTLEMGR)
    if ptr and is_readable_ptr(h, ptr):
        return ptr
    if giuroll:
        ptr = _read_u32le(h, PBATTLEMGR_GIUROLL)
        if ptr and is_readable_ptr(h, ptr):
            return ptr
    return None


def _read_battle_result(
    h: wt.HANDLE, giuroll: bool
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """(btl_mode, lwin, rwin) を返す。読めない場合は各要素 None。"""
    btl = _resolve_battle_mgr(h, giuroll)
    if not btl:
        return (None, None, None)

    btl_mode = _read_u32le(h, btl + BTLMODEOFS)

    lchar = _read_u32le(h, btl + LCHAROFS)
    lwin: Optional[int] = None
    if lchar and is_readable_ptr(h, lchar):
        lwin = _read_u8(h, lchar + WINCNTOFS)

    rchar = _read_u32le(h, btl + RCHAROFS)
    rwin: Optional[int] = None
    if rchar and is_readable_ptr(h, rchar):
        rwin = _read_u8(h, rchar + WINCNTOFS)

    return (btl_mode, lwin, rwin)


def _decide_mode(server08: Optional[int], server09: Optional[int], scene_id: Optional[int]) -> str:
    # 対戦 (シーン ID で確定。giuroll はシーン遷移を差し替えないため
    # MOD 有無に関わらず安定して判定できる)
    if scene_id in NET_BATTLE_SCENES:
        return "battle"

    if scene_id in NET_CHARSEL_SCENES:
        return "charsel"

    if server08 is None or server09 is None:
        return "idle"

    # 募集（確定）
    if server08 == 513 and server09 == 2:
        return "host_wait"

    # キャラセレ（確定）
    if server08 == 65281 and server09 == 255:
        return "charsel"

    return "other"


# ========================
# main public API
# ========================


def read_detection_state() -> DetectionState:
    pid = get_hisoutensoku_pid_by_process_name()
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
        )

    try:
        h = _open_process(pid)
    except Exception:
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
        )

    try:
        giu, ap = detect_tools_from_loaded_modules(pid)

        scene_id = _read_u32le(h, SCENEID)

        pnet = _read_u32le(h, PNETOBJECT)
        lprof = rprof = ""
        server = None
        if pnet:
            lprof = _sanitize_profile(_read_cpsz_cp932(h, pnet + LPROFOFS, PROFSZ))
            rprof = _sanitize_profile(_read_cpsz_cp932(h, pnet + RPROFOFS, PROFSZ))
            # 相手プロフィールはネット対戦系シーン以外では意味を持たない
            # (giuroll 環境では未初期化のゴミが残ることがある)
            if scene_id not in NET_SCENES:
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
        mode = _decide_mode(server08, server09, scene_id)

        lcid = _read_u32le(h, LCHARID)
        rcid = _read_u32le(h, RCHARID)

        btl_mode = lwin = rwin = None
        if scene_id in NET_BATTLE_SCENES:
            btl_mode, lwin, rwin = _read_battle_result(h, giu)

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
            btl_mode=btl_mode,
            lwin=lwin,
            rwin=rwin,
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
