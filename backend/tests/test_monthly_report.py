"""The report calculation engine (build plan 13C-13H).

`test_golden_month.py` proves the *values*. This module proves the machinery
around them: how the reporting period is derived and what the windows are
(13C), that the roster comes from the snapshot rather than from anywhere else
(13D), that preparation happens once and mutates nothing (13E), that the
company's figures are calculated once and every rep reads the same ones (13G),
and — at length — every condition build plan 13H asks the report to detect,
with the severity the specification declares.

Every failure below is driven by changing the golden month in exactly the one
way the test is about, so a test named for a blank `Total Price` differs from a
clean report in a blank `Total Price` and in nothing else.

Nothing here reaches the Data Library or a file. The engine takes frames.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.models.report_spec import (
    ASSIGNMENTS_CUSTOMER_COLUMN,
    ASSIGNMENTS_REP_COLUMN,
    REP_COLUMN,
    TRANSACTION_COLUMNS,
    Severity,
    WindowKey,
    severity_of,
)
from app.services import monthly_report as engine

from tests.fixtures import report_months as golden
from tests.fixtures.report_months import (
    ACME,
    BETH,
    BISTRO,
    CORNER,
    GOLDEN_MONTH,
    HARBOUR,
    JENNIFER,
    KEVIN,
)

DATE_COLUMN = TRANSACTION_COLUMNS.date
CUSTOMER = TRANSACTION_COLUMNS.customer
REVENUE = TRANSACTION_COLUMNS.revenue
QUANTITY = TRANSACTION_COLUMNS.quantity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def prepare(
    sales: pl.DataFrame | None = None,
    samples: pl.DataFrame | None = None,
    assignments: pl.DataFrame | None = None,
) -> engine.PreparedReport:
    """Prepare the golden month, with any part of it replaced."""
    return engine.prepare(
        golden.sales_frame() if sales is None else sales,
        golden.sample_frame() if samples is None else samples,
        golden.assignment_frame() if assignments is None else assignments,
    )


def codes(issues) -> list[str]:
    return sorted(issue.code for issue in issues)


@pytest.fixture
def clean() -> engine.PreparedReport:
    """The golden month, prepared and usable."""
    prepared = prepare()
    assert prepared.usable, codes(prepared.errors)
    return prepared


# ---------------------------------------------------------------------------
# The reporting period (build plan 13C)
# ---------------------------------------------------------------------------


def test_the_reporting_period_is_the_newest_month_in_the_sales_history(
    clean,
) -> None:
    assert clean.period is not None
    assert clean.period.month == GOLDEN_MONTH
    assert clean.period.label == "September 2026"


def test_the_period_is_read_from_the_data_not_from_anything_else() -> None:
    """Build plan 10B's rule, applied to the report (rule ``report_month``)."""
    trimmed = golden.sales_frame(("2025-08", "2025-09", "2026-08"))

    prepared = prepare(sales=trimmed)

    assert prepared.period is not None
    assert prepared.period.month == "2026-08"


def test_all_five_windows_are_derived_from_the_one_month(clean) -> None:
    period = clean.require_period()

    assert {window.key for window in period.windows} == set(WindowKey)
    assert period.window(WindowKey.CURRENT_MONTH).start == date(2026, 9, 1)
    assert period.window(WindowKey.CURRENT_MONTH).end == date(2026, 9, 30)
    assert period.window(WindowKey.PRIOR_MONTH).start == date(2026, 8, 1)
    assert period.window(WindowKey.PRIOR_MONTH).end == date(2026, 8, 31)
    assert period.window(WindowKey.PRIOR_YEAR_MONTH).start == date(2025, 9, 1)
    assert period.window(WindowKey.PRIOR_YEAR_MONTH).end == date(2025, 9, 30)
    assert period.window(WindowKey.YEAR_TO_DATE).start == date(2026, 1, 1)
    assert period.window(WindowKey.YEAR_TO_DATE).end == date(2026, 9, 30)
    assert period.window(WindowKey.PRIOR_YEAR_TO_DATE).start == date(2025, 1, 1)
    assert period.window(WindowKey.PRIOR_YEAR_TO_DATE).end == date(2025, 9, 30)


