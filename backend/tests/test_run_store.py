"""Run Store tests (build plan 6B.2-6B.6).

The five operations the build plan names — create, get, update, delete, list —
plus the two properties that make the abstraction worth having: any
implementation of the interface can replace the V1 one, and the V1 one is
process memory with no infrastructure behind it.

These tests build their own :class:`InMemoryRunStore` instances rather than
using the application's, exactly as the registry tests build their own
:class:`~app.actions.registry.ActionRegistry`. Two tests deliberately assert
against the application store.

The manifest round-trip, unknown-Run and malformed-ID cases that
`tests/test_storage.py` used to prove against ``manifest.json`` are proved here
against the store that replaced it.

Deliberately filesystem-free — nothing here uses the `runs_dir` fixture.
"""

from __future__ import annotations

import ast
import inspect
from datetime import timezone
from pathlib import Path

import pytest

from app.errors import UnknownRunError
from app.models.run import Run, new_run_id
from app.models.schemas import ActionReference, RunStatus
from app.services import run_store as run_store_module
from app.services.run_store import (
    RUN_STORE,
    DuplicateRunIdError,
    InMemoryRunStore,
    RunStore,
)

ACTION = ActionReference(id="passthrough", version="1.0.0", name="Passthrough")

#: Nothing persistent may be introduced to hold run state (build plan 6B.4).
FORBIDDEN_STORE_IMPORTS: frozenset[str] = frozenset(
    {
        "sqlite3",
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "redis",
        "pymongo",
        "supabase",
        "boto3",
        "duckdb",
        "alembic",
    }
)


@pytest.fixture
def store() -> InMemoryRunStore:
    """A store the test owns outright."""
    return InMemoryRunStore()


def _run(**changes) -> Run:
    return Run.create(ACTION, **changes)


# ---------------------------------------------------------------------------
# 6B.2 / 6B.3 Create and retrieve
# ---------------------------------------------------------------------------


def test_a_run_can_be_created_and_retrieved(store: InMemoryRunStore) -> None:
    run = store.create_run(_run())

    assert store.get_run(run.run_id) == run


def test_create_returns_the_run_it_recorded(store: InMemoryRunStore) -> None:
    run = _run()

    assert store.create_run(run) is run


def test_a_run_is_unknown_until_it_is_created(store: InMemoryRunStore) -> None:
    run = _run()
    assert run.run_id not in store

    store.create_run(run)

    assert run.run_id in store
    assert len(store) == 1


def test_get_run_raises_for_an_unknown_run(store: InMemoryRunStore) -> None:
    with pytest.raises(UnknownRunError) as raised:
        store.get_run(new_run_id())

    assert raised.value.http_status == 404
    assert raised.value.code == "UNKNOWN_RUN"


@pytest.mark.parametrize(
    "malformed", ["", "not-a-uuid", "../../etc/passwd", "4f27d4bb-7464-4d04-a21b"]
)
def test_get_run_raises_for_a_malformed_id(
    store: InMemoryRunStore, malformed: str
) -> None:
    with pytest.raises(UnknownRunError):
        store.get_run(malformed)


def test_a_malformed_id_is_never_recorded(store: InMemoryRunStore) -> None:
    assert "../../etc" not in store


def test_a_run_id_is_matched_case_insensitively(store: InMemoryRunStore) -> None:
    run = store.create_run(_run())

    assert store.get_run(run.run_id.upper()) == run


def test_a_duplicate_run_id_is_rejected_not_silently_overwritten(
    store: InMemoryRunStore,
) -> None:
    original = store.create_run(_run())
    replacement = _run(run_id=original.run_id).with_changes(
        status=RunStatus.SUCCEEDED
    )

    with pytest.raises(DuplicateRunIdError):
        store.create_run(replacement)

    assert store.get_run(original.run_id) == original
    assert len(store) == 1


def test_duplicate_run_id_error_is_a_value_error() -> None:
    assert issubclass(DuplicateRunIdError, ValueError)


# ---------------------------------------------------------------------------
# 6B.2 Update
# ---------------------------------------------------------------------------


def test_update_replaces_the_recorded_state(store: InMemoryRunStore) -> None:
    run = store.create_run(_run())

    updated = store.update_run(run.with_changes(status=RunStatus.SUCCEEDED))

    assert store.get_run(run.run_id) == updated
    assert store.get_run(run.run_id).status is RunStatus.SUCCEEDED
    assert len(store) == 1


def test_update_round_trips_everything_it_was_given(
    store: InMemoryRunStore,
) -> None:
    run = store.create_run(_run())
    final = run.with_changes(
        status=RunStatus.SUCCEEDED, duration_ms=42, metrics={"rows": 7}
    )

    store.update_run(final)
    reloaded = store.get_run(run.run_id)

    assert reloaded.run_id == run.run_id
    assert reloaded.action == ACTION
    assert reloaded.duration_ms == 42
    assert reloaded.metrics == {"rows": 7}
    assert reloaded.created_at.tzinfo is timezone.utc


def test_update_raises_for_a_run_that_was_never_created(
    store: InMemoryRunStore,
) -> None:
    with pytest.raises(UnknownRunError):
        store.update_run(_run())


def test_a_rejected_update_leaves_the_store_unchanged(
    store: InMemoryRunStore,
) -> None:
    recorded = store.create_run(_run())

    with pytest.raises(UnknownRunError):
        store.update_run(_run(run_id=new_run_id()))

    assert store.list_runs() == [recorded]


# ---------------------------------------------------------------------------
# 6B.6 Delete
# ---------------------------------------------------------------------------


def test_a_run_can_be_deleted(store: InMemoryRunStore) -> None:
    run = store.create_run(_run())

    assert store.delete_run(run.run_id) is True
    assert run.run_id not in store
    assert store.list_runs() == []

    with pytest.raises(UnknownRunError):
        store.get_run(run.run_id)


