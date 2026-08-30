"""Preview service tests (build plan 3.14, section 31 and 6D.7).

Since Phase 6D the preview slices the result DataFrame the Run is holding
rather than reading an internal Parquet file, so these drive it with a frame.
The limit rules it enforces are unchanged public contract.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from app.errors import InvalidRequestError
from app.services import preview


@pytest.fixture
def frame() -> pl.DataFrame:
    """A result table of 1,000 numbered rows."""
    return pl.DataFrame(
        {"i": range(1000), "label": [f"row-{n}" for n in range(1000)]}
    )


def test_the_default_page_is_one_hundred_rows(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame)

    assert preview.DEFAULT_PREVIEW_LIMIT == 100
    assert page.limit == 100
    assert len(page.rows) == 100
    assert page.offset == 0


def test_the_page_reports_the_full_row_count(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame)

    assert page.total_rows == 1000
    assert len(page.rows) == 100


def test_only_the_requested_rows_are_returned(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame, offset=500, limit=5)

    assert page.rows == [
        [500, "row-500"],
        [501, "row-501"],
        [502, "row-502"],
        [503, "row-503"],
        [504, "row-504"],
    ]


def test_the_columns_are_reported_in_order(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame, limit=1)

    assert page.columns == ("i", "label")


def test_the_columns_are_reported_even_for_an_empty_page(
    frame: pl.DataFrame,
) -> None:
    """A page past the end still describes the table's schema."""
    page = preview.read_preview(frame, offset=5000, limit=10)

    assert page.columns == ("i", "label")


def test_a_page_past_the_end_is_empty(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame, offset=5000, limit=10)

    assert page.rows == []
    assert page.total_rows == 1000


def test_the_maximum_limit_is_five_hundred(frame: pl.DataFrame) -> None:
    page = preview.read_preview(frame, limit=preview.MAX_PREVIEW_LIMIT)

    assert preview.MAX_PREVIEW_LIMIT == 500
    assert len(page.rows) == 500


@pytest.mark.parametrize("limit", [0, -1, 501, 1_000_000])
def test_an_out_of_range_limit_is_refused(frame: pl.DataFrame, limit: int) -> None:
    with pytest.raises(InvalidRequestError):
        preview.read_preview(frame, limit=limit)


def test_a_negative_offset_is_refused(frame: pl.DataFrame) -> None:
    with pytest.raises(InvalidRequestError):
        preview.read_preview(frame, offset=-1)


def test_an_over_large_limit_is_refused_rather_than_clamped(
    frame: pl.DataFrame,
) -> None:
    """A silently reduced page would misreport what the caller received."""
    with pytest.raises(InvalidRequestError) as raised:
        preview.read_preview(frame, limit=501)

    assert raised.value.details["maximum"] == 500


def test_null_values_are_preserved_as_null() -> None:
    page = preview.read_preview(pl.DataFrame({"a": [1, None], "b": ["x", None]}))

    assert page.rows == [[1, "x"], [None, None]]


def test_an_empty_result_previews_as_no_rows() -> None:
    page = preview.read_preview(pl.DataFrame({"a": [], "b": []}))

    assert page.rows == []
    assert page.total_rows == 0
    assert page.columns == ("a", "b")


def test_the_source_frame_is_not_modified(frame: pl.DataFrame) -> None:
    """Slicing a page must never disturb the table the Run is holding."""
    before = frame.rows()

    preview.read_preview(frame, offset=10, limit=5)

    assert frame.rows() == before


def test_previewing_reads_nothing_from_disk(
    tmp_path: Path, runs_dir: Path, frame: pl.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build plan 6D.7: the retained frame is the source, not a written file."""
    empty = tmp_path / "cwd"
    empty.mkdir()
    monkeypatch.chdir(empty)

    page = preview.read_preview(frame, limit=1)

    assert page.rows == [[0, "row-0"]]
    assert list(empty.iterdir()) == []
    assert list(runs_dir.rglob("*")) == []