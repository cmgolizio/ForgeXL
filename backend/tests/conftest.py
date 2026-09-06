"""Fixtures shared by the backend pipeline tests.

Every test gets its own Run Store, so run state never leaks from one test into
the next.

Until Phase 6I this module also owned a ``runs_dir`` fixture that redirected
``config.RUNS_DIRECTORY`` at a temporary directory, so the suite could never
touch the real ``data/runs``, and a ``run_paths`` fixture that created a Run
directory. Both settings and both directories are gone: the backend has no
configured place to write (build plan 6I.1). :func:`quarantine` replaced them
and guards the one place a stray write could still land.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.actions import registry as registry_module
from app.actions.base import Action
from app.actions.registry import ActionRegistry
from app.main import app
from app.services import run_store as run_store_module
from app.services.run_store import InMemoryRunStore


@pytest.fixture(autouse=True)
def quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty directory the test runs inside, which must stay empty.

    This is the replacement for the ``runs_dir`` redirect. Since Phase 6I the
    backend reads no data-directory setting, so there is no configured path
    left to point somewhere harmless — which is the point. What remains is a
    *relative* path resolved against the process working directory, so the
    working directory is pointed at this empty one and a test that cares
    asserts it is still empty afterwards.

    Autouse, because that is what the fixture it replaces was doing. Over a
    hundred tests declared ``runs_dir`` without ever reading it, purely to buy
    the redirect; dropping the parameter from all of them would have quietly
    removed that protection. Requesting it by name still returns this same
    directory, for the tests that assert on it.

    The other two places a stray write could land are covered elsewhere and
    deliberately not duplicated here: the OS temporary directory, by the
    ``tempfile`` spies in ``test_export.py`` and ``test_upload_form.py``, and
    an absolute path written into the source, by ``test_contract_freeze.py``.
    """
    directory = tmp_path / "quarantine"
    directory.mkdir()
    monkeypatch.chdir(directory)
    return directory


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
def client(quarantine: Path):
    """A client bound to the real application, inside an empty directory."""
    with TestClient(app) as test_client:
        yield test_client