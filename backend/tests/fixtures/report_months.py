"""The golden month the report is checked against (build plan 13I).

Build plan 13I asks for "at least one previously completed monthly report whose
values have been manually spot-checked", reproduced as synthetic fixtures, and
for the report's *values* to be verified rather than its row counts.

No such month exists in this repository — see
`docs/monthly-sales-rep-report-spec.md` §Status — so what is frozen here is a
month small enough to work out **by hand** and wide enough to exercise every
rule the specification states. :data:`EXPECTED` holds those hand-worked
figures, and `test_golden_month.py` asserts the engine reproduces them. Every
number in it was calculated from the rows below with a pencil, not captured
from a run: a figure copied out of the implementation would only prove the
implementation agrees with itself.

Twelve sales rows and three sample rows across four months, chosen so that:

* all five comparison windows contain rows, so no growth figure is vacuous;
* one rep owns two accounts and another owns one, so rep totals and company
  totals are different sums;
* one rep owns an account with no activity at all, so a rep who sold nothing
  still gets a row (rule ``rep_roster``);
* a credit carries a negative quantity and a negative value, so the signed
  treatment of returns is visible in a total (rule ``credits``);
* two placements happen and two near-misses do not — a product the account
  already bought, and a credit (rule ``placement``);
* one rep sells a supplier the other does not, so the company-versus-rep
  comparison has a zero row and a share above the company's;
* accented producer and selection values travel through every table.

Everything here is synthetic (build plan 6H.8). The names are the ones the
Phase 10 fixtures already use, so a reader moving between the two modules
meets the same cast.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import polars as pl

from app.models.report_spec import (
    ASSIGNMENTS_CUSTOMER_COLUMN,
    ASSIGNMENTS_REP_COLUMN,
    TRANSACTION_COLUMNS,
)

from tests.fixtures.monthly_sources import (
    ASSIGNMENT_HEADER,
    TRANSACTION_HEADER,
    assignments,
    transaction_row,
    transactions,
)
from tests.fixtures.spreadsheets import Table

#: The month the golden report is for.
GOLDEN_MONTH = "2026-09"

#: Every month the golden sales history covers, oldest first.
SALES_MONTHS: tuple[str, ...] = ("2025-08", "2025-09", "2026-08", "2026-09")

#: Every month the golden sample history covers.
SAMPLE_MONTHS: tuple[str, ...] = ("2026-08", "2026-09")

# ---------------------------------------------------------------------------
# The cast
# ---------------------------------------------------------------------------

BETH = "Beth Comeaux"
KEVIN = "Kevin Wardell"
JENNIFER = "Jennifer Jones"

ACME = "Acme Wine Bar"
BISTRO = "Bistro Lumière"
CORNER = "Corner Bottle"
HARBOUR = "Harbour Cellars"

#: Account -> owning rep for the reporting month. Jennifer owns an account
#: that never trades, which is what proves a silent rep still gets a report.
OWNERSHIP: tuple[tuple[str, str], ...] = (
    (ACME, BETH),
    (CORNER, BETH),
    (BISTRO, KEVIN),
    (HARBOUR, JENNIFER),
)

CUSTOMER_TYPES: dict[str, str] = {
    ACME: "On Premise",
    BISTRO: "On Premise",
    CORNER: "Off Premise",
    HARBOUR: "Off Premise",
}

ACME_IMPORTS = "Acme Imports"
GLOBAL_VINES = "Global Vines"

#: SKU -> (supplier, producer, selection, vintage, volume). The accents are
#: the default rather than a special case: nothing in this pipeline may fold
#: them.
PRODUCTS: dict[str, tuple[str, str, str, str, str]] = {
    "SKU-100": (ACME_IMPORTS, "Château Margaux", "Réserve", "2021", "750ml"),
    "SKU-200": (GLOBAL_VINES, "Bodega Muñoz", "Classic", "2020", "750ml"),
    "SKU-300": (ACME_IMPORTS, "Domaine Père", "Blanc", "2022", "1.5L"),
}


def _line(
    day: str,
    customer: str,
    invoice_number: str,
    sku: str,
    quantity: float,
    item_price: float,
    total_price: float,
    *,
    invoice_type: str = "Invoice",
    sales_person: str | None = None,
) -> tuple[Any, ...]:
    """One transaction row, with the product's own description filled in."""
    supplier, producer, selection, vintage, volume = PRODUCTS[sku]
    owner = dict(OWNERSHIP)[customer]
    return transaction_row(
        invoice_date=day,
        invoice_type=invoice_type,
        invoice_number=invoice_number,
        customer=customer,
        cust_type=CUSTOMER_TYPES[customer],
        sales_person=sales_person if sales_person is not None else owner,
        sku=sku,
        vintage=vintage,
        supplier=supplier,
        producer=producer,
        selection=selection,
        volume=volume,
        quantity=quantity,
        item_price=item_price,
        total_price=total_price,
    )


