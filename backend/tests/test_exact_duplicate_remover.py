"""Exact Duplicate Remover accuracy tests (build plan Phase 4A, section 26).

The controlled dataset and its expected output are defined by hand in
`tests.fixtures.duplicate_rows`. These tests assert exact equality against that
definition — not against anything recomputed here — so a change in the
transformation shows up as a failure rather than as a quietly different result.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from app.actions.exact_duplicate_remover import (
    INPUT_SLOT_ID,
    OUTPUT_ID,
    ExactDuplicateRemoverAction,
)
from app.services.runner import execute_run

from tests.fixtures import duplicate_rows as fixture
from tests.helpers import csv_bytes, upload, xlsx_bytes


@pytest.fixture
def action() -> ExactDuplicateRemoverAction:
    return ExactDuplicateRemoverAction()


@pytest.fixture
def source_frame() -> pl.DataFrame:
    """The fixture as the parser would hand it to the Action."""
    return pl.read_csv(csv_bytes(fixture.HEADER, fixture.ROWS))


# ---------------------------------------------------------------------------
# Declared metadata (build plan section 26)
# ---------------------------------------------------------------------------


def test_the_action_declares_the_specified_identity(action) -> None:
    assert action.id == "exact_duplicate_remover"
    assert action.version == "1.0.0"
    assert action.name == "Exact Duplicate Remover"
    assert action.description


def test_the_action_declares_one_unconstrained_source_slot(action) -> None:
    (slot,) = action.inputs

    assert slot.id == INPUT_SLOT_ID == "source_file"
    assert slot.required is True
    assert slot.accepted_extensions == (".csv", ".xlsx")
    # Section 26: this Action imposes no schema at all.
    assert slot.required_columns == ()


def test_the_action_declares_the_specified_output(action) -> None:
    (output,) = action.outputs

    assert output.id == OUTPUT_ID == "deduplicated_data"
    assert output.label == "Deduplicated Data"
    assert output.formats == ("csv", "xlsx")


# ---------------------------------------------------------------------------
# Exact output (build plan Phase 4A)
# ---------------------------------------------------------------------------


def test_the_output_matches_the_expected_rows_exactly(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.outputs[OUTPUT_ID].rows() == list(fixture.EXPECTED_ROWS)


def test_the_output_preserves_the_column_order_of_the_upload(
    action, source_frame
) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert tuple(result.outputs[OUTPUT_ID].columns) == fixture.HEADER


def test_the_output_preserves_the_dtypes_of_the_upload(action, source_frame) -> None:
    """Deduplication must not retype a column on the way through."""
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.outputs[OUTPUT_ID].schema == source_frame.schema


def test_the_metrics_match_the_expected_counts(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.metrics == {
        "input_rows": fixture.EXPECTED_INPUT_ROWS,
        "output_rows": fixture.EXPECTED_OUTPUT_ROWS,
        "duplicates_removed": fixture.EXPECTED_DUPLICATES_REMOVED,
    }


def test_duplicates_removed_is_the_difference_between_the_row_counts(
    action, source_frame
) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})
    metrics = result.metrics

    assert metrics["input_rows"] == source_frame.height
    assert metrics["output_rows"] == result.outputs[OUTPUT_ID].height
    assert (
        metrics["duplicates_removed"]
        == metrics["input_rows"] - metrics["output_rows"]
    )


def test_the_upload_is_not_mutated(action, source_frame) -> None:
    before = source_frame.rows()

    action.run({INPUT_SLOT_ID: source_frame})

    assert source_frame.rows() == before


def test_the_same_input_always_produces_the_same_output(action, source_frame) -> None:
    """Determinism (build plan section 3.3)."""
    first = action.run({INPUT_SLOT_ID: source_frame})
    second = action.run({INPUT_SLOT_ID: source_frame})

    assert first.outputs[OUTPUT_ID].rows() == second.outputs[OUTPUT_ID].rows()
    assert first.metrics == second.metrics


def test_a_dataset_with_no_duplicates_is_returned_unchanged(action) -> None:
    frame = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    result = action.run({INPUT_SLOT_ID: frame})

    assert result.outputs[OUTPUT_ID].rows() == frame.rows()
    assert result.metrics["duplicates_removed"] == 0


# ---------------------------------------------------------------------------
# What the Action must NOT do (build plan section 26)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "values"),
    [
        ("untrimmed whitespace", ["Widget", " Widget", "Widget "]),
        ("differing case", ["Widget", "widget", "WIDGET"]),
        ("accents", ["Réserve", "Reserve", "reserve"]),
        ("near duplicates", ["Château Margaux", "Chateau Margaux", "Ch. Margaux"]),
        ("blank vs whitespace", ["", " ", "  "]),
    ],
)
def test_values_that_merely_look_alike_are_never_combined(
    action, label: str, values: list[str]
) -> None:
    frame = pl.DataFrame({"Product": values})

    result = action.run({INPUT_SLOT_ID: frame})

    assert result.outputs[OUTPUT_ID].height == len(values), label
    assert result.outputs[OUTPUT_ID]["Product"].to_list() == values, label


def test_values_are_returned_exactly_as_uploaded(action) -> None:
    values = [" Château Margaux ", "château margaux", " Château Margaux "]
    frame = pl.DataFrame({"Producer": values})

    result = action.run({INPUT_SLOT_ID: frame})

    # The first and third are identical, so one is dropped; nothing is
    # trimmed, re-cased or stripped of its accents.
    assert result.outputs[OUTPUT_ID]["Producer"].to_list() == [
        " Château Margaux ",
        "château margaux",
    ]


def test_rows_that_are_blank_in_the_same_places_are_exact_duplicates(action) -> None:
    frame = pl.DataFrame(
        {"a": [None, None, "x"], "b": [None, None, None]},
        schema={"a": pl.String, "b": pl.String},
    )

    result = action.run({INPUT_SLOT_ID: frame})

    assert result.outputs[OUTPUT_ID].rows() == [(None, None), ("x", None)]


def test_a_null_is_not_treated_as_an_empty_string(action) -> None:
    frame = pl.DataFrame({"a": [None, ""]}, schema={"a": pl.String})

    result = action.run({INPUT_SLOT_ID: frame})

    assert result.outputs[OUTPUT_ID].height == 2
    assert result.outputs[OUTPUT_ID]["a"].to_list() == [None, ""]


# ---------------------------------------------------------------------------
# Through the real pipeline
# ---------------------------------------------------------------------------


def test_a_csv_upload_produces_the_expected_run(runs_dir: Path, action) -> None:
    payload = csv_bytes(fixture.HEADER, fixture.ROWS)

    outcome = execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    manifest = outcome.manifest
    assert manifest.status.value == "succeeded"
    assert manifest.metrics == {
        "input_rows": fixture.EXPECTED_INPUT_ROWS,
        "output_rows": fixture.EXPECTED_OUTPUT_ROWS,
        "duplicates_removed": fixture.EXPECTED_DUPLICATES_REMOVED,
    }

    (output,) = manifest.outputs
    assert output.id == OUTPUT_ID
    assert output.label == "Deduplicated Data"
    assert output.row_count == fixture.EXPECTED_OUTPUT_ROWS
    assert output.columns == fixture.HEADER

    written = pl.read_parquet(outcome.paths.working_artifact(OUTPUT_ID))
    assert written.rows() == list(fixture.EXPECTED_ROWS)


def test_an_xlsx_upload_produces_the_same_rows_as_the_csv(
    runs_dir: Path, action
) -> None:
    payload = xlsx_bytes({"Data": [fixture.HEADER, *fixture.ROWS]})

    outcome = execute_run(action, {INPUT_SLOT_ID: upload("sales.xlsx", payload)})

    written = pl.read_parquet(outcome.paths.working_artifact(OUTPUT_ID))
    assert tuple(written.columns) == fixture.HEADER
    # Excel stores every number as a float, so `Units` returns as 10.0 rather
    # than 10. The values, the row count and the row order are identical.
    assert written.rows() == [
        tuple(float(v) if isinstance(v, int) else v for v in row)
        for row in fixture.EXPECTED_ROWS
    ]
    assert outcome.manifest.metrics == {
        "input_rows": fixture.EXPECTED_INPUT_ROWS,
        "output_rows": fixture.EXPECTED_OUTPUT_ROWS,
        "duplicates_removed": fixture.EXPECTED_DUPLICATES_REMOVED,
    }