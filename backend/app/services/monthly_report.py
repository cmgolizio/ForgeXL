"""The Monthly Sales Rep Report calculation engine (build plan 13C-13H).

Everything this module does is specified by :mod:`app.models.report_spec` and
explained in `docs/monthly-sales-rep-report-spec.md`. It spells no business
rule of its own: which column holds the money, what a placement is, which
conditions fail a report and which merely qualify it are all declarations it
reads. Changing a rule is a change there, not here.

The shape of the work follows build plan 13C-13H exactly:

    resolved frames
        -> one reporting period, resolved once            (13C)
        -> one prepared model: dates, measures, ownership (13E)
        -> the rep roster, read from the snapshot         (13D)
        -> validation                                     (13H)
        -> company figures, calculated once               (13G)
        -> every report table                             (13F)

Two properties are worth stating because they shaped the code.

**Preparation happens once per Run, not once per rep.** Build plan 13E: "Avoid
recalculating identical normalization, period, and join logic separately for
every rep." A rep's figures are a grouped aggregation over the one prepared
frame, so a company of forty reps costs one pass, not forty. Build plan 13G
says the same thing about company totals, and they are computed once and
joined in.

**Nothing is mutated.** The frames arriving here are the Data Library's rows,
handed over by the runner. Every step returns a new frame; build plan 13E:
"Do not mutate original Data Library versions."

This module is not an Action. It is imported by
:mod:`app.actions.monthly_sales_rep_report`, which owns the Action contract —
its ID, its version, its slots and its outputs — and delegates the arithmetic
here for the same reason :mod:`app.services.workbook` owns the spreadsheet
engine: one file per concern, and the Action stays readable. It reaches no
file, no clock and no library of its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from app.models.report_spec import (
    ASSIGNMENTS_CUSTOMER_COLUMN,
    ASSIGNMENTS_REP_COLUMN,
    ASSIGNMENTS_SCHEMA,
    KNOWN_INVOICE_TYPES,
    MINIMUM_PLACEMENT_HISTORY_MONTHS,
    MONEY_DECIMALS,
    PROVISIONAL_RULES,
    REP_COLUMN,
    SALES_SCHEMA,
    SAMPLES_SCHEMA,
    TRANSACTION_COLUMNS,
    WINDOW_LABELS,
    Severity,
    WindowKey,
    condition,
    severity_of,
)
from app.models.schemas import ValidationIssue
from app.models.source_schemas import SourceSchema
from app.services.reporting_period import period_of, read_dates

# ---------------------------------------------------------------------------
# Internal column names
#
# Prefixed so they can never collide with a source column, however the export
# changes. They exist only inside the prepared frames and never reach a report
# table (build plan 6E.6: audit and working data stay out of the user's data).
# ---------------------------------------------------------------------------

DATE = "_date"
MONTH = "_month"
REVENUE = "_revenue"
QUANTITY = "_quantity"
OWNER = "_owner"

#: How many offending values an error message shows before it stops.
MAX_EXAMPLES = 5

#: Column prefixes for each comparison window. The reporting month is bare —
#: "Revenue" means this month's revenue, because that is what a monthly report
#: is about — and every other window says which one it is.
WINDOW_PREFIXES: dict[WindowKey, str] = {
    WindowKey.CURRENT_MONTH: "",
    WindowKey.PRIOR_MONTH: "Prior Month ",
    WindowKey.PRIOR_YEAR_MONTH: "Last Year ",
    WindowKey.YEAR_TO_DATE: "YTD ",
    WindowKey.PRIOR_YEAR_TO_DATE: "Prior YTD ",
}


def measure_column(window: WindowKey, measure: str) -> str:
    """The report column one measure is reported in for one window."""
    return f"{WINDOW_PREFIXES[window]}{measure}"


# ---------------------------------------------------------------------------
# The reporting period (build plan 13C)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One comparison window, inclusive of both ends."""

    key: WindowKey
    label: str
    start: date
    end: date

    def covers(self, column: str = DATE) -> pl.Expr:
        """An expression selecting the rows inside this window."""
        return pl.col(column).is_between(self.start, self.end, closed="both")


@dataclass(frozen=True)
class ReportPeriod:
    """The one period every figure in the report is measured against.

    Build plan 13C: "Do not allow different sections of the same report to
    independently decide what 'current month' means." Every window below is
    derived from :attr:`month`, and every table asks this object rather than
    working a date out for itself.
    """

    #: The reporting month, as ``YYYY-MM``.
    month: str

    #: Every window, in declaration order.
    windows: tuple[Window, ...]

    @property
    def label(self) -> str:
        """How the period is written in a report, e.g. "September 2026"."""
        return _month_label(self.month)

    @property
    def start(self) -> date:
        """The first day of the reporting month."""
        return self.window(WindowKey.CURRENT_MONTH).start

    @property
    def end(self) -> date:
        """The last day of the reporting month."""
        return self.window(WindowKey.CURRENT_MONTH).end

    def window(self, key: WindowKey) -> Window:
        """Return one window by its key."""
        for item in self.windows:
            if item.key is key:
                return item
        raise KeyError(f"No window is declared for {key!r}.")


def report_period(month: str) -> ReportPeriod:
    """Build the five comparison windows for a reporting month.

    Args:
        month: The reporting month as ``YYYY-MM``.

    All five are calendar windows, inclusive of both ends (rule
    ``comparison_windows``). Year-to-date runs from 1 January of the reporting
    year, and the prior-year windows are the same shapes shifted back twelve
    months, so February in a leap year compares with February in a non-leap
    year without either being truncated.
    """
    year, number = _split_month(month)
    prior_year, prior_number = _shift_month(year, number, -1)
    last_year, last_number = year - 1, number

    windows = (
        Window(
            key=WindowKey.CURRENT_MONTH,
            label=WINDOW_LABELS[WindowKey.CURRENT_MONTH],
            start=date(year, number, 1),
            end=_month_end(year, number),
        ),
        Window(
            key=WindowKey.PRIOR_MONTH,
            label=WINDOW_LABELS[WindowKey.PRIOR_MONTH],
            start=date(prior_year, prior_number, 1),
            end=_month_end(prior_year, prior_number),
        ),
        Window(
            key=WindowKey.PRIOR_YEAR_MONTH,
            label=WINDOW_LABELS[WindowKey.PRIOR_YEAR_MONTH],
            start=date(last_year, last_number, 1),
            end=_month_end(last_year, last_number),
        ),
        Window(
            key=WindowKey.YEAR_TO_DATE,
            label=WINDOW_LABELS[WindowKey.YEAR_TO_DATE],
            start=date(year, 1, 1),
            end=_month_end(year, number),
        ),
        Window(
            key=WindowKey.PRIOR_YEAR_TO_DATE,
            label=WINDOW_LABELS[WindowKey.PRIOR_YEAR_TO_DATE],
            start=date(last_year, 1, 1),
            end=_month_end(last_year, last_number),
        ),
    )
    return ReportPeriod(month=month, windows=windows)