#: The golden sales history, one entry per month.
SALES_ROWS: dict[str, tuple[tuple[Any, ...], ...]] = {
    # Prior year to date only.
    "2025-08": (
        _line("2025-08-05", ACME, "INV-2508-1", "SKU-100", 10, 20.00, 200.00),
        _line("2025-08-06", BISTRO, "INV-2508-2", "SKU-200", 5, 30.00, 150.00),
    ),
    # The year-over-year month, and part of prior year to date.
    "2025-09": (
        _line("2025-09-10", ACME, "INV-2509-1", "SKU-100", 20, 20.00, 400.00),
        _line("2025-09-11", BISTRO, "INV-2509-2", "SKU-200", 10, 30.00, 300.00),
        _line("2025-09-12", CORNER, "INV-2509-3", "SKU-100", 5, 20.00, 100.00),
    ),
    # The month before the reporting month, and part of year to date.
    "2026-08": (
        _line("2026-08-03", ACME, "INV-2608-1", "SKU-100", 12, 25.00, 300.00),
        _line("2026-08-04", BISTRO, "INV-2608-2", "SKU-200", 8, 30.00, 240.00),
    ),
    # The reporting month.
    "2026-09": (
        _line("2026-09-02", ACME, "INV-2609-1", "SKU-100", 15, 25.00, 375.00),
        # A placement: this account has never bought SKU-300 before.
        _line("2026-09-05", ACME, "INV-2609-2", "SKU-300", 4, 50.00, 200.00),
        # A placement: this account has never bought SKU-200 before.
        _line("2026-09-09", CORNER, "INV-2609-3", "SKU-200", 6, 30.00, 180.00),
        # Not a placement: bought in August 2025.
        _line("2026-09-15", BISTRO, "INV-2609-4", "SKU-200", 10, 30.00, 300.00),
        # A credit. Negative quantity and value, counted, never a placement.
        _line(
            "2026-09-20",
            BISTRO,
            "CR-2609-1",
            "SKU-200",
            -2,
            30.00,
            -60.00,
            invoice_type="Credit",
        ),
    ),
}

#: The golden sample history. Samples are their own dataset and are never
#: added to sales (build plan 10D).
SAMPLE_ROWS: dict[str, tuple[tuple[Any, ...], ...]] = {
    "2026-08": (
        _line("2026-08-20", ACME, "SMP-2608-1", "SKU-100", 3, 25.00, 75.00),
    ),
    "2026-09": (
        _line("2026-09-04", ACME, "SMP-2609-1", "SKU-300", 2, 50.00, 100.00),
        _line("2026-09-08", BISTRO, "SMP-2609-2", "SKU-100", 1, 25.00, 25.00),
    ),
}


# ---------------------------------------------------------------------------
# As files, for the ingestion and end-to-end paths
# ---------------------------------------------------------------------------


def sales_table(period: str) -> Table:
    """One month of the golden sales history as an uploadable table."""
    return transactions(
        SALES_ROWS[period],
        name=f"golden-sales-{period}",
        description=f"The golden month's sales for {period}.",
    )


def sample_table(period: str) -> Table:
    """One month of the golden sample history as an uploadable table."""
    return transactions(
        SAMPLE_ROWS[period],
        name=f"golden-samples-{period}",
        description=f"The golden month's samples for {period}.",
    )


def assignment_table() -> Table:
    """The account-assignment snapshot for the reporting month."""
    return assignments(
        OWNERSHIP,
        name="golden-assignments",
        description="Ownership as of the golden reporting month.",
    )


# ---------------------------------------------------------------------------
# As frames, for the engine
# ---------------------------------------------------------------------------


def _frame(
    header: Sequence[str], rows: Sequence[Sequence[Any]]
) -> pl.DataFrame:
    """Build a frame shaped the way the Data Library hands one back.

    Dates arrive as text, which is what a CSV month stores; the workbook path
    stores real dates and is covered end to end by the Action tests.
    """
    if not rows:
        return pl.DataFrame(schema={name: pl.String for name in header})
    return pl.DataFrame(
        [list(row) for row in rows], schema=list(header), orient="row"
    )


def sales_frame(months: Sequence[str] = SALES_MONTHS) -> pl.DataFrame:
    """The golden sales history, as the resolver would hand it over."""
    rows = [row for month in months for row in SALES_ROWS[month]]
    return _frame(TRANSACTION_HEADER, rows)


def sample_frame(months: Sequence[str] = SAMPLE_MONTHS) -> pl.DataFrame:
    """The golden sample history."""
    rows = [row for month in months for row in SAMPLE_ROWS[month]]
    return _frame(TRANSACTION_HEADER, rows)


