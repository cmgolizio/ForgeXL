"""Golden-month accuracy (build plan 13I), and the Action end to end.

Build plan 13I:

    Verify exact expected results for: rep totals, company totals, account
    metrics, supplier metrics, percentage calculations, placements, sample
    counts, representative detail rows. Do not validate only row counts.
    Validate values.

Every figure asserted here comes from :data:`tests.fixtures.report_months.EXPECTED`,
which was worked out by hand from twelve sales rows and three sample rows. A
figure captured from a run would only prove the implementation agrees with
itself; these were calculated against it.

The second half drives the registered Action the way the application does:
three months of real committed Data Library versions, resolved through the
`history` selector, executed by the Run pipeline, and downloaded over HTTP.
That is what proves the report is reachable rather than merely calculable.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.actions import registry
from app.actions.monthly_sales_rep_report import (
    ASSIGNMENTS_SLOT,
    SALES_SLOT,
    SAMPLES_SLOT,
)
from app.models.report_spec import (
    REPORT_ACTION_ID,
    REP_COLUMN,
    TRANSACTION_COLUMNS,
)
from app.services import monthly_report as engine
from app.services.ingestion import (
    SourceFile,
    commit_account_assignments,
    commit_monthly_sales,
    commit_monthly_samples,
)
from app.services.data_library import LocalDataLibrary, ensure_known_datasets
from app.services.runner import execute_run

from tests.fixtures import report_months as golden
from tests.fixtures.report_months import (
    ACME,
    ACME_IMPORTS,
    BETH,
    BISTRO,
    CORNER,
    EXPECTED,
    GLOBAL_VINES,
    GOLDEN_MONTH,
    HARBOUR,
    JENNIFER,
    KEVIN,
)

CUSTOMER = TRANSACTION_COLUMNS.customer
SUPPLIER = TRANSACTION_COLUMNS.supplier
SKU = TRANSACTION_COLUMNS.sku

#: How close a calculated figure must be to the hand-worked one. Tight enough
#: that a different formula fails and loose enough that float addition does
#: not: money is rounded to two decimals, and a share is a ratio of two such
#: sums.
TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tables() -> dict[str, pl.DataFrame]:
    """The golden month's report tables."""
    prepared = engine.prepare(
        golden.sales_frame(), golden.sample_frame(), golden.assignment_frame()
    )
    assert prepared.usable, [issue.code for issue in prepared.errors]
    return engine.build_tables(prepared)


def row(frame: pl.DataFrame, **keys: object) -> dict[str, object]:
    """The single row of `frame` matching `keys`, as a mapping."""
    selected = frame
    for column, value in keys.items():
        selected = selected.filter(pl.col(column) == value)
    assert selected.height == 1, f"{keys} matched {selected.height} rows"
    return selected.row(0, named=True)


def assert_values(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    """Every expected figure, compared by value."""
    for column, value in expected.items():
        found = actual[column]
        if value is None:
            assert found is None, f"{column}: expected no value, got {found!r}"
        elif isinstance(value, float):
            assert found == pytest.approx(value, abs=TOLERANCE), column
        else:
            assert found == value, column


# ---------------------------------------------------------------------------
# Company totals
# ---------------------------------------------------------------------------


def test_the_company_totals_are_exact(tables) -> None:
    assert_values(tables["company_summary"].row(0, named=True), EXPECTED["company"])


def test_the_company_summary_names_the_reporting_period(tables) -> None:
    assert (
        tables["company_summary"]["Reporting Period"][0]
        == EXPECTED["report_label"]
    )


def test_the_company_revenue_includes_the_credit_at_its_signed_value(
    tables,
) -> None:
    """995.00 is 1055.00 of invoices less a 60.00 credit (rule ``credits``)."""
    assert tables["company_summary"]["Revenue"][0] == 995.00
    assert tables["company_summary"]["Quantity"][0] == 33.0


# ---------------------------------------------------------------------------
# Rep totals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rep", [BETH, KEVIN, JENNIFER])
def test_each_reps_totals_are_exact(tables, rep) -> None:
    assert_values(
        row(tables["rep_summary"], **{REP_COLUMN: rep}),
        EXPECTED["reps_summary"][rep],
    )


def test_every_rep_on_the_roster_has_exactly_one_summary_row(tables) -> None:
    summary = tables["rep_summary"]

    assert sorted(summary[REP_COLUMN].to_list()) == sorted(EXPECTED["reps"])
    assert summary.height == len(EXPECTED["reps"])


def test_the_summary_is_ordered_by_revenue(tables) -> None:
    assert tables["rep_summary"][REP_COLUMN].to_list() == [
        BETH,
        KEVIN,
        JENNIFER,
    ]


# ---------------------------------------------------------------------------
# Account metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rep,account",
    [(BETH, ACME), (BETH, CORNER), (KEVIN, BISTRO), (JENNIFER, HARBOUR)],
)
def test_each_accounts_metrics_are_exact(tables, rep, account) -> None:
    assert_values(
        row(tables["account_performance"], **{REP_COLUMN: rep, CUSTOMER: account}),
        EXPECTED["accounts"][(rep, account)],
    )


