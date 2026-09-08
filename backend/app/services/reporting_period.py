"""Deriving a reporting period from the data itself (build plan 10B).

    Do not rely solely on filenames such as `September Sales.csv`.
    Validate applicable dates contained inside the dataset.

So the month a monthly upload belongs to is read from its date column, never
from what the file is called. This module is the only place that happens.

Build plan 10B lists the situations that must be detected, and each has a check
here:

    wrong month uploaded                  the detected month is not the
                                          expected one
    file spanning an unexpected period    more than one month is present
    empty reporting period                no row carries a readable date
    future-dated rows                     a date is later than today
    duplicate monthly upload              not here — that is a question about
                                          the Data Library, and is answered in
                                          `app.services.ingestion`
    mismatched periods between files      also `ingestion`, which is the layer
                                          that sees more than one file

Two properties shape the implementation.

**Nothing is guessed.** A date column is read with the formats its schema
declares, applied whole: a format is accepted only if it reads *every*
populated value. When two declared formats both read the column and disagree
about any row — ``03/04/2026`` is 4 March or 3 April — the column is reported
as ambiguous and an explicit format is required. Build plan 10B says so in as
many words: "Where automatic determination is genuinely ambiguous, require
explicit user selection rather than guessing."

**Nothing is repaired.** No value is trimmed, coerced or substituted, and the
frame is never modified. The dates this module reads are used to *describe*
the file — its month, its earliest and latest row — and the frame that reaches
the Data Library is the one the parser produced (build plan section 3.3).

Detection reports rather than raises. A single monthly commit turns the report
into an exception; the coordinated import of build plan 10F needs every issue
from all three files at once, which it cannot collect from the first failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

import polars as pl

from app.models.library import parse_period
from app.models.schemas import ValidationIssue
from app.models.source_schemas import DATETIME_FORMATS, SourceSchema

#: How many example values a message quotes before it stops. Enough to
#: recognise the problem, few enough that an error stays readable when every
#: row is wrong.
MAX_EXAMPLES = 5


def period_of(value: date) -> str:
    """The canonical ``YYYY-MM`` month `value` falls in."""
    return f"{value.year:04d}-{value.month:02d}"


@dataclass(frozen=True)
class PeriodDetection:
    """What the dates in one file say about which month it covers.

    `period` is set only when exactly one month is present, which is what a
    recurring monthly upload must be. A historical bootstrap legitimately spans
    several (build plan 10G), so `periods` carries them all and the caller
    decides which shape it is asking for.
    """

    #: The single month covered, or None when there is not exactly one.
    period: str | None

    #: Every month present, ascending. Empty when no date could be read.
    periods: tuple[str, ...]

    #: Rows per month, keyed by month — the evidence behind `periods`, and what
    #: makes "3 rows are in August" visible rather than just "two months".
    rows_by_period: dict[str, int]

    min_date: date | None
    max_date: date | None

    #: The format the column was read with, or None when the column already
    #: carried dates and no text parsing was needed.
    date_format: str | None

    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether a period was established with no errors."""
        return not self.errors


