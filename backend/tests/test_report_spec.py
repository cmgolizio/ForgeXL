"""The report specification itself (build plan 13A).

Build plan 13A asks for the report's business definitions to be frozen before
the engine is written. `app/models/report_spec.py` is that freeze, and this
module is what keeps it honest:

* every rule carries a confidence and a basis, and a provisional one is
  visible rather than quietly assumed;
* the status is coupled to the things it should drive — the warning a Run
  reports, the Action's version — so it cannot be half-confirmed;
* the column roles the engine calculates with really exist in the confirmed
  source schema, so renaming a column there fails here;
* every report category build plan 13F lists is answered by a section that
  exists.

None of it touches a file, a clock or the library: the specification is
declarations, and this module asserts what they say.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.actions import registry
from app.models import report_spec
from app.models.report_spec import (
    ASSIGNMENTS_CUSTOMER_COLUMN,
    ASSIGNMENTS_DATASET_ID,
    ASSIGNMENTS_REP_COLUMN,
    BUILD_PLAN_13F_CATEGORIES,
    CONDITIONS,
    ERROR_CODES,
    KNOWN_INVOICE_TYPES,
    MINIMUM_PLACEMENT_HISTORY_MONTHS,
    MONEY_DECIMALS,
    PROVISIONAL_RULES,
    REP_COLUMN,
    REPORT_ACTION_ID,
    REPORT_ACTION_VERSION,
    REPORT_RULES,
    REPORT_SECTIONS,
    SALES_DATASET_ID,
    SAMPLES_DATASET_ID,
    SPEC_CONFIRMED,
    TRANSACTION_COLUMNS,
    WARNING_CODES,
    WINDOW_LABELS,
    Confidence,
    Severity,
    WindowKey,
    condition,
    rule,
    section,
    severity_of,
)
from app.models.source_schemas import (
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
    SALES_SOURCE_SCHEMA,
    SAMPLES_SOURCE_SCHEMA,
)

SPEC_DOCUMENT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "monthly-sales-rep-report-spec.md"
)


# ---------------------------------------------------------------------------
# The rules exist and say something
# ---------------------------------------------------------------------------


def test_every_rule_has_a_key_a_statement_and_a_basis() -> None:
    for item in REPORT_RULES:
        assert item.key.strip()
        assert item.statement.strip()
        assert item.basis.strip(), f"{item.key} states no basis"


def test_rule_keys_are_unique() -> None:
    keys = [item.key for item in REPORT_RULES]
    assert len(set(keys)) == len(keys)


def test_a_rule_is_looked_up_by_key_and_never_guessed() -> None:
    assert rule("revenue").key == "revenue"
    for near_miss in ("Revenue", " revenue", "revenues", ""):
        with pytest.raises(KeyError):
            rule(near_miss)


@pytest.mark.parametrize(
    "key",
    [
        "sources",
        "source_schemas",
        "report_month",
        "comparison_windows",
        "revenue",
        "quantity",
        "credits",
        "money_precision",
        "missing_measures",
        "ownership",
        "ownership_matching",
        "unowned_accounts",
        "duplicate_ownership",
        "rep_roster",
        "unrecognised_reps",
        "placement",
        "placement_history",
        "sample",
        "sample_period",
        "share",
        "growth",
        "comparison_index",
        "sorting",
        "totals",
        "duplicate_source_rows",
    ],
)
def test_build_plan_13a_asks_for_this_rule_and_it_is_declared(key) -> None:
    """Build plan 13A's list of things to document, each as a declared rule.

    13A names: source datasets, accepted schemas, invoice/sample rules,
    date-window rules, sales-rep ownership rules, account rules,
    company-vs-rep comparison rules, supplier calculations, percentage
    calculations, placement definitions, sample definitions, required report
    sections, sorting rules, displayed totals, credits/returns, missing
    ownership and zero/null values. Each is a key above; the sections are
    :data:`REPORT_SECTIONS` and are checked separately.
    """
    assert rule(key).statement.strip()


# ---------------------------------------------------------------------------
# Provisional status, and the things it drives
# ---------------------------------------------------------------------------


def test_the_provisional_rules_are_derived_from_the_declarations() -> None:
    assert PROVISIONAL_RULES == tuple(
        item
        for item in REPORT_RULES
        if item.confidence is Confidence.PROVISIONAL
    )


def test_the_specification_is_not_yet_confirmed() -> None:
    """The report has never been supplied, so some rules remain provisional.

    This test is expected to fail the day the last rule is confirmed, and
    that is deliberate: the change that confirms it must also raise the
    Action's version and update the specification document, and a failing
    test here is what says so.
    """
    assert PROVISIONAL_RULES, (
        "Every rule is confirmed. Raise REPORT_ACTION_VERSION to 1.0.0, "
        "update docs/monthly-sales-rep-report-spec.md, and invert this test."
    )
    assert SPEC_CONFIRMED is False


def test_an_unconfirmed_specification_keeps_the_action_below_one_point_oh() -> None:
    """The version is a statement about the definitions, not a placeholder."""
    if PROVISIONAL_RULES:
        assert REPORT_ACTION_VERSION.startswith("0."), (
            "The report's definitions are not confirmed, so its Action must "
            "not claim a 1.x version."
        )
    else:
        assert not REPORT_ACTION_VERSION.startswith("0.")


def test_an_unconfirmed_specification_is_reported_on_every_run() -> None:
    """A reader is never left to assume the arithmetic has been signed off."""
    assert "PROVISIONAL_REPORT_RULES" in WARNING_CODES
    assert severity_of("PROVISIONAL_REPORT_RULES") is Severity.WARNING


def test_every_provisional_rule_explains_its_default() -> None:
    for item in PROVISIONAL_RULES:
        assert len(item.basis) > 80, (
            f"{item.key} is provisional and its basis is too thin to act on."
        )


def test_the_specification_document_exists_and_names_the_status() -> None:
    text = SPEC_DOCUMENT.read_text(encoding="utf-8")

    assert "PARTLY PROVISIONAL" in text
    for item in PROVISIONAL_RULES:
        assert f"`{item.key}`" in text, (
            f"{item.key} is provisional and the specification document does "
            "not list it."
        )


def test_the_specification_document_names_the_action_and_its_version() -> None:
    text = SPEC_DOCUMENT.read_text(encoding="utf-8")

    assert REPORT_ACTION_ID in text
    assert REPORT_ACTION_VERSION in text


# ---------------------------------------------------------------------------
# Column roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,name",
    [
        ("date", "Invoice Date"),
        ("invoice_type", "Invoice Type"),
        ("invoice_number", "Invoice Number"),
        ("customer", "Customer"),
        ("customer_type", "Cust Type"),
        ("transaction_rep", "Sales Person"),
        ("sku", "SKU"),
        ("vintage", "Vintage"),
        ("supplier", "Supplier"),
        ("producer", "Producer"),
        ("selection", "Selection"),
        ("volume", "Volume"),
        ("quantity", "Quantity"),
        ("item_price", "Item Price"),
        ("revenue", "Total Price"),
    ],
)
def test_each_column_role_names_a_declared_source_column(role, name) -> None:
    """A role must point at a column the confirmed schema really has.

    This is what keeps `report_spec` from being a second, drifting copy of the
    source schema: renaming a column in `source_schemas.py` fails here rather
    than producing a report built on a column that is gone.
    """
    assert getattr(TRANSACTION_COLUMNS, role) == name
    assert SALES_SOURCE_SCHEMA.column(name) is not None


def test_every_transaction_column_has_exactly_one_role() -> None:
    roles = [
        getattr(TRANSACTION_COLUMNS, field)
        for field in (
            "date", "invoice_type", "invoice_number", "customer",
            "customer_type", "transaction_rep", "sku", "vintage", "supplier",
            "producer", "selection", "volume", "quantity", "item_price",
            "revenue",
        )
    ]

    assert len(set(roles)) == len(roles)
    assert set(roles) == set(SALES_SOURCE_SCHEMA.column_names)


def test_the_measures_are_the_schemas_own_numeric_columns() -> None:
    assert set(TRANSACTION_COLUMNS.measures) <= set(
        SALES_SOURCE_SCHEMA.number_columns
    )


def test_the_product_key_is_the_product_masters_key_plus_its_supplier() -> None:
    """Consistency with build plan section 27's definition of a product."""
    assert TRANSACTION_COLUMNS.product_key == (
        "SKU",
        "Supplier",
        "Producer",
        "Selection",
        "Vintage",
        "Volume",
    )