def test_every_owned_account_appears_once(tables) -> None:
    accounts = tables["account_performance"]

    assert accounts.height == len(golden.OWNERSHIP)
    assert accounts.select(REP_COLUMN, CUSTOMER).unique().height == len(
        golden.OWNERSHIP
    )


def test_an_accounts_classification_is_carried_through(tables) -> None:
    assert (
        row(tables["account_performance"], **{REP_COLUMN: BETH, CUSTOMER: CORNER})[
            TRANSACTION_COLUMNS.customer_type
        ]
        == "Off Premise"
    )


def test_the_account_revenues_add_up_to_the_rep_total(tables) -> None:
    beth = tables["account_performance"].filter(pl.col(REP_COLUMN) == BETH)

    assert beth["Revenue"].sum() == pytest.approx(
        EXPECTED["reps_summary"][BETH]["Revenue"]
    )


# ---------------------------------------------------------------------------
# Supplier metrics and percentages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("supplier", [ACME_IMPORTS, GLOBAL_VINES])
def test_each_company_suppliers_metrics_are_exact(tables, supplier) -> None:
    assert_values(
        row(tables["company_supplier_performance"], **{SUPPLIER: supplier}),
        EXPECTED["company_suppliers"][supplier],
    )


@pytest.mark.parametrize(
    "rep,supplier",
    [(BETH, ACME_IMPORTS), (BETH, GLOBAL_VINES), (KEVIN, GLOBAL_VINES)],
)
def test_each_rep_suppliers_metrics_are_exact(tables, rep, supplier) -> None:
    assert_values(
        row(tables["supplier_performance"], **{REP_COLUMN: rep, SUPPLIER: supplier}),
        EXPECTED["rep_suppliers"][(rep, supplier)],
    )


def test_a_reps_supplier_shares_sum_to_one(tables) -> None:
    beth = tables["supplier_performance"].filter(pl.col(REP_COLUMN) == BETH)

    assert beth["Share of Rep Revenue"].sum() == pytest.approx(1.0)


def test_the_company_supplier_shares_sum_to_one(tables) -> None:
    suppliers = tables["company_supplier_performance"]

    assert suppliers["Share of Company Revenue"].sum() == pytest.approx(1.0)


@pytest.mark.parametrize(
    "rep,supplier", [(KEVIN, GLOBAL_VINES), (KEVIN, ACME_IMPORTS)]
)
def test_the_company_versus_rep_comparison_is_exact(tables, rep, supplier) -> None:
    assert_values(
        row(tables["supplier_comparison"], **{REP_COLUMN: rep, SUPPLIER: supplier}),
        EXPECTED["supplier_comparison"][(rep, supplier)],
    )


def test_an_index_above_one_means_the_rep_over_indexes(tables) -> None:
    """Kevin sells only Global Vines; the company sells 42% of it."""
    kevin = row(
        tables["supplier_comparison"],
        **{REP_COLUMN: KEVIN, SUPPLIER: GLOBAL_VINES},
    )

    index = float(str(kevin["Index"]))
    rep_share = float(str(kevin["Rep Share"]))
    company_share = float(str(kevin["Company Share"]))

    assert index > 1.0
    assert kevin["Share Difference"] == pytest.approx(rep_share - company_share)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def test_the_product_table_is_keyed_by_the_full_product(tables) -> None:
    products = tables["product_performance"]

    assert products.height == 4
    assert products.select(
        REP_COLUMN, *TRANSACTION_COLUMNS.product_key
    ).unique().height == 4


