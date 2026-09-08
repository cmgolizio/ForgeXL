"""Monthly ingestion into the Data Library (build plan 10C-10G).

The exit criterion this module exists to prove:

    ForgeXL can safely establish historical data once and subsequently add one
    reporting period at a time without duplicating or corrupting history.

Both halves are tested end to end: :func:`test_a_bootstrap_then_one_month_at_a_time`
does exactly what that sentence describes.

Every test gets an empty Data Library from the autouse `data_library` fixture
in `conftest.py`, so nothing here can reach the repository's real library, and
no test sees what another one committed.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.errors import (
    IngestionValidationError,
    InvalidDatasetCommitError,
    UnknownDatasetError,
)
from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    SAMPLE_HISTORY,
    content_hash,
)
from app.services import data_library
from app.services.ingestion import (
    SourceFile,
    bootstrap_history,
    commit_account_assignments,
    commit_monthly_sales,
    commit_monthly_samples,
    import_reporting_cycle,
    validate_reporting_cycle,
    validate_source,
)

from tests.fixtures import monthly_sources as ms
from tests.fixtures.spreadsheets import UPLOAD_EXTENSIONS

TODAY = date(2026, 12, 31)


def source(table, extension: str = ".csv", *, name: str | None = None) -> SourceFile:
    """Render a fixture as an upload, exactly as one would arrive."""
    return SourceFile(
        filename=name or table.filename(extension),
        payload=table.payload(extension),
    )


def sales(period: str = "2026-09", **kwargs) -> SourceFile:
    return source(ms.month(period, **kwargs))


def samples(period: str = "2026-09") -> SourceFile:
    # Different rows from the sales fixture, so the two files are never the
    # same bytes by accident — which the cycle refuses, correctly.
    return source(ms.month(period, days=(3, 12), name=f"samples-{period}"))


def assignments(table=ms.CLEAN_ASSIGNMENTS) -> SourceFile:
    return source(table)


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def versions(dataset_id: str):
    """Committed versions of `dataset_id`, or none if it was never created.

    A dataset that has never been written to does not exist, and asking the
    library about it raises rather than inventing an empty one (Phase 9). A
    test asserting "nothing was committed" means both, so it asks this.
    """
    try:
        return data_library.list_versions(dataset_id)
    except UnknownDatasetError:
        return []


def current(dataset_id: str, period: str):
    """The one live version of `dataset_id` for `period`."""
    return data_library.DATA_LIBRARY.current_version(dataset_id, period)


# ---------------------------------------------------------------------------
# 10C — the monthly sales commit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", UPLOAD_EXTENSIONS)
def test_a_month_of_sales_is_committed_as_one_version(extension) -> None:
    version = commit_monthly_sales(source(ms.month("2026-09"), extension), today=TODAY)

    assert version.dataset_id == SALES_HISTORY.id
    assert version.period == "2026-09"
    assert version.row_count == 4
    assert version.column_count == 15
    assert version.min_date == date(2026, 9, 1)
    assert version.max_date == date(2026, 9, 26)
    assert data_library.load_version(SALES_HISTORY.id, version.version_id).height == 4


def test_the_committed_frame_is_the_uploaded_frame() -> None:
    """No column is added, renamed, reordered or coerced (build plan 10E, 3.3).

    The Data Library holds the source snapshot. Anything this layer inferred —
    the month, the earliest and latest date — is metadata on the version, and
    none of it is written into the rows.
    """
    table = ms.month("2026-09")
    version = commit_monthly_sales(source(table), today=TODAY)

    stored = data_library.load_version(SALES_HISTORY.id, version.version_id)

    assert stored.columns == list(table.header)
    assert stored.height == table.row_count
    # And the values are the uploaded values, accents and all.
    assert stored.row(0)[table.header.index("Producer")] == "Château Margaux"
    assert stored.row(0)[table.header.index("Selection")] == "Réserve"


def test_the_source_hash_and_filename_are_recorded() -> None:
    """Build plan 10C.4, and the provenance every version already required."""
    upload = sales()

    version = commit_monthly_sales(upload, today=TODAY)

    assert version.source_sha256 == content_hash(upload.payload)
    assert version.source_filename == upload.filename
    assert version.source_byte_size == len(upload.payload)
    assert version.parser_engine == "polars-csv"


def test_the_same_file_uploaded_twice_is_refused(caplog) -> None:
    """Build plan 10C: "An accidental repeat upload of the same source file
    must not duplicate sales history"."""
    upload = sales()
    commit_monthly_sales(upload, today=TODAY)

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(upload, today=TODAY)

    assert raised.value.code == "DUPLICATE_SOURCE_FILE"
    assert len(versions(SALES_HISTORY.id)) == 1