# ---------------------------------------------------------------------------
# The prepared model (build plan 13E)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedReport:
    """Everything the tables are built from, prepared once.

    `errors` being non-empty means no report may be produced: the Action
    returns them from its ``validate`` hook and the Run fails before any table
    is calculated (build plan 13H). `warnings` travel into the Data Quality
    table instead, because an Action has no warning channel of its own.
    """

    #: None only when preparation failed before a period could be derived.
    period: ReportPeriod | None

    #: Sales rows with their date, numeric measures and owning rep attached.
    sales: pl.DataFrame

    #: Sample rows, prepared the same way.
    samples: pl.DataFrame

    #: `Customer` -> `Sales Rep`, one row per account.
    ownership: pl.DataFrame

    #: Every rep the report covers, in name order (build plan 13D).
    reps: tuple[str, ...]

    #: The months the sales history covers, oldest first.
    history_months: tuple[str, ...]

    #: Conditions that stop the report (build plan 13H).
    errors: tuple[ValidationIssue, ...] = ()

    #: Conditions reported beside the report.
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def usable(self) -> bool:
        """Whether a report may be produced from this."""
        return not self.errors and self.period is not None

    def require_period(self) -> ReportPeriod:
        """The reporting period, or a clear failure if there is none."""
        if self.period is None:
            raise ValueError(
                "The report has no reporting period; validate() should have "
                "failed the Run before it reached here."
            )
        return self.period


@dataclass
class _Collected:
    """Issues gathered while preparing, split by the declared severity."""

    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        slot_id: str | None = None,
    ) -> None:
        """Record one condition, routed by the severity the spec declares.

        The severity is never decided here. :mod:`app.models.report_spec` says
        whether a condition stops the report, so relaxing one to a warning is
        an edit to the specification rather than to the engine.
        """
        issue = ValidationIssue(
            code=code,
            message=message,
            details=dict(details or {}),
            slot_id=slot_id,
        )
        if severity_of(code) is Severity.ERROR:
            self.errors.append(issue)
        else:
            self.warnings.append(issue)


def prepare(
    sales: pl.DataFrame,
    samples: pl.DataFrame,
    assignments: pl.DataFrame,
    *,
    sales_slot: str | None = None,
    samples_slot: str | None = None,
    assignments_slot: str | None = None,
) -> PreparedReport:
    """Build the shared prepared model and check it (build plan 13E, 13H).

    Deterministic and side-effect free, so calling it twice — which is exactly
    what the Action does, once to validate and once to calculate — produces
    the same answer both times. An Action instance holds no per-Run state, so
    there is nowhere to cache it between the two, and preparing twice is the
    honest cost of that rule rather than a missed optimisation.

    Args:
        sales: Sales history rows, every month the Run read.
        samples: Sample history rows.
        assignments: The account-assignment snapshot for the month.
        sales_slot, samples_slot, assignments_slot: Input slot IDs, so an
            issue points at the input it came from.
    """
    collected = _Collected()

    ownership, reps = _prepare_ownership(
        assignments, collected, slot_id=assignments_slot
    )

    prepared_sales = _prepare_transactions(
        sales, SALES_SCHEMA, collected, slot_id=sales_slot
    )
    prepared_samples = _prepare_transactions(
        samples, SAMPLES_SCHEMA, collected, slot_id=samples_slot
    )

    months = _months_of(prepared_sales)
    if prepared_sales.height == 0:
        collected.add(
            "EMPTY_SALES_HISTORY",
            "The sales history this Run read contains no rows, so there is no "
            "reporting month to report on.",
            slot_id=sales_slot,
        )

    period = report_period(months[-1]) if months else None

    if period is not None:
        prepared_sales = _attach_owner(prepared_sales, ownership)
        prepared_samples = _attach_owner(prepared_samples, ownership)

        _check_ownership_covers_activity(
            prepared_sales, period, collected, slot_id=assignments_slot
        )
        _check_ownership_covers_activity(
            prepared_samples, period, collected, slot_id=assignments_slot
        )
        _check_sample_period(prepared_samples, period, collected, samples_slot)
        _check_history_depth(months, period, collected, sales_slot)
        _check_comparison_windows(prepared_sales, period, collected)
        _check_duplicate_rows(sales, prepared_sales, period, collected, sales_slot)
        _check_invoice_types(prepared_sales, collected, sales_slot)
        _check_invoice_types(prepared_samples, collected, samples_slot)
        _check_unrecognised_reps(
            prepared_sales, prepared_samples, reps, collected, sales_slot
        )

    _check_source_columns(sales, SALES_SCHEMA, collected, sales_slot)
    _check_source_columns(samples, SAMPLES_SCHEMA, collected, samples_slot)
    _check_source_columns(
        assignments, ASSIGNMENTS_SCHEMA, collected, assignments_slot
    )
    _note_provisional_rules(collected)

    return PreparedReport(
        period=period,
        sales=prepared_sales,
        samples=prepared_samples,
        ownership=ownership,
        reps=reps,
        history_months=months,
        errors=tuple(collected.errors),
        warnings=tuple(collected.warnings),
    )