def test_a_products_revenue_is_exact(tables) -> None:
    placement = row(
        tables["product_performance"], **{REP_COLUMN: BETH, SKU: "SKU-300"}
    )

    assert placement["Revenue"] == 200.00
    assert placement["Quantity"] == 4.0
    assert placement["Producer"] == "Domaine Père"
    assert placement["Accounts"] == 1


def test_a_products_revenue_nets_its_credit(tables) -> None:
    """Kevin's SKU-200: 300.00 invoiced less a 60.00 credit."""
    product = row(
        tables["product_performance"], **{REP_COLUMN: KEVIN, SKU: "SKU-200"}
    )

    assert product["Revenue"] == 240.00
    assert product["Quantity"] == 8.0


# ---------------------------------------------------------------------------
# Placements
# ---------------------------------------------------------------------------


def test_the_placements_are_exactly_the_expected_two(tables) -> None:
    placements = tables["placements"]

    assert placements.height == 2
    found = set(
        zip(
            placements[REP_COLUMN].to_list(),
            placements[CUSTOMER].to_list(),
            placements[SKU].to_list(),
        )
    )
    assert found == set(EXPECTED["placements"])


@pytest.mark.parametrize("key", list(EXPECTED["placements"]))
def test_each_placements_values_are_exact(tables, key) -> None:
    rep, account, sku = key
    expected = dict(EXPECTED["placements"][key])
    expected["First Sold"] = date.fromisoformat(str(expected["First Sold"]))

    assert_values(
        row(
            tables["placements"],
            **{REP_COLUMN: rep, CUSTOMER: account, SKU: sku},
        ),
        expected,
    )


def test_a_placement_detail_row_is_the_invoice_line_behind_it(tables) -> None:
    detail = row(
        tables["placement_detail"],
        **{TRANSACTION_COLUMNS.invoice_number: "INV-2609-2"},
    )

    assert detail[REP_COLUMN] == BETH
    assert detail[CUSTOMER] == ACME
    assert detail[SKU] == "SKU-300"
    assert detail[TRANSACTION_COLUMNS.date] == date(2026, 9, 5)
    assert detail[TRANSACTION_COLUMNS.revenue] == 200.00
    assert detail[TRANSACTION_COLUMNS.item_price] == 50.00


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", list(EXPECTED["samples"]))
def test_each_accounts_sample_counts_are_exact(tables, key) -> None:
    rep, account = key

    assert_values(
        row(tables["samples"], **{REP_COLUMN: rep, CUSTOMER: account}),
        EXPECTED["samples"][key],
    )


def test_the_sample_values_add_up_to_the_company_figure(tables) -> None:
    assert tables["samples"]["Sample Value"].sum() == pytest.approx(
        EXPECTED["company"]["Sample Value"]
    )


def test_a_sample_detail_row_is_the_sample_line_behind_it(tables) -> None:
    detail = row(
        tables["sample_detail"],
        **{TRANSACTION_COLUMNS.invoice_number: "SMP-2609-2"},
    )

    assert detail[REP_COLUMN] == KEVIN
    assert detail[CUSTOMER] == BISTRO
    assert detail[SKU] == "SKU-100"
    assert detail[TRANSACTION_COLUMNS.revenue] == 25.00


# ---------------------------------------------------------------------------
# The Action itself
# ---------------------------------------------------------------------------


def test_the_action_is_registered_and_reads_the_library() -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    assert [slot.id for slot in action.inputs] == [
        SALES_SLOT,
        SAMPLES_SLOT,
        ASSIGNMENTS_SLOT,
    ]
    assert all(slot.source.value == "library" for slot in action.inputs)
    assert all(slot.accepted_extensions == () for slot in action.inputs)