def test_deleting_twice_reports_that_there_was_nothing_left(
    store: InMemoryRunStore,
) -> None:
    run = store.create_run(_run())
    store.delete_run(run.run_id)

    assert store.delete_run(run.run_id) is False


def test_deleting_an_unknown_or_malformed_id_is_not_an_error(
    store: InMemoryRunStore,
) -> None:
    assert store.delete_run(new_run_id()) is False
    assert store.delete_run("../../etc") is False


def test_delete_matches_a_run_id_case_insensitively(
    store: InMemoryRunStore,
) -> None:
    run = store.create_run(_run())

    assert store.delete_run(run.run_id.upper()) is True
    assert store.list_runs() == []


def test_deleting_one_run_leaves_the_others(store: InMemoryRunStore) -> None:
    kept = store.create_run(_run())
    removed = store.create_run(_run())

    store.delete_run(removed.run_id)

    assert store.list_runs() == [kept]


# ---------------------------------------------------------------------------
# 6B.2 List
# ---------------------------------------------------------------------------


def test_list_runs_is_empty_for_a_new_store(store: InMemoryRunStore) -> None:
    assert store.list_runs() == []


def test_list_runs_returns_every_run_oldest_first(
    store: InMemoryRunStore,
) -> None:
    created = [store.create_run(_run()) for _ in range(3)]

    assert store.list_runs() == created


def test_list_runs_reflects_updates(store: InMemoryRunStore) -> None:
    run = store.create_run(_run())
    store.update_run(run.with_changes(status=RunStatus.FAILED))

    (listed,) = store.list_runs()
    assert listed.status is RunStatus.FAILED


def test_the_returned_list_is_a_copy(store: InMemoryRunStore) -> None:
    store.create_run(_run())

    store.list_runs().clear()

    assert len(store.list_runs()) == 1


def test_two_stores_are_independent() -> None:
    first, second = InMemoryRunStore(), InMemoryRunStore()
    run = first.create_run(_run())

    assert second.list_runs() == []
    with pytest.raises(UnknownRunError):
        second.get_run(run.run_id)


# ---------------------------------------------------------------------------
# 6B.5 The interface is what business logic depends on
# ---------------------------------------------------------------------------


def test_the_store_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        RunStore()  # pyright: ignore[reportAbstractUsage]


def test_the_interface_declares_exactly_the_five_operations() -> None:
    declared = {
        name
        for name, member in vars(RunStore).items()
        if getattr(member, "__isabstractmethod__", False)
    }

    assert declared == {
        "create_run",
        "get_run",
        "update_run",
        "delete_run",
        "list_runs",
    }


def test_another_implementation_can_replace_the_in_memory_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a future PersistentRunStore does: implement five methods, and be
    assigned. Nothing above the store changes."""
    calls: list[str] = []

    class _RecordingStore(RunStore):
        def __init__(self) -> None:
            self._inner = InMemoryRunStore()

        def create_run(self, run: Run) -> Run:
            calls.append("create_run")
            return self._inner.create_run(run)

        def get_run(self, run_id: str) -> Run:
            calls.append("get_run")
            return self._inner.get_run(run_id)

        def update_run(self, run: Run) -> Run:
            calls.append("update_run")
            return self._inner.update_run(run)

        def delete_run(self, run_id: str) -> bool:
            calls.append("delete_run")
            return self._inner.delete_run(run_id)

        def list_runs(self) -> list[Run]:
            calls.append("list_runs")
            return self._inner.list_runs()

    monkeypatch.setattr(run_store_module, "RUN_STORE", _RecordingStore())

    run = run_store_module.create_run(_run())
    run_store_module.update_run(run.with_changes(status=RunStatus.SUCCEEDED))
    assert run_store_module.get_run(run.run_id).status is RunStatus.SUCCEEDED
    assert run_store_module.list_runs()
    assert run_store_module.delete_run(run.run_id) is True

    assert calls == [
        "create_run",
        "update_run",
        "get_run",
        "list_runs",
        "delete_run",
    ]


def test_the_module_functions_read_the_application_store(
    run_store: InMemoryRunStore,
) -> None:
    """The `run_store` fixture is the application store for this test."""
    run = run_store_module.create_run(_run())

    assert run.run_id in run_store
    assert run_store_module.get_run(run.run_id) == run
    assert run_store_module.list_runs() == [run]


def test_the_application_store_is_an_in_memory_store() -> None:
    assert isinstance(RUN_STORE, InMemoryRunStore)


# ---------------------------------------------------------------------------
# 6B.4 No persistent infrastructure was introduced
# ---------------------------------------------------------------------------


def test_the_run_store_imports_no_database(store: InMemoryRunStore) -> None:
    tree = ast.parse(inspect.getsource(run_store_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & FORBIDDEN_STORE_IMPORTS


def test_v1_run_state_does_not_survive_the_process() -> None:
    """Build plan Phase 6 rules 14-15: memory is the V1 store, and a restart
    legitimately clears run history. A fresh store stands in for a restart."""
    before = InMemoryRunStore()
    before.create_run(_run())

    after = InMemoryRunStore()

    assert after.list_runs() == []


def test_no_run_state_reaches_the_filesystem(
    store: InMemoryRunStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config

    monkeypatch.setattr(config, "DATA_DIRECTORY", tmp_path / "nowhere")
    monkeypatch.setattr(config, "RUNS_DIRECTORY", tmp_path / "nowhere" / "runs")

    run = store.create_run(_run())
    store.update_run(run.with_changes(status=RunStatus.SUCCEEDED))
    store.delete_run(run.run_id)

    assert not (tmp_path / "nowhere").exists()