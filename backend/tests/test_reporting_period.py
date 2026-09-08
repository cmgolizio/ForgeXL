"""Reporting period detection (build plan 10B).

Build plan 10B names six situations the program "must detect". Four of them are
about one file and are tested here; the two that need the Data Library or a
second file — a duplicate monthly upload and mismatched periods between related
files — are tested in `test_ingestion.py`, which is the layer that can see
them.

    wrong month uploaded               -> test_a_file_for_another_month_is_refused
    file spanning an unexpected period -> test_two_months_in_one_file_are_refused
    empty reporting period             -> test_a_file_with_no_dates_is_refused
    future-dated rows                  -> test_rows_dated_after_today_are_refused

Every fixture is rendered as **both** CSV and XLSX wherever the difference
could matter, because the two formats reach this module by genuinely different
paths: a CSV's dates arrive as text and are parsed here, a workbook's arrive as
dates and are used as they stand.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.models.source_schemas import (
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
    SALES_SOURCE_SCHEMA,
)
from app.services import parser
from app.services.reporting_period import (
    detect_reporting_period,
    period_of,
    read_dates,
)

from tests.fixtures import monthly_sources as ms
from tests.fixtures.spreadsheets import UPLOAD_EXTENSIONS

#: Every fixture in this module is dated 2026, so "today" for the checks that
#: need one is a date after all of it. Stated rather than read from a clock, so
#: the suite's result does not depend on when it runs.
TODAY = date(2026, 12, 31)


def frame_of(table, extension: str = ".csv") -> pl.DataFrame:
    """Parse a fixture through the real parser, exactly as an upload arrives."""
    return parser.parse_tabular_bytes(table.payload(extension), extension).frame


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", UPLOAD_EXTENSIONS)
def test_a_clean_month_is_detected_from_the_data(extension) -> None:
    """The month comes from Invoice Date, in either upload format."""
    frame = frame_of(ms.month("2026-09"), extension)

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.period == "2026-09"
    assert detection.periods == ("2026-09",)
    assert detection.min_date == date(2026, 9, 1)
    assert detection.max_date == date(2026, 9, 26)


def test_the_month_comes_from_the_data_and_not_from_the_filename() -> None:
    """Build plan 10B: "Do not rely solely on filenames such as September Sales.csv".

    The fixture is *named* for August and *dated* September. September wins,
    because the file is the evidence and its name is not.
    """
    table = ms.month("2026-09", name="August Sales")
    frame = frame_of(table)

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert "August" in table.filename(".csv")
    assert detection.period == "2026-09"


@pytest.mark.parametrize("extension", UPLOAD_EXTENSIONS)
def test_rows_are_counted_per_month(extension) -> None:
    frame = frame_of(ms.months(("2026-07", "2026-08", "2026-09")), extension)

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=TODAY, allow_multiple_periods=True
    )

    assert detection.ok
    assert detection.rows_by_period == {"2026-07": 1, "2026-08": 2, "2026-09": 3}


def test_period_of_formats_a_month_sortably() -> None:
    """`YYYY-MM` because months in that form sort correctly as text."""
    assert period_of(date(2026, 9, 7)) == "2026-09"
    assert period_of(date(2026, 12, 31)) == "2026-12"
    assert sorted(("2026-10", "2026-09")) == ["2026-09", "2026-10"]


# ---------------------------------------------------------------------------
# 10B — wrong month uploaded
# ---------------------------------------------------------------------------


def test_a_file_for_another_month_is_refused() -> None:
    frame = frame_of(ms.month("2026-08"))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, expected_period="2026-09", today=TODAY
    )

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "UNEXPECTED_REPORTING_PERIOD"
    assert issue.details["expected_period"] == "2026-09"
    assert issue.details["periods"] == ["2026-08"]
    # The message says both months, because "wrong month" is only useful with
    # the month it actually is.
    assert "2026-09" in issue.message and "2026-08" in issue.message


def test_the_expected_month_matching_is_not_an_error() -> None:
    frame = frame_of(ms.month("2026-09"))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, expected_period="2026-09", today=TODAY
    )

    assert detection.ok


# ---------------------------------------------------------------------------
# 10B — a file spanning an unexpected period
# ---------------------------------------------------------------------------


def test_two_months_in_one_file_are_refused() -> None:
    frame = frame_of(ms.months(("2026-08", "2026-09")))

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "MULTIPLE_REPORTING_PERIODS"
    assert issue.details["periods"] == ["2026-08", "2026-09"]
    assert issue.details["rows_by_period"] == {"2026-08": 1, "2026-09": 2}
    assert detection.period is None


def test_several_months_are_allowed_for_a_bootstrap() -> None:
    """The one difference build plan 10G's bootstrap makes (10G)."""
    frame = frame_of(ms.months(("2026-07", "2026-08", "2026-09")))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=TODAY, allow_multiple_periods=True
    )

    assert detection.ok
    assert detection.periods == ("2026-07", "2026-08", "2026-09")
    # Still no single period: the file does not have one, and saying it did
    # would be the guess this module exists to avoid.
    assert detection.period is None


