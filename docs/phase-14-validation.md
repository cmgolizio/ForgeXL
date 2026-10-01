# Phase 14 validation — 2026-10-01

Phase 14A–14E are implemented and 14F's automated checks pass. The remaining
manual Microsoft Excel for Mac opening check is **not performed**: this
execution environment is Linux. Full-company completed-month net-sales/sample
acceptance also remains incomplete until complete inputs and their business acceptance are verified. No acceptance is inferred from
passing synthetic tests or from month-presence coverage.

## Accepted scope and implementation

The report contract uses six sections, invoice salesperson performance, signed
credits, and R12 product/account detail. The final authoritative worksheet
names and calculations are in
[monthly-sales-rep-report-spec.md](monthly-sales-rep-report-spec.md).

| Subphase | Evidence |
| --- | --- |
| 14A — structure | Six accepted worksheets are specified, with accurate R12 labels and year-bearing monthly buckets. |
| 14B — formatting | Shared Sheet/Column renderer, literal numeric values, period/coverage notes, widths, filters, frozen headers and precomputed footers. |
| 14C — each rep | Snapshot roster plus invoice/sample reps active in current R12; idle snapshot reps remain included. Safe names retain rep and month. |
| 14D — one batch | One Action returns every workbook. A forced failure in the second renderer publishes no partial batch. |
| 14E — ZIP | A real HTTP Run against committed fixtures downloads three individual workbooks and one `August 2026 Sales Rep Reports.zip` containing their exact bytes. |
| 14F — automated | Every data/footer cell in all six sheets for three synthetic reps is reopened and compared, with numeric types preserved. |
| 14F — Excel for Mac | Pending manual opening. Review copies are supplied separately; no Microsoft Excel check is claimed. |

Phase 15's dedicated monthly reporting UI and production workflow remain
outside this change. The registered Action is reachable through the existing
Run API/in-process selectors; generic downloads need no per-rep frontend logic.

## Production evidence

Real-data reconciliation and source coverage findings are retained in a private
review document. Public verification below uses synthetic identities, prices
and hand-worked controls. A successful calculation does not prove completeness of
an export, and calendar month presence alone cannot prove all transactions or
credits were included.

## Synthetic controls published with the code

`tests/fixtures/completed_report_month.py` reproduces the relevant scenarios
with synthetic names and prices. Twenty-four monthly 1.25 invoices establish
both R12 windows. Current August adds Alpha's 47.50 invoice minus a 4.75 credit
and Beta's 14.25 invoice on an account currently assigned to Alpha. Twelve
monthly samples plus a sample credit exercise both sample invoice types.

| Control | Expected value |
| --- | --- |
| Company August net sales | 58.25 |
| Alpha August net sales | 44.00 |
| Alpha current/prior R12 | 57.75 / 15.00 |
| Alpha R12 change/growth | 42.75 / 2.85 |
| Beta current-R12 sales on transferred account | 14.25, retained with Beta |
| Supplier Two, Alpha / company net sales | 42.75 / 57.00 |
| Alpha share of company Supplier Two sales | 0.75 |
| Alpha supplier-sheet footer share | 44.00 / 58.25 |
| Beta company denominator on suppliers shown | 57.00, not all-company 58.25 |
| Alpha August / R12 sample bottles | 0 / 11 |
| Idle rep | Six sheets, retained headers and safe zero/blank footer values. |

The tests also cover calendar year boundaries, sample exports with blank
customers, optional assignment totals that cannot overwrite invoice totals,
wrong/blank invoice types, missing interior R12 months, zero denominators,
historical and sample duplicate warnings without dropping rows, safe Unicode
filename truncation/collisions, formula-looking source text, note capacity
limits, deterministic XLSX bytes, and unchanged input DataFrames. Existing
Phases 0–13 regressions remain in the full suite.

## Final automated verification

- `npm test`: **2,252 passed**, no failures, skips or xfails; one upstream
  Starlette/httpx deprecation warning, 54.54 seconds.
- `npx --no-install pyright`: **0 errors, 0 warnings**.
- `npm run lint`: passed.
- `npm run build`: passed (Next.js 16.3.2 production build).

## Manual review and production-data follow-up

The synthetic `Rep-Alpha-August-2026.xlsx` review copy uses the hand-worked
controls above. Private-source validation copies and their coverage limits
are retained separately.

Open the synthetic file in Microsoft Excel for Mac and check that Excel opens
it without a repair prompt, all six tabs appear, period/coverage labels and
headers are readable, filtering/frozen headers work, and representative
currency and percent values remain numbers. Compare the synthetic controls
above. Record display or repair issues before marking 14F's manual check done.

Before a production completed-month sign-off, verify complete credit and sample
history for all required periods/reps and a valid ownership snapshot. The
engine cannot infer omitted records from an export containing some transactions
in every month. Zero-import month representation and placement-preview
definitions remain documented open points; placement sheets are not included
in this workbook batch.
