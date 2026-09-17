"""Guard failures are tested anywhere; real OS exclusion is tested on Windows."""
from __future__ import annotations

import ctypes
import queue
import subprocess
import sys
import threading
import types
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import single_instance as mod


@pytest.fixture
def api(monkeypatch):
    state = {"error": 0, "handle": 0x123456789ABC, "create_error": 0}
    def create(*args):
        state["error"] = state["create_error"]
        return state["handle"]
    def win_error(code):
        error = OSError(code, "Windows lock error")
        error.winerror = code
        return error
    kernel = Mock()
    kernel.CreateMutexW.side_effect = create
    kernel.CloseHandle.return_value = True
    monkeypatch.setattr(mod, "_load_kernel32", lambda: kernel)
    monkeypatch.setattr(ctypes, "set_last_error", lambda code: state.update(error=code), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["error"], raising=False)
    monkeypatch.setattr(ctypes, "WinError", win_error, raising=False)
    return kernel, state


def test_win32_signatures_keep_full_64_bit_handles_and_saved_errors(monkeypatch):
    kernel = Mock()
    loader = Mock(return_value=kernel)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    assert mod._load_kernel32() is kernel
    loader.assert_called_once_with("kernel32", use_last_error=True)
    assert kernel.CreateMutexW.restype is mod.wintypes.HANDLE
    assert kernel.CreateMutexW.argtypes == [mod.wintypes.LPVOID, mod.wintypes.BOOL, mod.wintypes.LPCWSTR]
    assert kernel.CloseHandle.argtypes == [mod.wintypes.HANDLE]


def test_lock_lives_until_close_and_close_is_idempotent(api):
    kernel, state = api
    state["error"] = mod.ERROR_ALREADY_EXISTS  # Discard a stale error.
    guard = mod.SingleInstance()
    assert guard.acquire()
    kernel.CreateMutexW.assert_called_once_with(None, False, "Local\\asobby_client_mutex")
    kernel.CloseHandle.assert_not_called()
    with pytest.raises(RuntimeError):
        guard.acquire()
    guard.close()
    guard.close()
    kernel.CloseHandle.assert_called_once_with(state["handle"])
    assert guard.acquire()  # Can acquire again after a normal close.
    guard.close()


def test_existing_instance_closes_only_its_own_handle_and_never_starts(api, monkeypatch):
    kernel, state = api
    state["create_error"] = mod.ERROR_ALREADY_EXISTS
    # Other API calls can clobber last error; classification must be captured.
    kernel.CloseHandle.side_effect = lambda handle: state.update(error=0) or True
    notice, start = Mock(), Mock()
    monkeypatch.setattr(mod, "_show_notice", notice)
    assert mod.run_single_instance(start) == 0
    start.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(state["handle"])
    assert "既に起動" in notice.call_args.args[0]


@pytest.mark.parametrize("code", [0, 5, 6, 8, 183])
def test_all_lock_creation_failures_stop_before_app_initialization(api, monkeypatch, code):
    kernel, state = api
    state.update(handle=None, create_error=code)
    notice, start = Mock(), Mock()
    monkeypatch.setattr(mod, "_show_notice", notice)
    assert mod.run_single_instance(start) == 1
    start.assert_not_called()
    kernel.CloseHandle.assert_not_called()
    assert notice.call_args.kwargs == {"error": True}
    assert "起動を中止" in notice.call_args.args[0]


@pytest.mark.parametrize("crash", [False, True])
def test_guard_is_held_during_startup_and_released_even_on_exception(api, crash):
    kernel, _ = api
    def start():
        kernel.CreateMutexW.assert_called_once()
        kernel.CloseHandle.assert_not_called()
        if crash:
            raise ValueError("startup failed")
    if crash:
        with pytest.raises(ValueError, match="startup failed"):
            mod.run_single_instance(start)
    else:
        assert mod.run_single_instance(start) == 0
    kernel.CloseHandle.assert_called_once()