def test_the_same_bytes_under_a_different_filename_are_still_a_duplicate() -> None:
    """The file is identified by its content, not by what it was called."""
    table = ms.month("2026-09")
    commit_monthly_sales(source(table, name="September.csv"), today=TODAY)

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(source(table, name="September (1).csv"), today=TODAY)

    assert raised.value.code == "DUPLICATE_SOURCE_FILE"


def test_a_different_file_for_the_same_month_is_refused_as_a_second_version() -> None:
    """Not a duplicate file, but still a second live version of one month.

    The Data Library's rule (build plan 9D): replacing a month is deliberate.
    Refused during **validation** rather than by the library at commit time,
    which is what stops a three-file cycle committing two inputs and failing on
    the third — see `test_a_cycle_whose_third_input_is_already_committed`.
    """
    commit_monthly_sales(sales("2026-09"), today=TODAY)

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(
            source(ms.month("2026-09", days=(2, 9))), today=TODAY
        )

    assert raised.value.code == "PERIOD_ALREADY_COMMITTED"
    assert raised.value.details["period"] == "2026-09"
    assert len(versions(SALES_HISTORY.id)) == 1


def test_a_cycle_whose_third_input_is_already_committed_commits_nothing() -> None:
    """The partial-commit hole the validation-time check closes.

    Ownership for September is already stored; sales and samples are not. The
    cycle commits in order, so without this check the first two would land and
    the third would fail — build plan 10F's misleading state exactly, reached
    by a rule that was only enforced at commit time.
    """
    commit_account_assignments(assignments(), period="2026-09")

    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(ms.REPEATED_ASSIGNMENTS),
        period="2026-09",
        today=TODAY,
    )

    assert not outcome.persisted
    assert not outcome.partial
    assert "PERIOD_ALREADY_COMMITTED" in codes(outcome.errors)
    assert versions(SALES_HISTORY.id) == []
    assert versions(SAMPLE_HISTORY.id) == []


def test_a_month_can_be_corrected_by_an_explicit_replacement() -> None:
    """The correction path, which is what makes a wrong month fixable at all."""
    original = commit_monthly_sales(sales("2026-09"), today=TODAY)

    corrected = commit_monthly_sales(
        source(ms.month("2026-09", days=(2, 9, 16, 23, 30))),
        today=TODAY,
        replaces=original.version_id,
        reason="Two invoices were posted after the export was taken.",
    )

    assert corrected.supersedes == original.version_id
    assert current(SALES_HISTORY.id, "2026-09") == corrected
    # The superseded version is still there and still readable, which is what
    # lets the report it fed be reproduced (build plan 9D).
    assert data_library.load_version(SALES_HISTORY.id, original.version_id).height == 4


def test_a_month_that_is_not_the_expected_one_is_refused() -> None:
    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(sales("2026-08"), expected_period="2026-09", today=TODAY)

    assert raised.value.code == "UNEXPECTED_REPORTING_PERIOD"
    assert data_library.list_datasets() == [] or not versions(
        SALES_HISTORY.id
    )


def test_a_file_missing_a_required_column_is_refused() -> None:
    table = ms.month("2026-09")
    narrowed = table.with_columns(*[c for c in table.header if c != "Total Price"])

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(source(narrowed), today=TODAY)

    assert raised.value.code == "SOURCE_SCHEMA_MISMATCH"
    assert raised.value.details["missing_columns"] == ["Total Price"]


def test_a_renamed_column_is_reported_as_missing_rather_than_matched() -> None:
    """Build plan 10A: a similar name is never treated as the same column."""
    table = ms.month("2026-09")
    renamed = table.with_columns(*table.header)
    renamed = type(table)(
        name=renamed.name,
        description=renamed.description,
        header=tuple(
            "Salesperson" if name == "Sales Person" else name
            for name in renamed.header
        ),
        rows=renamed.rows,
    )

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(source(renamed), today=TODAY)

    assert raised.value.code == "SOURCE_SCHEMA_MISMATCH"
    assert raised.value.details["missing_columns"] == ["Sales Person"]
    assert "Salesperson" in raised.value.details["found_columns"]


