# Monthly Sales Rep Report — Specification

The authoritative calculation and workbook specification for Action
`monthly_sales_rep_report`, version **`0.4.0`**. Phase 14 generates six
worksheets for every applicable rep in one Run, with individual XLSX downloads
and a reporting-month ZIP. Supplementary placement definitions remain
**PARTLY PROVISIONAL**; they are not included in the six accepted worksheets.

Declarations live in
[`backend/app/models/report_spec.py`](../backend/app/models/report_spec.py).
Shared preparation and legacy previews live in `services/monthly_report.py`;
accepted-view calculations and literal footer values live in
`services/report_views.py`. Presentation is isolated in
`services/report_workbooks.py`, the calculation-side `services/report_presentation.py` adapter, and the shared `services/workbook.py` renderer.

## Report contract

The implemented reporting policy uses invoice salesperson for rep performance,
signed credits/returns for net sales, and rolling twelve-month product/account
detail. Account context comes from distinct customer/rep pairs observed in
current-R12 transactions. Account assignment lists are not accepted or read. These choices define software
behavior; private source findings and business acceptance evidence are retained
in a separate private review document.

Worksheet labels state the actual period. Rolling-year summaries are labeled
R12, month keys retain the year, and the account comparison recalculates
both R12 periods directly from the selected sales history.

## Sources and exact replay

| Input slot | Selector for August 2026 | Purpose |
| --- | --- | --- |
| `sales_history` | `history:2026-08` | Committed sales months through August, including signed credit rows. |
| `sample_history` | `history:2026-08` | Separate committed sample months through August, including sample credits. |

The runner resolves the selectors before the Action runs and records every
immutable dataset version under `library_inputs`. The Action receives only
DataFrames. For replay, use `versions:<id>,<id>,...` for each history slot,
naming every recorded version. A history set may contain only one version
per month. Sample inputs must reach the selected sales month. Recorded
ingestion date interpretation is applied in a working copy; stored rows are
not changed.

Column names match exactly, without aliases or fuzzy matching. Sales and
samples require the fifteen transaction headers in
[monthly-source-schemas.md](monthly-source-schemas.md). There is no assignment input.

Accepted transaction types are exact:

| Dataset | Allowed `Invoice Type` values |
| --- | --- |
| Sales | `Invoice`, `Credit Invoice` |
| Samples | `Sample Invoice`, `Sample Credit Invoice` |

Blank, unfamiliar, and wrong-dataset types fail before rendering. A combined
export must be separated explicitly before ingestion; the Action does not
silently discard rows or guess what another document type means. Signed
amounts and quantities are summed as supplied; credits are not negated twice.

Sales in reporting windows require a nonblank `Customer` and `Sales Person`.
Samples require `Sales Person`; their customer field may be blank, when account identity is unavailable. Blank, unreadable, non-finite, or precision-losing
`Quantity` and `Total Price` values fail. Product dimensions and supplier names
preserve exact spelling, accents, and blanks; they are never fuzzy matched.

## Reporting period, roster and history coverage

One reporting period is resolved before calculation: the greatest calendar
month in the selected sales history. The explicit `history:YYYY-MM` endpoint
must exist, so the derived month is the requested month. Filenames never
choose the reporting period.

Every nonblank rep with sales/sample activity in **current R12** receives one
workbook. A rep with only prior-R12 activity does not receive a new workbook.
No external roster is consulted and no warning claims a transaction rep is
unrecognized because of missing assignments. Context account counts use exact
customer/rep pairs observed in current R12; company accounts count distinct
customers across those pairs. A rep can have zero current-month sales while
remaining active during the rolling year.

Every window includes both endpoints:

| Window | August 2026 example |
| --- | --- |
| Reporting month | 2026-08-01 through 2026-08-31 |
| Prior month | 2026-07-01 through 2026-07-31 |
| Same month last year | 2025-08-01 through 2025-08-31 |
| Current YTD | 2026-01-01 through 2026-08-31 |
| Prior YTD | 2025-01-01 through 2025-08-31 |
| Current R12 | 2025-09-01 through 2026-08-31 |
| Prior R12 | 2024-09-01 through 2025-08-31 |