def test_the_assignment_columns_come_from_the_assignment_schema() -> None:
    assert (
        ASSIGNMENTS_CUSTOMER_COLUMN
        == ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.customer_column
    )
    assert (
        ASSIGNMENTS_REP_COLUMN == ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.rep_column
    )


def test_the_report_names_the_owning_rep_differently_from_the_invoice_rep() -> None:
    """Two different facts must not share one column name (section 3.3)."""
    assert REP_COLUMN == "Sales Rep"
    assert REP_COLUMN != TRANSACTION_COLUMNS.transaction_rep
    assert REP_COLUMN not in SALES_SOURCE_SCHEMA.column_names


def test_the_dataset_ids_are_the_declared_ones() -> None:
    assert SALES_DATASET_ID == SALES_SOURCE_SCHEMA.dataset_id
    assert SAMPLES_DATASET_ID == SAMPLES_SOURCE_SCHEMA.dataset_id
    assert ASSIGNMENTS_DATASET_ID == ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.dataset_id
    assert len({SALES_DATASET_ID, SAMPLES_DATASET_ID, ASSIGNMENTS_DATASET_ID}) == 3


def test_sales_and_samples_stay_two_datasets() -> None:
    """Build plan 10D, restated where the report could blur it."""
    assert SALES_DATASET_ID != SAMPLES_DATASET_ID
    assert rule("sample").statement.lower().count("never added to sales") == 1


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_the_five_windows_are_declared_and_labelled() -> None:
    assert [key.value for key in WindowKey] == [
        "current_month",
        "prior_month",
        "prior_year_month",
        "year_to_date",
        "prior_year_to_date",
    ]
    for key in WindowKey:
        assert WINDOW_LABELS[key].strip()


