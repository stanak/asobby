"""Exercise the Linux wrapper with a fake runner, never with a real Wine prefix."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX launcher")
LAUNCHER = Path(__file__).resolve().parents[1] / "run-wine.sh"


@pytest.fixture
def launch_env(tmp_path):
    prefix = tmp_path / "game prefix"
    prefix.mkdir()
    (prefix / "system.reg").touch()
    exe_dir = tmp_path / "asobby files"
    exe_dir.mkdir()
    exe = exe_dir / "asobby.exe"
    exe.touch()
    runner = tmp_path / "custom wine"
    runner.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$WINEPREFIX" "$ASOBBY_WINE" "$@"\n', encoding="utf-8")
    runner.chmod(0o700)
    env = dict(os.environ, WINEPREFIX=str(prefix), WINE=str(runner))
    return env, exe


def launch(env, args, cwd):
    return subprocess.run(["sh", str(LAUNCHER), *args], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=10)


def test_same_prefix_runner_working_directory_and_quoted_args(launch_env, tmp_path):
    env, exe = launch_env
    # Relative runner/exe paths are resolved before cd; spaces are not split.
    env["WINE"] = "./custom wine"
    result = launch(env, [str(exe.relative_to(tmp_path)), "argument with spaces"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(exe.parent), env["WINEPREFIX"], "1", str(exe), "argument with spaces"]


def test_runner_symlink_entry_point_is_preserved(launch_env, tmp_path):
    env, exe = launch_env
    runner = Path(env["WINE"])
    runner.write_text('#!/bin/sh\nprintf "%s\\n" "$0"\n', encoding="utf-8")
    entry = tmp_path / "wine64"
    entry.symlink_to(runner)
    env["WINE"] = str(entry)
    result = launch(env, [str(exe)], tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(entry)


@pytest.mark.parametrize("kind", ["unset_prefix", "relative_prefix", "new_prefix", "missing_exe", "missing_runner", "missing_arg"])
def test_invalid_input_never_starts_wine(launch_env, tmp_path, kind):
    env, exe = launch_env
    args = [str(exe)]
    if kind == "unset_prefix":
        env.pop("WINEPREFIX")
    elif kind == "relative_prefix":
        env["WINEPREFIX"] = "game prefix"
    elif kind == "new_prefix":
        env["WINEPREFIX"] = str(tmp_path / "must not be created")
    elif kind == "missing_exe":
        args = [str(tmp_path / "missing.exe")]
    elif kind == "missing_runner":
        env["WINE"] = str(tmp_path / "missing wine")
    else:
        args = []
    result = launch(env, args, tmp_path)
    assert result.returncode != 0
    assert result.stderr
    assert not result.stdout
    assert not (tmp_path / "must not be created").exists()