def test_an_extra_column_is_a_warning_and_is_stored() -> None:
    """Kept, not refused: a month must not be blocked by a column nothing reads.

    The column is still reported, because an unexplained source-schema change
    is something build plan 13H will want to know about.
    """
    table = ms.month("2026-09")
    widened = type(table)(
        name="widened",
        description="An export that has gained a column.",
        header=table.header + ("Warehouse",),
        rows=tuple(row + ("Main",) for row in table.rows),
    )
    upload = source(widened)

    check = validate_source(SALES_HISTORY.id, upload, today=TODAY)
    version = commit_monthly_sales(upload, today=TODAY)

    assert check.ok
    assert "UNEXPECTED_SOURCE_COLUMNS" in codes(check.warnings)
    assert "Warehouse" in data_library.load_version(
        SALES_HISTORY.id, version.version_id
    ).columns


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(IngestionValidationError):
        commit_monthly_sales(SourceFile(filename="sales.csv", payload=b""), today=TODAY)


def test_an_unsupported_extension_is_refused_by_the_same_parser_rule() -> None:
    """Ingestion uses the Run pipeline's parser, so the rules cannot drift."""
    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(
            SourceFile(filename="sales.xlsm", payload=b"anything"), today=TODAY
        )

    assert raised.value.code == "UNSUPPORTED_EXTENSION"


def test_a_price_column_that_arrived_as_text_is_warned_about_not_converted() -> None:
    """`$1,234.56` is reported and stored as it is; nothing invents a number."""
    upload = source(
        ms.transactions(
            [ms.transaction_row(invoice_date="2026-09-01", total_price="$1,234.56")]
        )
    )

    check = validate_source(SALES_HISTORY.id, upload, today=TODAY)
    version = commit_monthly_sales(upload, today=TODAY)

    assert check.ok
    warning = next(
        issue for issue in check.warnings if issue.code == "NON_NUMERIC_SOURCE_COLUMN"
    )
    assert warning.details["column"] == "Total Price"
    stored = data_library.load_version(SALES_HISTORY.id, version.version_id)
    assert stored.row(0)[stored.columns.index("Total Price")] == "$1,234.56"


def test_nothing_is_written_when_a_commit_is_refused() -> None:
    """Build plan 10F: a refusal leaves the library exactly as it was."""
    with pytest.raises(IngestionValidationError):
        commit_monthly_sales(sales("2026-08"), expected_period="2026-09", today=TODAY)

    library_root = data_library.DATA_LIBRARY.root  # type: ignore[attr-defined]
    assert not any(library_root.rglob("*.parquet"))


# ---------------------------------------------------------------------------
# 10D — samples are their own dataset
# ---------------------------------------------------------------------------


def test_samples_commit_to_their_own_dataset() -> None:
    version = commit_monthly_samples(samples("2026-09"), today=TODAY)

    assert version.dataset_id == SAMPLE_HISTORY.id
    assert versions(SALES_HISTORY.id) == []


def test_sales_and_samples_for_one_month_coexist() -> None:
    """Build plan 10D: distinct datasets, so one month has one of each."""
    sales_version = commit_monthly_sales(sales("2026-09"), today=TODAY)
    sample_version = commit_monthly_samples(samples("2026-09"), today=TODAY)

    assert sales_version.version_id != sample_version.version_id
    assert current(SALES_HISTORY.id, "2026-09") == sales_version
    assert current(SAMPLE_HISTORY.id, "2026-09") == sample_version


def test_the_same_file_committed_to_both_datasets_is_not_a_duplicate() -> None:
    """The duplicate check is per dataset, because the datasets are separate.

    Committing one file to both is still a mistake, and the coordinated import
    is where it is caught — see
    `test_the_same_file_in_two_slots_is_refused`.
    """
    upload = sales("2026-09")

    commit_monthly_sales(upload, today=TODAY)
    commit_monthly_samples(upload, today=TODAY)

    assert len(versions(SALES_HISTORY.id)) == 1
    assert len(versions(SAMPLE_HISTORY.id)) == 1