# ---------------------------------------------------------------------------
# 10B — an empty reporting period
# ---------------------------------------------------------------------------


def test_a_file_with_no_dates_is_refused() -> None:
    frame = frame_of(
        ms.transactions([ms.transaction_row(invoice_date=None) for _ in range(3)])
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    assert detection.errors[0].code == "EMPTY_REPORTING_PERIOD"
    assert detection.period is None


def test_a_file_with_no_rows_at_all_is_refused() -> None:
    frame = frame_of(ms.transactions([]))

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    assert detection.errors[0].code == "EMPTY_REPORTING_PERIOD"


def test_some_rows_without_a_date_are_refused_rather_than_dropped() -> None:
    """A row with no date cannot be placed in a month, so it is not ignored.

    Silently excluding it would mean the committed month held fewer rows than
    the file did, with nothing saying so — build plan section 3.3's "never
    silently drop rows".
    """
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="2026-09-01"),
                ms.transaction_row(invoice_date=None),
                ms.transaction_row(invoice_date="2026-09-02"),
            ]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    issue = next(
        error for error in detection.errors if error.code == "MISSING_REPORTING_DATE"
    )
    assert issue.details["rows_without_a_date"] == 1
    assert issue.details["row_count"] == 3


# ---------------------------------------------------------------------------
# 10B — future-dated rows
# ---------------------------------------------------------------------------


def test_rows_dated_after_today_are_refused() -> None:
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="2026-09-01"),
                ms.transaction_row(invoice_date="2026-09-30"),
            ]
        )
    )

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=date(2026, 9, 15)
    )

    assert not detection.ok
    issue = next(
        error for error in detection.errors if error.code == "FUTURE_DATED_ROWS"
    )
    assert issue.details["future_row_count"] == 1
    assert issue.details["latest_date"] == "2026-09-30"
    assert issue.details["today"] == "2026-09-15"


def test_a_row_dated_today_is_not_in_the_future() -> None:
    frame = frame_of(ms.transactions([ms.transaction_row(invoice_date="2026-09-15")]))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=date(2026, 9, 15)
    )

    assert detection.ok


def test_the_future_check_is_deterministic_rather_than_clock_driven() -> None:
    """`today` is injected, so the same file gives the same answer forever."""
    frame = frame_of(ms.transactions([ms.transaction_row(invoice_date="2026-09-20")]))

    before = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=date(2026, 9, 1)
    )
    after = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, today=date(2026, 10, 1)
    )

    assert not before.ok
    assert after.ok


# ---------------------------------------------------------------------------
# Reading the date column
# ---------------------------------------------------------------------------


def test_an_iso_date_column_is_read() -> None:
    frame = frame_of(ms.month("2026-09"))

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.date_format == "%Y-%m-%d"


def test_an_iso_datetime_column_is_read_as_its_date() -> None:
    frame = frame_of(
        ms.transactions(
            [ms.transaction_row(invoice_date="2026-09-04 14:30:00")]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.date_format == "%Y-%m-%d %H:%M:%S"
    assert detection.min_date == date(2026, 9, 4)


def test_a_us_slash_column_is_read_when_it_is_unambiguous() -> None:
    """A day past the 12th rules out the international reading on its own."""
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="09/01/2026"),
                ms.transaction_row(invoice_date="09/25/2026"),
            ]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.date_format == "%m/%d/%Y"
    assert detection.period == "2026-09"


def test_an_international_slash_column_is_read_when_it_is_unambiguous() -> None:
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="01/09/2026"),
                ms.transaction_row(invoice_date="25/09/2026"),
            ]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.date_format == "%d/%m/%Y"
    assert detection.period == "2026-09"


def test_a_workbook_date_column_needs_no_format_at_all() -> None:
    """An XLSX date cell arrives as a date, so nothing is parsed from text."""
    frame = frame_of(ms.month("2026-09"), ".xlsx")
    # The fixture writes ISO text, so force the real case with a typed column.
    typed = frame.with_columns(
        pl.col("Invoice Date").str.to_date("%Y-%m-%d").alias("Invoice Date")
    )

    detection = detect_reporting_period(typed, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.date_format is None
    assert detection.period == "2026-09"


def test_a_datetime_typed_column_is_read_as_its_date() -> None:
    frame = frame_of(ms.month("2026-09")).with_columns(
        pl.col("Invoice Date")
        .str.to_datetime("%Y-%m-%d")
        .alias("Invoice Date")
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.date_format is None
    assert detection.period == "2026-09"


def test_a_format_that_reads_only_some_values_is_not_accepted() -> None:
    """A format must read the whole column, not most of it.

    A format that reads four rows of five has not understood the column, and
    accepting it would silently drop the fifth row out of the month.
    """
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="2026-09-01"),
                ms.transaction_row(invoice_date="not a date"),
            ]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "UNREADABLE_REPORTING_DATE"
    assert "not a date" in str(issue.details["examples"])


