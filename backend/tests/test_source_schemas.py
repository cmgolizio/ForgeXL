"""The canonical source schemas (build plan 10A).

Build plan 10A's requirement is unusual in that most of it is about what the
code must *not* do: not guess a column name, not treat a similar name as
equivalent, not normalise or alias without saying so. So most of this module
asserts absences — that no aliasing exists, that comparison is exact, that the
confirmed schemas say what the export says.

The one thing here that is not confirmed is the account-assignment schema, and
that is asserted too: `confirmed` is False, and a test says so, so the
provisional status cannot quietly become permanent by being forgotten.
"""

from __future__ import annotations

import pytest

from app.models.library import ACCOUNT_ASSIGNMENTS, SALES_HISTORY, SAMPLE_HISTORY
from app.models.source_schemas import (
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
    DEFAULT_DATE_FORMATS,
    SALES_SOURCE_SCHEMA,
    SAMPLES_SOURCE_SCHEMA,
    SOURCE_SCHEMAS,
    SourceColumnKind,
    schema_for,
)

# ---------------------------------------------------------------------------
# The confirmed transaction schema
#
# Written out in full, deliberately. This literal is the test's independent
# statement of what the user supplied; asserting it against
# `schema.column_names` compares two things rather than one thing with itself,
# so a column quietly renamed in the declaration fails here.
# ---------------------------------------------------------------------------

CONFIRMED_TRANSACTION_COLUMNS = (
    "Invoice Date",
    "Invoice Type",
    "Invoice Number",
    "Customer",
    "Cust Type",
    "Sales Person",
    "SKU",
    "Vintage",
    "Supplier",
    "Producer",
    "Selection",
    "Volume",
    "Quantity",
    "Item Price",
    "Total Price",
)


@pytest.mark.parametrize(
    "schema", (SALES_SOURCE_SCHEMA, SAMPLES_SOURCE_SCHEMA), ids=("sales", "samples")
)
def test_the_transaction_schema_is_the_confirmed_export_header(schema) -> None:
    assert schema.column_names == CONFIRMED_TRANSACTION_COLUMNS


@pytest.mark.parametrize(
    "schema", (SALES_SOURCE_SCHEMA, SAMPLES_SOURCE_SCHEMA), ids=("sales", "samples")
)
def test_the_transaction_schemas_are_confirmed(schema) -> None:
    """Supplied verbatim from the real exports, so they are marked as such."""
    assert schema.confirmed is True


def test_the_account_assignment_schema_is_marked_unconfirmed() -> None:
    """It was not supplied, and pretending otherwise would be the guess 10A forbids.

    This test is the reason the provisional status cannot be lost: confirming
    the schema means editing the declaration *and* this assertion, which is a
    deliberate act rather than a forgotten one.
    """
    assert ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.confirmed is False


def test_the_provisional_schema_reuses_confirmed_spellings() -> None:
    """Its two column names come from the confirmed schema, not from invention.

    ``Customer`` and ``Sales Person`` are how this company's confirmed export
    spells those two things. A provisional schema that made up a third spelling
    would be a worse guess than one that is at least consistent.
    """
    for column in ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.column_names:
        assert column in SALES_SOURCE_SCHEMA.column_names


def test_the_provisional_schema_declares_a_minimum_rather_than_a_whole_file() -> None:
    """Narrow on purpose: extra columns are kept and warned about, not refused.

    The fewer columns a provisional schema requires, the smaller the chance
    that the unconfirmed part of it blocks a real export.
    """
    assert len(ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.columns) == 2


# ---------------------------------------------------------------------------
# Sales and samples are distinct (build plan 10D)
# ---------------------------------------------------------------------------


def test_sales_and_samples_are_separate_schemas_for_separate_datasets() -> None:
    """Build plan 10D: distinct datasets even though the exports look alike."""
    assert SALES_SOURCE_SCHEMA is not SAMPLES_SOURCE_SCHEMA
    assert SALES_SOURCE_SCHEMA.dataset_id == SALES_HISTORY.id
    assert SAMPLES_SOURCE_SCHEMA.dataset_id == SAMPLE_HISTORY.id
    assert SALES_SOURCE_SCHEMA.dataset_id != SAMPLES_SOURCE_SCHEMA.dataset_id


def test_every_source_schema_names_a_declared_dataset() -> None:
    declared = {SALES_HISTORY.id, SAMPLE_HISTORY.id, ACCOUNT_ASSIGNMENTS.id}

    assert {schema.dataset_id for schema in SOURCE_SCHEMAS} == declared