@pytest.mark.parametrize(
    "month,start,end",
    [
        ("2026-01", date(2026, 1, 1), date(2026, 1, 31)),
        ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),
        ("2024-02", date(2024, 2, 1), date(2024, 2, 29)),
        ("2026-12", date(2026, 12, 1), date(2026, 12, 31)),
    ],
)
def test_a_month_window_ends_on_the_last_day_of_that_month(
    month, start, end
) -> None:
    window = engine.report_period(month).window(WindowKey.CURRENT_MONTH)

    assert (window.start, window.end) == (start, end)


def test_january_compares_with_the_december_before_it() -> None:
    period = engine.report_period("2026-01")

    assert period.window(WindowKey.PRIOR_MONTH).start == date(2025, 12, 1)
    assert period.window(WindowKey.PRIOR_MONTH).end == date(2025, 12, 31)
    assert period.window(WindowKey.YEAR_TO_DATE).start == date(2026, 1, 1)
    assert period.window(WindowKey.YEAR_TO_DATE).end == date(2026, 1, 31)


def test_a_leap_february_compares_with_a_non_leap_february() -> None:
    """Neither window is truncated to match the other."""
    period = engine.report_period("2024-02")

    assert period.window(WindowKey.CURRENT_MONTH).end == date(2024, 2, 29)
    assert period.window(WindowKey.PRIOR_YEAR_MONTH).end == date(2023, 2, 28)


def test_a_window_is_inclusive_of_both_ends(clean) -> None:
    window = clean.require_period().window(WindowKey.CURRENT_MONTH)
    covered = clean.sales.filter(window.covers())

    dates = covered.get_column(engine.DATE).to_list()
    assert min(dates) >= window.start
    assert max(dates) <= window.end
    # The credit on the 20th and the invoice on the 2nd are both inside it.
    assert date(2026, 9, 2) in dates
    assert date(2026, 9, 20) in dates


# ---------------------------------------------------------------------------
# The rep roster (build plan 13D)
# ---------------------------------------------------------------------------


def test_the_roster_comes_from_the_assignment_snapshot(clean) -> None:
    assert clean.reps == (BETH, JENNIFER, KEVIN)


def test_a_rep_the_snapshot_adds_appears_without_a_code_change() -> None:
    extended = golden.assignment_frame(
        (*golden.OWNERSHIP, ("New Bar", "Dana Ruiz"))
    )

    prepared = prepare(assignments=extended)

    assert "Dana Ruiz" in prepared.reps


def test_a_rep_the_snapshot_drops_stops_appearing() -> None:
    without_kevin = golden.assignment_frame(
        tuple(pair for pair in golden.OWNERSHIP if pair[1] != KEVIN)
        + ((BISTRO, BETH),)
    )

    prepared = prepare(assignments=without_kevin)

    assert KEVIN not in prepared.reps
    assert prepared.reps == (BETH, JENNIFER)


def test_a_rep_with_no_activity_is_still_on_the_roster(clean) -> None:
    """Jennifer owns one account that never traded."""
    assert JENNIFER in clean.reps

    summary = engine.build_tables(clean)["rep_summary"]
    row = summary.filter(pl.col(REP_COLUMN) == JENNIFER)

    assert row.height == 1
    assert row["Revenue"][0] == 0.0


def test_a_blank_rep_in_the_snapshot_is_not_a_rep() -> None:
    with_blank = golden.assignment_frame(
        (*golden.OWNERSHIP, ("Ghost Bar", "   "))
    )

    prepared = prepare(assignments=with_blank)

    assert prepared.reps == (BETH, JENNIFER, KEVIN)


def test_the_roster_is_not_read_from_the_transactions() -> None:
    """Ownership is the snapshot's job, not the invoice's (build plan 9E)."""
    renamed = golden.sales_frame().with_columns(
        pl.lit("Someone Else").alias(TRANSACTION_COLUMNS.transaction_rep)
    )

    prepared = prepare(sales=renamed)

    assert "Someone Else" not in prepared.reps
    assert prepared.reps == (BETH, JENNIFER, KEVIN)


# ---------------------------------------------------------------------------
# The prepared model (build plan 13E)
# ---------------------------------------------------------------------------


