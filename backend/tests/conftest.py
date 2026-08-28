"""Fixtures shared by the backend pipeline tests.

Every test that touches storage runs against its own temporary runs directory,
so the suite never reads or writes the real ``data/runs``. Every test also gets
its own Run Store, so run state never leaks from one test into the next.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.actions import registry as registry_module
from app.actions.base import Action
from app.actions.registry import ActionRegistry
from app.main import app
from app.services import run_store as run_store_module
from app.services import storage
from app.services.run_store import InMemoryRunStore


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the runs directory at the one place storage reads it."""
    directory = tmp_path / "runs"
    directory.mkdir()
    monkeypatch.setattr(config, "RUNS_DIRECTORY", directory)
    return directory


@pytest.fixture
def run_paths(runs_dir: Path) -> storage.RunPaths:
    """A created, empty Run directory."""
    return storage.create_run()


@pytest.fixture(autouse=True)
def run_store(monkeypatch: pytest.MonkeyPatch) -> InMemoryRunStore:
    """Give each test its own Run Store.

    Autouse because the application store is process-global: without this a
    Run recorded by one test would still be there for the next one. Swapping
    the single :data:`app.services.run_store.RUN_STORE` instance is also the
    mechanism a persistent implementation would use (build plan 6B.5).
    """
    store = InMemoryRunStore()
    monkeypatch.setattr(run_store_module, "RUN_STORE", store)
    return store


@pytest.fixture
def registered_actions(monkeypatch: pytest.MonkeyPatch):
    """Replace the application registry with one the test controls.

    Lets the Run endpoints be exercised against Actions with known inputs and
    required columns, without depending on which Actions the application
    happens to register today. Build plan Phase 3 explicitly permits a simple
    temporary Action while the two proof Actions do not exist.
    """

    def _register(*actions: Action) -> ActionRegistry:
        registry = ActionRegistry(actions)
        monkeypatch.setattr(registry_module, "ACTION_REGISTRY", registry)
        return registry

    return _register


@pytest.fixture
def client(runs_dir: Path):
    """A client bound to the real application, on an isolated runs directory."""
    with TestClient(app) as test_client:
        yield test_client