# ---------------------------------------------------------------------------
# 10E — the account assignment snapshot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", UPLOAD_EXTENSIONS)
def test_an_assignment_snapshot_is_committed_for_a_stated_month(extension) -> None:
    version = commit_account_assignments(
        source(ms.CLEAN_ASSIGNMENTS, extension), period="2026-09"
    )

    assert version.dataset_id == ACCOUNT_ASSIGNMENTS.id
    assert version.period == "2026-09"
    assert version.row_count == 3


def test_a_snapshot_without_a_month_is_refused() -> None:
    """It carries no date, so the month must be stated (build plan 9E, 10B)."""
    check = validate_source(ACCOUNT_ASSIGNMENTS.id, assignments())

    assert not check.ok
    assert "REPORTING_PERIOD_REQUIRED" in codes(check.errors)


def test_the_whole_snapshot_is_stored_not_just_the_two_known_columns() -> None:
    """Build plan 10E: "Preserve the full source snapshot"."""
    table = ms.assignments(
        (("Acme Wine Bar", "Beth Comeaux"),), name="wide-assignments"
    )
    widened = type(table)(
        name="wide-assignments",
        description="An assignment export with extra columns.",
        header=table.header + ("Territory", "Region"),
        rows=tuple(row + ("South", "Gulf") for row in table.rows),
    )

    version = commit_account_assignments(source(widened), period="2026-09")

    stored = data_library.load_version(ACCOUNT_ASSIGNMENTS.id, version.version_id)
    assert stored.columns == ["Customer", "Sales Person", "Territory", "Region"]


def test_one_account_assigned_to_two_reps_is_refused() -> None:
    """Build plan 10E's duplicate customer assignment, and build plan 9E's point.

    A snapshot whose answer to "who owns this account" is two names cannot
    reproduce the month, and a report built on it looks exactly as finished as
    a correct one.
    """
    with pytest.raises(IngestionValidationError) as raised:
        commit_account_assignments(
            assignments(ms.CONFLICTING_ASSIGNMENTS), period="2026-09"
        )

    assert raised.value.code == "AMBIGUOUS_ACCOUNT_OWNERSHIP"
    assert raised.value.details["conflicting_account_count"] == 1
    conflict = raised.value.details["conflicts"][0]
    assert conflict["account"] == "Acme Wine Bar"
    assert conflict["reps"] == ["Beth Comeaux", "Kevin Wardell"]


def test_one_account_listed_twice_with_the_same_rep_is_accepted() -> None:
    """It says one true thing twice. Refusing it would be a rule about tidiness."""
    version = commit_account_assignments(
        assignments(ms.REPEATED_ASSIGNMENTS), period="2026-09"
    )

    assert version.row_count == 3


def test_a_blank_account_or_a_blank_rep_is_refused() -> None:
    """Build plan 10E: blank customer names, blank rep names."""
    check = validate_source(
        ACCOUNT_ASSIGNMENTS.id, assignments(ms.BLANK_ASSIGNMENTS), expected_period="2026-09"
    )

    assert not check.ok
    blanks = [
        issue
        for issue in check.errors
        if issue.code == "MISSING_ACCOUNT_ASSIGNMENT_FIELD"
    ]
    assert {issue.details["column"] for issue in blanks} == {"Customer", "Sales Person"}
    # Row numbers are the ones a user sees in the spreadsheet: the header is
    # row 1, so the second data row is row 3.
    assert [issue.details["rows"] for issue in blanks] == [[3], [4]]


def test_a_rep_of_only_whitespace_counts_as_blank() -> None:
    """A cell holding one space says nothing, and is not trimmed in the data."""
    check = validate_source(
        ACCOUNT_ASSIGNMENTS.id,
        assignments(ms.assignments((("Acme Wine Bar", "  "),))),
        expected_period="2026-09",
    )

    assert "MISSING_ACCOUNT_ASSIGNMENT_FIELD" in codes(check.errors)


def test_a_blank_rep_on_a_transaction_is_only_a_warning() -> None:
    """Ownership comes from the snapshot, so an invoice without one is not fatal."""
    upload = source(
        ms.transactions(
            [ms.transaction_row(invoice_date="2026-09-01", sales_person=None)]
        )
    )

    check = validate_source(SALES_HISTORY.id, upload, today=TODAY)

    assert check.ok
    assert "BLANK_SOURCE_IDENTIFIER" in codes(check.warnings)