def assignment_frame(
    pairs: Sequence[tuple[str, str]] = OWNERSHIP,
) -> pl.DataFrame:
    """The golden account-assignment snapshot."""
    return _frame(ASSIGNMENT_HEADER, [list(pair) for pair in pairs])


def replace_value(
    frame: pl.DataFrame, column: str, at: int, value: Any
) -> pl.DataFrame:
    """Return `frame` with one cell replaced, for the failure tests.

    A helper rather than a hand-built frame per test, so a test about a blank
    `Total Price` differs from the golden month in exactly that cell.

    Putting text into a numeric column widens the whole column to text, which
    is what a real export does: a CSV whose `Total Price` holds ``$1,234.56``
    on one line parses as text on every line. The other values keep their
    values and simply arrive as their own text, so they still read as numbers
    and only the one cell does not.
    """
    values = frame.get_column(column).to_list()
    values[at] = value

    dtype = frame.schema[column]
    if isinstance(value, str) and dtype != pl.String:
        values = [
            item if item is None or isinstance(item, str) else str(item)
            for item in values
        ]
        dtype = pl.String

    return frame.with_columns(pl.Series(column, values, dtype=dtype))


# ---------------------------------------------------------------------------
# The hand-worked answers (build plan 13I)
#
# Every figure below was calculated from the rows above by hand. None was
# read out of a run.
# ---------------------------------------------------------------------------

EXPECTED: dict[str, Any] = {
    "report_month": GOLDEN_MONTH,
    "report_label": "September 2026",
    "reps": (BETH, JENNIFER, KEVIN),
    "company": {
        # 375.00 + 200.00 + 180.00 + 300.00 - 60.00
        "Revenue": 995.00,
        # 15 + 4 + 6 + 10 - 2
        "Quantity": 33.0,
        "Lines": 5,
        "Accounts Sold": 3,
        "Accounts": 4,
        "Sales Reps": 3,
        # 300.00 + 240.00
        "Prior Month Revenue": 540.00,
        # (995 - 540) / 540
        "MoM Growth": 455.0 / 540.0,
        # 400.00 + 300.00 + 100.00
        "Last Year Revenue": 800.00,
        # (995 - 800) / 800
        "YoY Growth": 195.0 / 800.0,
        # 540.00 + 995.00
        "YTD Revenue": 1535.00,
        # (200 + 150) + 800
        "Prior YTD Revenue": 1150.00,
        # (1535 - 1150) / 1150
        "YTD Growth": 385.0 / 1150.0,
        "Placements": 2,
        "Sample Lines": 2,
        "Sample Quantity": 3.0,
        "Sample Value": 125.00,
    },
    "reps_summary": {
        BETH: {
            # 375.00 + 200.00 + 180.00
            "Revenue": 755.00,
            "Quantity": 25.0,
            "Lines": 3,
            "Accounts Sold": 2,
            "Accounts": 2,
            "Prior Month Revenue": 300.00,
            "MoM Growth": 455.0 / 300.0,
            # 400.00 + 100.00
            "Last Year Revenue": 500.00,
            "YoY Growth": 255.0 / 500.0,
            "YTD Revenue": 1055.00,
            # 200.00 + 400.00 + 100.00
            "Prior YTD Revenue": 700.00,
            "YTD Growth": 355.0 / 700.0,
            "Share of Company Revenue": 755.0 / 995.0,
            "Placements": 2,
            "Sample Lines": 1,
            "Sample Quantity": 2.0,
            "Sample Value": 100.00,
        },
        KEVIN: {
            # 300.00 - 60.00
            "Revenue": 240.00,
            "Quantity": 8.0,
            "Lines": 2,
            "Accounts Sold": 1,
            "Accounts": 1,
            "Prior Month Revenue": 240.00,
            "MoM Growth": 0.0,
            "Last Year Revenue": 300.00,
            "YoY Growth": -60.0 / 300.0,
            "YTD Revenue": 480.00,
            # 150.00 + 300.00
            "Prior YTD Revenue": 450.00,
            "YTD Growth": 30.0 / 450.0,
            "Share of Company Revenue": 240.0 / 995.0,
            "Placements": 0,
            "Sample Lines": 1,
            "Sample Quantity": 1.0,
            "Sample Value": 25.00,
        },
        JENNIFER: {
            "Revenue": 0.0,
            "Quantity": 0.0,
            "Lines": 0,
            "Accounts Sold": 0,
            "Accounts": 1,
            "Prior Month Revenue": 0.0,
            "MoM Growth": None,
            "Last Year Revenue": 0.0,
            "YoY Growth": None,
            "YTD Revenue": 0.0,
            "Prior YTD Revenue": 0.0,
            "YTD Growth": None,
            "Share of Company Revenue": 0.0,
            "Placements": 0,
            "Sample Lines": 0,
            "Sample Quantity": 0.0,
            "Sample Value": 0.0,
        },
    },
    "accounts": {
        (BETH, ACME): {"Revenue": 575.00, "Quantity": 19.0, "Lines": 2},
        (BETH, CORNER): {"Revenue": 180.00, "Quantity": 6.0, "Lines": 1},
        (KEVIN, BISTRO): {"Revenue": 240.00, "Quantity": 8.0, "Lines": 2},
        (JENNIFER, HARBOUR): {"Revenue": 0.0, "Quantity": 0.0, "Lines": 0},
    },
    "company_suppliers": {
        # SKU-100 375.00 + SKU-300 200.00
        ACME_IMPORTS: {
            "Revenue": 575.00,
            "Share of Company Revenue": 575.0 / 995.0,
        },
        # 180.00 + 300.00 - 60.00
        GLOBAL_VINES: {
            "Revenue": 420.00,
            "Share of Company Revenue": 420.0 / 995.0,
        },
    },
    "rep_suppliers": {
        (BETH, ACME_IMPORTS): {
            "Revenue": 575.00,
            "Share of Rep Revenue": 575.0 / 755.0,
        },
        (BETH, GLOBAL_VINES): {
            "Revenue": 180.00,
            "Share of Rep Revenue": 180.0 / 755.0,
        },
        (KEVIN, GLOBAL_VINES): {
            "Revenue": 240.00,
            "Share of Rep Revenue": 1.0,
        },
    },
    "supplier_comparison": {
        (KEVIN, GLOBAL_VINES): {
            "Rep Revenue": 240.00,
            "Rep Share": 1.0,
            "Company Share": 420.0 / 995.0,
            "Index": 1.0 / (420.0 / 995.0),
        },
        (KEVIN, ACME_IMPORTS): {
            "Rep Revenue": 0.0,
            "Rep Share": 0.0,
            "Company Share": 575.0 / 995.0,
            "Index": 0.0,
        },
    },
    "placements": {
        (BETH, ACME, "SKU-300"): {
            "Quantity": 4.0,
            "Revenue": 200.00,
            "First Sold": "2026-09-05",
        },
        (BETH, CORNER, "SKU-200"): {
            "Quantity": 6.0,
            "Revenue": 180.00,
            "First Sold": "2026-09-09",
        },
    },
    "samples": {
        (BETH, ACME): {
            "Sample Lines": 1,
            "Sample Quantity": 2.0,
            "Sample Value": 100.00,
            "Sample Products": 1,
        },
        (KEVIN, BISTRO): {
            "Sample Lines": 1,
            "Sample Quantity": 1.0,
            "Sample Value": 25.00,
            "Sample Products": 1,
        },
    },
    # The conditions a clean golden month still reports. Both are true of it:
    # only three months precede the reporting month, and the specification is
    # not yet confirmed.
    "warnings": ("PROVISIONAL_REPORT_RULES", "SHORT_PLACEMENT_HISTORY"),
}