def test_a_column_that_is_not_dates_at_all_is_refused() -> None:
    frame = frame_of(
        ms.transactions([ms.transaction_row(invoice_date=f"row-{n}") for n in range(3)])
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    assert detection.errors[0].code == "UNREADABLE_REPORTING_DATE"


def test_a_numeric_date_column_is_refused_rather_than_interpreted() -> None:
    """A column of numbers is not a date column, whatever the numbers mean.

    Excel stores a date as a serial number, and reading one as a date is a
    guess about which epoch it uses. Refusing says so.
    """
    frame = frame_of(ms.month("2026-09")).with_columns(
        pl.Series("Invoice Date", [46000, 46001, 46002, 46003])
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "UNREADABLE_REPORTING_DATE"
    assert issue.details["dtype"] == "Int64"


# ---------------------------------------------------------------------------
# Ambiguity (build plan 10B: require explicit selection rather than guessing)
# ---------------------------------------------------------------------------


def test_a_column_two_formats_read_differently_is_refused_as_ambiguous() -> None:
    """`03/04/2026` is 4 March or 3 April, and nothing here decides which."""
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="03/04/2026"),
                ms.transaction_row(invoice_date="05/04/2026"),
            ]
        )
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "AMBIGUOUS_DATE_FORMAT"
    assert set(issue.details["candidate_formats"]) == {"%m/%d/%Y", "%d/%m/%Y"}
    assert issue.details["example_value"] == "03/04/2026"
    # The message shows both readings, because that is what makes it obvious.
    assert "2026-04-03" in issue.message and "2026-03-04" in issue.message


def test_an_ambiguous_column_is_read_once_a_format_is_stated() -> None:
    """Build plan 10B's "require explicit user selection rather than guessing".

    The same two rows are March under one reading and April under the other,
    which is the whole point: the file cannot say which, and the caller can.
    """
    frame = frame_of(
        ms.transactions(
            [
                ms.transaction_row(invoice_date="03/04/2026"),
                ms.transaction_row(invoice_date="03/04/2026"),
            ]
        )
    )

    us = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, date_format="%m/%d/%Y", today=TODAY
    )
    international = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, date_format="%d/%m/%Y", today=TODAY
    )

    assert us.ok and us.period == "2026-03"
    assert international.ok and international.period == "2026-04"


def test_two_readings_that_agree_everywhere_are_not_ambiguous() -> None:
    """Ambiguity means the readings differ, not that two formats both parse.

    If every day in the column is past the 12th, only one order can read it;
    if two orders read it identically, there is nothing to choose between.
    """
    frame = frame_of(
        ms.transactions([ms.transaction_row(invoice_date="07/07/2026")])
    )

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert detection.ok
    assert detection.period == "2026-07"


def test_a_format_the_schema_does_not_declare_is_refused() -> None:
    """A caller cannot introduce a reading of the data nothing has documented."""
    frame = frame_of(ms.month("2026-09"))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, date_format="%d-%b-%Y", today=TODAY
    )

    assert not detection.ok
    issue = detection.errors[0]
    assert issue.code == "UNKNOWN_DATE_FORMAT"
    assert issue.details["requested_format"] == "%d-%b-%Y"


# ---------------------------------------------------------------------------
# A snapshot has no period to detect
# ---------------------------------------------------------------------------


def test_a_snapshot_takes_the_period_it_is_given() -> None:
    """It carries no date column, so there is nothing to read and nothing to guess."""
    frame = frame_of(ms.CLEAN_ASSIGNMENTS)

    detection = detect_reporting_period(
        frame, ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA, expected_period="2026-09"
    )

    assert detection.ok
    assert detection.period == "2026-09"
    assert detection.min_date is None


def test_a_missing_date_column_is_reported_rather_than_crashing() -> None:
    frame = frame_of(ms.month("2026-09")).drop("Invoice Date")

    detection = detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert not detection.ok
    assert detection.errors[0].code == "REPORTING_PERIOD_ERROR"


# ---------------------------------------------------------------------------
# The frame is never modified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", UPLOAD_EXTENSIONS)
def test_detection_leaves_the_frame_exactly_as_it_was(extension) -> None:
    """Build plan section 3.3: the dates are read to describe, never to rewrite."""
    frame = frame_of(ms.month("2026-09"), extension)
    before = frame.clone()

    detect_reporting_period(frame, SALES_SOURCE_SCHEMA, today=TODAY)

    assert frame.equals(before)
    assert frame.schema == before.schema


def test_read_dates_gives_the_caller_the_same_series_detection_used() -> None:
    """The bootstrap partitions on this, so it must be the same reading.

    Two readings of one date column is how a row gets counted in one month and
    stored in another.
    """
    frame = frame_of(ms.months(("2026-08", "2026-09")))

    detection = detect_reporting_period(
        frame, SALES_SOURCE_SCHEMA, allow_multiple_periods=True, today=TODAY
    )
    dates, used_format, errors = read_dates(
        frame, SALES_SOURCE_SCHEMA, "Invoice Date"
    )

    assert errors == ()
    assert used_format == detection.date_format
    assert sorted(set(dates.dt.strftime("%Y-%m").to_list())) == list(
        detection.periods
    )