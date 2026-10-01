"""Reading a span of months through one input slot (build plan 13B).

Phase 11 resolved exactly one Data Library version per input slot, which is
all Phase 11 needed. Build plan 13B requires a report Action's inputs to
"include the historical information required by the report specification", and
the year-over-year and year-to-date windows of 13C span more months than one
version holds — the library stores one version per month by construction.

The `history` selector added in Phase 13 resolves a *set*. This module is what
keeps it from weakening anything Phase 11 established:

* every version read is still recorded individually, by its immutable ID;
* a moving selector still stops moving before the Action executes;
* a bounding month that was never imported is refused rather than quietly
  falling back to an earlier one;
* months whose exports disagree about their columns are reported rather than
  reconciled;
* a superseded month is excluded from history, the same way it is from
  `latest` and `period:`;
* the Action still receives one frame and still cannot tell where it came
  from.

`test_library_inputs.py` remains the Phase 11 module and is untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.errors import (
    InconsistentDatasetVersionsError,
    InvalidDatasetSelectorError,
    UnknownDatasetVersionError,
)
from app.models.library import (
    SALES_HISTORY,
    DatasetCommit,
    DatasetSelector,
    DatasetSelectorKind,
)
from app.models.schemas import (
    ActionInput,
    ActionInputSource,
    ActionOutput,
)
from app.services import data_library, input_resolution
from app.services.data_library import LocalDataLibrary, ensure_known_datasets
from app.services.runner import execute_run

COLUMNS: tuple[str, ...] = ("Invoice Number", "Customer", "Total Price")


class _HistoryAction(Action):
    """One slot that reads a span of stored months."""

    id = "history_only"
    version = "1.0.0"
    name = "History Only"
    description = "Return every stored sales month it was given."
    inputs = (
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["sales_history"]})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def month_frame(period: str, *, rows: int, columns=COLUMNS) -> pl.DataFrame:
    """One month's rows, tagged so a test can tell the months apart."""
    return pl.DataFrame(
        {
            columns[0]: [f"INV-{period}-{index}" for index in range(rows)],
            columns[1]: [f"Account {index}" for index in range(rows)],
            columns[2]: [float(index) for index in range(rows)],
        }
    )


def commit(
    library: LocalDataLibrary,
    period: str,
    *,
    rows: int = 2,
    columns=COLUMNS,
    replaces: str | None = None,
    reason: str | None = None,
):
    """Commit one month directly, without going through ingestion."""
    frame = month_frame(period, rows=rows, columns=columns)
    return library.commit_version(
        SALES_HISTORY.id,
        DatasetCommit.from_upload(
            frame=frame,
            period=period,
            filename=f"sales-{period}.csv",
            payload=f"{period}:{rows}:{columns}".encode(),
            min_date=date.fromisoformat(f"{period}-01"),
            max_date=date.fromisoformat(f"{period}-28"),
            supersedes=replaces,
            supersession_reason=reason,
        ),
    )


@pytest.fixture
def library(data_library: LocalDataLibrary) -> LocalDataLibrary:
    ensure_known_datasets()
    return data_library


@pytest.fixture
def three_months(library: LocalDataLibrary) -> LocalDataLibrary:
    """June, July and August, with a different row count each."""
    commit(library, "2026-06", rows=1)
    commit(library, "2026-07", rows=2)
    commit(library, "2026-08", rows=3)
    return library


def resolve(reference: str):
    return input_resolution.resolve_slot(_HistoryAction.inputs[0], reference)


# ---------------------------------------------------------------------------
# The selector
# ---------------------------------------------------------------------------


def test_history_is_a_selector_that_names_a_set() -> None:
    selector = DatasetSelector.parse("history")

    assert selector.kind is DatasetSelectorKind.HISTORY
    assert selector.value is None
    assert selector.selects_many is True
    assert selector.as_text() == "history"


def test_history_may_be_bounded_at_a_month() -> None:
    selector = DatasetSelector.parse("history:2026-09")

    assert selector.kind is DatasetSelectorKind.HISTORY
    assert selector.value == "2026-09"
    assert selector.as_text() == "history:2026-09"


def test_the_three_original_selectors_still_name_one_version() -> None:
    for text in ("latest", "period:2026-09", "version:" + "0" * 8 + "-0000-4000-8000-" + "0" * 12):
        assert DatasetSelector.parse(text).selects_many is False


