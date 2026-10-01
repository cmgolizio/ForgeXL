"""Synthetic completed-month scenarios with independently hand-worked controls.

No customer names, source prices or actual company totals are published.
Amounts below are independently hand-worked controls, not engine snapshots.
Twenty-four monthly $1.25 invoices establish the two R12 coverage windows.
August adds $47.50 - $4.75 for Alpha and $14.25 for Beta on a transferred
account. Alpha therefore has $44.00 in August and $57.75 in current R12,
versus $15.00 in prior R12. Beta keeps its own $14.25 despite current ownership.
"""

from __future__ import annotations

import polars as pl

from tests.fixtures.monthly_sources import TRANSACTION_HEADER, transaction_row

ALPHA = "Rep Alpha"
BETA = "Rep Beta"
IDLE = "Rep Idle"
ACCOUNT = "Account One"
TRANSFERRED = "Transferred Account"
SUPPLIER_ONE = "Supplier One"
SUPPLIER_TWO = "Supplier Two"


def line(day: str, rep: str, customer: str, number: str, quantity: float,
         price: float, total: float, *, kind: str = "Invoice", second: bool = False) -> tuple:
    return transaction_row(
        invoice_date=day, invoice_type=kind, invoice_number=number,
        customer=customer, sales_person=rep, sku="SKU-2" if second else "SKU-1",
        supplier=SUPPLIER_TWO if second else SUPPLIER_ONE,
        producer="Producer Deux" if second else "Producer Élan",
        selection="Red" if second else "Blanc", quantity=quantity,
        item_price=price, total_price=total,
    )


def inputs() -> dict[str, pl.DataFrame]:
    sales = []
    samples = []
    for index in range(24):
        year, offset = divmod(2024 * 12 + 8 + index, 12)
        month = f"{year:04d}-{offset + 1:02d}"
        sales.append(line(month + "-10", ALPHA, ACCOUNT, f"INV-{month}", 1, 1.25, 1.25))
        if index >= 12:
            samples.append(line(month + "-12", ALPHA, ACCOUNT, f"SMP-{month}", 1, 1.25, 1.25, kind="Sample Invoice"))
    sales.extend((
        line("2026-08-15", ALPHA, TRANSFERRED, "INV-A", 10, 4.75, 47.50, second=True),
        line("2026-08-20", ALPHA, TRANSFERRED, "CR-A", -1, 4.75, -4.75, kind="Credit Invoice", second=True),
        line("2026-08-21", BETA, TRANSFERRED, "INV-B", 3, 4.75, 14.25, second=True),
    ))
    samples.extend((
        line("2026-08-25", ALPHA, ACCOUNT, "SCR-A", -1, 1.25, -1.25, kind="Sample Credit Invoice"),
        line("2026-08-26", BETA, TRANSFERRED, "SMP-B", 2, 4.75, 9.50, kind="Sample Invoice", second=True),
    ))
    return {
        "sales_history": pl.DataFrame(sales, schema=list(TRANSACTION_HEADER), orient="row"),
        "sample_history": pl.DataFrame(samples, schema=list(TRANSACTION_HEADER), orient="row"),
        "account_assignments": pl.DataFrame({"Customer": [ACCOUNT, TRANSFERRED, "Idle Account"],
                                             "Sales Person": [ALPHA, ALPHA, IDLE]}),
    }
