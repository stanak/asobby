"""Import the app from only the sources declared by the runtime Docker COPY.

No Docker daemon, production DB, network, or ASGI lifespan is involved.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def test_docker_runtime_copies_complete_importable_application(tmp_path):
    source = Path(__file__).resolve().parents[1]
    stage = tmp_path / "image"
    stage.mkdir()
    runtime = (source / "Dockerfile").read_text(encoding="utf-8").rsplit("FROM ", 1)[1]
    for line in runtime.splitlines():
        words = shlex.split(line, comments=True)
        if not words or words[0] != "COPY" or words[1].startswith("--from="):
            continue
        destination = stage / words[-1]
        for pattern in words[1:-1]:
            matches = list(source.glob(pattern))
            assert matches, f"Missing Docker COPY source: {pattern}"
            for path in matches:
                if path.is_dir():
                    shutil.copytree(path, destination, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"))
                else:
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination / path.name)
    expected_modules = {p.name for p in source.glob("*.py")}
    assert {p.name for p in stage.glob("*.py")} == expected_modules
    assert (stage / "alembic.ini").is_file()
    assert (stage / "migrations" / "env.py").is_file()
    assert (stage / "static" / "index.html").is_file()
    result = subprocess.run(
        [sys.executable, "-c", "import main, player_profiles, match_identity, lobby_order; "
         "from pathlib import Path; "
         "assert Path(main.__file__).parent == Path.cwd(); assert main.app.routes"],
        cwd=stage,
        env=dict(os.environ, PYTHONPATH=str(stage), DATABASE_URL="", ASOBBY_STORE="memory", ASOBBY_HOSTCHECK="off"),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