def test_a_history_selector_is_moving_and_must_be_resolved() -> None:
    """Build plan 11D: it may be asked with, never recorded as used."""
    assert DatasetSelector.parse("history").is_moving is True
    assert DatasetSelector.parse("history:2026-09").is_moving is True


@pytest.mark.parametrize(
    "text", ["histories", "history 2026-09", "history:", "history:September"]
)
def test_a_near_miss_is_refused_rather_than_interpreted(text) -> None:
    with pytest.raises(InvalidDatasetSelectorError):
        DatasetSelector.parse(text)


def test_a_history_selector_cannot_smuggle_a_path(library) -> None:
    with pytest.raises(InvalidDatasetSelectorError):
        DatasetSelector.parse("history:../../etc/passwd")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_history_reads_every_live_month_oldest_first(three_months) -> None:
    resolved = resolve("history")

    assert [item.version.period for item in resolved.versions] == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]


def test_the_months_arrive_as_one_frame(three_months) -> None:
    resolved = resolve("history")

    assert resolved.frame.height == 1 + 2 + 3
    assert tuple(resolved.frame.columns) == COLUMNS


def test_the_rows_are_in_period_order(three_months) -> None:
    resolved = resolve("history")

    invoices = resolved.frame.get_column(COLUMNS[0]).to_list()
    assert invoices[0].startswith("INV-2026-06")
    assert invoices[-1].startswith("INV-2026-08")


def test_a_bound_stops_at_that_month(three_months) -> None:
    resolved = resolve("history:2026-07")

    assert [item.version.period for item in resolved.versions] == [
        "2026-06",
        "2026-07",
    ]
    assert resolved.frame.height == 3


def test_a_bounding_month_that_was_never_imported_is_refused(
    three_months,
) -> None:
    """It must never fall back to an earlier month (rule ``report_month``)."""
    with pytest.raises(UnknownDatasetVersionError) as failure:
        resolve("history:2026-09")

    assert failure.value.details["period"] == "2026-09"
    assert failure.value.details["available_periods"] == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]


def test_an_empty_dataset_is_refused(library) -> None:
    with pytest.raises(UnknownDatasetVersionError):
        resolve("history")


def test_a_superseded_month_is_left_out(three_months) -> None:
    original = data_library.current_version(SALES_HISTORY.id, "2026-07")
    commit(
        three_months,
        "2026-07",
        rows=9,
        replaces=original.version_id,
        reason="July was restated.",
    )

    resolved = resolve("history")

    assert [item.version.period for item in resolved.versions] == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert original.version_id not in {
        item.version.version_id for item in resolved.versions
    }
    assert resolved.frame.height == 1 + 9 + 3


def test_a_single_version_selector_still_returns_one(three_months) -> None:
    resolved = resolve("period:2026-07")

    assert len(resolved.versions) == 1
    assert resolved.version.period == "2026-07"
    assert resolved.frame.height == 2


def test_the_merged_frame_is_the_versions_own_rows(three_months) -> None:
    """Nothing is added, renamed or reordered by the merge."""
    resolved = resolve("history")

    for item in resolved.versions:
        stored = data_library.load_version(
            SALES_HISTORY.id, item.version.version_id
        )
        assert item.frame.equals(stored)


# ---------------------------------------------------------------------------
# Months that disagree
# ---------------------------------------------------------------------------


def test_months_with_different_columns_are_refused(library) -> None:
    commit(library, "2026-06", rows=1)
    commit(
        library,
        "2026-07",
        rows=1,
        columns=("Invoice Number", "Customer", "Net Price"),
    )

    with pytest.raises(InconsistentDatasetVersionsError) as failure:
        resolve("history")

    details = failure.value.details
    assert details["missing_columns"] == ["Net Price"]
    assert details["unexpected_columns"] == ["Total Price"]


def test_the_refusal_names_the_months_that_differ(library) -> None:
    commit(library, "2026-06", rows=1)
    commit(
        library,
        "2026-07",
        rows=1,
        columns=("Invoice Number", "Customer", "Net Price"),
    )

    with pytest.raises(InconsistentDatasetVersionsError) as failure:
        resolve("history")

    assert "2026-06" in failure.value.message
    assert "2026-07" in failure.value.message