#: Every column the report's tables are keyed by, for the tests that look a
#: row up rather than asserting a whole frame.
KEYS: dict[str, tuple[str, ...]] = {
    "rep_summary": ("Sales Rep",),
    "account_performance": ("Sales Rep", TRANSACTION_COLUMNS.customer),
    "supplier_performance": ("Sales Rep", TRANSACTION_COLUMNS.supplier),
    "company_supplier_performance": (TRANSACTION_COLUMNS.supplier,),
    "supplier_comparison": ("Sales Rep", TRANSACTION_COLUMNS.supplier),
    "placements": (
        "Sales Rep",
        TRANSACTION_COLUMNS.customer,
        TRANSACTION_COLUMNS.sku,
    ),
    "samples": ("Sales Rep", TRANSACTION_COLUMNS.customer),
}

__all__ = [
    "ACME",
    "ACME_IMPORTS",
    "ASSIGNMENTS_CUSTOMER_COLUMN",
    "ASSIGNMENTS_REP_COLUMN",
    "BETH",
    "BISTRO",
    "CORNER",
    "EXPECTED",
    "GLOBAL_VINES",
    "GOLDEN_MONTH",
    "HARBOUR",
    "JENNIFER",
    "KEVIN",
    "KEYS",
    "OWNERSHIP",
    "PRODUCTS",
    "SALES_MONTHS",
    "SALES_ROWS",
    "SAMPLE_MONTHS",
    "SAMPLE_ROWS",
    "assignment_frame",
    "assignment_table",
    "replace_value",
    "sales_frame",
    "sales_table",
    "sample_frame",
    "sample_table",
]
