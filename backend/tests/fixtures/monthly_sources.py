"""Synthetic monthly source files for the ingestion tests (build plan 10, 6H).

Every file the Phase 10 tests import is built here, in memory, from Python
literals — the rule build plan 6H.8 states and `test_spreadsheet_fixtures.py`
enforces across the repository. No company export is committed, and none is
needed: what the ingestion layer has to get right is structural, and a
synthetic file exercises it exactly as a real one would.

The tables are built on :class:`tests.fixtures.spreadsheets.Table`, so every
one of them renders as **either** CSV or a one-worksheet XLSX. That matters
more here than anywhere else in the suite: a CSV carries its dates as text and
a workbook carries them as real dates, so the two formats reach
:mod:`app.services.reporting_period` by different paths. A fixture that only
existed as CSV would leave the workbook path untested.

Deliberately **not** added to `spreadsheets.CATALOGUE`. That catalogue's sweeps
assert every entry is read and returned unchanged, and several fixtures here
exist precisely to be refused. Keeping them separate preserves the property
that makes those sweeps evidence — the same reasoning Deviation 58 records for
`REFUSED_TABLES`.

Nothing here reads a clock. Dates are literals and "today" is passed into the
checks that need one, so a test that passes in September still passes in March.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.models.source_schemas import (
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
    SALES_SOURCE_SCHEMA,
)

from tests.fixtures.spreadsheets import Table

#: The confirmed 15-column header of the sales and sample exports, taken from
#: the schema declaration rather than retyped. A fixture that spelled a column
#: its own way would test the fixture instead of the application.
TRANSACTION_HEADER: tuple[str, ...] = SALES_SOURCE_SCHEMA.column_names

ASSIGNMENT_HEADER: tuple[str, ...] = ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.column_names

#: A date that is comfortably in the past for any plausible "today", so the
#: future-dated check never fires by accident on a fixture that is not about it.
#: The checks that *are* about it pass an explicit `today`.
DEFAULT_PERIOD = "2026-09"


def transaction_row(
    *,
    invoice_date: Any,
    customer: Any = "Acme Wine Bar",
    sales_person: Any = "Beth Comeaux",
    invoice_type: Any = "Invoice",
    invoice_number: Any = "INV-1001",
    cust_type: Any = "On Premise",
    sku: Any = "SKU-100",
    vintage: Any = "2021",
    supplier: Any = "Acme Imports",
    producer: Any = "Château Margaux",
    selection: Any = "Réserve",
    volume: Any = "750ml",
    quantity: Any = 6,
    item_price: Any = 24.5,
    total_price: Any = 147.0,
) -> tuple[Any, ...]:
    """One transaction row, in the export's column order.

    Every field has a default so a test states only what it is about: a test
    for period detection sets dates and nothing else, and a test for ownership
    sets customers and reps. Accented producer and selection values are the
    defaults rather than a special case, because nothing in this pipeline may
    fold them.
    """
    return (
        invoice_date,
        invoice_type,
        invoice_number,
        customer,
        cust_type,
        sales_person,
        sku,
        vintage,
        supplier,
        producer,
        selection,
        volume,
        quantity,
        item_price,
        total_price,
    )


def transactions(
    rows: Sequence[tuple[Any, ...]],
    *,
    name: str = "transactions",
    description: str = "A synthetic monthly transaction export.",
) -> Table:
    """Build a transaction table from rows built by :func:`transaction_row`."""
    return Table(
        name=name,
        description=description,
        header=TRANSACTION_HEADER,
        rows=tuple(rows),
    )


def month(
    period: str = DEFAULT_PERIOD,
    *,
    days: Sequence[int] = (1, 8, 17, 26),
    name: str | None = None,
    customer: str = "Acme Wine Bar",
    sales_person: str = "Beth Comeaux",
) -> Table:
    """One clean month of transactions, dated ``YYYY-MM-DD``.

    The ordinary case: several rows, all inside one month, unambiguous ISO
    dates. Used wherever a test needs a file that should simply import.
    """
    year, month_number = period.split("-")
    return transactions(
        [
            transaction_row(
                invoice_date=f"{year}-{month_number}-{day:02d}",
                invoice_number=f"INV-{period.replace('-', '')}-{index:03d}",
                customer=customer,
                sales_person=sales_person,
            )
            for index, day in enumerate(days, start=1)
        ],
        name=name or f"transactions-{period}",
        description=f"A clean {period} transaction export.",
    )


def months(periods: Sequence[str], *, name: str = "transactions-history") -> Table:
    """Transactions spanning several months — a historical bootstrap file.

    Each month gets a different number of rows so a test can tell the
    partitions apart by size rather than by trusting the order they come back
    in.
    """
    rows: list[tuple[Any, ...]] = []
    for count, period in enumerate(periods, start=1):
        year, month_number = period.split("-")
        rows.extend(
            transaction_row(
                invoice_date=f"{year}-{month_number}-{day:02d}",
                invoice_number=f"INV-{period.replace('-', '')}-{day:03d}",
            )
            for day in range(1, count + 1)
        )
    return transactions(
        rows,
        name=name,
        description=f"A historical export covering {', '.join(periods)}.",
    )


def assignments(
    pairs: Sequence[tuple[Any, Any]],
    *,
    name: str = "account-assignments",
    description: str = "A synthetic account-assignment snapshot.",
) -> Table:
    """Build an account-assignment snapshot from ``(customer, rep)`` pairs."""
    return Table(
        name=name,
        description=description,
        header=ASSIGNMENT_HEADER,
        rows=tuple(pairs),
    )


#: The ordinary assignment snapshot: three accounts, each with one owner.
CLEAN_ASSIGNMENTS = assignments(
    (
        ("Acme Wine Bar", "Beth Comeaux"),
        ("Bistro Lumière", "Kevin Wardell"),
        ("Corner Bottle", "Jennifer Jones"),
    ),
    name="account-assignments-clean",
    description="Three accounts, each owned by exactly one rep.",
)

#: One account named by two different reps — the condition build plan 10E calls
#: a duplicate customer assignment where ownership is expected to be unique.
CONFLICTING_ASSIGNMENTS = assignments(
    (
        ("Acme Wine Bar", "Beth Comeaux"),
        ("Acme Wine Bar", "Kevin Wardell"),
        ("Corner Bottle", "Jennifer Jones"),
    ),
    name="account-assignments-conflicting",
    description="One account assigned to two reps; ownership is ambiguous.",
)

#: The same account twice with the same owner. Says one true thing twice, so it
#: is not a conflict and must not be refused as one.
REPEATED_ASSIGNMENTS = assignments(
    (
        ("Acme Wine Bar", "Beth Comeaux"),
        ("Acme Wine Bar", "Beth Comeaux"),
        ("Corner Bottle", "Jennifer Jones"),
    ),
    name="account-assignments-repeated",
    description="One account listed twice with the same rep.",
)

#: A row with no account, and a row whose rep is whitespace. Both leave the
#: question a snapshot exists to answer unanswered.
BLANK_ASSIGNMENTS = assignments(
    (
        ("Acme Wine Bar", "Beth Comeaux"),
        (None, "Kevin Wardell"),
        ("Corner Bottle", "   "),
    ),
    name="account-assignments-blank",
    description="One row with no account and one row with no rep.",
)
