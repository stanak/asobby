"""Runtime detection/import tests also run without a Windows desktop."""
import builtins
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import runtime_compat as compat


@pytest.fixture(autouse=True)
def clear_runtime_cache(monkeypatch):
    compat.wine_version.cache_clear()
    monkeypatch.delenv("ASOBBY_WINE", raising=False)
    yield
    compat.wine_version.cache_clear()


def test_wine_export_uses_cdecl_and_is_cached(monkeypatch):
    get_version = Mock(return_value=b"10.0-test")
    loader = Mock(return_value=SimpleNamespace(wine_get_version=get_version))
    monkeypatch.setattr(compat, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert compat.wine_version() == "10.0-test"
    assert compat.is_wine()
    loader.assert_called_once_with("ntdll.dll")
    get_version.assert_called_once_with()
    assert get_version.argtypes == []
    assert get_version.restype is ctypes.c_char_p


@pytest.mark.parametrize("failure", [OSError("no DLL"), AttributeError("native Windows")])
def test_missing_export_means_native_windows(monkeypatch, failure):
    monkeypatch.setattr(compat, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ctypes, "CDLL", Mock(side_effect=failure))
    monkeypatch.setenv("WINEPREFIX", "/inherited/prefix")
    assert not compat.is_wine()


def test_linux_is_not_native_supported_runtime(monkeypatch):
    monkeypatch.setattr(compat, "sys", SimpleNamespace(platform="linux"))
    loader = Mock()
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert not compat.is_wine()
    loader.assert_not_called()


def test_force_compatibility_without_loading_dll(monkeypatch):
    monkeypatch.setenv("ASOBBY_WINE", "1")
    loader = Mock(side_effect=AssertionError("must not load DLL"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert compat.is_wine()
    loader.assert_not_called()


def load_toast():
    # Do not replace the application's real toast module or leak state to other tests.
    spec = importlib.util.spec_from_file_location("wine_test_toast", Path(compat.__file__).with_name("toast.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wine_does_not_import_winrt_at_all(monkeypatch):
    monkeypatch.setenv("ASOBBY_WINE", "1")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith(("windows_toasts", "winrt")):
            raise AssertionError("Wine must not load WinRT extensions")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    toast = load_toast()
    assert not toast.INTERACTIVE_AVAILABLE
    assert not toast.show_info_toast("test")
    assert not toast.show_request_toast("test", Mock())


@pytest.mark.parametrize("failure", [ImportError("missing"), OSError("DLL failed"), RuntimeError("WinRT failed")])
def test_native_toast_import_failures_are_recoverable(monkeypatch, failure):
    monkeypatch.setattr(compat, "is_wine", lambda: False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "windows_toasts":
            raise failure
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    toast = load_toast()
    assert not toast.INTERACTIVE_AVAILABLE
    assert str(failure) in toast.IMPORT_ERROR


def test_duplicate_instance_notice_points_to_wine_window(monkeypatch):
    import single_instance
    monkeypatch.setenv("ASOBBY_WINE", "1")
    guard = Mock()
    guard.acquire.return_value = False
    monkeypatch.setattr(single_instance, "SingleInstance", Mock(return_value=guard))
    notice, start = Mock(), Mock()
    monkeypatch.setattr(single_instance, "_show_notice", notice)
    assert single_instance.run_single_instance(start) == 0
    start.assert_not_called()
    from i18n import t
    notice.assert_called_once_with(t("wine.already_running"))


def test_wine_lock_failure_still_prevents_startup(monkeypatch):
    import single_instance
    from i18n import t
    monkeypatch.setenv("ASOBBY_WINE", "1")
    monkeypatch.setattr(single_instance, "SingleInstance", Mock(side_effect=OSError("mutex failed")))
    notice, start = Mock(), Mock()
    monkeypatch.setattr(single_instance, "_show_notice", notice)
    assert single_instance.run_single_instance(start) == 1
    start.assert_not_called()
    notice.assert_called_once_with(t("wine.instance_check_failed", error="mutex failed"), error=True)