def test_preparation_does_not_touch_the_frames_it_was_given() -> None:
    """Build plan 13E: do not mutate original Data Library versions."""
    sales = golden.sales_frame()
    samples = golden.sample_frame()
    assignments = golden.assignment_frame()
    before = (sales.clone(), samples.clone(), assignments.clone())

    engine.prepare(sales, samples, assignments)

    assert sales.equals(before[0])
    assert samples.equals(before[1])
    assert assignments.equals(before[2])
    assert sales.schema == before[0].schema


def test_preparing_twice_gives_the_same_answer() -> None:
    """Which is what lets validate() and run() each prepare for themselves."""
    first = prepare()
    second = prepare()

    assert codes(first.errors) == codes(second.errors)
    assert codes(first.warnings) == codes(second.warnings)
    assert first.reps == second.reps
    assert first.sales.equals(second.sales)


def test_the_report_is_deterministic_over_the_same_inputs(clean) -> None:
    first = engine.build_tables(clean)
    second = engine.build_tables(prepare())

    assert set(first) == set(second)
    for name, frame in first.items():
        assert frame.equals(second[name]), f"{name} differs between runs"


def test_row_order_in_the_source_does_not_change_the_report(clean) -> None:
    """Rule ``sorting``: ties break by name, never by arrival order."""
    reversed_rows = golden.sales_frame().reverse()

    shuffled = engine.build_tables(prepare(sales=reversed_rows))
    original = engine.build_tables(clean)

    for name, frame in original.items():
        assert frame.equals(shuffled[name]), f"{name} depends on row order"


def test_the_internal_columns_never_reach_a_report_table(clean) -> None:
    """Working state stays out of the user's data (build plan 6E.6)."""
    internal = {engine.DATE, engine.MONTH, engine.REVENUE, engine.QUANTITY,
                engine.OWNER}

    for name, frame in engine.build_tables(clean).items():
        assert not internal & set(frame.columns), f"{name} leaks working state"


def test_accents_survive_every_table(clean) -> None:
    tables = engine.build_tables(clean)

    producers = set(tables["product_performance"]["Producer"].to_list())
    assert "Château Margaux" in producers
    assert "Bodega Muñoz" in producers
    assert "Domaine Père" in producers

    accounts = set(tables["account_performance"][CUSTOMER].to_list())
    assert BISTRO in accounts


def test_the_history_months_are_recorded_oldest_first(clean) -> None:
    assert clean.history_months == ("2025-08", "2025-09", "2026-08", "2026-09")


def test_a_date_column_already_holding_dates_is_used_as_it_stands() -> None:
    """The workbook path stores real dates; the CSV path stores text."""
    typed = golden.sales_frame().with_columns(
        pl.col(DATE_COLUMN).str.to_date("%Y-%m-%d")
    )

    prepared = prepare(sales=typed)

    assert prepared.usable, codes(prepared.errors)
    assert prepared.period is not None
    assert prepared.period.month == GOLDEN_MONTH


# ---------------------------------------------------------------------------
# Company figures, calculated once (build plan 13G)
# ---------------------------------------------------------------------------


def test_the_company_figures_are_one_row(clean) -> None:
    figures = engine.company_figures(clean)

    assert figures.summary.height == 1


def test_every_rep_is_compared_against_the_same_company_figures(clean) -> None:
    """Build plan 13G: one company total, not one per rep."""
    tables = engine.build_tables(clean)
    comparison = tables["supplier_comparison"]

    for supplier in comparison[TRANSACTION_COLUMNS.supplier].unique():
        rows = comparison.filter(
            pl.col(TRANSACTION_COLUMNS.supplier) == supplier
        )
        assert rows["Company Revenue"].n_unique() == 1
        assert rows["Company Share"].n_unique() == 1


def test_the_rep_totals_add_up_to_the_company_total(clean) -> None:
    """True only because every account has exactly one owner."""
    tables = engine.build_tables(clean)

    reps = tables["rep_summary"]["Revenue"].sum()
    company = tables["company_summary"]["Revenue"][0]

    assert reps == pytest.approx(company)


def test_the_company_sees_every_supplier_a_rep_sees(clean) -> None:
    tables = engine.build_tables(clean)

    rep_suppliers = set(
        tables["supplier_performance"][TRANSACTION_COLUMNS.supplier].to_list()
    )
    company_suppliers = set(
        tables["company_supplier_performance"][
            TRANSACTION_COLUMNS.supplier
        ].to_list()
    )

    assert rep_suppliers <= company_suppliers


