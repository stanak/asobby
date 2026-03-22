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
PBATTLEMGR = 0x008985E4
PBATTLEMGR_GIUROLL = 0x0047579C
COMMMODE = 0x00898690
SCENEID = 0x008A0044

LCHARID = 0x00899D10
RCHARID = 0x00899D30

LCHAROFS = 0x0C
RCHAROFS = 0x10
WINCNTOFS = 0x573

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

def read_pbattlemgr_ptr(h: wt.HANDLE, giuroll_loaded: bool) -> Optional[int]:
    pbattlemgr = PBATTLEMGR_GIUROLL if giuroll_loaded else PBATTLEMGR
    return _read_u32le(h, pbattlemgr)

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


def _battlemgr_alive(h: wt.HANDLE, giuroll_loaded: bool) -> bool:
    btl = read_pbattlemgr_ptr(h, giuroll_loaded)
    if not btl or btl < 0x10000:
        return False
    p1 = _read_u32le(h, btl + LCHAROFS)
    p2 = _read_u32le(h, btl + RCHAROFS)
    if not p1 or not p2 or p1 < 0x10000 or p2 < 0x10000:
        return False
    # wincntが読めるなら相当“本物”
    w1 = _read_u8(h, p1 + WINCNTOFS)
    w2 = _read_u8(h, p2 + WINCNTOFS)
    return (w1 is not None) and (w2 is not None)


def _decide_mode(server08: Optional[int], server09: Optional[int], battle_alive: bool) -> str:
    # 対戦（PBATTLEMGRで確定）
    if battle_alive:
        return "battle"

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

        pnet = _read_u32le(h, PNETOBJECT)
        lprof = rprof = ""
        server = None
        if pnet:
            lprof = _read_cpsz_cp932(h, pnet + LPROFOFS, PROFSZ)
            rprof = _read_cpsz_cp932(h, pnet + RPROFOFS, PROFSZ)
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
        battle_alive = _battlemgr_alive(h, giu)
        mode = _decide_mode(server08, server09, battle_alive)

        lcid = _read_u32le(h, LCHARID)
        rcid = _read_u32le(h, RCHARID)

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