def test_two_months_of_ownership_are_independently_retrievable() -> None:
    """The scenario build plan 9E describes, arrived at through ingestion."""
    september = ms.assignments(
        (("Acme Wine Bar", "Beth Comeaux"),), name="assignments-09"
    )
    november = ms.assignments(
        (("Acme Wine Bar", "Kevin Wardell"),), name="assignments-11"
    )

    commit_account_assignments(source(september), period="2026-09")
    commit_account_assignments(source(november), period="2026-11")

    def owner(period: str) -> str:
        version = current(ACCOUNT_ASSIGNMENTS.id, period)
        frame = data_library.load_version(ACCOUNT_ASSIGNMENTS.id, version.version_id)
        return frame.row(0)[frame.columns.index("Sales Person")]

    assert owner("2026-09") == "Beth Comeaux"
    assert owner("2026-11") == "Kevin Wardell"


# ---------------------------------------------------------------------------
# 10F — the coordinated monthly import
# ---------------------------------------------------------------------------


def test_a_complete_cycle_commits_all_three() -> None:
    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert outcome.ok
    assert outcome.period == "2026-09"
    assert outcome.committed_dataset_ids() == (
        SALES_HISTORY.id,
        SAMPLE_HISTORY.id,
        ACCOUNT_ASSIGNMENTS.id,
    )
    for dataset_id in outcome.committed_dataset_ids():
        assert current(dataset_id, "2026-09") is not None


def test_the_period_is_taken_from_the_sales_file_when_none_is_stated() -> None:
    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        today=TODAY,
    )

    assert outcome.ok
    assert outcome.period == "2026-09"
    # And the snapshot, which carries no date of its own, took that month.
    assert (
        current(ACCOUNT_ASSIGNMENTS.id, "2026-09").period
        == "2026-09"
    )


def test_one_bad_input_commits_none_of_the_three() -> None:
    """Build plan 10F's central requirement.

    "Do not leave the application in a misleading state where September sales
    were committed successfully but the September ownership snapshot silently
    failed."
    """
    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(ms.CONFLICTING_ASSIGNMENTS),
        period="2026-09",
        today=TODAY,
    )

    assert not outcome.ok
    assert not outcome.persisted
    assert not outcome.partial
    assert outcome.committed == ()
    assert "AMBIGUOUS_ACCOUNT_OWNERSHIP" in codes(outcome.errors)
    # Nothing at all reached the library — not even the two valid files.
    for dataset_id in (SALES_HISTORY.id, SAMPLE_HISTORY.id, ACCOUNT_ASSIGNMENTS.id):
        assert versions(dataset_id) == []


def test_the_report_names_what_was_and_was_not_persisted() -> None:
    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(ms.BLANK_ASSIGNMENTS),
        period="2026-09",
        today=TODAY,
    )

    assert outcome.committed_dataset_ids() == ()
    assert set(outcome.uncommitted_dataset_ids()) == {
        SALES_HISTORY.id,
        SAMPLE_HISTORY.id,
        ACCOUNT_ASSIGNMENTS.id,
    }


def test_issues_from_every_input_are_reported_together() -> None:
    """All three files are checked, so one round tells the user everything."""
    outcome = validate_reporting_cycle(
        sales=sales("2026-08"),
        samples=samples("2026-07"),
        assignments=assignments(ms.CONFLICTING_ASSIGNMENTS),
        period="2026-09",
        today=TODAY,
    )

    reported = {(issue.slot_id, issue.code) for issue in outcome.errors}
    assert (SALES_HISTORY.id, "UNEXPECTED_REPORTING_PERIOD") in reported
    assert (SAMPLE_HISTORY.id, "UNEXPECTED_REPORTING_PERIOD") in reported
    assert (ACCOUNT_ASSIGNMENTS.id, "AMBIGUOUS_ACCOUNT_OWNERSHIP") in reported


def test_validation_alone_writes_nothing() -> None:
    """The "validate all -> show issues" step of 10F's diagram is read-only."""
    outcome = validate_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert outcome.errors == ()
    assert outcome.committed == ()
    assert data_library.list_datasets() == []