def test_every_rep_gets_a_row_for_every_company_supplier(clean) -> None:
    """A supplier a rep sells none of is the interesting comparison row."""
    tables = engine.build_tables(clean)
    comparison = tables["supplier_comparison"]

    assert comparison.height == len(clean.reps) * tables[
        "company_supplier_performance"
    ].height
    kevin = comparison.filter(pl.col(REP_COLUMN) == KEVIN)
    assert set(kevin[TRANSACTION_COLUMNS.supplier].to_list()) == {
        golden.ACME_IMPORTS,
        golden.GLOBAL_VINES,
    }


# ---------------------------------------------------------------------------
# Shares, growth, zero and null (rules ``share`` and ``growth``)
# ---------------------------------------------------------------------------


def test_a_share_of_nothing_is_no_share_rather_than_zero(clean) -> None:
    """Jennifer sold nothing, so her supplier shares have no denominator."""
    comparison = engine.build_tables(clean)["supplier_comparison"]
    jennifer = comparison.filter(pl.col(REP_COLUMN) == JENNIFER)

    assert jennifer.height == 2
    assert jennifer["Rep Share"].to_list() == [None, None]
    assert jennifer["Index"].to_list() == [None, None]


def test_a_zero_part_of_a_real_whole_is_a_zero_share(clean) -> None:
    """Kevin sells none of one supplier, and his total is not zero."""
    comparison = engine.build_tables(clean)["supplier_comparison"]
    row = comparison.filter(
        (pl.col(REP_COLUMN) == KEVIN)
        & (pl.col(TRANSACTION_COLUMNS.supplier) == golden.ACME_IMPORTS)
    )

    assert row["Rep Revenue"][0] == 0.0
    assert row["Rep Share"][0] == 0.0
    assert row["Index"][0] == 0.0


def test_growth_against_nothing_has_no_answer(clean) -> None:
    summary = engine.build_tables(clean)["rep_summary"]
    jennifer = summary.filter(pl.col(REP_COLUMN) == JENNIFER)

    assert jennifer["Prior Month Revenue"][0] == 0.0
    assert jennifer["MoM Growth"][0] is None
    assert jennifer["YoY Growth"][0] is None
    assert jennifer["YTD Growth"][0] is None


def test_growth_from_a_negative_prior_period_keeps_its_sign() -> None:
    """The denominator is the absolute prior value (rule ``growth``)."""
    expression = engine._growth(pl.col("current"), pl.col("prior"))
    frame = pl.DataFrame(
        {"current": [50.0, -50.0, 10.0], "prior": [-100.0, -100.0, 0.0]}
    ).with_columns(expression.alias("growth"))

    # From -100 to 50 is an improvement of 150 on a base of 100.
    assert frame["growth"][0] == pytest.approx(1.5)
    # From -100 to -50 is an improvement of 50 on a base of 100.
    assert frame["growth"][1] == pytest.approx(0.5)
    assert frame["growth"][2] is None


def test_shares_and_growth_are_numbers_not_formatted_text(clean) -> None:
    """Build plan 14F requires percentages to stay numeric."""
    tables = engine.build_tables(clean)

    for name, column in (
        ("rep_summary", "Share of Company Revenue"),
        ("rep_summary", "YoY Growth"),
        ("supplier_performance", "Share of Rep Revenue"),
        ("supplier_comparison", "Index"),
    ):
        assert tables[name].schema[column] == pl.Float64


def test_money_is_stated_at_two_decimals(clean) -> None:
    """A sum of floats must not show as 1550.7500000000002."""
    awkward = golden.sales_frame().with_columns(
        pl.when(pl.col(DATE_COLUMN) == "2026-09-02")
        .then(pl.lit(0.1))
        .otherwise(pl.col(REVENUE).cast(pl.Float64))
        .alias(REVENUE)
    )

    tables = engine.build_tables(prepare(sales=awkward))
    revenue = tables["company_summary"]["Revenue"][0]

    assert revenue == round(revenue, 2)


# ---------------------------------------------------------------------------
# Placements (rule ``placement``)
# ---------------------------------------------------------------------------


