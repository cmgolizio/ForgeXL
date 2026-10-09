"""Calculate the reference workbook's grouped display from verified views.

Flat preview/export tables retain their schemas. This adapter supplies literal
group subtotals, display columns and sorting to the shared workbook renderer.
It never reads the reference workbook or proprietary data at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
import calendar
import unicodedata

import polars as pl

from app.models.report_spec import TRANSACTION_COLUMNS
from app.services.report_views import (
    BOTTLES, CHANGE, COMPANY_SALES, COMPANY_SHARE, CURRENT_R12, GROWTH,
    NET_SALES, PRIOR_R12, PRODUCT, REP_SALES, REP_SHARE, R12_BOTTLES,
)
from app.services.workbook import CellFormat, Column, ReferenceLayout

LABEL = "Row Labels"


def _alphabetic(value: object) -> tuple[str, str]:
    """Ordering only: accents/case in identities and output are untouched."""
    text = str(value) if value is not None else ""
    return ("".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                    if not unicodedata.combining(c)), text)


def _grouped(
    frame: pl.DataFrame, parent: str, child: str, measures: list[str],
    sort_measure: str | None = None,
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    groups = frame.group_by(parent).agg([
        pl.when(pl.col(name).null_count() > 0).then(None)
        .otherwise(pl.col(name).sum()).alias(name) for name in measures
    ])
    parents = list(groups.iter_rows(named=True))
    if sort_measure:
        parents.sort(key=lambda r: (r[sort_measure] is None,
                                   -(r[sort_measure] or 0), _alphabetic(r[parent])))
    else:
        parents.sort(key=lambda r: _alphabetic(r[parent]))
    rows = []
    roles = []
    children: dict[object, list[dict]] = {}
    for record in frame.iter_rows(named=True):
        children.setdefault(record[parent], []).append(record)
    for group in parents:
        rows.append({LABEL: group[parent], **{name: group[name] for name in measures}})
        roles.append("group")
        for record in sorted(children[group[parent]], key=lambda r: _alphabetic(r[child])):
            rows.append({LABEL: record[child], **{name: record[name] for name in measures}})
            roles.append("detail")
    schema = {LABEL: pl.String, **{name: frame.schema[name] for name in measures}}
    return pl.DataFrame(rows, schema=schema), tuple(roles)


def reference_display(
    view_id: str, frame: pl.DataFrame, totals: Mapping[str, object], rep: str,
) -> tuple[pl.DataFrame, tuple[Column, ...], dict[str, object], ReferenceLayout]:
    supplier = TRANSACTION_COLUMNS.supplier
    customer = TRANSACTION_COLUMNS.customer
    money = CellFormat.CURRENCY_REFERENCE
    quantity = CellFormat.GENERAL
    totals = dict(totals)
    if view_id == "monthly_samples":
        display, roles = _grouped(frame, supplier, PRODUCT, [BOTTLES])
        columns = (Column(LABEL, width=76.6640625), Column(BOTTLES, "Sample Bottles", quantity, 13.1640625))
        return display, columns, totals, ReferenceLayout("samples", roles)
    if view_id == "rolling_samples":
        # Same Jan–Dec layout as the example. Each heading maps to the unique
        # year-bearing month in this exact R12 window; no years are combined.
        months = sorted((name for name in frame.columns if name not in {supplier, PRODUCT, R12_BOTTLES}),
                        key=lambda name: list(calendar.month_abbr).index(name.split()[0]))
        measures = [*months, R12_BOTTLES]
        display, roles = _grouped(frame, supplier, PRODUCT, measures, R12_BOTTLES)
        widths = [83.83203125, 16, 8.33203125, 6.33203125, 5, 4.5, 4.83203125,
                  4.1640625, 7, 10.1640625, 7.83203125, 9.6640625, 9.83203125, 10.5]
        columns = (Column(LABEL, width=widths[0]), *(
            Column(name, calendar.month_name[list(calendar.month_abbr).index(name.split()[0])], quantity, widths[index + 1])
            for index, name in enumerate(months)),
            Column(R12_BOTTLES, "Grand Total", quantity, widths[-1]))
        prefix = (("Sum of Bottles", "Column Labels"),)
        return display, columns, totals, ReferenceLayout("sample_months", roles, prefix)
    if view_id == "rolling_product_accounts":
        display, roles = _grouped(frame, PRODUCT, customer, [BOTTLES])
        columns = (Column(LABEL, width=85), Column(BOTTLES, "Sum of Bottles", quantity, 13.1640625))
        return display, columns, totals, ReferenceLayout("products", roles)
    display = frame.with_columns(pl.lit(rep).alias("Sales Person"))
    if view_id == "rolling_account_sales":
        columns = (Column(customer, width=42.1640625), Column("Sales Person", width=12.83203125),
                   Column(NET_SALES, "$", money, 12.6640625))
        return display, columns, totals, ReferenceLayout("sales")
    if view_id == "monthly_supplier_sales":
        columns = (Column(supplier, width=37.83203125),
                   Column(REP_SALES, f"$ ({rep.split()[0]})", money, 10.1640625),
                   Column(REP_SHARE, format=CellFormat.PERCENT_TWO_DECIMALS, width=13),
                   Column(COMPANY_SALES, "$ (Company)", money, 11.6640625),
                   Column(COMPANY_SHARE, "% of Company Sales", CellFormat.PERCENT_DECIMAL, 17.83203125))
        # The reference leaves this footer blank. Keep its correct computed
        # value in the calculation table rather than adding a new display item.
        totals[REP_SHARE] = None
        return display, columns, totals, ReferenceLayout("sales")
    columns = (Column(customer, width=33.1640625), Column("Sales Person", width=14.33203125),
               Column(PRIOR_R12, format=money, width=12.6640625),
               Column(CURRENT_R12, format=money, width=13.6640625),
               Column(CHANGE, format=money, width=11.5),
               Column(GROWTH, format=CellFormat.PERCENT, width=11.83203125),
               Column("Status", width=12.33203125))
    display = display.sort([CHANGE, customer], descending=[True, False], nulls_last=True)
    totals[GROWTH] = None
    return display, columns, totals, ReferenceLayout("comparison")