def _action_inputs() -> dict[str, pl.DataFrame]:
    return {
        SALES_SLOT: golden.sales_frame(),
        SAMPLES_SLOT: golden.sample_frame(),
        ASSIGNMENTS_SLOT: golden.assignment_frame(),
    }


def test_the_action_returns_every_declared_table() -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    result = action.run(_action_inputs())

    assert set(result.outputs) == {output.id for output in action.outputs}
    for frame in result.outputs.values():
        assert isinstance(frame, pl.DataFrame)


def test_the_action_produces_no_artifact() -> None:
    """Phase 13 calculates; Phase 14 renders (build plan Phase 13 exit)."""
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    assert action.run(_action_inputs()).artifacts == ()


def test_the_action_states_no_affected_row_count() -> None:
    """It measures rows; it does not change them (build plan 6E.5)."""
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    assert action.run(_action_inputs()).rows_affected is None


def test_the_action_refuses_before_it_calculates() -> None:
    """Build plan 13H: fail rather than produce a plausible workbook."""
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None
    inputs = _action_inputs()
    inputs[ASSIGNMENTS_SLOT] = golden.assignment_frame(
        tuple(pair for pair in golden.OWNERSHIP if pair[0] != CORNER)
    )

    issues = action.validate(inputs)

    assert [issue.code for issue in issues] == ["MISSING_ACCOUNT_OWNERSHIP"]


def test_the_action_passes_validation_on_a_clean_month() -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    assert action.validate(_action_inputs()) == []


def test_the_report_action_holds_no_per_run_state() -> None:
    """One instance serves every Run (build plan section 24)."""
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    before = dict(vars(action))
    first = action.run(_action_inputs())
    second = action.run(_action_inputs())

    assert dict(vars(action)) == before
    for output_id, frame in first.outputs.items():
        assert frame.equals(second.outputs[output_id])


def test_the_action_never_mutates_what_it_was_given() -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None
    inputs = _action_inputs()
    before = {name: frame.clone() for name, frame in inputs.items()}

    action.run(inputs)

    for name, frame in inputs.items():
        assert frame.equals(before[name])


# ---------------------------------------------------------------------------
# Through the Run pipeline, against a real Data Library
# ---------------------------------------------------------------------------


@pytest.fixture
def stocked_library(data_library: LocalDataLibrary) -> LocalDataLibrary:
    """The golden months, committed the way the monthly import commits them."""
    ensure_known_datasets()

    for period in golden.SALES_MONTHS:
        commit_monthly_sales(
            SourceFile(
                filename=f"sales-{period}.csv",
                payload=golden.sales_table(period).as_csv(),
            ),
            expected_period=period,
            today=date(2026, 10, 1),
        )
    for period in golden.SAMPLE_MONTHS:
        commit_monthly_samples(
            SourceFile(
                filename=f"samples-{period}.csv",
                payload=golden.sample_table(period).as_csv(),
            ),
            expected_period=period,
            today=date(2026, 10, 1),
        )
    commit_account_assignments(
        SourceFile(
            filename="assignments.csv",
            payload=golden.assignment_table().as_csv(),
        ),
        period=GOLDEN_MONTH,
    )
    return data_library


def _run_the_report(**references: str):
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None
    return execute_run(
        action,
        uploads={},
        dataset_references={
            SALES_SLOT: f"history:{GOLDEN_MONTH}",
            SAMPLES_SLOT: f"history:{GOLDEN_MONTH}",
            ASSIGNMENTS_SLOT: f"period:{GOLDEN_MONTH}",
            **references,
        },
    )


def test_the_report_runs_end_to_end_from_stored_versions(
    stocked_library, quarantine: Path
) -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None
    outcome = _run_the_report()
    manifest = outcome.manifest

    assert manifest.status.value == "succeeded"
    assert [output.id for output in manifest.outputs] == [
        output.id for output in action.outputs
    ]
    # Reading stored history is a read: the Run still writes nothing.
    assert list(quarantine.iterdir()) == []


def test_the_run_reports_the_same_company_total(stocked_library) -> None:
    outcome = _run_the_report()
    result = outcome.result
    assert result is not None

    company = result.table("company_summary")
    assert company is not None
    assert company["Revenue"][0] == EXPECTED["company"]["Revenue"]