def test_a_product_the_account_never_bought_is_a_placement(clean) -> None:
    placements = engine.build_tables(clean)["placements"]
    pairs = set(
        zip(
            placements[CUSTOMER].to_list(),
            placements[TRANSACTION_COLUMNS.sku].to_list(),
        )
    )

    assert (ACME, "SKU-300") in pairs
    assert (CORNER, "SKU-200") in pairs


def test_a_product_the_account_already_bought_is_not_a_placement(clean) -> None:
    placements = engine.build_tables(clean)["placements"]
    pairs = set(
        zip(
            placements[CUSTOMER].to_list(),
            placements[TRANSACTION_COLUMNS.sku].to_list(),
        )
    )

    assert (ACME, "SKU-100") not in pairs
    assert (BISTRO, "SKU-200") not in pairs


def test_a_credit_is_never_a_placement() -> None:
    """Only a positive quantity counts as a purchase."""
    credit_only = golden.sales_frame(("2026-09",)).filter(
        pl.col(TRANSACTION_COLUMNS.invoice_type) == "Credit"
    )

    prepared = prepare(sales=credit_only, samples=golden.sample_frame(("2026-09",)))
    placements = engine._placements(prepared.sales, prepared.require_period())

    assert placements.height == 0


def test_a_placement_belongs_to_the_reps_who_owns_the_account(clean) -> None:
    placements = engine.build_tables(clean)["placements"]

    assert set(placements[REP_COLUMN].to_list()) == {BETH}


def test_the_placement_detail_holds_only_those_placements_lines(clean) -> None:
    tables = engine.build_tables(clean)
    detail = tables["placement_detail"]

    assert detail.height == 2
    assert set(detail[TRANSACTION_COLUMNS.invoice_number].to_list()) == {
        "INV-2609-2",
        "INV-2609-3",
    }


def test_a_longer_history_removes_a_placement() -> None:
    """The look-back is the history the Run read (rule ``placement``)."""
    earlier = golden.sales_frame().vstack(
        pl.DataFrame(
            [
                list(
                    golden._line(
                        "2024-05-02", CORNER, "INV-2405-1", "SKU-200",
                        3, 30.00, 90.00,
                    )
                )
            ],
            schema=list(golden.TRANSACTION_HEADER),
            orient="row",
        )
    )

    prepared = prepare(sales=earlier)
    placements = engine.build_tables(prepared)["placements"]
    pairs = set(
        zip(
            placements[CUSTOMER].to_list(),
            placements[TRANSACTION_COLUMNS.sku].to_list(),
        )
    )

    assert (CORNER, "SKU-200") not in pairs
    assert (ACME, "SKU-300") in pairs


# ---------------------------------------------------------------------------
# Samples (rule ``sample``)
# ---------------------------------------------------------------------------


def test_samples_are_never_added_to_sales(clean) -> None:
    """Build plan 10D, where the report could most easily blur it."""
    tables = engine.build_tables(clean)
    company = tables["company_summary"]

    # 995.00 of sales; the samples are 125.00 and are reported separately.
    assert company["Revenue"][0] == 995.00
    assert company["Sample Value"][0] == 125.00


def test_a_sample_belongs_to_the_account_owner(clean) -> None:
    samples = engine.build_tables(clean)["samples"]
    rows = dict(zip(samples[CUSTOMER].to_list(), samples[REP_COLUMN].to_list()))

    assert rows[ACME] == BETH
    assert rows[BISTRO] == KEVIN


def test_only_the_reporting_months_samples_are_counted(clean) -> None:
    """August's sample line exists and is not in September's totals."""
    samples = engine.build_tables(clean)["samples"]

    assert samples["Sample Lines"].sum() == 2
    assert "2026-08" in golden.SAMPLE_MONTHS


def test_the_sample_detail_holds_the_reporting_months_lines(clean) -> None:
    detail = engine.build_tables(clean)["sample_detail"]

    assert detail.height == 2
    assert set(detail[TRANSACTION_COLUMNS.invoice_number].to_list()) == {
        "SMP-2609-1",
        "SMP-2609-2",
    }


# ---------------------------------------------------------------------------
# Errors that stop the report (build plan 13H)
# ---------------------------------------------------------------------------