def test_files_for_different_months_are_refused_as_a_mismatch() -> None:
    """Build plan 10B: "mismatched periods between related files"."""
    outcome = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-08"),
        assignments=assignments(),
        today=TODAY,
    )

    assert not outcome.persisted
    assert "MISMATCHED_REPORTING_PERIODS" in codes(outcome.errors)


def test_the_same_file_in_two_slots_is_refused() -> None:
    """Sales and samples are different datasets and need different exports."""
    one_file = sales("2026-09")

    outcome = import_reporting_cycle(
        sales=one_file,
        samples=one_file,
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert not outcome.persisted
    issue = next(
        error
        for error in outcome.errors
        if error.code == "SAME_FILE_FOR_SEVERAL_DATASETS"
    )
    assert issue.details["dataset_ids"] == sorted(
        [SALES_HISTORY.id, SAMPLE_HISTORY.id]
    )


def test_a_second_import_of_a_committed_cycle_is_refused_whole() -> None:
    """The month is already there, so re-running the cycle changes nothing."""
    first = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )
    assert first.ok

    second = import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert not second.persisted
    assert "DUPLICATE_SOURCE_FILE" in codes(second.errors)
    for dataset_id in (SALES_HISTORY.id, SAMPLE_HISTORY.id, ACCOUNT_ASSIGNMENTS.id):
        assert len(versions(dataset_id)) == 1