def test_months_differing_only_in_type_are_merged(library) -> None:
    """A CSV month and a workbook month can type one column differently."""
    integers = month_frame("2026-06", rows=2).with_columns(
        pl.col(COLUMNS[2]).cast(pl.Int64)
    )
    library.commit_version(
        SALES_HISTORY.id,
        DatasetCommit.from_upload(
            frame=integers,
            period="2026-06",
            filename="june.csv",
            payload=b"june",
        ),
    )
    commit(library, "2026-07", rows=2)

    resolved = resolve("history")

    assert resolved.frame.height == 4
    assert resolved.frame.schema[COLUMNS[2]] == pl.Float64


def test_a_reordered_month_is_merged_not_refused(library) -> None:
    """Column *order* is presentation; only the set has to match."""
    commit(library, "2026-06", rows=2)
    reordered = month_frame("2026-07", rows=2).select(
        COLUMNS[2], COLUMNS[0], COLUMNS[1]
    )
    library.commit_version(
        SALES_HISTORY.id,
        DatasetCommit.from_upload(
            frame=reordered,
            period="2026-07",
            filename="july.csv",
            payload=b"july",
        ),
    )

    resolved = resolve("history")

    assert resolved.frame.height == 4
    # The newest version's order wins, and every row keeps its own values.
    assert tuple(resolved.frame.columns) == (
        COLUMNS[2],
        COLUMNS[0],
        COLUMNS[1],
    )


# ---------------------------------------------------------------------------
# Provenance (build plan 11C)
# ---------------------------------------------------------------------------


def test_the_run_records_one_entry_per_month_read(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )
    records = outcome.manifest.library_inputs

    assert [record.period for record in records] == [
        "2026-06",
        "2026-07",
        "2026-08",
    ]
    assert all(record.slot_id == "sales_history" for record in records)
    assert len({record.version_id for record in records}) == 3


def test_each_recorded_month_names_what_was_asked_for(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history:2026-07"},
    )

    for record in outcome.manifest.library_inputs:
        assert record.requested == "history:2026-07"
        assert record.version_id != "history:2026-07"


def test_each_recorded_month_carries_its_own_source(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )

    filenames = [
        record.source_filename for record in outcome.manifest.library_inputs
    ]
    assert filenames == [
        "sales-2026-06.csv",
        "sales-2026-07.csv",
        "sales-2026-08.csv",
    ]
    hashes = {record.source_sha256 for record in outcome.manifest.library_inputs}
    assert len(hashes) == 3


def test_the_audit_counts_every_month_that_went_in(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )
    audit = outcome.manifest.audit

    assert audit is not None
    assert len(audit.library_inputs) == 3
    assert audit.rows_received == 1 + 2 + 3


def test_a_recorded_history_run_is_unchanged_by_a_later_month(
    three_months,
) -> None:
    """Build plan 11D: what a Run recorded stays what it recorded."""
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )
    before = [record.version_id for record in outcome.manifest.library_inputs]

    commit(three_months, "2026-09", rows=4)

    from app.services import run_store

    stored = run_store.get_run(outcome.run.run_id)
    assert stored is not None
    assert [
        record.version_id for record in stored.library_inputs
    ] == before


def test_the_manifest_carries_no_filesystem_path(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )

    text = outcome.manifest.model_dump_json()
    for fragment in ("/home/", "/Users/", "/tmp", ".parquet", "versions/"):
        assert fragment not in text


# ---------------------------------------------------------------------------
# What did not change
# ---------------------------------------------------------------------------


def test_the_action_cannot_tell_it_read_several_months(three_months) -> None:
    outcome = execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )
    result = outcome.result
    assert result is not None
    table = result.table("result")
    assert table is not None

    assert table.height == 6


def test_a_history_run_writes_nothing(three_months, quarantine: Path) -> None:
    execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )

    assert list(quarantine.iterdir()) == []


def test_a_history_run_commits_nothing(three_months) -> None:
    before = len(data_library.list_versions(SALES_HISTORY.id))

    execute_run(
        _HistoryAction(),
        uploads={},
        dataset_references={"sales_history": "history"},
    )

    assert len(data_library.list_versions(SALES_HISTORY.id)) == before