def test_an_empty_sales_history_stops_the_report() -> None:
    prepared = prepare(sales=golden.sales_frame(()))

    assert "EMPTY_SALES_HISTORY" in codes(prepared.errors)
    assert not prepared.usable


def test_a_blank_total_price_stops_the_report() -> None:
    blank = golden.replace_value(golden.sales_frame(), REVENUE, 8, None)

    prepared = prepare(sales=blank)

    assert "MISSING_MEASURE" in codes(prepared.errors)


def test_a_blank_quantity_stops_the_report() -> None:
    blank = golden.replace_value(golden.sales_frame(), QUANTITY, 8, "")

    prepared = prepare(sales=blank)

    assert "MISSING_MEASURE" in codes(prepared.errors)


def test_a_currency_formatted_total_price_stops_the_report() -> None:
    """`$1,234.56` is not a number, and guessing what it meant is forbidden."""
    formatted = golden.replace_value(
        golden.sales_frame(), REVENUE, 8, "$1,234.56"
    )

    prepared = prepare(sales=formatted)

    assert "NON_NUMERIC_MEASURE" in codes(prepared.errors)


def test_a_parenthesised_negative_stops_the_report() -> None:
    formatted = golden.replace_value(golden.sales_frame(), REVENUE, 8, "(45.00)")

    prepared = prepare(sales=formatted)

    assert "NON_NUMERIC_MEASURE" in codes(prepared.errors)


def test_a_measure_is_never_read_as_zero() -> None:
    """The failure exists so a blank cannot quietly become a zero."""
    blank = golden.replace_value(golden.sales_frame(), REVENUE, 8, None)

    prepared = prepare(sales=blank)

    assert prepared.errors
    with pytest.raises(ValueError):
        engine.build_tables(prepared)


def test_an_unreadable_invoice_date_stops_the_report() -> None:
    broken = golden.replace_value(
        golden.sales_frame(), DATE_COLUMN, 8, "not a date"
    )

    prepared = prepare(sales=broken)

    assert "MALFORMED_INVOICE_DATE" in codes(prepared.errors)


def test_a_blank_invoice_date_stops_the_report() -> None:
    typed = golden.sales_frame().with_columns(
        pl.col(DATE_COLUMN).str.to_date("%Y-%m-%d")
    )
    blanked = typed.with_columns(
        pl.when(pl.col(TRANSACTION_COLUMNS.invoice_number) == "INV-2609-1")
        .then(None)
        .otherwise(pl.col(DATE_COLUMN))
        .alias(DATE_COLUMN)
    )

    prepared = prepare(sales=blanked)

    assert "MALFORMED_INVOICE_DATE" in codes(prepared.errors)


def test_an_account_with_activity_and_no_owner_stops_the_report() -> None:
    orphaned = golden.assignment_frame(
        tuple(pair for pair in golden.OWNERSHIP if pair[0] != CORNER)
    )

    prepared = prepare(assignments=orphaned)

    assert "MISSING_ACCOUNT_OWNERSHIP" in codes(prepared.errors)
    (issue,) = [
        item
        for item in prepared.errors
        if item.code == "MISSING_ACCOUNT_OWNERSHIP"
    ]
    assert CORNER in issue.details["accounts"]


def test_an_account_with_no_activity_and_no_owner_is_fine() -> None:
    """Harbour Cellars never trades, so dropping it changes nothing."""
    without_harbour = golden.assignment_frame(
        tuple(pair for pair in golden.OWNERSHIP if pair[0] != HARBOUR)
    )

    prepared = prepare(assignments=without_harbour)

    assert prepared.usable, codes(prepared.errors)
    assert JENNIFER not in prepared.reps


def test_an_account_owned_by_two_reps_stops_the_report() -> None:
    conflicted = golden.assignment_frame(
        (*golden.OWNERSHIP, (ACME, KEVIN))
    )

    prepared = prepare(assignments=conflicted)

    assert "DUPLICATE_ACCOUNT_OWNERSHIP" in codes(prepared.errors)
    (issue,) = [
        item
        for item in prepared.errors
        if item.code == "DUPLICATE_ACCOUNT_OWNERSHIP"
    ]
    assert ACME in issue.details["accounts"]


