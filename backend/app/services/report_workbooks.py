"""Presentation of already-calculated monthly report views (Phase 14).

No library access, source-file access, totals, ratios or grouping lives here.
One shared sheet policy serves every dynamically discovered rep.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

from app.models.artifact import (
    Artifact, MAX_ARTIFACT_FILENAME_BYTES, MAX_ARTIFACT_FILENAME_LENGTH,
    artifact_filename, artifact_ids, check_artifact_filename,
)
from app.models.report_spec import REP_COLUMN, TRANSACTION_COLUMNS, WindowKey
from app.services.monthly_report import PreparedReport, missing_months
from app.services.report_views import (
    BOTTLES, CHANGE, COMPANY_SALES, COMPANY_SHARE, CURRENT_R12, GROWTH,
    NET_SALES, PRIOR_R12, PRODUCT, REP_SALES, REP_SHARE, R12_BOTTLES, VIEW_IDS,
)
from app.services.workbook import CellFormat, Column, ConditionalFormat, ConditionalRule, Sheet, render_workbook


def workbook_names(reps: Sequence[str], period_label: str) -> tuple[str, ...]:
    """Preserve the month and suffix while de-colliding sanitized rep names."""
    used: set[str] = set()
    result = []
    for rep in reps:
        stem = artifact_filename(rep, "")
        counter = 1
        while True:
            suffix = f" - {period_label}{f' ({counter})' if counter > 1 else ''}.xlsx"
            character_room = MAX_ARTIFACT_FILENAME_LENGTH - len(suffix)
            byte_room = MAX_ARTIFACT_FILENAME_BYTES - len(suffix.encode("utf-8"))
            prefix = stem[:character_room].encode("utf-8")[:byte_room].decode("utf-8", "ignore").rstrip(" .") or "Rep"
            candidate = check_artifact_filename(prefix + suffix)
            if candidate.casefold() not in used:
                used.add(candidate.casefold())
                result.append(candidate)
                break
            counter += 1
    return tuple(result)


def _format(name: str, dtype: pl.DataType) -> CellFormat:
    if name in (REP_SHARE, COMPANY_SHARE, GROWTH):
        return CellFormat.PERCENT_DECIMAL
    if name in (NET_SALES, REP_SALES, COMPANY_SALES, PRIOR_R12, CURRENT_R12, CHANGE):
        return CellFormat.CURRENCY
    if dtype.is_numeric():
        # Quantities remain numeric and retain fractional source units.
        return CellFormat.GENERAL
    return CellFormat.TEXT


def report_sheets(
    prepared: PreparedReport, tables: Mapping[str, pl.DataFrame], rep: str,
) -> tuple[Sheet, ...]:
    period = prepared.require_period()
    rolling = period.window(WindowKey.ROLLING_YEAR)
    prior = period.window(WindowKey.PRIOR_ROLLING_YEAR)
    definitions = (
        (VIEW_IDS[0], f"Samples {period.label}", "Samples by supplier and product", period.label),
        (VIEW_IDS[1], "Samples R12", "Samples by supplier, product and month", f"{rolling.start:%b %d, %Y} to {rolling.end:%b %d, %Y}"),
        (VIEW_IDS[2], "Sales R12 by Account", "Net sales by account", f"{rolling.start:%b %d, %Y} to {rolling.end:%b %d, %Y}"),
        (VIEW_IDS[3], f"Sales {period.label}", "Net sales by supplier", period.label),
        (VIEW_IDS[4], "Sales by Product and Account", "Net bottles by product and account", f"{rolling.start:%b %d, %Y} to {rolling.end:%b %d, %Y}"),
        (VIEW_IDS[5], "Sales by Account R12", "Account performance: current versus prior R12", f"Prior: {prior.start:%b %Y} to {prior.end:%b %Y}. Current: {rolling.start:%b %Y} to {rolling.end:%b %Y}."),
    )
    notes = ["Activity follows the salesperson on the invoice. Sales and sample credits use their signed values."]
    for issue in prepared.warnings:
        if issue.code not in {"PROVISIONAL_REPORT_RULES", "SHORT_PLACEMENT_HISTORY", "MISSING_COMPARISON_PERIOD"}:
            notes.append(issue.message)
    result = []
    for view_id, name, title, subtitle in definitions:
        frame = tables[view_id].filter(pl.col(REP_COLUMN) == rep).drop(REP_COLUMN)
        columns = tuple(Column(name=column, format=_format(column, dtype)) for column, dtype in frame.schema.items())
        totals = dict(tables["workbook_totals"].filter(
            (pl.col(REP_COLUMN) == rep) & (pl.col("Section") == view_id)
        ).select("Measure", "Value").iter_rows())
        view_notes = list(notes)
        if view_id == "rolling_samples":
            gaps = missing_months(prepared.samples, rolling)
            if gaps:
                view_notes.append("Sample months not supplied: " + ", ".join(gaps) + ". Missing months and R12 totals are blank.")
        if view_id in {"rolling_account_sales", "rolling_product_accounts", "rolling_account_comparison"}:
            for window in ((rolling, prior) if view_id == "rolling_account_comparison" else (rolling,)):
                gaps = missing_months(prepared.sales, window)
                if gaps:
                    view_notes.append(window.label + " sales months not supplied: " + ", ".join(gaps) + ". Unavailable totals and growth are blank.")
        if view_id == "monthly_supplier_sales":
            view_notes.append("Company supplier amounts and the footer cover the suppliers shown here. The footer percentage is a ratio of totals.")
        if prepared.samples.height == 0 and view_id in {"monthly_samples", "rolling_samples"}:
            view_notes.append("No sample records were supplied. Zero totals describe the supplied rows; they do not establish a complete zero-sample month.")
        if frame.height == 0:
            view_notes.append("No activity in the supplied rows for this section.")
        result.append(Sheet(
            name=name, frame=frame, columns=columns, title=f"{rep} | {title}", subtitle=subtitle,
            notes=tuple(view_notes), total_row=totals, hide_gridlines=True,
            header_height=34, table_style="Table Style Medium 2",
            conditional_formats=tuple(ConditionalFormat(column.name, ConditionalRule.NEGATIVE_RED)
                                      for column in columns if column.format in {CellFormat.CURRENCY, CellFormat.PERCENT_DECIMAL}),
        ))
    return tuple(result)


def render_rep_workbooks(
    prepared: PreparedReport, tables: Mapping[str, pl.DataFrame],
) -> tuple[Artifact, ...]:
    period = prepared.require_period()
    ids = artifact_ids(prepared.reps)
    filenames = workbook_names(prepared.reps, period.label)
    return tuple(Artifact.workbook(
        id=artifact_id, label=f"{rep} - {period.label}", filename=filename,
        payload=render_workbook(report_sheets(prepared, tables, rep)),
    ) for rep, artifact_id, filename in zip(prepared.reps, ids, filenames, strict=True))
