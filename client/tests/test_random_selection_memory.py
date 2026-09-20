import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Win32 memory reader", allow_module_level=True)

import hisoutensoku_memory as memory


@pytest.mark.parametrize("scene_id", [memory.SCENE_SELECTSV, memory.SCENE_SELECTCL])
def test_select_snapshot_uses_verified_offsets(monkeypatch, scene_id):
    values = {memory.PCURRENTSCENE: 0x1000, memory.SCENEID: scene_id,
              memory.LCHARID: 20, memory.RCHARID: 5}
    monkeypatch.setattr(memory, "_read_u32le", lambda h, addr: values[addr])
    monkeypatch.setattr(memory, "_read_bytes", lambda h, addr, size: b"\x03\x03" if (addr, size) == (0x32C0, 2) else None)
    snapshot = memory._read_character_selection(1, scene_id)
    assert (snapshot.left_char, snapshot.right_char) == (20, 5)
    assert (snapshot.left_stage, snapshot.right_stage) == (3, 3)


@pytest.mark.parametrize("failure", ["null_pointer", "changed_scene", "changed_pointer", "bad_char", "unreadable", "bad_stage", "changed_stage"])
def test_invalid_or_torn_snapshot_is_unknown(monkeypatch, failure):
    values = {memory.PCURRENTSCENE: 0 if failure == "null_pointer" else 0x1000,
              memory.SCENEID: 13 if failure == "changed_scene" else 8,
              memory.LCHARID: 255 if failure == "bad_char" else 20, memory.RCHARID: 5}
    reads = []
    def read_u32(h, addr):
        reads.append(addr)
        if failure == "changed_pointer" and reads.count(addr) > 1 and addr == memory.PCURRENTSCENE:
            return 0x2000
        return values[addr]
    count = [0]
    def read_bytes(h, addr, size):
        count[0] += 1
        if failure == "unreadable":
            return None
        if failure == "bad_stage":
            return b"\xff\x03"
        if failure == "changed_stage" and count[0] > 1:
            return b"\x00\x03"
        return b"\x03\x03"
    monkeypatch.setattr(memory, "_read_u32le", read_u32)
    monkeypatch.setattr(memory, "_read_bytes", read_bytes)
    assert memory._read_character_selection(1, 8) is None


@pytest.mark.parametrize("scene", [None, 2, 3, 10, 11, 12, 13, 14, 15])
def test_never_reads_select_object_outside_online_select(monkeypatch, scene):
    def forbidden(*args):
        pytest.fail("must not dereference Select here")
    monkeypatch.setattr(memory, "_read_u32le", forbidden)
    assert memory._read_character_selection(1, scene) is None