def _prepare_ownership(
    assignments: pl.DataFrame,
    collected: _Collected,
    *,
    slot_id: str | None,
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Read the snapshot into `Customer` -> `Sales Rep` (build plan 9E, 13D).

    A row whose account or rep is blank is left out of the map: it answers
    neither question the snapshot exists to answer. Nothing is dropped
    silently by doing so — an account that ends up with no owner and has
    activity is reported as ``MISSING_ACCOUNT_OWNERSHIP``, which is the
    condition that actually matters. The ingestion layer already refuses such
    a snapshot outright (build plan 10E); this is the same rule holding for a
    version that reached the library another way.
    """
    customer = ASSIGNMENTS_CUSTOMER_COLUMN
    rep = ASSIGNMENTS_REP_COLUMN

    if customer not in assignments.columns or rep not in assignments.columns:
        # The runner's required-column check has already failed the Run; this
        # is the defensive path that keeps preparation from raising.
        return _empty_ownership(), ()

    named = (
        assignments.select(
            pl.col(customer).cast(pl.String).alias(customer),
            pl.col(rep).cast(pl.String).alias(REP_COLUMN),
        )
        .filter(_is_present(pl.col(customer)) & _is_present(pl.col(REP_COLUMN)))
        .unique(maintain_order=True)
    )

    conflicts = (
        named.group_by(customer)
        .agg(pl.col(REP_COLUMN).n_unique().alias("reps"))
        .filter(pl.col("reps") > 1)
        .sort(customer)
    )
    if conflicts.height:
        accounts = conflicts[customer].to_list()
        collected.add(
            "DUPLICATE_ACCOUNT_OWNERSHIP",
            "The account-assignment snapshot gives more than one rep to "
            f"{_human_list(accounts[:MAX_EXAMPLES])}"
            f"{'' if conflicts.height <= MAX_EXAMPLES else ', and others'}. "
            "Each account must have exactly one owner, or its sales would be "
            "counted in more than one report.",
            details={"accounts": accounts, "account_count": conflicts.height},
            slot_id=slot_id,
        )

    reps = tuple(sorted(set(named[REP_COLUMN].to_list())))
    if not reps:
        collected.add(
            "NO_SALES_REPS",
            "The account-assignment snapshot names no sales rep, so there is "
            "nobody to produce a report for.",
            slot_id=slot_id,
        )

    return named, reps


def _prepare_transactions(
    frame: pl.DataFrame,
    schema: SourceSchema,
    collected: _Collected,
    *,
    slot_id: str | None,
) -> pl.DataFrame:
    """Attach a usable date and numeric measures to a transaction frame.

    Nothing is repaired: a value that cannot be read is reported and the
    report fails. The frame handed in is never modified (build plan 13E).
    """
    columns = TRANSACTION_COLUMNS
    if not {columns.date, columns.quantity, columns.revenue} <= set(
        frame.columns
    ):
        return _empty_transactions(frame)

    dates, _format, issues = read_dates(
        frame, schema, columns.date, slot_id=slot_id
    )
    if issues:
        collected.add(
            "MALFORMED_INVOICE_DATE",
            f"The {columns.date} column of {schema.label} cannot be read as "
            "dates, so its rows cannot be placed in a reporting period.",
            details={
                "column": columns.date,
                "issues": [
                    {"code": item.code, "message": item.message}
                    for item in issues
                ],
            },
            slot_id=slot_id,
        )
        return _empty_transactions(frame)

    prepared = frame.with_columns(dates.alias(DATE))

    blank_dates = prepared.filter(pl.col(DATE).is_null()).height
    if blank_dates:
        collected.add(
            "MALFORMED_INVOICE_DATE",
            f"{blank_dates} row(s) of {schema.label} have no "
            f"{columns.date}, so they belong to no reporting period.",
            details={"column": columns.date, "row_count": blank_dates},
            slot_id=slot_id,
        )

    for source, target in (
        (columns.quantity, QUANTITY),
        (columns.revenue, REVENUE),
    ):
        prepared = _with_measure(
            prepared, source, target, schema, collected, slot_id=slot_id
        )

    return prepared.with_columns(
        pl.col(DATE).dt.strftime("%Y-%m").alias(MONTH)
    )


def _with_measure(
    frame: pl.DataFrame,
    source: str,
    target: str,
    schema: SourceSchema,
    collected: _Collected,
    *,
    slot_id: str | None,
) -> pl.DataFrame:
    """Add `target` as the numeric reading of `source`, or report why not.

    Two failures, kept apart because they send a reader to different places
    (rule ``missing_measures``):

    * blank — the export left the cell empty, and reading it as zero would
      understate a total invisibly;
    * unreadable — the export wrote something like ``$1,234.56`` or
      ``(45.00)``, and deciding what it meant would be a guess.

    Nothing is stripped before the cast, for the same reason Phase 10B does
    not strip a date: ``" 12.5"`` is reported rather than quietly repaired.
    """
    series = frame.get_column(source)
    blank = _is_blank(pl.col(source))

    if series.dtype == pl.String:
        numeric = pl.col(source).cast(pl.Float64, strict=False)
    elif series.dtype.is_numeric():
        numeric = pl.col(source).cast(pl.Float64)
    else:
        collected.add(
            "NON_NUMERIC_MEASURE",
            f"The {source} column of {schema.label} holds {series.dtype} "
            "values, which are not numbers.",
            details={"column": source, "dtype": str(series.dtype)},
            slot_id=slot_id,
        )
        return frame.with_columns(pl.lit(None, pl.Float64).alias(target))

    prepared = frame.with_columns(numeric.alias(target))

    missing = prepared.filter(blank)
    if missing.height:
        collected.add(
            "MISSING_MEASURE",
            f"{missing.height} row(s) of {schema.label} have no {source}. A "
            "blank measure is not zero, and reading it as zero would "
            "understate every total it belongs to.",
            details={
                "column": source,
                "row_count": missing.height,
                "examples": _examples(missing, TRANSACTION_COLUMNS.invoice_number),
            },
            slot_id=slot_id,
        )

    unreadable = prepared.filter(~blank & pl.col(target).is_null())
    if unreadable.height:
        collected.add(
            "NON_NUMERIC_MEASURE",
            f"{unreadable.height} row(s) of {schema.label} have a {source} "
            "that is not a number.",
            details={
                "column": source,
                "row_count": unreadable.height,
                "examples": _examples(unreadable, source),
            },
            slot_id=slot_id,
        )

    return prepared


def _attach_owner(
    frame: pl.DataFrame, ownership: pl.DataFrame
) -> pl.DataFrame:
    """Join each transaction to the rep who owns its account.

    A left join, matched exactly on `Customer` (rule ``ownership_matching``).
    A row whose account is not in the snapshot keeps a null owner and is
    reported by :func:`_check_ownership_covers_activity`; it is never assigned
    to the rep named on the document, because that is not what ownership means
    (build plan 9E).
    """
    customer = TRANSACTION_COLUMNS.customer
    if customer not in frame.columns or frame.height == 0:
        return frame.with_columns(pl.lit(None, pl.String).alias(OWNER))

    return frame.join(
        ownership.rename({ASSIGNMENTS_CUSTOMER_COLUMN: customer}).rename(
            {REP_COLUMN: OWNER}
        ),
        on=customer,
        how="left",
    )


# ---------------------------------------------------------------------------
# Checks (build plan 13H)
# ---------------------------------------------------------------------------


def _check_ownership_covers_activity(
    frame: pl.DataFrame,
    period: ReportPeriod,
    collected: _Collected,
    *,
    slot_id: str | None,
) -> None:
    """Fail on an account that traded in a report window and has no owner.

    Scoped to the windows the report actually measures. An account that
    traded three years ago and has since closed is not a problem the current
    snapshot has to answer for; one that traded this month is.
    """
    if frame.height == 0 or OWNER not in frame.columns:
        return

    covered = _in_any_window(period)
    orphans = (
        frame.filter(covered & pl.col(OWNER).is_null())
        .group_by(TRANSACTION_COLUMNS.customer)
        .agg(
            pl.len().alias("rows"),
            pl.col(REVENUE).sum().alias("revenue"),
        )
        .sort("revenue", descending=True)
    )
    if orphans.height == 0:
        return

    accounts = orphans[TRANSACTION_COLUMNS.customer].to_list()
    collected.add(
        "MISSING_ACCOUNT_OWNERSHIP",
        f"{orphans.height} account(s) traded inside a reporting window and "
        "are not in the account-assignment snapshot: "
        f"{_human_list([_label(name) for name in accounts[:MAX_EXAMPLES]])}"
        f"{'' if orphans.height <= MAX_EXAMPLES else ', and others'}. Their "
        "sales would be in the company total and in no rep's report.",
        details={
            "accounts": [_label(name) for name in accounts],
            "account_count": orphans.height,
            "row_count": int(orphans["rows"].sum()),
        },
        slot_id=slot_id,
    )


def _check_sample_period(
    samples: pl.DataFrame,
    period: ReportPeriod,
    collected: _Collected,
    slot_id: str | None,
) -> None:
    """Fail when the sample history skips the reporting month.

    Only when it carries earlier months: a sample history that reaches the
    reporting month and simply has no rows in it is a month in which no
    samples were given, which is a fact rather than a gap (rule
    ``sample_period``).
    """
    months = _months_of(samples)
    if not months or period.month in months:
        return
    if not any(month < period.month for month in months):
        return

    collected.add(
        "SAMPLE_PERIOD_MISMATCH",
        f"The sample history has no data for {period.label} although it has "
        f"data up to {_month_label(months[-1])}. Every rep would be reported "
        "as having given no samples, which is not the same as having given "
        "none.",
        details={
            "report_month": period.month,
            "latest_sample_month": months[-1],
            "sample_months": list(months),
        },
        slot_id=slot_id,
    )


def _check_history_depth(
    months: Sequence[str],
    period: ReportPeriod,
    collected: _Collected,
    slot_id: str | None,
) -> None:
    """Warn when placements rest on less history than the rule assumes."""
    preceding = [month for month in months if month < period.month]
    if len(preceding) >= MINIMUM_PLACEMENT_HISTORY_MONTHS:
        return

    collected.add(
        "SHORT_PLACEMENT_HISTORY",
        f"Only {len(preceding)} month(s) of sales history precede "
        f"{period.label}; the placement rule assumes at least "
        f"{MINIMUM_PLACEMENT_HISTORY_MONTHS}. Placements are overstated, "
        "because a product bought before the history begins looks new.",
        details={
            "months_available": len(preceding),
            "months_expected": MINIMUM_PLACEMENT_HISTORY_MONTHS,
            "report_month": period.month,
        },
        slot_id=slot_id,
    )


def _check_comparison_windows(
    sales: pl.DataFrame, period: ReportPeriod, collected: _Collected
) -> None:
    """Warn for each comparison window the history cannot fill."""
    empty = [
        window
        for window in period.windows
        if window.key is not WindowKey.CURRENT_MONTH
        and sales.filter(window.covers()).height == 0
    ]
    if not empty:
        return

    collected.add(
        "MISSING_COMPARISON_PERIOD",
        "The sales history contains no rows for "
        f"{_human_list([window.label.lower() for window in empty])}, so the "
        "growth figures against it are reported as absent rather than as zero.",
        details={"windows": [window.key.value for window in empty]},
    )


def _check_duplicate_rows(
    source: pl.DataFrame,
    prepared: pl.DataFrame,
    period: ReportPeriod,
    collected: _Collected,
    slot_id: str | None,
) -> None:
    """Warn when identical rows repeat inside the reporting month.

    Compared across every source column, which is this application's own
    definition of a duplicate row (build plan section 26). None is removed:
    two identical invoice lines can be genuine, and removing one would be the
    silent dropping build plan section 3.3 forbids.
    """
    if prepared.height == 0:
        return

    month = prepared.filter(period.window(WindowKey.CURRENT_MONTH).covers())
    columns = [name for name in source.columns if name in month.columns]
    if not columns or month.height == 0:
        return

    repeated = month.height - month.select(columns).unique().height
    if repeated <= 0:
        return

    collected.add(
        "DUPLICATE_SOURCE_ROWS",
        f"{repeated} row(s) in {period.label} repeat another row exactly. "
        "None has been removed — two identical invoice lines can be genuine — "
        "but a month imported twice would look like this.",
        details={"report_month": period.month, "row_count": repeated},
        slot_id=slot_id,
    )


def _check_invoice_types(
    frame: pl.DataFrame, collected: _Collected, slot_id: str | None
) -> None:
    """Warn for an Invoice Type the specification does not expect."""
    column = TRANSACTION_COLUMNS.invoice_type
    if frame.height == 0 or column not in frame.columns:
        return

    seen = [
        value
        for value in frame.get_column(column).cast(pl.String).unique().to_list()
        if value is not None and value.strip()
    ]
    unexpected = sorted(set(seen) - set(KNOWN_INVOICE_TYPES))
    if not unexpected:
        return

    collected.add(
        "UNEXPECTED_INVOICE_TYPE",
        f"{_human_list(unexpected)} "
        f"{'is' if len(unexpected) == 1 else 'are'} not "
        f"{'a value' if len(unexpected) == 1 else 'values'} the report "
        f"expects in {column}. Every row is counted whatever its type, so no "
        "figure changes; the export may have changed.",
        details={
            "column": column,
            "unexpected": unexpected,
            "expected": list(KNOWN_INVOICE_TYPES),
        },
        slot_id=slot_id,
    )


def _check_unrecognised_reps(
    sales: pl.DataFrame,
    samples: pl.DataFrame,
    reps: Sequence[str],
    collected: _Collected,
    slot_id: str | None,
) -> None:
    """Warn for a transaction rep the snapshot does not name."""
    column = TRANSACTION_COLUMNS.transaction_rep
    named: set[str] = set()
    for frame in (sales, samples):
        if frame.height and column in frame.columns:
            named.update(
                value
                for value in frame.get_column(column)
                .cast(pl.String)
                .unique()
                .to_list()
                if value is not None and value.strip()
            )

    unknown = sorted(named - set(reps))
    if not unknown:
        return

    collected.add(
        "UNRECOGNISED_SALES_REP",
        f"{_human_list(unknown)} "
        f"{'appears' if len(unknown) == 1 else 'appear'} on transactions but "
        f"{'is' if len(unknown) == 1 else 'are'} not in the "
        "account-assignment snapshot, so no report is produced for "
        f"{'them' if len(unknown) > 1 else 'them'}. Revenue is unaffected: it "
        "follows each account's owner.",
        details={"reps": unknown, "column": column},
        slot_id=slot_id,
    )


def _check_source_columns(
    frame: pl.DataFrame,
    schema: SourceSchema,
    collected: _Collected,
    slot_id: str | None,
) -> None:
    """Warn for columns the source carries that its schema does not declare."""
    unexpected = schema.unexpected_in(tuple(frame.columns))
    if not unexpected:
        return

    collected.add(
        "UNEXPECTED_SOURCE_COLUMNS",
        f"{schema.label} carries {_human_list(list(unexpected))}, which its "
        "schema does not declare. The report reads only declared columns, so "
        "nothing it calculates is affected — but the export may have changed.",
        details={"columns": list(unexpected), "dataset": schema.dataset_id},
        slot_id=slot_id,
    )


def _note_provisional_rules(collected: _Collected) -> None:
    """Report, on every Run, that some definitions are not yet confirmed.

    It disappears by itself: :data:`PROVISIONAL_RULES` is derived from the
    declarations, so confirming the last rule removes this warning without
    anybody having to remember to.
    """
    if not PROVISIONAL_RULES:
        return

    collected.add(
        "PROVISIONAL_REPORT_RULES",
        f"{len(PROVISIONAL_RULES)} of this report's business definitions have "
        "not been confirmed against the finished monthly report: "
        f"{_human_list([item.key for item in PROVISIONAL_RULES])}. See "
        "docs/monthly-sales-rep-report-spec.md.",
        details={"rules": [item.key for item in PROVISIONAL_RULES]},
    )


# ---------------------------------------------------------------------------
# Company figures, calculated once (build plan 13G)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyFigures:
    """What the whole company did, computed once and shared by every rep.

    Build plan 13G: "Calculate company-level comparison data once where
    possible rather than independently rebuilding the same company totals for
    every rep." Every rep's share, index and comparison reads these, so two
    reps can never be shown company totals that disagree.
    """

    #: One row, the company's measures across every window.
    summary: pl.DataFrame

    #: One row per supplier, with the company's share of each.
    suppliers: pl.DataFrame

    @property
    def revenue(self) -> float:
        """Company revenue in the reporting month."""
        return float(self.summary["Revenue"][0]) if self.summary.height else 0.0


def company_figures(prepared: PreparedReport) -> CompanyFigures:
    """Build the company-level tables (build plan 13G)."""
    period = prepared.require_period()
    sales, samples = prepared.sales, prepared.samples

    summary = _windowed(sales, period, group_by=())
    summary = summary.with_columns(
        pl.lit(period.label).alias("Reporting Period"),
        pl.lit(len(prepared.reps), pl.Int64).alias("Sales Reps"),
        pl.lit(prepared.ownership.height, pl.Int64).alias("Accounts"),
        pl.lit(_placements(sales, period).height, pl.Int64).alias("Placements"),
    )
    summary = _with_sample_measures(summary, samples, period, group_by=())
    summary = summary.select(
        "Reporting Period",
        "Sales Reps",
        "Accounts",
        *_measure_column_order(),
        "Placements",
        "Sample Lines",
        "Sample Quantity",
        "Sample Value",
    )

    suppliers = _windowed(
        sales, period, group_by=(TRANSACTION_COLUMNS.supplier,)
    )
    total = float(summary["Revenue"][0]) if summary.height else 0.0
    suppliers = suppliers.with_columns(
        _share(pl.col("Revenue"), total).alias("Share of Company Revenue")
    ).sort(
        ["Revenue", TRANSACTION_COLUMNS.supplier],
        descending=[True, False],
        nulls_last=True,
    )

    return CompanyFigures(summary=summary, suppliers=suppliers)


# ---------------------------------------------------------------------------
# The report tables (build plan 13F)
# ---------------------------------------------------------------------------


def build_tables(prepared: PreparedReport) -> dict[str, pl.DataFrame]:
    """Build every table the specification declares, keyed by section ID.

    Raises:
        ValueError: `prepared` carries errors, so no report may be produced.
            The Action's ``validate`` hook fails the Run long before this, so
            reaching it means a caller skipped that step.
    """
    if prepared.errors:
        raise ValueError(
            "The report cannot be calculated: "
            + "; ".join(issue.code for issue in prepared.errors)
        )

    company = company_figures(prepared)

    return {
        "rep_summary": _rep_summary(prepared, company),
        "company_summary": company.summary,
        "account_performance": _account_performance(prepared),
        "supplier_performance": _supplier_performance(prepared),
        "company_supplier_performance": company.suppliers,
        "supplier_comparison": _supplier_comparison(prepared, company),
        "product_performance": _product_performance(prepared),
        "placements": _placement_table(prepared),
        "placement_detail": _placement_detail(prepared),
        "samples": _sample_table(prepared),
        "sample_detail": _sample_detail(prepared),
        "data_quality": data_quality_table(prepared),
    }


def _rep_summary(
    prepared: PreparedReport, company: CompanyFigures
) -> pl.DataFrame:
    """One row per rep, whether or not they sold anything."""
    period = prepared.require_period()
    roster = pl.DataFrame({REP_COLUMN: list(prepared.reps)}, schema={REP_COLUMN: pl.String})

    measures = _windowed(prepared.sales, period, group_by=(OWNER,)).rename(
        {OWNER: REP_COLUMN}
    )
    accounts = (
        prepared.ownership.group_by(REP_COLUMN)
        .agg(pl.len().alias("Accounts"))
    )
    placements = (
        _placements(prepared.sales, period)
        .group_by(OWNER)
        .agg(pl.len().alias("Placements"))
        .rename({OWNER: REP_COLUMN})
    )

    table = (
        roster.join(measures, on=REP_COLUMN, how="left")
        .join(accounts, on=REP_COLUMN, how="left")
        .join(placements, on=REP_COLUMN, how="left")
    )
    table = _with_sample_measures(
        table, prepared.samples, period, group_by=(OWNER,), on=REP_COLUMN
    )
    table = _fill_counts(
        table,
        [*_count_columns(), "Accounts", "Placements", "Sample Lines",
         "Sample Quantity", "Sample Value"],
    )
    table = table.with_columns(
        _share(pl.col("Revenue"), company.revenue).alias(
            "Share of Company Revenue"
        )
    )

    return table.select(
        REP_COLUMN,
        "Accounts",
        *_measure_column_order(),
        "Share of Company Revenue",
        "Placements",
        "Sample Lines",
        "Sample Quantity",
        "Sample Value",
    ).sort(["Revenue", REP_COLUMN], descending=[True, False], nulls_last=True)


def _account_performance(prepared: PreparedReport) -> pl.DataFrame:
    """One row per account a rep owns, including accounts that did nothing."""
    period = prepared.require_period()
    customer = TRANSACTION_COLUMNS.customer

    owned = prepared.ownership.rename({ASSIGNMENTS_CUSTOMER_COLUMN: customer})
    measures = _windowed(
        prepared.sales, period, group_by=(OWNER, customer)
    ).rename({OWNER: REP_COLUMN})
    placements = (
        _placements(prepared.sales, period)
        .group_by([OWNER, customer])
        .agg(pl.len().alias("Placements"))
        .rename({OWNER: REP_COLUMN})
    )

    table = (
        owned.join(measures, on=[REP_COLUMN, customer], how="left")
        .join(placements, on=[REP_COLUMN, customer], how="left")
        .join(_customer_types(prepared.sales), on=customer, how="left")
    )
    table = _with_sample_measures(
        table,
        prepared.samples,
        period,
        group_by=(OWNER, customer),
        on=[REP_COLUMN, customer],
    )
    table = _fill_counts(
        table,
        [*_count_columns(), "Placements", "Sample Lines", "Sample Quantity",
         "Sample Value"],
    )
    table = table.with_columns(
        _share(
            pl.col("Revenue"), pl.col("Revenue").sum().over(REP_COLUMN)
        ).alias("Share of Rep Revenue")
    )

    return table.select(
        REP_COLUMN,
        customer,
        TRANSACTION_COLUMNS.customer_type,
        *_measure_column_order(),
        "Share of Rep Revenue",
        "Placements",
        "Sample Lines",
        "Sample Value",
    ).sort(
        [REP_COLUMN, "Revenue", customer],
        descending=[False, True, False],
        nulls_last=True,
    )


def _supplier_performance(prepared: PreparedReport) -> pl.DataFrame:
    """One row per supplier each rep sold, with its share of their sales."""
    period = prepared.require_period()
    supplier = TRANSACTION_COLUMNS.supplier

    table = _windowed(
        prepared.sales, period, group_by=(OWNER, supplier)
    ).rename({OWNER: REP_COLUMN})
    table = table.filter(pl.col(REP_COLUMN).is_not_null())
    table = table.with_columns(
        _share(
            pl.col("Revenue"), pl.col("Revenue").sum().over(REP_COLUMN)
        ).alias("Share of Rep Revenue")
    )

    return table.select(
        REP_COLUMN,
        supplier,
        *_measure_column_order(),
        "Share of Rep Revenue",
    ).sort(
        [REP_COLUMN, "Revenue", supplier],
        descending=[False, True, False],
        nulls_last=True,
    )


def _supplier_comparison(
    prepared: PreparedReport, company: CompanyFigures
) -> pl.DataFrame:
    """Every company supplier beside each rep's own figures for it.

    Every rep is given a row for every supplier the company sold, not only for
    the ones they sold themselves. A supplier a rep sells nothing of is the
    interesting row in a comparison, and dropping it would leave the gap
    invisible.
    """
    period = prepared.require_period()
    supplier = TRANSACTION_COLUMNS.supplier

    rep_figures = (
        _windowed(prepared.sales, period, group_by=(OWNER, supplier))
        .rename({OWNER: REP_COLUMN})
        .filter(pl.col(REP_COLUMN).is_not_null())
        .select(REP_COLUMN, supplier, pl.col("Revenue").alias("Rep Revenue"))
    )
    company_figures_ = company.suppliers.select(
        supplier,
        pl.col("Revenue").alias("Company Revenue"),
        pl.col("Share of Company Revenue").alias("Company Share"),
    )

    grid = pl.DataFrame(
        {REP_COLUMN: list(prepared.reps)}, schema={REP_COLUMN: pl.String}
    ).join(company_figures_, how="cross")

    table = (
        grid.join(rep_figures, on=[REP_COLUMN, supplier], how="left")
        .with_columns(pl.col("Rep Revenue").fill_null(0.0))
        .with_columns(
            _share(
                pl.col("Rep Revenue"),
                pl.col("Rep Revenue").sum().over(REP_COLUMN),
            ).alias("Rep Share")
        )
        .with_columns(
            (pl.col("Rep Share") - pl.col("Company Share")).alias(
                "Share Difference"
            ),
            pl.when(
                pl.col("Company Share").is_not_null()
                & (pl.col("Company Share") != 0)
            )
            .then(pl.col("Rep Share") / pl.col("Company Share"))
            .otherwise(None)
            .alias("Index"),
        )
    )

    return table.select(
        REP_COLUMN,
        supplier,
        "Rep Revenue",
        "Rep Share",
        "Company Revenue",
        "Company Share",
        "Share Difference",
        "Index",
    ).sort(
        [REP_COLUMN, "Rep Revenue", supplier],
        descending=[False, True, False],
        nulls_last=True,
    )


def _product_performance(prepared: PreparedReport) -> pl.DataFrame:
    """One row per product each rep sold in the reporting month."""
    period = prepared.require_period()
    key = list(TRANSACTION_COLUMNS.product_key)

    table = _windowed(
        prepared.sales, period, group_by=(OWNER, *key)
    ).rename({OWNER: REP_COLUMN})
    table = table.filter(pl.col(REP_COLUMN).is_not_null())

    accounts = (
        prepared.sales.filter(
            period.window(WindowKey.CURRENT_MONTH).covers()
            & pl.col(OWNER).is_not_null()
        )
        .group_by([OWNER, *key])
        .agg(pl.col(TRANSACTION_COLUMNS.customer).n_unique().alias("Accounts"))
        .rename({OWNER: REP_COLUMN})
    )

    table = table.join(accounts, on=[REP_COLUMN, *key], how="left")
    table = _fill_counts(table, ["Accounts"])

    return table.select(
        REP_COLUMN, *key, *_measure_column_order(), "Accounts"
    ).sort(
        [REP_COLUMN, "Revenue", TRANSACTION_COLUMNS.sku],
        descending=[False, True, False],
        nulls_last=True,
    )


def _placement_table(prepared: PreparedReport) -> pl.DataFrame:
    """One row per new placement (rule ``placement``)."""
    period = prepared.require_period()
    placements = _placements(prepared.sales, period)
    key = [TRANSACTION_COLUMNS.customer, TRANSACTION_COLUMNS.sku]

    descriptions = (
        prepared.sales.filter(period.window(WindowKey.CURRENT_MONTH).covers())
        .sort([DATE, TRANSACTION_COLUMNS.invoice_number])
        .group_by(key)
        .agg(
            *[
                pl.col(name).last().alias(name)
                for name in TRANSACTION_COLUMNS.product_key
                if name != TRANSACTION_COLUMNS.sku
            ]
        )
    )

    table = placements.rename({OWNER: REP_COLUMN}).join(
        descriptions, on=key, how="left"
    )

    return table.select(
        REP_COLUMN,
        TRANSACTION_COLUMNS.customer,
        *TRANSACTION_COLUMNS.product_key,
        pl.col("First Sold").alias("First Sold"),
        "Quantity",
        "Revenue",
    ).sort(
        [
            REP_COLUMN,
            "Revenue",
            TRANSACTION_COLUMNS.customer,
            TRANSACTION_COLUMNS.sku,
        ],
        descending=[False, True, False, False],
        nulls_last=True,
    )


def _placement_detail(prepared: PreparedReport) -> pl.DataFrame:
    """The reporting-month lines behind the placements."""
    period = prepared.require_period()
    key = [TRANSACTION_COLUMNS.customer, TRANSACTION_COLUMNS.sku]
    placements = _placements(prepared.sales, period).select(key)

    lines = prepared.sales.filter(
        period.window(WindowKey.CURRENT_MONTH).covers()
    ).join(placements, on=key, how="semi")

    return _detail_table(lines)


def _sample_table(prepared: PreparedReport) -> pl.DataFrame:
    """One row per account sampled in the reporting month."""
    period = prepared.require_period()
    customer = TRANSACTION_COLUMNS.customer

    table = (
        prepared.samples.filter(
            period.window(WindowKey.CURRENT_MONTH).covers()
        )
        .group_by([OWNER, customer])
        .agg(
            pl.len().alias("Sample Lines"),
            pl.col(QUANTITY).sum().alias("Sample Quantity"),
            _money(pl.col(REVENUE).sum()).alias("Sample Value"),
            pl.col(TRANSACTION_COLUMNS.sku).n_unique().alias("Sample Products"),
        )
        .rename({OWNER: REP_COLUMN})
        .filter(pl.col(REP_COLUMN).is_not_null())
    )

    return table.select(
        REP_COLUMN,
        customer,
        "Sample Lines",
        "Sample Quantity",
        "Sample Value",
        "Sample Products",
    ).sort(
        [REP_COLUMN, "Sample Value", customer],
        descending=[False, True, False],
        nulls_last=True,
    )


def _sample_detail(prepared: PreparedReport) -> pl.DataFrame:
    """The reporting-month sample lines."""
    period = prepared.require_period()
    lines = prepared.samples.filter(
        period.window(WindowKey.CURRENT_MONTH).covers()
    )
    return _detail_table(lines)


def data_quality_table(prepared: PreparedReport) -> pl.DataFrame:
    """The supporting validation table (build plan 13F, 13H).

    Warnings only. An error never reaches here, because an error fails the Run
    before a single table is calculated — which is build plan 13H's "fail
    rather than producing a plausible-looking workbook".
    """
    rows = [
        {
            "Severity": severity_of(issue.code).value.title(),
            "Code": issue.code,
            "Condition": condition(issue.code).summary,
            "Message": issue.message,
            "Affected Rows": _affected_rows(issue),
            "Input": issue.slot_id or "",
        }
        for issue in prepared.warnings
    ]

    schema = {
        "Severity": pl.String,
        "Code": pl.String,
        "Condition": pl.String,
        "Message": pl.String,
        "Affected Rows": pl.Int64,
        "Input": pl.String,
    }
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema).sort(["Severity", "Code"])


def report_metrics(prepared: PreparedReport) -> dict[str, int]:
    """Counts worth reporting on the Run (build plan 6E.1).

    Integers only, the same contract the two proof Actions keep. Money belongs
    in `company_summary`, where it is stated at the report's own precision
    beside the period it is a total for.
    """
    period = prepared.period
    month_rows = (
        prepared.sales.filter(
            period.window(WindowKey.CURRENT_MONTH).covers()
        ).height
        if period is not None
        else 0
    )
    sample_rows = (
        prepared.samples.filter(
            period.window(WindowKey.CURRENT_MONTH).covers()
        ).height
        if period is not None
        else 0
    )
    placements = (
        _placements(prepared.sales, period).height if period is not None else 0
    )

    return {
        "sales_reps": len(prepared.reps),
        "accounts": prepared.ownership.height,
        "history_months": len(prepared.history_months),
        "sales_rows": month_rows,
        "sample_rows": sample_rows,
        "placements": placements,
        "warnings": len(prepared.warnings),
    }


# ---------------------------------------------------------------------------
# Shared calculation helpers
# ---------------------------------------------------------------------------


def _windowed(
    frame: pl.DataFrame,
    period: ReportPeriod,
    *,
    group_by: Sequence[str],
) -> pl.DataFrame:
    """Measure `frame` over every window, one column set per window.

    The one aggregation every table is built from (build plan 13E). Grouping
    by nothing produces the company's single row; grouping by the owner
    produces one row per rep; grouping by owner and account produces the
    account table. The arithmetic is written once.
    """
    keys = list(group_by)
    parts: list[pl.DataFrame] = []

    for window in period.windows:
        prefix = WINDOW_PREFIXES[window.key]
        measured = frame.filter(window.covers())
        aggregations = [
            _money(pl.col(REVENUE).sum()).alias(f"{prefix}Revenue"),
            pl.col(QUANTITY).sum().alias(f"{prefix}Quantity"),
            pl.len().alias(f"{prefix}Lines"),
            pl.col(TRANSACTION_COLUMNS.customer)
            .n_unique()
            .alias(f"{prefix}Accounts Sold"),
        ]
        if keys:
            part = measured.group_by(keys).agg(aggregations)
        else:
            part = measured.select(aggregations)
        parts.append(part)

    table = parts[0]
    for part in parts[1:]:
        # Grouped windows are joined on their keys, because a group can be
        # present in one window and absent from another. The company's single
        # row has no keys and every part is exactly one row, so the windows
        # are stacked side by side; `hstack` is the operation that says both
        # of those things and checks the second.
        table = (
            table.join(part, on=keys, how="full", coalesce=True)
            if keys
            else table.hstack(part)
        )

    table = _fill_counts(table, _count_columns())
    return table.with_columns(
        [
            _growth(
                pl.col(measure_column(current, "Revenue")),
                pl.col(measure_column(prior, "Revenue")),
            ).alias(name)
            for name, current, prior in GROWTH_COLUMNS
        ]
    )


#: The measures every window is summarised by, in the order a table shows them.
MEASURES: tuple[str, ...] = ("Revenue", "Quantity", "Lines", "Accounts Sold")

#: The growth figures a windowed table derives, and which window each compares
#: against which. Declared as data so the column order below and the
#: expressions that fill them cannot drift apart.
GROWTH_COLUMNS: tuple[tuple[str, WindowKey, WindowKey], ...] = (
    ("MoM Growth", WindowKey.CURRENT_MONTH, WindowKey.PRIOR_MONTH),
    ("YoY Growth", WindowKey.CURRENT_MONTH, WindowKey.PRIOR_YEAR_MONTH),
    (
        "YTD Growth",
        WindowKey.YEAR_TO_DATE,
        WindowKey.PRIOR_YEAR_TO_DATE,
    ),
)


def _count_columns() -> list[str]:
    """Every summed or counted column a windowed table carries.

    Separate from :func:`_measure_column_order` because an absent count means
    zero and an absent share or growth means *no answer* — filling the second
    the way the first is filled would state a ratio that does not exist
    (rule ``share``).
    """
    return [
        measure_column(window, measure)
        for window in WindowKey
        for measure in MEASURES
    ]


def _measure_column_order() -> list[str]:
    """Every measure and growth column, in the order a table shows them.

    Each comparison window follows the window it is compared against, so a
    reader meets this month, then last month, then the change between them.
    """
    order: list[str] = []
    growth_after = {
        source: name for name, _current, source in GROWTH_COLUMNS
    }
    for window in WindowKey:
        order.extend(measure_column(window, measure) for measure in MEASURES)
        if window in growth_after:
            order.append(growth_after[window])
    return order


def _with_sample_measures(
    table: pl.DataFrame,
    samples: pl.DataFrame,
    period: ReportPeriod,
    *,
    group_by: Sequence[str],
    on: str | Sequence[str] | None = None,
) -> pl.DataFrame:
    """Join the reporting month's sample measures onto `table`."""
    keys = list(group_by)
    measured = samples.filter(period.window(WindowKey.CURRENT_MONTH).covers())
    aggregations = [
        pl.len().alias("Sample Lines"),
        pl.col(QUANTITY).sum().alias("Sample Quantity"),
        _money(pl.col(REVENUE).sum()).alias("Sample Value"),
    ]

    if not keys:
        part = measured.select(aggregations)
        return table.hstack(part)

    part = measured.group_by(keys).agg(aggregations)
    renames = {OWNER: REP_COLUMN} if OWNER in keys else {}
    part = part.rename(renames)

    join_on: list[str] = (
        [on or REP_COLUMN] if on is None or isinstance(on, str) else list(on)
    )
    return table.join(part, on=join_on, how="left")


def _placements(frame: pl.DataFrame, period: ReportPeriod) -> pl.DataFrame:
    """The (account, product) pairs first sold in the reporting month.

    Rule ``placement``. A pair qualifies when its first row of *positive*
    quantity anywhere in the history the Run read falls inside the reporting
    month, so a credit never creates a placement and a product bought before
    the history begins never looks new twice.
    """
    key = [TRANSACTION_COLUMNS.customer, TRANSACTION_COLUMNS.sku]
    bought = frame.filter(pl.col(QUANTITY) > 0)
    if bought.height == 0:
        return _empty_placements()

    first = bought.group_by(key).agg(pl.col(DATE).min().alias("First Sold"))
    new = first.filter(
        period.window(WindowKey.CURRENT_MONTH).covers("First Sold")
    )
    if new.height == 0:
        return _empty_placements()

    month = bought.filter(period.window(WindowKey.CURRENT_MONTH).covers())
    measured = month.join(new, on=key, how="semi").group_by([OWNER, *key]).agg(
        pl.col(QUANTITY).sum().alias("Quantity"),
        _money(pl.col(REVENUE).sum()).alias("Revenue"),
    )
    return measured.join(new, on=key, how="left")


def _detail_table(lines: pl.DataFrame) -> pl.DataFrame:
    """Render transaction lines as a detail table, in report column order."""
    columns = TRANSACTION_COLUMNS
    wanted = [
        columns.invoice_type,
        columns.invoice_number,
        columns.customer,
        *columns.product_key,
        columns.quantity,
        columns.item_price,
        columns.revenue,
    ]
    present = [name for name in wanted if name in lines.columns]

    if lines.height == 0:
        return pl.DataFrame(
            schema={
                REP_COLUMN: pl.String,
                columns.date: pl.Date,
                **{name: lines.schema[name] for name in present},
            }
        )

    return (
        lines.select(
            pl.col(OWNER).alias(REP_COLUMN),
            pl.col(DATE).alias(columns.date),
            *present,
        )
        .filter(pl.col(REP_COLUMN).is_not_null())
        .sort(
            [REP_COLUMN, columns.date, columns.customer, columns.sku],
            nulls_last=True,
        )
    )


def _customer_types(sales: pl.DataFrame) -> pl.DataFrame:
    """Each account's most recently recorded classification.

    Presentation metadata rather than a measure, so the most recent row wins
    when an account's `Cust Type` changed. Ordered by date and then invoice
    number so the answer does not depend on the order rows arrived in.
    """
    customer = TRANSACTION_COLUMNS.customer
    kind = TRANSACTION_COLUMNS.customer_type
    if kind not in sales.columns or sales.height == 0:
        return pl.DataFrame(schema={customer: pl.String, kind: pl.String})

    return (
        sales.sort([DATE, TRANSACTION_COLUMNS.invoice_number], nulls_last=True)
        .group_by(customer)
        .agg(pl.col(kind).last().alias(kind))
    )


def _share(part: pl.Expr, whole: pl.Expr | float) -> pl.Expr:
    """`part` as a fraction of `whole`, or null when there is no whole.

    Rule ``share``: a zero or absent whole gives no share, never zero. Zero
    would be a claim about a ratio that does not exist.
    """
    total = pl.lit(whole) if isinstance(whole, (int, float)) else whole
    return (
        pl.when(total.is_not_null() & (total != 0))
        .then(part / total)
        .otherwise(None)
    )


def _growth(current: pl.Expr, prior: pl.Expr) -> pl.Expr:
    """Change from `prior` to `current` as a fraction (rule ``growth``).

    The denominator is the absolute prior value, so the sign of the change
    stays meaningful when the prior period was negative — which a month
    dominated by credits can be.
    """
    return (
        pl.when(prior.is_not_null() & (prior != 0))
        .then((current - prior) / prior.abs())
        .otherwise(None)
    )


def _money(value: pl.Expr) -> pl.Expr:
    """State an aggregated money figure at the source's own precision."""
    return value.round(MONEY_DECIMALS)


def _fill_counts(frame: pl.DataFrame, columns: Iterable[str]) -> pl.DataFrame:
    """Read an absent aggregate as zero, for counted columns only.

    A rep with no rows in a window did not sell nothing-we-don't-know; they
    sold nothing, and a left join reports that as null. Filling it with zero
    states the fact. It is applied only to *counts and sums*, never to a share
    or a growth figure: those genuinely have no answer when their denominator
    is missing, and rule ``share`` says so.
    """
    present = [name for name in columns if name in frame.columns]
    if not present:
        return frame
    return frame.with_columns(
        [pl.col(name).fill_null(0) for name in present]
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_present(column: pl.Expr) -> pl.Expr:
    """Whether a text value says something."""
    return column.is_not_null() & (column.str.strip_chars() != "")


def _is_blank(column: pl.Expr) -> pl.Expr:
    """Whether a cell is empty — null, or text with nothing in it."""
    return column.is_null() | (column.cast(pl.String).str.strip_chars() == "")


def _months_of(frame: pl.DataFrame) -> tuple[str, ...]:
    """Every reporting month present in a prepared frame, oldest first."""
    if frame.height == 0 or MONTH not in frame.columns:
        return ()
    months = frame.get_column(MONTH).drop_nulls().unique().to_list()
    return tuple(sorted(months))


def _in_any_window(period: ReportPeriod) -> pl.Expr:
    """Rows inside any window the report measures."""
    expression = period.windows[0].covers()
    for window in period.windows[1:]:
        expression = expression | window.covers()
    return expression


def _examples(frame: pl.DataFrame, column: str) -> list[str]:
    """Up to :data:`MAX_EXAMPLES` values of `column`, as text.

    Offending rows are reported by example rather than by row number: a
    history slot merges several months into one frame, so "row 42" would name
    a position in a concatenation and no position in any file the user has.
    """
    if column not in frame.columns:
        return []
    values = frame.get_column(column).head(MAX_EXAMPLES).to_list()
    return [_label(value) for value in values]


def _label(value: object) -> str:
    """A value as text, with a blank rendered visibly rather than as ''."""
    if value is None:
        return "(blank)"
    text = str(value)
    return text if text.strip() else "(blank)"


def _human_list(values: Sequence[str]) -> str:
    """Join values the way a sentence would."""
    items = [str(value) for value in values]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _month_label(month: str) -> str:
    """``2026-09`` as ``September 2026``."""
    year, number = _split_month(month)
    return f"{date(year, number, 1):%B} {year}"


def _split_month(month: str) -> tuple[int, int]:
    year, _, number = month.partition("-")
    return int(year), int(number)


def _shift_month(year: int, number: int, by: int) -> tuple[int, int]:
    index = (year * 12 + (number - 1)) + by
    return index // 12, index % 12 + 1


def _month_end(year: int, number: int) -> date:
    next_year, next_number = _shift_month(year, number, 1)
    return date.fromordinal(date(next_year, next_number, 1).toordinal() - 1)


def _affected_rows(issue: ValidationIssue) -> int | None:
    """The row or item count a warning names, when it names one."""
    for key in ("row_count", "account_count"):
        value = issue.details.get(key)
        if isinstance(value, int):
            return value
    return None


def _empty_ownership() -> pl.DataFrame:
    return pl.DataFrame(
        schema={ASSIGNMENTS_CUSTOMER_COLUMN: pl.String, REP_COLUMN: pl.String}
    )


def _empty_transactions(frame: pl.DataFrame) -> pl.DataFrame:
    """An empty frame with the prepared columns, so callers need no branch."""
    return frame.clear().with_columns(
        pl.lit(None, pl.Date).alias(DATE),
        pl.lit(None, pl.Float64).alias(QUANTITY),
        pl.lit(None, pl.Float64).alias(REVENUE),
        pl.lit(None, pl.String).alias(MONTH),
    )


def _empty_placements() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            OWNER: pl.String,
            TRANSACTION_COLUMNS.customer: pl.String,
            TRANSACTION_COLUMNS.sku: pl.String,
            "Quantity": pl.Float64,
            "Revenue": pl.Float64,
            "First Sold": pl.Date,
        }
    )
