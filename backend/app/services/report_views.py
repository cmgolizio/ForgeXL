"""Pure calculations for six configured monthly workbook sections.

Invoice attribution, signed credits and rolling-year detail are explicit
software policies. Presentation receives their results without recalculation.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.models.report_spec import MONEY_DECIMALS, REP_COLUMN, TRANSACTION_COLUMNS, WindowKey
from app.services.monthly_report import (
    DATE, MONTH, OWNER, QUANTITY, REVENUE, PreparedReport, missing_months,
    months_in_window,
)

PRODUCT = "Producer | Selection"
BOTTLES = "Bottles"
R12_BOTTLES = "R12 Bottles"
NET_SALES = "Net Sales"
PRIOR_R12 = "Prior R12"
CURRENT_R12 = "Current R12"
CHANGE = "$ Change"
GROWTH = "% Change"
REP_SALES = "Rep Net Sales"
REP_SHARE = "% of Rep Sales"
COMPANY_SALES = "Company Supplier Net Sales"
COMPANY_SHARE = "% of Company Supplier Sales"

VIEW_IDS = (
    "monthly_samples", "rolling_samples", "rolling_account_sales",
    "monthly_supplier_sales", "rolling_product_accounts", "rolling_account_comparison",
)


def _products(frame: pl.DataFrame) -> pl.DataFrame:
    # The accepted display grouping retains blanks without writing "None".
    return frame.with_columns(
        pl.concat_str(
            [pl.col(TRANSACTION_COLUMNS.producer).fill_null(""),
             pl.col(TRANSACTION_COLUMNS.selection).fill_null("")], separator=" | "
        ).alias(PRODUCT)
    )


def _ratio(part: pl.Expr, whole: pl.Expr) -> pl.Expr:
    return pl.when(whole.is_not_null() & (whole != 0)).then(part / whole).otherwise(None)


def month_heading(month: str) -> str:
    """A chronological month heading that includes the year."""
    from datetime import date
    year, number = map(int, month.split("-"))
    return date(year, number, 1).strftime("%b %Y")


def build_report_views(
    prepared: PreparedReport, tables: Mapping[str, pl.DataFrame]
) -> dict[str, pl.DataFrame]:
    period = prepared.require_period()
    supplier = TRANSACTION_COLUMNS.supplier
    customer = TRANSACTION_COLUMNS.customer
    current = period.window(WindowKey.CURRENT_MONTH)
    rolling = period.window(WindowKey.ROLLING_YEAR)
    prior = period.window(WindowKey.PRIOR_ROLLING_YEAR)
    sales = _products(prepared.sales)
    samples = _products(prepared.samples)

    monthly_samples = (
        samples.filter(current.covers())
        .group_by([OWNER, supplier, PRODUCT])
        .agg(pl.col(QUANTITY).sum().alias(BOTTLES))
        .rename({OWNER: REP_COLUMN})
        .sort([REP_COLUMN, supplier, BOTTLES, PRODUCT], descending=[False, False, True, False], nulls_last=True)
    )

    # Month names alone would combine January from different years. Every
    # bucket has one exact YYYY-MM key and a year-bearing display heading.
    month_keys = months_in_window(rolling)
    missing_sample_months = set(missing_months(samples, rolling))
    rolling_samples = (
        samples.filter(rolling.covers())
        .group_by([OWNER, supplier, PRODUCT])
        .agg([
            (pl.lit(None, pl.Float64) if month in missing_sample_months else
             pl.col(QUANTITY).filter(pl.col(MONTH) == month).sum())
            .alias(month_heading(month)) for month in month_keys
        ] + [pl.col(QUANTITY).sum().alias(R12_BOTTLES)])
        .rename({OWNER: REP_COLUMN})
        .sort([REP_COLUMN, supplier, R12_BOTTLES, PRODUCT], descending=[False, False, True, False], nulls_last=True)
    )
    if missing_sample_months:
        rolling_samples = rolling_samples.with_columns(pl.lit(None, pl.Float64).alias(R12_BOTTLES))

    accounts = tables["account_performance"]
    current_complete = not missing_months(sales, rolling)
    prior_complete = not missing_months(sales, prior)
    # The standalone current-R12 list contains accounts with activity in that
    # window. The comparison includes prior-only accounts, but those must not
    # leak into this list as artificial zero rows.
    rolling_accounts = (
        sales.filter(rolling.covers()).group_by([OWNER, customer])
        .agg(pl.col(REVENUE).sum().round(MONEY_DECIMALS).alias(NET_SALES))
        .rename({OWNER: REP_COLUMN})
        .sort([REP_COLUMN, NET_SALES, customer], descending=[False, True, False], nulls_last=True)
    )
    if not current_complete:
        rolling_accounts = rolling_accounts.with_columns(pl.lit(None, pl.Float64).alias(NET_SALES))

    # Include credit-only and net-zero suppliers when they have current-month
    # activity. Historical-only suppliers do not belong to this month's sheet.
    monthly_suppliers = (
        tables["supplier_performance"].filter(pl.col("Lines") > 0)
        .select(REP_COLUMN, supplier, pl.col("Revenue").alias(REP_SALES),
                pl.col("Share of Rep Revenue").alias(REP_SHARE))
        .join(tables["company_supplier_performance"].select(
            supplier, pl.col("Revenue").alias(COMPANY_SALES)),
            on=supplier, how="left", nulls_equal=True)
        .with_columns(_ratio(pl.col(REP_SALES), pl.col(COMPANY_SALES)).alias(COMPANY_SHARE))
        .select(REP_COLUMN, supplier, REP_SALES, REP_SHARE, COMPANY_SALES, COMPANY_SHARE)
        .sort([REP_COLUMN, REP_SALES, supplier], descending=[False, True, False], nulls_last=True)
    )

    products = (
        sales.filter(rolling.covers()).group_by([OWNER, PRODUCT, customer])
        .agg(pl.col(QUANTITY).sum().alias(BOTTLES))
        .rename({OWNER: REP_COLUMN})
        .sort([REP_COLUMN, PRODUCT, BOTTLES, customer], descending=[False, False, True, False], nulls_last=True)
    )
    if not current_complete:
        # An incomplete twelve-month total is not a twelve-month total.
        products = products.with_columns(pl.lit(None, pl.Float64).alias(BOTTLES))

    comparisons = accounts.select(
        REP_COLUMN, customer,
        (pl.col("Prior R12 Revenue") if prior_complete else pl.lit(None, pl.Float64)).alias(PRIOR_R12),
        (pl.col("R12 Revenue") if current_complete else pl.lit(None, pl.Float64)).alias(CURRENT_R12),
    ).with_columns((pl.col(CURRENT_R12) - pl.col(PRIOR_R12)).round(MONEY_DECIMALS).alias(CHANGE))
    comparisons = comparisons.with_columns(
        _ratio(pl.col(CHANGE), pl.col(PRIOR_R12).abs()).alias(GROWTH),
        pl.when(pl.col(PRIOR_R12).is_null() | pl.col(CURRENT_R12).is_null()).then(pl.lit("Incomplete history"))
        .when((pl.col(PRIOR_R12) == 0) & (pl.col(CURRENT_R12) > 0)).then(pl.lit("New"))
        .when((pl.col(PRIOR_R12) > 0) & (pl.col(CURRENT_R12) == 0)).then(pl.lit("Lost / Inactive"))
        .when(pl.col(CURRENT_R12) > pl.col(PRIOR_R12)).then(pl.lit("Growing"))
        .when(pl.col(CURRENT_R12) < pl.col(PRIOR_R12)).then(pl.lit("Declining"))
        .otherwise(pl.lit("Flat")).alias("Status"),
    ).sort([REP_COLUMN, CURRENT_R12, customer], descending=[False, True, False], nulls_last=True)

    views: dict[str, pl.DataFrame] = dict(zip(VIEW_IDS, (monthly_samples, rolling_samples, rolling_accounts,
                               monthly_suppliers, products, comparisons), strict=True))
    views["workbook_totals"] = _totals(prepared, views, month_keys, current_complete, prior_complete, not missing_sample_months)
    return views


def _totals(
    prepared: PreparedReport, views: Mapping[str, pl.DataFrame], month_keys: tuple[str, ...],
    current_complete: bool, prior_complete: bool, samples_complete: bool,
) -> pl.DataFrame:
    """Calculate all footer values here, including ratios of summed amounts."""
    rows = []
    for rep in prepared.reps:
        for section_id, frame in views.items():
            data = frame.filter(pl.col(REP_COLUMN) == rep)
            numeric = [name for name, dtype in frame.schema.items() if dtype.is_numeric()]
            values: dict[str, float | None] = {}
            for name in numeric:
                column = data[name]
                values[name] = float(column.sum() or 0) if column.null_count() < column.len() or not column.len() else None
            if section_id == "rolling_samples":
                for month in month_keys:
                    if month in missing_months(prepared.samples, prepared.require_period().window(WindowKey.ROLLING_YEAR)):
                        values[month_heading(month)] = None
                if not samples_complete:
                    values[R12_BOTTLES] = None
            if section_id in ("rolling_account_sales", "rolling_product_accounts") and not current_complete:
                values[NET_SALES if section_id == "rolling_account_sales" else BOTTLES] = None
            if section_id == "monthly_supplier_sales":
                numerator, denominator = values[REP_SALES], values[COMPANY_SALES]
                values[REP_SHARE] = 1.0 if numerator else None
                values[COMPANY_SHARE] = numerator / denominator if numerator is not None and denominator else None
            if section_id == "rolling_account_comparison":
                if not current_complete:
                    values[CURRENT_R12] = None
                if not prior_complete:
                    values[PRIOR_R12] = None
                now, before = values[CURRENT_R12], values[PRIOR_R12]
                values[CHANGE] = round(now - before, MONEY_DECIMALS) if now is not None and before is not None else None
                values[GROWTH] = (now - before) / abs(before) if now is not None and before else None
            for measure, value in values.items():
                rows.append({REP_COLUMN: rep, "Section": section_id, "Measure": measure, "Value": value})
    return pl.DataFrame(rows, schema={REP_COLUMN: pl.String, "Section": pl.String, "Measure": pl.String, "Value": pl.Float64})