Calendar coverage checks include **interior months**, not just endpoints.
The present input contract cannot distinguish a company-wide zero-activity
month from a month never imported. Conservatively, any missing sales month
makes the affected accepted R12 totals unavailable: blank numeric values,
blank changes/growth and `Incomplete history` status. A missing sample month
is blank in its monthly bucket and makes the sample R12 total blank. Complete
monthly sheets remain usable. Notes name every absent month. A rep with no
activity in a globally supplied month has zero activity, not missing history.

This is month-presence coverage, not proof of complete exports within a month
or complete credits/samples for every rep. The existing twelve supplementary
preview tables may still show sums of available rows; their Data Quality
warning qualifies partial history. They are not the accepted R12 workbooks.

## Exact workbook structure (14A)

All six worksheets are visible, in this order. The monthly sheet names change
with the selected reporting period; the example uses August 2026.

| # | Worksheet | Calculation table | Display columns and grouping |
| --- | --- | --- | --- |
| 1 | `Samples August 2026` | `monthly_samples` | `Row Labels`, `Sample Bottles`; supplier subtotal followed by indented products, both alphabetically ordered. |
| 2 | `Samples R12` | `rolling_samples` | Supplier/product grouped rows; January–December columns and `Grand Total`; suppliers ordered by descending R12 quantity, products alphabetically. |
| 3 | `Sales R12 by Account` | `rolling_account_sales` | `Customer`, `Sales Person`, `$`; current-R12 activity only, descending net sales. |
| 4 | `Sales August 2026` | `monthly_supplier_sales` | `Supplier`, `$ (<rep first name>)`, `% of Rep Sales`, `$ (Company)`, `% of Company Sales`; descending rep sales. |
| 5 | `Sales by Product and Account` | `rolling_product_accounts` | `Row Labels`, `Sum of Bottles`; product subtotals followed by indented customer detail, both alphabetically ordered. |
| 6 | `Sales by Account R12` | `rolling_account_comparison` | `Customer`, `Sales Person`, `Prior R12`, `Current R12`, `$ Change`, `% Change`, `Status`; descending dollar change. |

These display schemas follow the supplied reference. Flat browser/CSV tables
retain their original field names and chronological month headings. The display
adapter calculates grouped literal subtotals from those verified views; the
renderer only formats them. Group and detail rows represent the same quantities,
so consumers must not add both levels. Grand totals come from `workbook_totals`,
never a sum of duplicated display rows.

`Producer | Selection` is this report's combined product display
identity. It deliberately aggregates across SKU/vintage/volume variants sharing
that identity; the supplementary product table retains the full source key.
The current-R12 account lists use transaction-derived context; assignment-only
accounts are absent. Historical comparison detail retains accounts observed in
the comparison windows for applicable reps.
Monthly supplier sheets include suppliers with any monthly lines, including
credit-only and net-zero activity, and exclude historical-only suppliers.

Rows sort deterministically, with descending measures and name tie-breakers.
Samples group within supplier; product/account detail groups within product.
The R12 calculation keys include the year; the workbook maps each to its unique
calendar month and states the exact year-bearing range in the subtitle. No
months from different years are combined. The product/account subtitle describes
R12, correcting the reference's misleading monthly caption.

## Calculations and totals

| Measure | Definition |
| --- | --- |
| Net sales | Sum signed `Total Price` within the window/group, then round to two decimals. |
| Bottles | Sum signed `Quantity`; fractional source quantities stay numeric and are not truncated. |
| Rep supplier mix | Rep supplier net sales / rep total monthly net sales. |
| Share of company supplier | Rep supplier net sales / company net sales for that supplier. |
| Change | Current R12 minus prior R12. |
| Growth | Change / absolute value of prior R12. |

Ratios are numeric fractions with percentage formatting. A zero or unavailable
denominator produces a blank value, never infinity or an invented zero.
`New` means prior zero and current positive; `Lost / Inactive` means prior
positive and current zero. Other complete comparisons are `Growing`,
`Declining`, or `Flat`. A new account has no percentage growth.