def test_guard_initialization_failure_stops_startup(monkeypatch):
    monkeypatch.setattr(mod, "_load_kernel32", Mock(side_effect=OSError("DLL unavailable")))
    notice, start = Mock(), Mock()
    monkeypatch.setattr(mod, "_show_notice", notice)
    assert mod.run_single_instance(start) == 1
    start.assert_not_called()
    assert notice.call_args.kwargs == {"error": True}


@pytest.mark.parametrize("error", [False, True])
def test_notice_stays_visible_and_destroys_temporary_root(monkeypatch, error):
    root, dialogs = Mock(), Mock()
    monkeypatch.setitem(sys.modules, "tkinter", types.SimpleNamespace(Tk=lambda: root, messagebox=dialogs))
    mod._show_notice("notice", error=error)
    root.withdraw.assert_called_once()
    root.attributes.assert_called_once_with("-topmost", True)
    (dialogs.showerror if error else dialogs.showinfo).assert_called_once_with("asobby", "notice", parent=root)
    root.destroy.assert_called_once()


def test_entrypoint_guards_app_construction():
    # Inspect the packaged entrypoint without importing Windows UI/detectors.
    import ast
    tree = ast.parse((Path(mod.__file__).with_name("main.py")).read_text(encoding="utf-8"))
    entry = tree.body[-1]
    assert isinstance(entry, ast.If) and isinstance(entry.body[-1], ast.Raise)
    call = entry.body[-1].exc.args[0]
    assert call.func.id == "run_single_instance"
    assert isinstance(call.args[0], ast.Lambda)
    assert ast.unparse(call.args[0].body) == "TrayApp().run()"


CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from single_instance import SingleInstance
guard = SingleInstance(sys.argv[2])
try:
    acquired = guard.acquire()
    print('held' if acquired else 'busy', flush=True)
    if acquired:
        sys.stdin.readline()
finally:
    guard.close()
"""


def spawn(name, *, cwd=None):
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(Path(mod.__file__).parent), name],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=cwd,
    )


def read_line(process):
    result = queue.Queue()
    threading.Thread(target=lambda: result.put(process.stdout.readline().strip()), daemon=True).start()
    return result.get(timeout=10)


def finish(process):
    try:
        process.communicate("\n" if process.poll() is None else None, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()  # Only the test-owned process, never a running client.
        process.communicate(timeout=10)
        raise


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows kernel required")
@pytest.mark.parametrize("forced", [False, True])
def test_windows_two_processes_and_restart_after_normal_or_forced_exit(tmp_path, forced):
    name = mod.MUTEX_NAME + "_test_" + uuid.uuid4().hex
    owner = spawn(name)
    other = None
    try:
        assert read_line(owner) == "held"
        other = spawn(name, cwd=tmp_path)  # Working directory must not matter.
        output, errors = other.communicate(timeout=10)
        assert other.returncode == 0 and output.strip() == "busy", errors
        if forced:
            owner.terminate()
            owner.communicate(timeout=10)
        else:
            finish(owner)
        replacement = spawn(name, cwd=tmp_path)
        try:
            assert read_line(replacement) == "held"
        finally:
            finish(replacement)
    finally:
        finish(owner)
        if other is not None:
            finish(other)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows kernel required")
def test_windows_simultaneous_start_has_exactly_one_owner():
    name = mod.MUTEX_NAME + "_test_" + uuid.uuid4().hex
    processes = []
    try:
        for _ in range(6):
            processes.append(spawn(name))
        answers = [read_line(process) for process in processes]
        assert answers.count("held") == 1 and answers.count("busy") == 5
    finally:
        for process in processes:
            finish(process)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows kernel required")
def test_windows_compatible_with_existing_legacy_named_mutex():
    name = mod.MUTEX_NAME + "_test_" + uuid.uuid4().hex
    kernel = mod._load_kernel32()
    legacy_handle = kernel.CreateMutexW(None, False, name)
    assert legacy_handle
    guard = mod.SingleInstance(name)
    try:
        assert not guard.acquire()
    finally:
        kernel.CloseHandle(legacy_handle)
        guard.close()
    assert guard.acquire()
    guard.close()