def test_the_run_records_every_month_it_read(stocked_library) -> None:
    """Build plan 11C: the exact dataset versions used, not a summary."""
    manifest = _run_the_report().manifest

    sales = [
        record
        for record in manifest.library_inputs
        if record.slot_id == SALES_SLOT
    ]
    assert [record.period for record in sales] == list(golden.SALES_MONTHS)
    assert len({record.version_id for record in sales}) == len(sales)
    for record in sales:
        assert record.requested == f"history:{GOLDEN_MONTH}"


def test_naming_the_recorded_versions_reproduces_the_report(
    stocked_library,
) -> None:
    """Build plan 11D: a Run is reproducible from what it recorded."""
    first = _run_the_report()
    september = next(
        record
        for record in first.manifest.library_inputs
        if record.slot_id == ASSIGNMENTS_SLOT
    )

    second = _run_the_report(
        **{ASSIGNMENTS_SLOT: f"version:{september.version_id}"}
    )

    assert first.result is not None
    assert second.result is not None
    original = first.result.table("rep_summary")
    reproduced = second.result.table("rep_summary")
    assert original is not None and reproduced is not None

    assert original.equals(reproduced)


def test_a_month_that_was_never_imported_fails_clearly(stocked_library) -> None:
    from app.errors import RunValidationError

    with pytest.raises(RunValidationError) as failure:
        _run_the_report(**{SALES_SLOT: "history:2027-01"})

    assert [issue.code for issue in failure.value.issues] == [
        "UNKNOWN_DATASET_VERSION"
    ]


def test_bounding_the_history_moves_the_reporting_period(
    stocked_library,
) -> None:
    outcome = _run_the_report(
        **{
            SALES_SLOT: "history:2026-08",
            SAMPLES_SLOT: "history:2026-08",
            ASSIGNMENTS_SLOT: f"period:{GOLDEN_MONTH}",
        }
    )
    result = outcome.result
    assert result is not None
    company = result.table("company_summary")
    assert company is not None

    assert company["Reporting Period"][0] == "August 2026"


def test_the_report_downloads_as_csv_over_http(
    stocked_library, client
) -> None:
    outcome = _run_the_report()
    run_id = outcome.run.run_id

    response = client.get(
        f"/api/runs/{run_id}/outputs/rep_summary/download/csv"
    )

    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    beth = next(entry for entry in rows if entry[REP_COLUMN] == BETH)
    assert float(beth["Revenue"]) == EXPECTED["reps_summary"][BETH]["Revenue"]


def test_the_whole_report_downloads_as_one_workbook(
    stocked_library, client
) -> None:
    import fastexcel

    outcome = _run_the_report()
    run_id = outcome.run.run_id

    response = client.get(f"/api/runs/{run_id}/download/xlsx")

    assert response.status_code == 200
    sheets = fastexcel.read_excel(response.content).sheet_names
    assert len(sheets) == 12
    assert "Rep Summary" in sheets


def test_the_manifest_carries_the_reports_metrics(stocked_library) -> None:
    manifest = _run_the_report().manifest

    assert manifest.metrics == {
        "sales_reps": 3,
        "accounts": 4,
        "history_months": 4,
        "sales_rows": 5,
        "sample_rows": 2,
        "placements": 2,
        "warnings": 2,
    }


def test_an_unreliable_month_fails_the_run_with_its_condition(
    stocked_library,
) -> None:
    """The snapshot for August names an account September's data trades with.

    Committing a snapshot that omits Corner Bottle and then reporting on it
    fails the Run before a table is built, with the condition that says why.
    """
    from app.errors import RunValidationError

    commit_account_assignments(
        SourceFile(
            filename="assignments-2026-08.csv",
            payload=golden.assignments(
                tuple(pair for pair in golden.OWNERSHIP if pair[0] != CORNER),
                name="partial",
            ).as_csv(),
        ),
        period="2026-08",
    )

    with pytest.raises(RunValidationError) as failure:
        _run_the_report(**{ASSIGNMENTS_SLOT: "period:2026-08"})

    assert "MISSING_ACCOUNT_OWNERSHIP" in [
        issue.code for issue in failure.value.issues
    ]