def detect_reporting_period(
    frame: pl.DataFrame,
    schema: SourceSchema,
    *,
    expected_period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
    allow_multiple_periods: bool = False,
    slot_id: str | None = None,
) -> PeriodDetection:
    """Read `frame`'s date column and say which month it covers.

    Args:
        frame: The parsed upload, read but never modified.
        schema: Declares the date column and the formats to try.
        expected_period: The month the caller is importing, ``YYYY-MM``. When
            given, a file covering a different month is an error — build plan
            10B's "wrong month uploaded".
        date_format: An explicit format, which resolves an ambiguous column.
            Must be one the schema declares, so a caller cannot introduce a
            reading of the data that nothing has documented or tested.
        today: The date "future-dated" is measured against. Injected so the
            check is deterministic in tests; defaults to the real today.
        allow_multiple_periods: True for a historical bootstrap, which may
            legitimately span months (build plan 10G). False for a recurring
            monthly upload, where a second month means the wrong file.
        slot_id: Recorded on every issue, so an import of three files can say
            which one an issue came from.

    Returns:
        A :class:`PeriodDetection`. Never raises for a problem with the data;
        `errors` carries those.
    """
    if schema.period_column is None:
        # A snapshot states ownership as it stands and carries no date. Its
        # month is supplied by the caller rather than read from rows, so there
        # is nothing here to detect and nothing to complain about.
        return PeriodDetection(
            period=expected_period,
            periods=(expected_period,) if expected_period else (),
            rows_by_period={},
            min_date=None,
            max_date=None,
            date_format=None,
        )

    column = schema.period_column
    if column not in frame.columns:
        # The schema check reports the missing column with all the others; this
        # only stops the read below from failing on a column that is not there.
        return _failed(
            code="REPORTING_PERIOD_ERROR",
            message=(
                f"{schema.label} has no {column!r} column, so the reporting "
                "period cannot be established."
            ),
            details={"period_column": column, "columns": list(frame.columns)},
            slot_id=slot_id,
        )

    dates, used_format, read_errors = read_dates(
        frame, schema, column, date_format=date_format, slot_id=slot_id
    )
    if read_errors:
        return _failed_with(read_errors)

    populated = dates.drop_nulls()
    if populated.is_empty():
        return _failed(
            code="EMPTY_REPORTING_PERIOD",
            message=(
                f"{schema.label} contains no rows with a readable "
                f"{column}, so there is no reporting period to import."
            ),
            details={"period_column": column, "row_count": frame.height},
            slot_id=slot_id,
        )

    counts = Counter(period_of(value) for value in populated.to_list())
    periods = tuple(sorted(counts))
    minimum = populated.min()
    maximum = populated.max()
    assert isinstance(minimum, date) and isinstance(maximum, date)

    detection = PeriodDetection(
        period=periods[0] if len(periods) == 1 else None,
        periods=periods,
        rows_by_period=dict(sorted(counts.items())),
        min_date=minimum,
        max_date=maximum,
        date_format=used_format,
    )

    errors: list[ValidationIssue] = []

    # A blank date is a row that cannot be placed in any month. It is not a
    # parse failure — the cell is genuinely empty — but it is still a row the
    # month would silently not contain, so it is reported rather than dropped.
    blank = frame.height - populated.len()
    if blank:
        errors.append(
            ValidationIssue(
                code="MISSING_REPORTING_DATE",
                message=(
                    f"{blank} of {frame.height} rows in {schema.label} have no "
                    f"{column}. Every row must carry one, because a row with "
                    "no date cannot be placed in a reporting month."
                ),
                details={
                    "period_column": column,
                    "rows_without_a_date": blank,
                    "row_count": frame.height,
                },
                slot_id=slot_id,
            )
        )

    errors.extend(
        _period_shape_errors(
            detection,
            schema,
            expected_period=expected_period,
            allow_multiple_periods=allow_multiple_periods,
            slot_id=slot_id,
        )
    )
    errors.extend(
        _future_date_errors(
            populated, schema, column, today=today or date.today(), slot_id=slot_id
        )
    )

    return PeriodDetection(
        period=detection.period,
        periods=detection.periods,
        rows_by_period=detection.rows_by_period,
        min_date=detection.min_date,
        max_date=detection.max_date,
        date_format=detection.date_format,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# Reading the column
# ---------------------------------------------------------------------------


def read_dates(
    frame: pl.DataFrame,
    schema: SourceSchema,
    column: str,
    *,
    date_format: str | None = None,
    slot_id: str | None = None,
) -> tuple[pl.Series, str | None, tuple[ValidationIssue, ...]]:
    """Return the date column as dates, the format used, and any refusals.

    Public because the historical bootstrap of build plan 10G partitions a
    multi-month file by month and must do it against *the same* reading of the
    column that detection reported. Two readings of one column is exactly how
    a row ends up counted in one month and stored in another.

    A column that already holds dates — which is what an XLSX date cell
    produces — is used as it stands. A text column is read with the schema's
    declared formats, and a format is accepted only if it reads every populated
    value; a format that reads most of them has not understood the column.
    """
    series = frame.get_column(column)

    if series.dtype == pl.Date:
        return series, None, ()
    if isinstance(series.dtype, pl.Datetime) or series.dtype == pl.Datetime:
        return series.dt.date(), None, ()
    if series.dtype != pl.String:
        return (
            series,
            None,
            (
                ValidationIssue(
                    code="UNREADABLE_REPORTING_DATE",
                    message=(
                        f"The {column} column of {schema.label} holds "
                        f"{series.dtype} values, which are not dates. It must "
                        "hold dates, or text this application can read as "
                        f"dates: {_human_list(schema.date_formats)}."
                    ),
                    details={
                        "period_column": column,
                        "dtype": str(series.dtype),
                        "accepted_formats": list(schema.date_formats),
                    },
                    slot_id=slot_id,
                ),
            ),
        )

    populated = series.drop_nulls()
    if populated.is_empty():
        # Nothing to read a format from. The caller reports the empty period.
        return series.cast(pl.Date), None, ()

    if date_format is not None:
        if date_format not in schema.date_formats:
            return (
                series,
                None,
                (
                    ValidationIssue(
                        code="UNKNOWN_DATE_FORMAT",
                        message=(
                            f"{date_format!r} is not a date format this "
                            "application reads. The formats it accepts are "
                            f"{_human_list(schema.date_formats)}."
                        ),
                        details={
                            "requested_format": date_format,
                            "accepted_formats": list(schema.date_formats),
                        },
                        slot_id=slot_id,
                    ),
                ),
            )
        candidates = (date_format,)
    else:
        candidates = schema.date_formats

    viable: list[tuple[str, pl.Series]] = []
    for candidate in candidates:
        parsed = _parse_with(series, candidate)
        # Every populated cell must be read, not most of them.
        if parsed.null_count() == series.null_count():
            viable.append((candidate, parsed))

    if not viable:
        return (
            series,
            None,
            (
                ValidationIssue(
                    code="UNREADABLE_REPORTING_DATE",
                    message=(
                        f"The {column} column of {schema.label} could not be "
                        "read as dates. This application reads "
                        f"{_human_list(candidates)}, and no single one of "
                        "those reads every value in the column. Examples: "
                        f"{_examples(populated)}."
                    ),
                    details={
                        "period_column": column,
                        "accepted_formats": list(candidates),
                        "examples": _example_list(populated),
                    },
                    slot_id=slot_id,
                ),
            ),
        )

    chosen_format, chosen = viable[0]

    # Two formats that both read the column and agree everywhere are the same
    # reading of the data, and there is nothing to choose between them. Two
    # that disagree are a genuine ambiguity: 03/04/2026 is two different days
    # and picking one would move rows into the wrong month.
    for other_format, other in viable[1:]:
        if not chosen.equals(other):
            disagreement = _first_disagreement(series, chosen, other)
            return (
                series,
                None,
                (
                    ValidationIssue(
                        code="AMBIGUOUS_DATE_FORMAT",
                        message=(
                            f"The {column} column of {schema.label} can be "
                            f"read as {chosen_format} or as {other_format}, "
                            "and the two disagree"
                            + (
                                f" — {disagreement} is either "
                                f"{_show(chosen, series, disagreement)} or "
                                f"{_show(other, series, disagreement)}"
                                if disagreement is not None
                                else ""
                            )
                            + ". Say which format the file uses, or export it "
                            "with unambiguous dates."
                        ),
                        details={
                            "period_column": column,
                            "candidate_formats": [chosen_format, other_format],
                            "example_value": disagreement,
                        },
                        slot_id=slot_id,
                    ),
                ),
            )

    return chosen, chosen_format, ()


def _parse_with(series: pl.Series, fmt: str) -> pl.Series:
    """Read `series` with one format, leaving unreadable values null.

    ``strict=False`` is what makes a format testable: a format that cannot read
    a value produces a null there rather than an exception, so the caller can
    ask "did this read *everything*" instead of "did this raise".
    """
    if fmt in DATETIME_FORMATS:
        return series.str.to_datetime(fmt, strict=False).dt.date()
    return series.str.to_date(fmt, strict=False)


def _first_disagreement(
    source: pl.Series, left: pl.Series, right: pl.Series
) -> str | None:
    """The first raw value two readings of the column disagree about."""
    for index, (a, b) in enumerate(zip(left.to_list(), right.to_list())):
        if a != b:
            value = source.item(index)
            return None if value is None else str(value)
    return None


def _show(parsed: pl.Series, source: pl.Series, raw: str) -> str:
    """Render what one reading made of the raw value `raw`."""
    for index, value in enumerate(source.to_list()):
        if value is not None and str(value) == raw:
            read = parsed.item(index)
            return read.isoformat() if isinstance(read, date) else "unreadable"
    return "unreadable"


# ---------------------------------------------------------------------------
# The checks build plan 10B lists
# ---------------------------------------------------------------------------


def _period_shape_errors(
    detection: PeriodDetection,
    schema: SourceSchema,
    *,
    expected_period: str | None,
    allow_multiple_periods: bool,
    slot_id: str | None,
) -> list[ValidationIssue]:
    """"Wrong month uploaded" and "file spanning an unexpected period"."""
    issues: list[ValidationIssue] = []

    if len(detection.periods) > 1 and not allow_multiple_periods:
        issues.append(
            ValidationIssue(
                code="MULTIPLE_REPORTING_PERIODS",
                message=(
                    f"{schema.label} covers {len(detection.periods)} months "
                    f"({_human_list(detection.periods)}), and a monthly import "
                    "takes one. Rows per month: "
                    + ", ".join(
                        f"{period} {count:,}"
                        for period, count in detection.rows_by_period.items()
                    )
                    + ". Export the single month, or use the historical "
                    "bootstrap if this is the first load of past data."
                ),
                details={
                    "periods": list(detection.periods),
                    "rows_by_period": detection.rows_by_period,
                },
                slot_id=slot_id,
            )
        )

    if expected_period is not None:
        wanted = parse_period(expected_period)
        unexpected = [
            period for period in detection.periods if period != wanted
        ]
        if unexpected:
            issues.append(
                ValidationIssue(
                    code="UNEXPECTED_REPORTING_PERIOD",
                    message=(
                        f"{schema.label} was imported for {wanted} but holds "
                        f"rows dated {_human_list(tuple(unexpected))}."
                        + (
                            f" No row is dated {wanted}."
                            if wanted not in detection.periods
                            else ""
                        )
                    ),
                    details={
                        "expected_period": wanted,
                        "periods": list(detection.periods),
                        "rows_by_period": detection.rows_by_period,
                    },
                    slot_id=slot_id,
                )
            )

    return issues


def _future_date_errors(
    populated: pl.Series,
    schema: SourceSchema,
    column: str,
    *,
    today: date,
    slot_id: str | None,
) -> list[ValidationIssue]:
    """"Future-dated rows".

    Refused rather than warned about. A month is imported once it has
    happened, and a date in the future is a data-entry error that decides which
    month a row lands in — the one thing this module exists to get right.
    """
    future = populated.filter(populated > today)
    if future.is_empty():
        return []

    latest = future.max()
    assert isinstance(latest, date)
    return [
        ValidationIssue(
            code="FUTURE_DATED_ROWS",
            message=(
                f"{future.len()} rows in {schema.label} have a {column} later "
                f"than today ({today.isoformat()}), the latest being "
                f"{latest.isoformat()}. A reporting month is imported after it "
                "has happened, so these dates are wrong and would place rows "
                "in a month that has not occurred."
            ),
            details={
                "period_column": column,
                "today": today.isoformat(),
                "future_row_count": future.len(),
                "latest_date": latest.isoformat(),
            },
            slot_id=slot_id,
        )
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _failed(
    *, code: str, message: str, details: dict[str, object], slot_id: str | None
) -> PeriodDetection:
    return _failed_with(
        (
            ValidationIssue(
                code=code, message=message, details=details, slot_id=slot_id
            ),
        )
    )


def _failed_with(errors: tuple[ValidationIssue, ...]) -> PeriodDetection:
    return PeriodDetection(
        period=None,
        periods=(),
        rows_by_period={},
        min_date=None,
        max_date=None,
        date_format=None,
        errors=errors,
    )


def _examples(series: pl.Series) -> str:
    return ", ".join(repr(value) for value in _example_list(series))


def _example_list(series: pl.Series) -> list[str]:
    return [str(value) for value in series.head(MAX_EXAMPLES).to_list()]


def _human_list(values: tuple[str, ...]) -> str:
    """Join `values` for a user-facing message: 'a, b and c'."""
    if len(values) <= 1:
        return "".join(values)
    return f"{', '.join(values[:-1])} and {values[-1]}"