def test_an_account_listed_twice_with_the_same_rep_is_fine() -> None:
    """It says one true thing twice (the rule Phase 10E already set)."""
    repeated = golden.assignment_frame((*golden.OWNERSHIP, (ACME, BETH)))

    prepared = prepare(assignments=repeated)

    assert prepared.usable, codes(prepared.errors)
    assert prepared.ownership.height == len(golden.OWNERSHIP)


def test_a_snapshot_with_no_rep_stops_the_report() -> None:
    empty = golden.assignment_frame(())

    prepared = prepare(assignments=empty)

    assert "NO_SALES_REPS" in codes(prepared.errors)


def test_a_sample_history_that_skips_the_reporting_month_stops_the_report() -> None:
    august_only = golden.sample_frame(("2026-08",))

    prepared = prepare(samples=august_only)

    assert "SAMPLE_PERIOD_MISMATCH" in codes(prepared.errors)


def test_a_month_with_genuinely_no_samples_is_not_a_failure() -> None:
    """A sample history reaching the month with no rows in it is a fact."""
    september_empty = golden.sample_frame(("2026-09",)).clear()

    prepared = prepare(samples=september_empty)

    assert "SAMPLE_PERIOD_MISMATCH" not in codes(prepared.errors)


def test_every_error_names_the_input_it_came_from() -> None:
    orphaned = golden.assignment_frame(
        tuple(pair for pair in golden.OWNERSHIP if pair[0] != CORNER)
    )

    prepared = engine.prepare(
        golden.sales_frame(),
        golden.sample_frame(),
        orphaned,
        sales_slot="sales_history",
        samples_slot="sample_history",
        assignments_slot="account_assignments",
    )

    (issue,) = [
        item
        for item in prepared.errors
        if item.code == "MISSING_ACCOUNT_OWNERSHIP"
    ]
    assert issue.slot_id == "account_assignments"


def test_no_table_is_built_when_the_report_is_unsafe() -> None:
    """Build plan 13H: fail rather than producing a plausible workbook."""
    prepared = prepare(sales=golden.sales_frame(()))

    with pytest.raises(ValueError) as failure:
        engine.build_tables(prepared)

    assert "EMPTY_SALES_HISTORY" in str(failure.value)


# ---------------------------------------------------------------------------
# Warnings the report carries (build plan 13H)
# ---------------------------------------------------------------------------


def test_the_golden_month_reports_exactly_its_expected_warnings(clean) -> None:
    assert codes(clean.warnings) == list(golden.EXPECTED["warnings"])


def test_a_transaction_rep_the_snapshot_does_not_name_is_a_warning() -> None:
    renamed = golden.sales_frame().with_columns(
        pl.when(pl.col(TRANSACTION_COLUMNS.invoice_number) == "INV-2609-1")
        .then(pl.lit("Temp Cover"))
        .otherwise(pl.col(TRANSACTION_COLUMNS.transaction_rep))
        .alias(TRANSACTION_COLUMNS.transaction_rep)
    )

    prepared = prepare(sales=renamed)

    assert prepared.usable, codes(prepared.errors)
    assert "UNRECOGNISED_SALES_REP" in codes(prepared.warnings)


def test_an_unrecognised_rep_changes_no_figure() -> None:
    """Revenue follows the account's owner, not the invoice's rep."""
    renamed = golden.sales_frame().with_columns(
        pl.lit("Temp Cover").alias(TRANSACTION_COLUMNS.transaction_rep)
    )

    original = engine.build_tables(prepare())["rep_summary"]
    altered = engine.build_tables(prepare(sales=renamed))["rep_summary"]

    assert original.equals(altered)


def test_an_unexpected_invoice_type_is_a_warning_and_still_counted() -> None:
    relabelled = golden.replace_value(
        golden.sales_frame(), TRANSACTION_COLUMNS.invoice_type, 11, "Rebate"
    )

    prepared = prepare(sales=relabelled)
    tables = engine.build_tables(prepared)

    assert "UNEXPECTED_INVOICE_TYPE" in codes(prepared.warnings)
    # The credit is still counted: the company total is unchanged.
    assert tables["company_summary"]["Revenue"][0] == 995.00