Raw currency can include subcent precision. It remains unchanged in the library;
rounding occurs after aggregation, not on individual lines. Money footer values
sum the displayed rounded group amounts, which may differ by a cent from
rounding one company-wide raw sum. Footer percentages are **ratios of footer
amounts**, not sums of row percentages. The supplier footer's company amount
covers **the suppliers shown on that rep's sheet**, following this
report contract. It is not labeled or used as an all-supplier company
total. Each supplier appears once per rep.

All footer values live in the `workbook_totals` calculation table before
rendering. The renderer writes literal values and never formulas, grouping,
ratios, totals or other business calculations. Empty sections retain their
headers, numeric zero footers where history is available, and a no-activity
note. Incomplete R12 footers remain blank even for idle reps.

## Shared formatting, filenames and delivery (14B–14E)

One reference rendering policy applies to every rep: Aptos Narrow 12-point body,
20-point centered bold title, 16-point centered period captions, matching column
widths (within XlsxWriter's pixel rounding), 16-point default row height, visible
gridlines and no frozen panes. Notes remain below the literal grand totals.
Grouped sample/product ranges use green headers and Excel outline levels;
sales tables use Light4 green bands, and account comparisons use Light2 blue
bands. Comparison dollar-change cells show positive/negative/zero colors across
all data rows. Currency retains two decimals; sample quantities retain fractions;
rep mix uses 0.00%, supplier share 0.0%, and growth 0% from numeric fractions.
New-account growth is blank. Rep-mix and growth footer cells are blank to match
the reference; their calculated values remain available in the model.

The grouped ranges provide collapsible finished report rows. They are not native
editable PivotTables or connected Power Query tables. No reference workbook or
proprietary record is embedded in application source or required at runtime.
No hidden raw-data or unrelated challenge worksheets are included.

Each artifact is named `<rep> - <Month Year>.xlsx`; the ZIP is
`<Month Year> Sales Rep Reports.zip`. Names are flat, sanitized and checked for
both character and UTF-8 byte limits. Case-insensitive sanitized collisions
receive stable numbered suffixes while preserving the month and extension.
The shared renderer uses a fixed creation property so identical logical inputs
produce byte-reproducible XLSX artifacts rather than clock-dependent files.

A successful Action Run publishes the entire batch. A rendering failure fails
the Run and exposes no partial workbook set. Individual artifact downloads and
the generic ZIP route remain available alongside nineteen preview/export
tables: the twelve earlier tables, six accepted views and `workbook_totals`.
The public Run manifest schema is unchanged. Runs, result frames and artifact
bytes remain in memory and are lost on backend restart; source versions persist.

## Warnings, provisional definitions and acceptance (14F)

Exact repeated source rows are reported and preserved: equality cannot prove a
legitimate repeated line is an error. Overlapping correction files must be
reconciled before import, not blindly appended. Unexpected source columns,
missing invoice identities and incomplete history remain explicit.

The remaining provisional rules are:

| Rule | Scope and open point |
| --- | --- |
| `placement` | Supplementary previews only: first positive Customer/SKU sale in all supplied history; accepted six-sheet workbooks exclude placements. |
| `placement_history` | Supplementary previews only: twelve preceding months minimum is still an unconfirmed look-back assumption. |
| `sample_period` | Existing behavior fails when nonempty sample history ends before the reporting month; an explicit representation of a genuinely zero company sample month remains unresolved. |

`PROVISIONAL_REPORT_RULES` remains in Data Quality and Run metrics while these
rules remain open; version `0.4.0` deliberately stays below 1.0.0. Passing synthetic
tests verifies the contract; it does not establish completeness or business
acceptance of a production export.

See [phase-14-validation.md](phase-14-validation.md) for automated round-trip
and download checks and the remaining **manual Microsoft Excel for Mac opening
check**. Production source reconciliation and coverage findings remain private.
The monthly workflow accepts single-month and multi-year uploads in the same
form. It checks all source partitions before saving any. Exact unchanged
stored partitions are reused; conflicting values, types or date interpretation
block generation until the user chooses saved data or an explicit single-month
correction. New months after the chosen report month may be saved, but are
excluded from that report’s pinned version set. Receipts survive restart; old
schema-v1 receipts retain their recorded sales/sample sources and explicitly
warn that report Action 0.4.0 differs from their original version.