def test_the_windows_cover_build_plan_13cs_four_kinds_of_comparison() -> None:
    keys = set(WindowKey)

    assert WindowKey.CURRENT_MONTH in keys  # current period
    assert WindowKey.PRIOR_MONTH in keys  # month over month
    assert WindowKey.PRIOR_YEAR_MONTH in keys  # year over year
    assert WindowKey.PRIOR_YEAR_TO_DATE in keys  # prior period


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def test_section_ids_are_unique() -> None:
    ids = [item.id for item in REPORT_SECTIONS]
    assert len(set(ids)) == len(ids)


def test_a_section_is_looked_up_by_id_and_never_guessed() -> None:
    assert section("rep_summary").label == "Rep Summary"
    for near_miss in ("Rep Summary", "rep-summary", ""):
        with pytest.raises(KeyError):
            section(near_miss)


def test_every_13f_category_is_answered_by_a_section() -> None:
    """Build plan 13F's list, mapped onto tables that exist.

    "Do not remove an existing accepted report section merely because it is
    inconvenient to implement" — so the mapping is data rather than prose,
    and a category nothing answers fails here.
    """
    answered = {
        category
        for item in REPORT_SECTIONS
        for category in item.categories
    }

    missing = sorted(set(BUILD_PLAN_13F_CATEGORIES) - answered)
    assert not missing, f"no section answers {missing}"


def test_no_section_claims_a_category_build_plan_13f_does_not_list() -> None:
    for item in REPORT_SECTIONS:
        for category in item.categories:
            assert category in BUILD_PLAN_13F_CATEGORIES, (
                f"{item.id} claims {category!r}, which build plan 13F does "
                "not list."
            )


def test_the_company_tables_are_the_ones_that_are_not_per_rep() -> None:
    """Build plan 13G: company figures are calculated once, not per rep."""
    shared = {item.id for item in REPORT_SECTIONS if not item.per_rep}

    assert shared == {
        "company_summary",
        "company_supplier_performance",
        "data_quality",
    }


def test_the_registered_action_declares_exactly_the_declared_sections() -> None:
    action = registry.get_action(REPORT_ACTION_ID)
    assert action is not None

    assert [output.id for output in action.outputs] == [
        item.id for item in REPORT_SECTIONS
    ]
    assert [output.label for output in action.outputs] == [
        item.label for item in REPORT_SECTIONS
    ]


# ---------------------------------------------------------------------------
# Conditions (build plan 13H)
# ---------------------------------------------------------------------------