def test_warnings_do_not_stop_a_cycle() -> None:
    """A warning means "worth knowing", never "cannot continue"."""
    table = ms.month("2026-09")
    widened = type(table)(
        name="widened",
        description="An export that has gained a column.",
        header=table.header + ("Warehouse",),
        rows=tuple(row + ("Main",) for row in table.rows),
    )

    outcome = import_reporting_cycle(
        sales=source(widened),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert outcome.ok
    assert "UNEXPECTED_SOURCE_COLUMNS" in codes(outcome.warnings)


# ---------------------------------------------------------------------------
# 10G — the historical bootstrap
# ---------------------------------------------------------------------------


def test_a_bootstrap_loads_one_version_per_month() -> None:
    """Build plan 10G, and the reason it is per month rather than per file.

    A version per period is what lets `current_version` answer a question about
    one month, what lets one wrong month be corrected on its own, and what lets
    the next monthly import add a month without colliding with anything.
    """
    versions = bootstrap_history(
        SALES_HISTORY.id,
        source(ms.months(("2026-06", "2026-07", "2026-08"))),
        today=TODAY,
    )

    assert [version.period for version in versions] == ["2026-06", "2026-07", "2026-08"]
    assert [version.row_count for version in versions] == [1, 2, 3]
    for version in versions:
        stored = data_library.load_version(SALES_HISTORY.id, version.version_id)
        assert stored.height == version.row_count
        months = stored.get_column("Invoice Date").str.slice(0, 7).unique().to_list()
        assert months == [version.period]


def test_every_bootstrap_partition_records_its_own_date_range() -> None:
    versions = bootstrap_history(
        SALES_HISTORY.id, source(ms.months(("2026-06", "2026-07"))), today=TODAY
    )

    june, july = versions
    assert june.min_date == date(2026, 6, 1) and june.max_date == date(2026, 6, 1)
    assert july.min_date == date(2026, 7, 1) and july.max_date == date(2026, 7, 2)


def test_every_bootstrap_partition_records_the_file_it_came_from() -> None:
    """All of them share one source, because all of them came from one file."""
    upload = source(ms.months(("2026-06", "2026-07")))

    versions = bootstrap_history(SALES_HISTORY.id, upload, today=TODAY)

    assert {version.source_sha256 for version in versions} == {
        content_hash(upload.payload)
    }
    assert {version.source_filename for version in versions} == {upload.filename}


def test_a_bootstrap_into_a_dataset_that_already_has_data_is_refused() -> None:
    """"One-time" is the whole idea (build plan 10G)."""
    commit_monthly_sales(sales("2026-09"), today=TODAY)

    with pytest.raises(InvalidDatasetCommitError) as raised:
        bootstrap_history(
            SALES_HISTORY.id, source(ms.months(("2026-06", "2026-07"))), today=TODAY
        )

    assert raised.value.details["existing_version_count"] == 1
    assert len(versions(SALES_HISTORY.id)) == 1


def test_running_the_same_bootstrap_twice_is_refused() -> None:
    """The precondition catches it; a content hash could not, because every
    partition of one bootstrap shares that file's hash by construction."""
    upload = source(ms.months(("2026-06", "2026-07")))
    bootstrap_history(SALES_HISTORY.id, upload, today=TODAY)

    with pytest.raises(InvalidDatasetCommitError):
        bootstrap_history(SALES_HISTORY.id, upload, today=TODAY)

    assert len(versions(SALES_HISTORY.id)) == 2


def test_a_snapshot_dataset_cannot_be_bootstrapped() -> None:
    """Ownership has no history to load; each month's snapshot is committed."""
    with pytest.raises(InvalidDatasetCommitError) as raised:
        bootstrap_history(ACCOUNT_ASSIGNMENTS.id, assignments(), today=TODAY)

    assert raised.value.details["dataset_kind"] == "snapshot"
    assert versions(ACCOUNT_ASSIGNMENTS.id) == []


def test_a_bootstrap_still_refuses_a_file_it_cannot_read() -> None:
    """The bootstrap relaxes exactly one rule — several months — and no others."""
    table = ms.months(("2026-06", "2026-07"))
    narrowed = table.with_columns(*[c for c in table.header if c != "Customer"])

    with pytest.raises(IngestionValidationError) as raised:
        bootstrap_history(SALES_HISTORY.id, source(narrowed), today=TODAY)

    assert raised.value.code == "SOURCE_SCHEMA_MISMATCH"
    assert versions(SALES_HISTORY.id) == []


def test_a_bootstrap_still_refuses_future_dated_rows() -> None:
    with pytest.raises(IngestionValidationError) as raised:
        bootstrap_history(
            SALES_HISTORY.id,
            source(ms.months(("2026-06", "2026-07"))),
            today=date(2026, 6, 15),
        )

    assert raised.value.code == "FUTURE_DATED_ROWS"


# ---------------------------------------------------------------------------
# The Phase 10 exit criterion, end to end
# ---------------------------------------------------------------------------


def test_a_bootstrap_then_one_month_at_a_time() -> None:
    """Build plan Phase 10's exit criterion, exactly as it is worded.

        ForgeXL can safely establish historical data once and subsequently add
        one reporting period at a time without duplicating or corrupting
        history.

    And 10G's consequence: "Do not require the user to re-upload the complete
    historical dataset every month." The monthly imports below carry one month
    each, and the history stays.
    """
    # Establish the history once.
    bootstrap_history(
        SALES_HISTORY.id, source(ms.months(("2026-06", "2026-07"))), today=TODAY
    )
    bootstrap_history(
        SAMPLE_HISTORY.id,
        source(ms.months(("2026-06", "2026-07"), name="samples-history")),
        today=TODAY,
    )

    # Then add one reporting period at a time, uploading only that month.
    for period in ("2026-08", "2026-09"):
        outcome = import_reporting_cycle(
            sales=sales(period),
            samples=samples(period),
            assignments=assignments(),
            period=period,
            today=TODAY,
        )
        assert outcome.ok, [issue.message for issue in outcome.errors]

    sales_versions = data_library.DATA_LIBRARY.current_versions(SALES_HISTORY.id)
    assert [version.period for version in sales_versions] == [
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
    ]
    # Nothing was duplicated: one live version per month, and every month's own
    # rows are still in it.
    assert len({version.period for version in sales_versions}) == len(sales_versions)
    assert current(SALES_HISTORY.id, "2026-06").row_count == 1

    # And the history is readable as one table, which is what Phase 11 will do.
    history = pl.concat(
        data_library.load_version(SALES_HISTORY.id, version.version_id)
        for version in sales_versions
    )
    assert history.height == 1 + 2 + 4 + 4


def test_an_unknown_dataset_cannot_be_imported_into() -> None:
    with pytest.raises(UnknownDatasetError):
        validate_source("not_a_dataset", sales(), today=TODAY)


@pytest.mark.parametrize(
    "dataset_id", ("../../etc/passwd", "/etc/passwd", "..", "Sales History", "")
)
def test_a_path_shaped_dataset_id_never_reaches_the_library(dataset_id) -> None:
    """The same rule Phase 9 applies: an ID is checked for shape before use."""
    with pytest.raises(UnknownDatasetError):
        validate_source(dataset_id, sales(), today=TODAY)


# ---------------------------------------------------------------------------
# What "the same file" means for each kind of dataset
#
# The duplicate check matches the content hash *and* the reporting period, and
# the two dataset kinds are the reason. These two tests are the pair that
# pins it.
# ---------------------------------------------------------------------------


def test_an_unchanged_ownership_list_can_be_committed_for_a_later_month() -> None:
    """Ownership that did not change produces a byte-identical export.

    Refusing it as a duplicate would force the user to perturb a correct file
    to record a true fact, so only the same snapshot for the *same* month is a
    duplicate. Committing it for October is what "ownership was the same in
    October" looks like.
    """
    unchanged = assignments()

    september = commit_account_assignments(unchanged, period="2026-09")
    october = commit_account_assignments(unchanged, period="2026-10")

    assert september.version_id != october.version_id
    assert september.source_sha256 == october.source_sha256
    assert current(ACCOUNT_ASSIGNMENTS.id, "2026-09").version_id == september.version_id
    assert current(ACCOUNT_ASSIGNMENTS.id, "2026-10").version_id == october.version_id


def test_the_same_snapshot_for_the_same_month_is_a_duplicate() -> None:
    """The other half of the rule: a repeat upload of one month is still refused."""
    unchanged = assignments()
    commit_account_assignments(unchanged, period="2026-09")

    with pytest.raises(IngestionValidationError) as raised:
        commit_account_assignments(unchanged, period="2026-09")

    assert raised.value.code == "DUPLICATE_SOURCE_FILE"
    assert len(versions(ACCOUNT_ASSIGNMENTS.id)) == 1


def test_a_superseded_version_still_counts_as_already_imported() -> None:
    """Re-importing a file that was corrected would reinstate the wrong data."""
    original = commit_monthly_sales(sales("2026-09"), today=TODAY)
    commit_monthly_sales(
        source(ms.month("2026-09", days=(2, 9, 16))),
        today=TODAY,
        replaces=original.version_id,
        reason="Corrected export.",
    )

    with pytest.raises(IngestionValidationError) as raised:
        commit_monthly_sales(sales("2026-09"), today=TODAY)

    assert raised.value.code == "DUPLICATE_SOURCE_FILE"


# ---------------------------------------------------------------------------
# Ingested history outlives the process (build plan Phase 9's whole point,
# reached through Phase 10's front door)
# ---------------------------------------------------------------------------


def test_committed_months_are_still_there_after_the_library_is_reopened() -> None:
    """"Add one reporting period at a time" only means anything if it persists."""
    from app.services.data_library import LocalDataLibrary

    bootstrap_history(
        SALES_HISTORY.id, source(ms.months(("2026-06", "2026-07"))), today=TODAY
    )
    import_reporting_cycle(
        sales=sales("2026-08"),
        samples=samples("2026-08"),
        assignments=assignments(),
        period="2026-08",
        today=TODAY,
    )
    root = data_library.DATA_LIBRARY.root  # type: ignore[attr-defined]

    reopened = LocalDataLibrary(root)

    assert [
        version.period for version in reopened.current_versions(SALES_HISTORY.id)
    ] == ["2026-06", "2026-07", "2026-08"]
    august = reopened.current_version(SALES_HISTORY.id, "2026-08")
    assert reopened.load_version(SALES_HISTORY.id, august.version_id).height == 4


# ---------------------------------------------------------------------------
# Ingestion writes to the Data Library and nowhere else
# ---------------------------------------------------------------------------


def test_ingestion_writes_nothing_outside_the_library(quarantine) -> None:
    """The Run pipeline's rule still holds: an upload is parsed in memory.

    The Data Library is the one place ForgeXL writes, and `quarantine` is the
    working directory that must stay empty. A stray temporary file, or an
    upload written out to be read back, would land here.
    """
    import_reporting_cycle(
        sales=sales("2026-09"),
        samples=samples("2026-09"),
        assignments=assignments(),
        period="2026-09",
        today=TODAY,
    )

    assert list(quarantine.rglob("*")) == []