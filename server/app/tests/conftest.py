"""Shared pytest fixtures for server tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_post_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Avoid persisting/restoring lobby state across tests."""
    monkeypatch.setenv("ASOBBY_STORE", "memory")
    monkeypatch.setenv("ASOBBY_STORE_DIR", str(tmp_path / "asobby_store"))
