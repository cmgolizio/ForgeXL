"""Logical Run model tests (build plan 6B.1, 6B.7, 6D.5).

The Run is runtime state with no filesystem in it. These tests pin four
things: the Run ID convention Phase 3 established is preserved, a Run is a
value that is derived rather than mutated, the public manifest is produced
faithfully from it, and — since Phase 6D — the result tables it carries are
one primary plus any secondaries, never rows in the manifest.

Deliberately filesystem-free — nothing here uses the `runs_dir` fixture.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from app.errors import UnknownRunError
from app.models.run import Run, RunResult, new_run_id, now, parse_run_id
from app.models.schemas import (
    ActionReference,
    InputMetadata,
    OutputMetadata,
    RunError,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)

ACTION = ActionReference(id="passthrough", version="1.0.0", name="Passthrough")


def _run(**changes) -> Run:
    return Run.create(ACTION, **changes)


# ---------------------------------------------------------------------------
# 6B.7 Run identity — the Phase 3 convention, unchanged
# ---------------------------------------------------------------------------


def test_new_run_id_is_a_uuid_string() -> None:
    run_id = new_run_id()

    assert str(uuid.UUID(run_id)) == run_id
    assert parse_run_id(run_id) == run_id


def test_every_run_id_is_distinct() -> None:
    assert len({new_run_id() for _ in range(100)}) == 100


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "not-a-uuid",
        "../../etc/passwd",
        "..",
        "/etc/passwd",
        "4f27d4bb-7464-4d04-a21b",
        "4f27d4bb74644d04a21bdeadbeef0000",
        "4f27d4bb-7464-4d04-a21b-000000000000/../../..",
    ],
)
def test_parse_run_id_rejects_anything_that_is_not_a_uuid(candidate: str) -> None:
    with pytest.raises(UnknownRunError):
        parse_run_id(candidate)


def test_parse_run_id_normalises_case() -> None:
    """Accepted, because it is the same UUID — and returned canonically."""
    canonical = new_run_id()

    assert parse_run_id(canonical.upper()) == canonical


# ---------------------------------------------------------------------------
# 6B.1 A Run is state, not a directory
# ---------------------------------------------------------------------------


def test_a_new_run_starts_running_with_matching_timestamps() -> None:
    run = _run()

    assert run.status is RunStatus.RUNNING
    assert run.created_at == run.updated_at == run.started_at
    assert run.created_at.tzinfo is not None
    assert run.completed_at is None
    assert run.duration_ms is None
    assert run.inputs == ()
    assert run.outputs == ()
    assert run.metrics == {}
    assert run.error is None
    assert run.validation.passed is True


def test_a_new_run_gets_its_own_id() -> None:
    assert _run().run_id != _run().run_id


def test_a_run_id_may_be_supplied() -> None:
    run_id = new_run_id()

    assert _run(run_id=run_id).run_id == run_id


def test_warnings_are_recorded_without_failing_validation() -> None:
    warning = ValidationIssue(code="UNEXPECTED_INPUT", message="ignored")

    run = _run(warnings=(warning,))

    assert run.validation.warnings == (warning,)
    assert run.validation.passed is True


def test_no_run_field_holds_a_filesystem_path() -> None:
    """Build plan 6B.1: no meaningless filesystem paths belong in a Run."""
    fields = dataclasses.fields(Run)

    assert not [f for f in fields if f.type is Path or "Path" in str(f.type)]
    assert not [
        f.name
        for f in fields
        if f.name.endswith(("_path", "_dir", "_directory", "_file"))
    ]


# ---------------------------------------------------------------------------
# Derivation — a Run is never edited in place
# ---------------------------------------------------------------------------


def test_a_run_cannot_be_mutated() -> None:
    run = _run()

    with pytest.raises(dataclasses.FrozenInstanceError):
        run.status = RunStatus.SUCCEEDED  # pyright: ignore[reportAttributeAccessIssue]


def test_with_changes_leaves_the_original_untouched() -> None:
    run = _run()

    derived = run.with_changes(status=RunStatus.SUCCEEDED)

    assert derived is not run
    assert derived.status is RunStatus.SUCCEEDED
    assert run.status is RunStatus.RUNNING


def test_with_changes_preserves_every_unnamed_field() -> None:
    run = _run().with_changes(
        inputs=(
            InputMetadata(
                slot_id="source_file",
                original_filename="sales.csv",
                stored_filename="source.csv",
                file_size_bytes=12,
                extension=".csv",
                row_count=2,
                column_count=1,
                columns=("SKU",),
            ),
        )
    )

    derived = run.with_changes(status=RunStatus.SUCCEEDED)

    assert derived.run_id == run.run_id
    assert derived.action == run.action
    assert derived.created_at == run.created_at
    assert derived.inputs == run.inputs


def test_with_changes_stamps_the_updated_timestamp() -> None:
    run = _run(created_at=now() - timedelta(minutes=5))

    derived = run.with_changes(status=RunStatus.SUCCEEDED)

    assert derived.updated_at > run.updated_at
    assert derived.created_at == run.created_at


def test_an_explicit_updated_timestamp_wins() -> None:
    stamp = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    assert _run().with_changes(updated_at=stamp).updated_at == stamp


# ---------------------------------------------------------------------------
# The public manifest is derived from the Run
# ---------------------------------------------------------------------------


def test_to_manifest_carries_every_recorded_field() -> None:
    created = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
    completed = created + timedelta(milliseconds=42)
    output = OutputMetadata(
        id="result",
        label="Result",
        row_count=2,
        column_count=1,
        columns=("SKU",),
        formats=("csv", "xlsx"),
    )
    run = _run(created_at=created).with_changes(
        status=RunStatus.SUCCEEDED,
        completed_at=completed,
        duration_ms=42,
        outputs=(output,),
        metrics={"duplicates_removed": 3},
    )

    manifest = run.to_manifest()

    assert manifest.run_id == run.run_id
    assert manifest.action == ACTION
    assert manifest.status is RunStatus.SUCCEEDED
    assert manifest.created_at == created
    assert manifest.started_at == created
    assert manifest.completed_at == completed
    assert manifest.duration_ms == 42
    assert manifest.outputs == (output,)
    assert manifest.metrics == {"duplicates_removed": 3}
    assert manifest.error is None
    assert manifest.schema_version == 1


def test_to_manifest_carries_a_failure() -> None:
    issue = ValidationIssue(code="MISSING_COLUMNS", message="missing")
    run = _run().with_changes(
        status=RunStatus.FAILED,
        validation=ValidationSummary(passed=False, errors=(issue,)),
        error=RunError(code="MISSING_COLUMNS", message="missing"),
    )

    manifest = run.to_manifest()

    assert manifest.status is RunStatus.FAILED
    assert manifest.validation.passed is False
    assert manifest.validation.errors == (issue,)
    assert manifest.error is not None
    assert manifest.error.code == "MISSING_COLUMNS"


def test_the_manifest_carries_no_updated_timestamp() -> None:
    """`updated_at` is runtime bookkeeping; the API shape is frozen."""
    assert "updated_at" not in Run.create(ACTION).to_manifest().model_dump()


def test_the_manifest_serialises() -> None:
    payload = json.loads(_run().to_manifest().model_dump_json())

    assert payload["status"] == "running"
    assert payload["action"]["id"] == "passthrough"


def test_the_manifest_metrics_are_a_copy() -> None:
    """A caller editing the manifest cannot reach back into the Run."""
    metrics = {"rows": 1}
    manifest = _run().with_changes(metrics=metrics).to_manifest()

    manifest.metrics["rows"] = 999

    assert metrics == {"rows": 1}

# ---------------------------------------------------------------------------
# 6D.5 One or more result tables
# ---------------------------------------------------------------------------


PRIMARY = pl.DataFrame({"SKU": ["A1", "A2"]})
SECONDARY = pl.DataFrame({"note": ["duplicate"]})


def test_a_single_table_result_needs_no_ceremony() -> None:
    """Build plan 6D.5: multiple results must never be required."""
    result = RunResult.of({"result": PRIMARY})

    assert result.primary_output_id == "result"
    assert result.primary.rows() == PRIMARY.rows()
    assert dict(result.secondary) == {}


def test_the_first_table_given_is_the_primary_one() -> None:
    """The runner builds the mapping in declaration order, so first is first."""
    result = RunResult.of({"main": PRIMARY, "rejects": SECONDARY})

    assert result.primary_output_id == "main"
    assert result.primary.rows() == PRIMARY.rows()
    assert list(result.secondary) == ["rejects"]


def test_a_table_can_be_looked_up_by_output_id() -> None:
    result = RunResult.of({"main": PRIMARY, "rejects": SECONDARY})

    main = result.table("main")
    rejects = result.table("rejects")
    assert main is not None and main.rows() == PRIMARY.rows()
    assert rejects is not None and rejects.rows() == SECONDARY.rows()


def test_an_unknown_output_id_returns_none_rather_than_guessing() -> None:
    result = RunResult.of({"main": PRIMARY})

    assert result.table("rejects") is None
    assert result.table("MAIN") is None
    assert result.table("../main") is None


def test_a_result_must_contain_at_least_one_table() -> None:
    with pytest.raises(ValueError):
        RunResult.of({})


def test_the_primary_output_must_be_one_of_the_tables() -> None:
    with pytest.raises(ValueError):
        RunResult(tables={"main": PRIMARY}, primary_output_id="rejects")


def test_the_tables_cannot_be_added_to_or_removed_from() -> None:
    """A caller must not be able to change what a Run produced."""
    result = RunResult.of({"main": PRIMARY})

    with pytest.raises(TypeError):
        result.tables["rejects"] = SECONDARY  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.primary_output_id = "rejects"  # type: ignore[misc]


def test_the_result_is_not_a_copy_of_the_action_frames() -> None:
    """The Run keeps the Action's own frames; nothing is re-materialised."""
    result = RunResult.of({"main": PRIMARY})

    assert result.primary is PRIMARY


def test_a_run_carries_no_result_until_it_has_one() -> None:
    assert Run.create(ACTION).result is None


def test_a_run_can_carry_its_result() -> None:
    result = RunResult.of({"result": PRIMARY})

    run = Run.create(ACTION).with_changes(result=result)

    assert run.result is result


def test_the_manifest_never_carries_result_rows() -> None:
    """Build plan section 23: the manifest describes results, never their rows."""
    run = Run.create(ACTION).with_changes(result=RunResult.of({"result": PRIMARY}))

    payload = run.to_manifest().model_dump()

    assert "result" not in payload
    assert "A1" not in json.dumps(payload, default=str)