def test_every_condition_has_a_code_a_summary_and_a_basis() -> None:
    for item in CONDITIONS:
        assert item.code.strip() and item.code.isupper()
        assert item.summary.strip()
        assert item.basis.strip(), f"{item.code} states no basis"


def test_condition_codes_are_unique() -> None:
    codes = [item.code for item in CONDITIONS]
    assert len(set(codes)) == len(codes)


def test_a_condition_is_looked_up_by_code_and_never_guessed() -> None:
    assert condition("MISSING_MEASURE").severity is Severity.ERROR
    for near_miss in ("missing_measure", "MISSING MEASURE", ""):
        with pytest.raises(KeyError):
            condition(near_miss)


def test_the_error_and_warning_lists_partition_the_conditions() -> None:
    assert set(ERROR_CODES) | set(WARNING_CODES) == {
        item.code for item in CONDITIONS
    }
    assert not set(ERROR_CODES) & set(WARNING_CODES)


@pytest.mark.parametrize(
    "code",
    [
        "UNRECOGNISED_SALES_REP",
        "MISSING_ACCOUNT_OWNERSHIP",
        "DUPLICATE_ACCOUNT_OWNERSHIP",
        "MALFORMED_INVOICE_DATE",
        "UNEXPECTED_INVOICE_TYPE",
        "MISSING_MEASURE",
        "SAMPLE_PERIOD_MISMATCH",
        "UNEXPECTED_SOURCE_COLUMNS",
        "DUPLICATE_SOURCE_ROWS",
        "MISSING_COMPARISON_PERIOD",
    ],
)
def test_build_plan_13h_names_this_condition_and_it_is_declared(code) -> None:
    """Every example build plan 13H lists, as a declared condition.

    13H names: unrecognised sales reps, missing account ownership, duplicate
    account ownership, malformed invoice dates, unexpected invoice types,
    missing required monetary/quantity fields, reporting-period mismatch,
    unexplained source-schema changes, duplicate monthly source data, and a
    missing historical comparison period.
    """
    assert condition(code).summary.strip()


def test_a_condition_that_would_state_something_false_is_an_error() -> None:
    """13H: fail where a condition makes the report unreliable."""
    for code in (
        "MISSING_ACCOUNT_OWNERSHIP",
        "DUPLICATE_ACCOUNT_OWNERSHIP",
        "MISSING_MEASURE",
        "NON_NUMERIC_MEASURE",
        "MALFORMED_INVOICE_DATE",
        "SAMPLE_PERIOD_MISMATCH",
        "EMPTY_SALES_HISTORY",
        "NO_SALES_REPS",
    ):
        assert severity_of(code) is Severity.ERROR


def test_a_condition_that_changes_no_figure_is_a_warning() -> None:
    """13H: warnings only where continuing is genuinely safe."""
    for code in (
        "UNRECOGNISED_SALES_REP",
        "UNEXPECTED_INVOICE_TYPE",
        "UNEXPECTED_SOURCE_COLUMNS",
        "DUPLICATE_SOURCE_ROWS",
        "MISSING_COMPARISON_PERIOD",
        "SHORT_PLACEMENT_HISTORY",
        "PROVISIONAL_REPORT_RULES",
    ):
        assert severity_of(code) is Severity.WARNING


# ---------------------------------------------------------------------------
# The values the engine reads
# ---------------------------------------------------------------------------


def test_the_expected_invoice_types_are_declared() -> None:
    assert KNOWN_INVOICE_TYPES == ("Invoice", "Credit")
    assert rule("known_invoice_types").confidence is Confidence.PROVISIONAL


def test_money_is_stated_at_the_sources_own_precision() -> None:
    assert MONEY_DECIMALS == 2
    assert rule("money_precision").confidence is Confidence.CONFIRMED


def test_the_placement_history_expectation_matches_the_year_over_year_span() -> None:
    assert MINIMUM_PLACEMENT_HISTORY_MONTHS == 12
    assert rule("placement_history").confidence is Confidence.PROVISIONAL


def test_the_spec_module_reads_no_file_and_no_clock() -> None:
    """It is declarations. Nothing in it may do work."""
    source = Path(report_spec.__file__).read_text(encoding="utf-8")

    for forbidden in ("open(", "datetime.now", "date.today", "os.", "Path("):
        assert forbidden not in source, (
            f"report_spec.py uses {forbidden!r}; the specification is "
            "declarations and must not do work."
        )
