"""Result metadata and audit summary, through the pipeline (build plan 6E).

`test_results.py` covers the description of one frame in isolation. This module
covers what a real Run reports: that the metadata reaches the manifest, that
the audit explains what happened, and — the rule that matters most — that none
of it leaks into the user's data (build plan 6E.6).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.errors import RunValidationError
from app.models.schemas import (
    ActionInput,
    ActionOutput,
    ColumnKind,
    RunStatus,
)
from app.services import run_store
from app.services.runner import execute_run

from tests.helpers import csv_bytes, make_action, upload

SALES_HEADER = ["SKU", "Vintage", "Supplier", "Customer"]
SALES_ROWS = [
    ["A1", 2019, "Acme", "North"],
    ["A2", 2020, "Beta", "South"],
    ["A1", 2019, "Acme", "North"],
]


def _sales_csv() -> bytes:
    return csv_bytes(SALES_HEADER, SALES_ROWS)


class _SelectTwo(Action):
    """Selects two of the uploaded columns and reports what it removed."""

    id = "select_two"
    version = "2.1.0"
    name = "Select Two"
    description = "Keeps SKU and Vintage."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs["source_file"]
        kept = source.select(("SKU", "Vintage")).unique(
            keep="first", maintain_order=True
        )
        return ActionResult(
            outputs={"result": kept},
            metrics={"kept": kept.height},
            rows_affected=source.height - kept.height,
        )


class _AddsAColumn(Action):
    id = "adds_a_column"
    version = "1.0.0"
    name = "Adds A Column"
    description = "Adds one derived column."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs["source_file"]
        return ActionResult(
            outputs={"result": source.with_columns(pl.lit(1).alias("Flag"))}
        )


class _TwoTables(Action):
    id = "two_tables"
    version = "1.0.0"
    name = "Two Tables"
    description = "Produces a primary and a secondary result."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (
        ActionOutput(id="kept", label="Kept"),
        ActionOutput(id="dropped", label="Dropped"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs["source_file"]
        kept = source.unique(keep="first", maintain_order=True)
        return ActionResult(
            outputs={"kept": kept, "dropped": source.head(1)},
            rows_affected=source.height - kept.height,
        )


def _run(action: Action, payload: bytes | None = None):
    return execute_run(
        action, {"source_file": upload("sales.csv", payload or _sales_csv())}
    )


# ---------------------------------------------------------------------------
# 6E.1 Result metadata
# ---------------------------------------------------------------------------


def test_a_result_reports_the_rows_the_run_received(runs_dir: Path) -> None:
    (output,) = _run(_SelectTwo()).manifest.outputs

    assert output.input_row_count == 3
    assert output.row_count == 2


def test_a_result_reports_the_columns_it_dropped(runs_dir: Path) -> None:
    (output,) = _run(_SelectTwo()).manifest.outputs

    assert output.columns_removed == ("Supplier", "Customer")
    assert output.columns_added == ()


def test_a_result_reports_a_column_the_action_created(runs_dir: Path) -> None:
    (output,) = _run(_AddsAColumn()).manifest.outputs

    assert output.columns_added == ("Flag",)
    assert output.columns_removed == ()


def test_a_result_reports_its_schema_with_types(runs_dir: Path) -> None:
    (output,) = _run(_SelectTwo()).manifest.outputs

    assert [(c.name, c.kind) for c in output.column_schema] == [
        ("SKU", ColumnKind.TEXT),
        ("Vintage", ColumnKind.NUMBER),
    ]


def test_the_schema_names_match_the_reported_columns(runs_dir: Path) -> None:
    """`columns` and `column_schema` describe the same table, in one order."""
    (output,) = _run(_SelectTwo()).manifest.outputs

    assert tuple(c.name for c in output.column_schema) == output.columns


def test_every_declared_result_table_is_described(runs_dir: Path) -> None:
    outputs = _run(_TwoTables()).manifest.outputs

    assert [output.id for output in outputs] == ["kept", "dropped"]
    assert all(output.input_row_count == 3 for output in outputs)
    assert [output.row_count for output in outputs] == [2, 1]


def test_the_metadata_is_measured_from_the_frame_not_from_the_metrics(
    runs_dir: Path,
) -> None:
    """An Action that reports a wrong count cannot make the metadata wrong."""

    class _Liar(_SelectTwo):
        id = "liar"

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            result = super().run(inputs)
            return ActionResult(
                outputs=result.outputs,
                metrics={"kept": 999},
                rows_affected=result.rows_affected,
            )

    (output,) = _run(_Liar()).manifest.outputs

    assert output.row_count == 2


# ---------------------------------------------------------------------------
# 6E.5 The audit summary
# ---------------------------------------------------------------------------


def test_the_audit_names_the_action_that_executed(runs_dir: Path) -> None:
    audit = _run(_SelectTwo()).manifest.audit

    assert audit.action.id == "select_two"
    assert audit.action.version == "2.1.0"
    assert audit.action.name == "Select Two"
    assert audit.status is RunStatus.SUCCEEDED


def test_the_audit_lists_the_inputs_that_were_used(runs_dir: Path) -> None:
    (used,) = _run(_SelectTwo()).manifest.audit.inputs

    assert used.slot_id == "source_file"
    assert used.original_filename == "sales.csv"
    assert used.row_count == 3
    assert used.column_count == 4


def test_the_audit_reports_rows_received_and_returned(runs_dir: Path) -> None:
    audit = _run(_SelectTwo()).manifest.audit

    assert audit.rows_received == 3
    assert audit.rows_returned == 2


def test_the_audit_reports_the_effect_the_action_stated(runs_dir: Path) -> None:
    assert _run(_SelectTwo()).manifest.audit.rows_affected == 1


def test_an_action_that_states_no_effect_reports_null_not_a_guess(
    runs_dir: Path,
) -> None:
    """Build plan section 3.3: an unstated figure is never inferred."""
    audit = _run(_AddsAColumn()).manifest.audit

    assert audit.rows_affected is None
    assert audit.rows_received == 3
    assert audit.rows_returned == 3


def test_rows_returned_is_the_primary_table_not_a_total(runs_dir: Path) -> None:
    audit = _run(_TwoTables()).manifest.audit

    assert audit.primary_result_id == "kept"
    assert audit.rows_returned == 2
    assert [(r.output_id, r.row_count) for r in audit.results] == [
        ("kept", 2),
        ("dropped", 1),
    ]


def test_the_audit_carries_the_actions_own_metrics(runs_dir: Path) -> None:
    assert _run(_SelectTwo()).manifest.audit.metrics == {"kept": 2}


def test_the_audit_reports_the_execution_duration(runs_dir: Path) -> None:
    manifest = _run(_SelectTwo()).manifest

    assert manifest.audit.duration_ms == manifest.duration_ms
    assert manifest.audit.duration_ms is not None


def test_the_audit_carries_the_validation_warnings(runs_dir: Path) -> None:
    outcome = execute_run(
        _SelectTwo(),
        {
            "source_file": upload("sales.csv", _sales_csv()),
            "not_a_slot": upload("extra.csv", _sales_csv()),
        },
    )

    (warning,) = outcome.manifest.audit.warnings
    assert warning.code == "UNEXPECTED_INPUT"


def test_a_failed_run_still_explains_itself(runs_dir: Path) -> None:
    """Build plan 3.9: a failure keeps its evidence, and the audit shows it."""
    strict = make_action("strict", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(strict, {"source_file": upload("sales.csv", _sales_csv())})

    (run,) = run_store.list_runs()
    audit = run.to_manifest().audit

    assert audit.status is RunStatus.FAILED
    assert audit.rows_received == 3
    assert audit.rows_returned is None
    assert audit.rows_affected is None
    assert audit.results == ()
    assert audit.errors[0].code == "MISSING_COLUMNS"


def test_a_running_run_reports_the_state_it_is_actually_in(runs_dir: Path) -> None:
    """A Run explains itself from the moment it exists, not only at the end."""
    from app.models.run import Run
    from app.models.schemas import ActionReference

    run = Run.create(ActionReference(id="a", version="1.0.0", name="A"))
    audit = run.to_manifest().audit

    assert audit.status is RunStatus.RUNNING
    assert audit.rows_received == 0
    assert audit.rows_returned is None
    assert audit.results == ()


def test_the_audit_agrees_with_the_manifest_it_sits_in(runs_dir: Path) -> None:
    """Derived, not recorded: the two can never drift apart."""
    manifest = _run(_SelectTwo()).manifest

    assert manifest.audit.action == manifest.action
    assert manifest.audit.status == manifest.status
    assert manifest.audit.metrics == manifest.metrics
    assert manifest.audit.warnings == manifest.validation.warnings
    assert manifest.audit.errors == manifest.validation.errors
    assert [r.output_id for r in manifest.audit.results] == [
        output.id for output in manifest.outputs
    ]


# ---------------------------------------------------------------------------
# 6E.6 Audit data stays out of the user's data
# ---------------------------------------------------------------------------


def test_no_audit_value_is_added_to_the_result_table(runs_dir: Path) -> None:
    outcome = _run(_SelectTwo())
    assert outcome.result is not None

    frame = outcome.result.primary

    assert frame.columns == ["SKU", "Vintage"]
    for forbidden in (
        "rows_received",
        "rows_returned",
        "rows_affected",
        "run_id",
        "status",
        "duration_ms",
    ):
        assert forbidden not in frame.columns


def test_the_result_table_is_exactly_what_the_action_returned(
    runs_dir: Path,
) -> None:
    outcome = _run(_SelectTwo())
    assert outcome.result is not None

    expected = pl.DataFrame({"SKU": ["A1", "A2"], "Vintage": [2019, 2020]})

    assert outcome.result.primary.equals(expected)


def test_describing_a_result_does_not_alter_it(runs_dir: Path) -> None:
    """Building metadata reads the frame; it must never rewrite it."""
    action = _SelectTwo()
    source = pl.read_csv(_sales_csv())
    expected = action.run({"source_file": source}).outputs["result"]

    outcome = _run(action)
    assert outcome.result is not None

    assert outcome.result.primary.equals(expected)