def test_an_extra_source_column_is_a_warning_and_changes_nothing() -> None:
    widened = golden.sales_frame().with_columns(
        pl.lit("extra").alias("Territory")
    )

    prepared = prepare(sales=widened)
    tables = engine.build_tables(prepared)

    assert "UNEXPECTED_SOURCE_COLUMNS" in codes(prepared.warnings)
    assert tables["company_summary"]["Revenue"][0] == 995.00


def test_identical_repeated_rows_are_warned_about_and_never_removed() -> None:
    doubled = golden.sales_frame().vstack(
        golden.sales_frame(("2026-09",)).head(1)
    )

    prepared = prepare(sales=doubled)
    tables = engine.build_tables(prepared)

    assert "DUPLICATE_SOURCE_ROWS" in codes(prepared.warnings)
    # 995.00 plus the repeated 375.00 line, because nothing was dropped.
    assert tables["company_summary"]["Revenue"][0] == 1370.00


def test_a_missing_comparison_window_is_a_warning() -> None:
    this_year_only = golden.sales_frame(("2026-08", "2026-09"))

    prepared = prepare(sales=this_year_only)

    assert "MISSING_COMPARISON_PERIOD" in codes(prepared.warnings)
    summary = engine.build_tables(prepared)["rep_summary"]
    assert summary["Last Year Revenue"].to_list() == [0.0, 0.0, 0.0]
    assert summary["YoY Growth"].to_list() == [None, None, None]


def test_a_short_history_warns_that_placements_are_overstated(clean) -> None:
    assert "SHORT_PLACEMENT_HISTORY" in codes(clean.warnings)
    (issue,) = [
        item
        for item in clean.warnings
        if item.code == "SHORT_PLACEMENT_HISTORY"
    ]
    assert issue.details["months_available"] == 3
    assert issue.details["months_expected"] == 12


def test_every_warning_reaches_the_data_quality_table(clean) -> None:
    table = engine.build_tables(clean)["data_quality"]

    assert set(table["Code"].to_list()) == {
        issue.code for issue in clean.warnings
    }
    assert set(table["Severity"].to_list()) == {"Warning"}


def test_the_data_quality_table_carries_the_declared_summary(clean) -> None:
    table = engine.build_tables(clean)["data_quality"]
    row = table.filter(pl.col("Code") == "SHORT_PLACEMENT_HISTORY")

    assert row["Condition"][0].startswith("Fewer months of history")


def test_no_error_ever_appears_in_the_data_quality_table(clean) -> None:
    """An error fails the Run; only warnings reach a produced report."""
    table = engine.build_tables(clean)["data_quality"]

    for code in table["Code"].to_list():
        assert severity_of(code) is Severity.WARNING


def test_the_data_quality_table_has_its_schema_even_when_empty() -> None:
    empty = engine.data_quality_table(
        engine.PreparedReport(
            period=None,
            sales=golden.sales_frame(()),
            samples=golden.sample_frame(()),
            ownership=golden.assignment_frame(()),
            reps=(),
            history_months=(),
        )
    )

    assert empty.height == 0
    assert empty.columns == [
        "Severity", "Code", "Condition", "Message", "Affected Rows", "Input"
    ]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_the_metrics_are_counts(clean) -> None:
    metrics = engine.report_metrics(clean)

    assert metrics == {
        "sales_reps": 3,
        "accounts": 4,
        "history_months": 4,
        "sales_rows": 5,
        "sample_rows": 2,
        "placements": 2,
        "warnings": 2,
    }
    assert all(isinstance(value, int) for value in metrics.values())


# ---------------------------------------------------------------------------
# The ownership map itself
# ---------------------------------------------------------------------------


def test_ownership_is_matched_exactly(clean) -> None:
    """`acme wine bar` is not `Acme Wine Bar` (rule ``ownership_matching``)."""
    lowered = golden.replace_value(
        golden.sales_frame(), CUSTOMER, 8, ACME.lower()
    )

    prepared = prepare(sales=lowered)

    assert "MISSING_ACCOUNT_OWNERSHIP" in codes(prepared.errors)


def test_the_ownership_map_names_the_report_column_not_the_source_one(
    clean,
) -> None:
    assert clean.ownership.columns == [
        ASSIGNMENTS_CUSTOMER_COLUMN,
        REP_COLUMN,
    ]
    assert ASSIGNMENTS_REP_COLUMN not in clean.ownership.columns
