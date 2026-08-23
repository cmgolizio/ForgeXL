"""Preview service tests (build plan 3.14 and section 31)."""

from __future__ import annotations

import polars as pl
import pytest

from app.errors import InvalidRequestError, MissingArtifactError
from app.services import export, preview, storage


@pytest.fixture
def populated(run_paths: storage.RunPaths) -> storage.RunPaths:
    """A Run whose ``result`` output holds 1,000 numbered rows."""
    export.write_output(
        run_paths,
        "result",
        pl.DataFrame({"i": range(1000), "label": [f"row-{n}" for n in range(1000)]}),
    )
    return run_paths


def test_the_default_page_is_one_hundred_rows(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result")

    assert preview.DEFAULT_PREVIEW_LIMIT == 100
    assert page.limit == 100
    assert len(page.rows) == 100
    assert page.offset == 0


def test_the_page_reports_the_full_row_count(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result")

    assert page.total_rows == 1000
    assert len(page.rows) == 100


def test_only_the_requested_rows_are_returned(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result", offset=500, limit=5)

    assert page.rows == [
        [500, "row-500"],
        [501, "row-501"],
        [502, "row-502"],
        [503, "row-503"],
        [504, "row-504"],
    ]


def test_the_columns_are_reported_in_order(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result", limit=1)

    assert page.columns == ("i", "label")


def test_a_page_past_the_end_is_empty(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result", offset=5000, limit=10)

    assert page.rows == []
    assert page.total_rows == 1000


def test_the_maximum_limit_is_five_hundred(populated: storage.RunPaths) -> None:
    page = preview.read_preview(populated, "result", limit=preview.MAX_PREVIEW_LIMIT)

    assert preview.MAX_PREVIEW_LIMIT == 500
    assert len(page.rows) == 500


@pytest.mark.parametrize("limit", [0, -1, 501, 1_000_000])
def test_an_out_of_range_limit_is_refused(
    populated: storage.RunPaths, limit: int
) -> None:
    with pytest.raises(InvalidRequestError):
        preview.read_preview(populated, "result", limit=limit)


def test_a_negative_offset_is_refused(populated: storage.RunPaths) -> None:
    with pytest.raises(InvalidRequestError):
        preview.read_preview(populated, "result", offset=-1)


def test_an_over_large_limit_is_refused_rather_than_clamped(
    populated: storage.RunPaths,
) -> None:
    """A silently reduced page would misreport what the caller received."""
    with pytest.raises(InvalidRequestError) as raised:
        preview.read_preview(populated, "result", limit=501)

    assert raised.value.details["maximum"] == 500


def test_a_missing_parquet_artifact_is_reported(
    run_paths: storage.RunPaths,
) -> None:
    with pytest.raises(MissingArtifactError):
        preview.read_preview(run_paths, "result")


def test_the_preview_reads_parquet_not_the_csv_export(
    populated: storage.RunPaths,
) -> None:
    """Build plan section 28: Parquet is the internal preview source."""
    populated.export_artifact("result", "csv").unlink()

    page = preview.read_preview(populated, "result", limit=1)

    assert page.rows == [[0, "row-0"]]


def test_null_values_are_preserved_as_null(run_paths: storage.RunPaths) -> None:
    export.write_output(
        run_paths, "result", pl.DataFrame({"a": [1, None], "b": ["x", None]})
    )

    page = preview.read_preview(run_paths, "result")

    assert page.rows == [[1, "x"], [None, None]]