# ---------------------------------------------------------------------------
# Exact comparison, and no aliasing at all (build plan 10A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    (
        "Salesperson",
        "sales person",
        "SALES PERSON",
        "Sales  Person",
        " Sales Person",
        "Sales Person ",
        "Sales_Person",
    ),
)
def test_a_differently_spelled_column_is_not_the_declared_one(spelling) -> None:
    """Build plan 10A: no case folding, no trimming, no near match.

    Every one of these is a plausible way another system spells the same idea,
    and treating any of them as ``Sales Person`` would be exactly the silent
    equivalence 10A forbids.
    """
    present = tuple(
        spelling if name == "Sales Person" else name
        for name in SALES_SOURCE_SCHEMA.column_names
    )

    assert SALES_SOURCE_SCHEMA.missing_from(present) == ("Sales Person",)
    assert spelling in SALES_SOURCE_SCHEMA.unexpected_in(present)


def test_no_schema_declares_an_alias() -> None:
    """There is no aliasing mechanism, so there is nothing to specify or test.

    Build plan 10A permits normalisation only if it is "explicitly specified,
    deterministic, and tested". The simplest way to satisfy that is to have
    none, and this asserts the absence rather than trusting it: a column is
    matched by its one declared name and nothing else.
    """
    for schema in SOURCE_SCHEMAS:
        names = schema.column_names
        assert len(set(names)) == len(names), schema.dataset_id
        for column in schema.columns:
            assert not hasattr(column, "aliases")


def test_missing_columns_are_reported_in_canonical_order() -> None:
    """So a message reads in the order the export produces, not hash order."""
    present = ("Invoice Type", "Customer")

    missing = SALES_SOURCE_SCHEMA.missing_from(present)

    assert missing == tuple(
        name for name in CONFIRMED_TRANSACTION_COLUMNS if name not in present
    )


def test_column_order_is_not_required() -> None:
    """A reordered export has lost nothing, so it is not refused.

    Refusing it would be a rule about presentation rather than about data.
    """
    reversed_order = tuple(reversed(SALES_SOURCE_SCHEMA.column_names))

    assert SALES_SOURCE_SCHEMA.missing_from(reversed_order) == ()
    assert SALES_SOURCE_SCHEMA.unexpected_in(reversed_order) == ()


def test_extra_columns_are_identified_without_being_required() -> None:
    present = SALES_SOURCE_SCHEMA.column_names + ("Warehouse", "Notes")

    assert SALES_SOURCE_SCHEMA.missing_from(present) == ()
    assert SALES_SOURCE_SCHEMA.unexpected_in(present) == ("Warehouse", "Notes")


# ---------------------------------------------------------------------------
# Roles the ingestion layer reads
# ---------------------------------------------------------------------------


def test_the_transaction_period_column_is_the_invoice_date() -> None:
    assert SALES_SOURCE_SCHEMA.period_column == "Invoice Date"
    assert SAMPLES_SOURCE_SCHEMA.period_column == "Invoice Date"


def test_a_snapshot_has_no_period_column() -> None:
    """Ownership as it stands carries no date; its month is stated explicitly."""
    assert ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.period_column is None


def test_the_numeric_columns_are_the_three_measures() -> None:
    assert SALES_SOURCE_SCHEMA.number_columns == (
        "Quantity",
        "Item Price",
        "Total Price",
    )


def test_identifier_columns_are_declared_as_text() -> None:
    """An invoice number that leads with a zero must keep it, so it is not a number."""
    for name in ("Invoice Number", "SKU", "Vintage"):
        column = SALES_SOURCE_SCHEMA.column(name)
        assert column is not None
        assert column.kind is SourceColumnKind.TEXT


def test_every_schema_declares_a_kind_for_every_column() -> None:
    for schema in SOURCE_SCHEMAS:
        for column in schema.columns:
            assert isinstance(column.kind, SourceColumnKind)
            assert column.description.strip()


# ---------------------------------------------------------------------------
# Date formats
# ---------------------------------------------------------------------------


def test_both_slash_orders_are_declared_so_neither_is_assumed() -> None:
    """The ambiguity is kept visible rather than resolved by preference.

    If only ``%m/%d/%Y`` were declared, ``13/04/2026`` would be unreadable and
    ``03/04/2026`` would be silently read as 4 March. Declaring both is what
    lets the reader notice that a column can be read two ways.
    """
    assert "%m/%d/%Y" in DEFAULT_DATE_FORMATS
    assert "%d/%m/%Y" in DEFAULT_DATE_FORMATS


def test_no_two_digit_year_format_is_declared() -> None:
    """They multiply ambiguity and no export in evidence produces one."""
    for fmt in DEFAULT_DATE_FORMATS:
        assert "%y" not in fmt


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_schema_for_returns_the_declared_schema() -> None:
    assert schema_for(SALES_HISTORY.id) is SALES_SOURCE_SCHEMA
    assert schema_for(SAMPLE_HISTORY.id) is SAMPLES_SOURCE_SCHEMA
    assert schema_for(ACCOUNT_ASSIGNMENTS.id) is ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA


@pytest.mark.parametrize(
    "dataset_id", ("", "sales", "sales_history_2", "unknown", "SALES_HISTORY")
)
def test_an_unrecognised_dataset_id_gets_no_schema(dataset_id) -> None:
    """Never a near match, the same rule the Action registry follows."""
    assert schema_for(dataset_id) is None