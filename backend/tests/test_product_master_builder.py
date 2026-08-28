"""Product Master Builder accuracy and negative tests (build plan Phase 4B/4C).

The controlled sales extract and the Product Master it must produce are defined
by hand in `tests.fixtures.product_rows`. The accuracy tests assert exact
equality against that definition.

The negative tests (Phase 4C) go through the real pipeline rather than calling
`run()` directly, because the point is that a bad file is refused *before* the
transformation happens and leaves no partial output behind.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from app.actions.product_master_builder import (
    INPUT_SLOT_ID,
    OUTPUT_ID,
    PRODUCT_COLUMNS,
    ProductMasterBuilderAction,
)
from app.errors import RunValidationError
from app.models.schemas import RunStatus
from app.services import run_store, storage
from app.services.runner import execute_run

from tests.fixtures import product_rows as fixture
from tests.helpers import csv_bytes, upload, xlsx_bytes


@pytest.fixture
def action() -> ProductMasterBuilderAction:
    return ProductMasterBuilderAction()


@pytest.fixture
def source_frame() -> pl.DataFrame:
    """The fixture as the parser would hand it to the Action."""
    return pl.read_csv(csv_bytes(fixture.HEADER, fixture.ROWS))


def _header_without(column: str) -> tuple[str, ...]:
    return tuple(name for name in fixture.HEADER if name != column)


def _rows_without(column: str) -> list[tuple[object, ...]]:
    index = fixture.HEADER.index(column)
    return [row[:index] + row[index + 1 :] for row in fixture.ROWS]


def _header_renaming(column: str, replacement: str) -> tuple[str, ...]:
    return tuple(replacement if name == column else name for name in fixture.HEADER)


# ---------------------------------------------------------------------------
# Declared metadata (build plan section 27)
# ---------------------------------------------------------------------------


def test_the_action_declares_the_specified_identity(action) -> None:
    assert action.id == "product_master_builder"
    assert action.version == "1.0.0"
    assert action.name == "Product Master Builder"
    assert action.description


def test_the_action_requires_exactly_the_six_columns(action) -> None:
    (slot,) = action.inputs

    assert slot.id == INPUT_SLOT_ID == "sales_file"
    assert slot.required is True
    assert slot.accepted_extensions == (".csv", ".xlsx")
    assert slot.required_columns == (
        "SKU",
        "Vintage",
        "Supplier",
        "Producer",
        "Selection",
        "Volume",
    )
    assert slot.required_columns == PRODUCT_COLUMNS


def test_the_action_declares_the_specified_output(action) -> None:
    (output,) = action.outputs

    assert output.id == OUTPUT_ID == "product_master"
    assert output.label == "Product Master"
    assert output.formats == ("csv", "xlsx")


# ---------------------------------------------------------------------------
# Exact output (build plan Phase 4B)
# ---------------------------------------------------------------------------


def test_the_output_columns_are_the_six_in_the_required_order(
    action, source_frame
) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert tuple(result.outputs[OUTPUT_ID].columns) == fixture.EXPECTED_COLUMNS
    assert tuple(result.outputs[OUTPUT_ID].columns) == PRODUCT_COLUMNS


def test_the_output_column_order_does_not_follow_the_upload(source_frame) -> None:
    """Guards the test above: the upload lists the six in a different order."""
    uploaded_order = tuple(
        name for name in source_frame.columns if name in PRODUCT_COLUMNS
    )

    assert uploaded_order != PRODUCT_COLUMNS


def test_the_output_matches_the_expected_rows_exactly(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.outputs[OUTPUT_ID].rows() == list(fixture.EXPECTED_ROWS)


def test_the_output_row_count_matches(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.outputs[OUTPUT_ID].height == fixture.EXPECTED_OUTPUT_ROWS


def test_the_metrics_match_the_expected_counts(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})

    assert result.metrics == {
        "input_rows": fixture.EXPECTED_INPUT_ROWS,
        "output_rows": fixture.EXPECTED_OUTPUT_ROWS,
        "duplicate_product_rows_removed": (
            fixture.EXPECTED_DUPLICATE_PRODUCT_ROWS_REMOVED
        ),
    }


def test_duplicate_product_rows_removed_is_the_difference_in_row_counts(
    action, source_frame
) -> None:
    metrics = action.run({INPUT_SLOT_ID: source_frame}).metrics

    assert metrics["input_rows"] == source_frame.height
    assert (
        metrics["duplicate_product_rows_removed"]
        == metrics["input_rows"] - metrics["output_rows"]
    )


def test_the_sales_only_columns_are_dropped(action, source_frame) -> None:
    result = action.run({INPUT_SLOT_ID: source_frame})
    columns = result.outputs[OUTPUT_ID].columns

    assert "Order Id" not in columns
    assert "Quantity" not in columns
    assert len(columns) == 6


def test_the_same_input_always_produces_the_same_output(action, source_frame) -> None:
    """Determinism (build plan section 3.3)."""
    first = action.run({INPUT_SLOT_ID: source_frame})
    second = action.run({INPUT_SLOT_ID: source_frame})

    assert first.outputs[OUTPUT_ID].rows() == second.outputs[OUTPUT_ID].rows()
    assert first.metrics == second.metrics


def test_the_upload_is_not_mutated(action, source_frame) -> None:
    before = source_frame.rows()

    action.run({INPUT_SLOT_ID: source_frame})

    assert source_frame.rows() == before


# ---------------------------------------------------------------------------
# Company data is copied, never corrected (build plan section 27)
# ---------------------------------------------------------------------------


def test_accented_text_stays_accented(action, source_frame) -> None:
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]

    assert "Château Margaux" in output["Producer"].to_list()
    assert "Côtes du Rhône" in output["Selection"].to_list()
    assert "Réserve" in output["Selection"].to_list()
    assert "Domaine Père et Fils" in output["Producer"].to_list()


def test_an_accent_is_the_difference_between_two_products(
    action, source_frame
) -> None:
    """Stripping accents would silently merge these two rows into one."""
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]
    same_sku = output.filter(pl.col("SKU") == "DOM-750-21")

    assert same_sku.height == 2
    assert same_sku["Producer"].to_list() == [
        "Domaine Père et Fils",
        "Domaine Pere et Fils",
    ]


def test_a_blank_vintage_stays_blank(action, source_frame) -> None:
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]
    blank_vintage = output.filter(pl.col("SKU") == "BCZ-750-NV")

    assert blank_vintage.height == 1
    assert blank_vintage["Vintage"].to_list() == [None]


def test_a_blank_selection_stays_blank(action, source_frame) -> None:
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]

    assert output.filter(pl.col("SKU") == "DOM-750-21")["Selection"].to_list() == [
        None,
        None,
    ]


def test_no_sku_is_invented_or_changed(action, source_frame) -> None:
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]

    assert set(output["SKU"].to_list()) <= set(source_frame["SKU"].to_list())
    assert output["SKU"].null_count() == 0


def test_differing_vintage_volume_and_selection_each_make_a_distinct_product(
    action, source_frame
) -> None:
    output = action.run({INPUT_SLOT_ID: source_frame}).outputs[OUTPUT_ID]
    margaux = output.filter(pl.col("Producer") == "Château Margaux")

    assert margaux.rows() == [
        ("CM-750-19", 2019, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "750ml"),
        ("CM-750-18", 2018, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "750ml"),
        ("CM-150-19", 2019, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "1.5L"),
        ("CM-750-19", 2019, "Wine Imports Co", "Château Margaux", "Réserve", "750ml"),
    ]


# ---------------------------------------------------------------------------
# Through the real pipeline
# ---------------------------------------------------------------------------


def test_a_csv_upload_produces_the_expected_run(runs_dir: Path, action) -> None:
    payload = csv_bytes(fixture.HEADER, fixture.ROWS)

    outcome = execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    manifest = outcome.manifest
    assert manifest.status is RunStatus.SUCCEEDED
    assert manifest.metrics == {
        "input_rows": fixture.EXPECTED_INPUT_ROWS,
        "output_rows": fixture.EXPECTED_OUTPUT_ROWS,
        "duplicate_product_rows_removed": (
            fixture.EXPECTED_DUPLICATE_PRODUCT_ROWS_REMOVED
        ),
    }

    (output,) = manifest.outputs
    assert output.id == OUTPUT_ID
    assert output.label == "Product Master"
    assert output.row_count == fixture.EXPECTED_OUTPUT_ROWS
    assert output.column_count == 6
    assert output.columns == fixture.EXPECTED_COLUMNS

    written = pl.read_parquet(outcome.paths.working_artifact(OUTPUT_ID))
    assert written.rows() == list(fixture.EXPECTED_ROWS)


def test_an_xlsx_upload_produces_the_same_product_master(
    runs_dir: Path, action
) -> None:
    payload = xlsx_bytes({"Sales": [fixture.HEADER, *fixture.ROWS]})

    outcome = execute_run(action, {INPUT_SLOT_ID: upload("sales.xlsx", payload)})

    written = pl.read_parquet(outcome.paths.working_artifact(OUTPUT_ID))
    assert tuple(written.columns) == fixture.EXPECTED_COLUMNS
    # Excel stores every number as a float, so `Vintage` returns as 2019.0
    # rather than 2019. The values, the row count and the row order match.
    assert written.rows() == [
        tuple(float(v) if isinstance(v, int) else v for v in row)
        for row in fixture.EXPECTED_ROWS
    ]


# ---------------------------------------------------------------------------
# Negative tests (build plan Phase 4C)
#
# Each must fail clearly, and none may leave partial output behind.
# ---------------------------------------------------------------------------


def _assert_failed_cleanly(runs_dir: Path, expected_code: str) -> None:
    """Assert exactly one Run exists, that it failed for `expected_code`, and
    that it produced no output artifact of any kind."""
    (run_directory,) = list(runs_dir.iterdir())
    manifest = run_store.get_run(run_directory.name).to_manifest()

    assert manifest.status is RunStatus.FAILED
    assert manifest.error is not None
    assert manifest.error.code == expected_code
    assert manifest.validation.passed is False
    assert manifest.outputs == ()

    paths = storage.run_paths(run_directory.name)
    assert list(paths.working.iterdir()) == [], "a failed Run wrote working data"
    assert list(paths.exports.iterdir()) == [], "a failed Run wrote an export"


def test_a_missing_sku_column_fails_the_run(runs_dir: Path, action) -> None:
    payload = csv_bytes(_header_without("SKU"), _rows_without("SKU"))

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    assert raised.value.code == "MISSING_COLUMNS"
    assert raised.value.http_status == 422
    assert raised.value.details["missing_columns"] == ["SKU"]
    _assert_failed_cleanly(runs_dir, "MISSING_COLUMNS")


def test_a_misspelled_supplier_column_fails_the_run(runs_dir: Path, action) -> None:
    """`Suplier` is not silently accepted as `Supplier` (build plan 3.7)."""
    payload = csv_bytes(_header_renaming("Supplier", "Suplier"), fixture.ROWS)

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    assert raised.value.code == "MISSING_COLUMNS"
    assert raised.value.details["missing_columns"] == ["Supplier"]
    # The report names what was actually in the file, so the user can see the
    # near miss rather than being told only that something is absent.
    assert "Suplier" in raised.value.details["found_columns"]
    _assert_failed_cleanly(runs_dir, "MISSING_COLUMNS")


@pytest.mark.parametrize(
    ("column", "misspelling"),
    [
        ("SKU", "Sku"),
        ("Vintage", "vintage"),
        ("Supplier", "Supplier Name"),
        ("Producer", "Produce"),
        ("Selection", "Selections"),
        ("Volume", " Volume"),
    ],
)
def test_every_required_column_is_matched_exactly(
    runs_dir: Path, action, column: str, misspelling: str
) -> None:
    payload = csv_bytes(_header_renaming(column, misspelling), fixture.ROWS)

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    assert raised.value.details["missing_columns"] == [column]


def test_a_file_with_a_header_but_no_rows_fails_the_run(
    runs_dir: Path, action
) -> None:
    payload = csv_bytes(fixture.HEADER, [])

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", payload)})

    assert raised.value.code == "EMPTY_DATASET"
    assert raised.value.http_status == 422
    _assert_failed_cleanly(runs_dir, "EMPTY_DATASET")


def test_a_completely_empty_file_fails_the_run(runs_dir: Path, action) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload("sales.csv", b"")})

    assert raised.value.code == "PARSE_ERROR"
    assert raised.value.http_status == 422
    _assert_failed_cleanly(runs_dir, "PARSE_ERROR")


@pytest.mark.parametrize("filename", ["sales.xls", "sales.xlsm", "sales.json"])
def test_an_unsupported_extension_fails_the_run(
    runs_dir: Path, action, filename: str
) -> None:
    payload = csv_bytes(fixture.HEADER, fixture.ROWS)

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {INPUT_SLOT_ID: upload(filename, payload)})

    assert raised.value.code == "UNSUPPORTED_EXTENSION"
    assert raised.value.http_status == 422
    _assert_failed_cleanly(runs_dir, "UNSUPPORTED_EXTENSION")


def test_a_missing_sales_file_fails_the_run(runs_dir: Path, action) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {})

    assert raised.value.code == "MISSING_INPUT"
    _assert_failed_cleanly(runs_dir, "MISSING_INPUT")