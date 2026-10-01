# Implementation Status

Last updated: 2026-10-01. Latest implemented phase: **Phase 14 — Batch Sales
Rep Workbook Generation**. Automated checks pass. Manual Excel for Mac opening
and production completed-month business acceptance remain outstanding.
**Stop after Phase 14; Phase 15 is not implemented.**

## Current behavior

Action `monthly_sales_rep_report`, version `0.2.0`, receives three resolved
DataFrames from the persistent Data Library. One successful Run returns
nineteen calculation/preview tables and a six-sheet workbook for every
applicable rep. Individual downloads and a reporting-month ZIP use the existing
generic artifact routes.

Performance follows invoice salesperson. Net sales and samples use signed
credits in separate datasets. Snapshot ownership supplies roster/account
context without moving historical performance. The roster includes snapshot
reps and current-R12 sales/sample activity; idle snapshot reps remain included.

The six sections cover monthly samples, R12 samples, R12 account sales,
monthly supplier sales and ratios, R12 product/account quantities, and current
versus prior R12 account comparisons. Missing interior R12 months blank the
affected totals/growth and produce coverage notes. Footer percentages are
ratios of footer amounts. Source values remain unchanged.

Workbook presentation is shared across reps and separate from calculations.
Filenames retain the rep and month while resolving sanitization collisions and
character/UTF-8 byte limits. A fixed XLSX creation property makes rendering
byte-reproducible. Any rendering failure fails the entire Run without exposing
a partial artifact batch. Runs and artifact bytes remain process memory;
immutable source versions remain persistent.

## Phase progression and audit repairs

| Phase | Implemented foundation |
| --- | --- |
| 0–8 | Local browser workbench, reusable Actions, in-memory Runs, preview/export/audit, transport and failure checks. |
| 9 | Persistent Data Library with immutable dataset versions and monthly snapshot/history semantics. |
| 10 | Exact source schemas, date interpretation, validation, monthly ingestion and history bootstrap. |
| 11 | Library-backed Action inputs and per-version Run provenance. |
| 12 | Optional artifacts, shared workbook rendering, safe names, individual and ZIP downloads. |
| 13 | Shared report preparation, dynamic roster, company/rep calculations and supporting tables. |
| 14 | Six accepted workbook views, literal footer values, every rep artifact in one Run and month-specific ZIP delivery. |

The preceding audit repairs null-key aggregation joins, unsafe/non-finite
measures, precision-losing history coercion, internal-column collisions,
required-column/hook ordering, mismatched input periods, merged snapshots,
exact immutable-version replay, preserved date interpretation and CSV text
identifiers, partial-import failure reporting, and worksheet/filename capacity
limits. Those regressions remain in the full test suite.

The two proof Actions retain their original behavior. Runtime bundle naming and
source-policy additions have inactive defaults for existing callers. The public
manifest schema version remains unchanged. No source version is migrated in
place; deliberate replacements preserve the prior immutable version.

## Verification

- Full suite: **2,252 passed**, no failures/skips/xfails, one upstream
  Starlette/httpx deprecation warning (54.54 seconds).
- Pyright: **0 errors, 0 warnings**.
- ESLint and the Next.js production build: passed.
- XLSX round trips check every data/footer cell of the six sheets for three
  synthetic reps, numeric money/percentages, frozen headers, filters and names.
- HTTP Run checks verify every individual artifact and the exact ZIP bytes.
- Reversal/replay, safe filenames, incomplete history, signed credits,
  transferred accounts, zero activity, duplicate preservation and a forced
  second-workbook failure are covered by independent synthetic controls.

Production-source reconciliation findings and review copies are retained
privately. Public fixtures contain synthetic identities and amounts.

## Open checks and next phase

Manual Microsoft Excel for Mac opening is pending; programmatic reopening is
not recorded as a Microsoft Excel check. Production source completeness and
completed-month business acceptance must be verified separately.

Placement previews and the true-zero sample-month policy remain provisional.
They retain Data Quality warnings; placements are excluded from the six-sheet
workbooks. Phase 15's dedicated monthly reporting/ingestion UI and production
workflow remain future work. Current reports use explicit Run API/in-process
library selectors.

The exact contract is in [monthly-sales-rep-report-spec.md](monthly-sales-rep-report-spec.md),
source requirements in [monthly-source-schemas.md](monthly-source-schemas.md),
automated acceptance in [phase-14-validation.md](phase-14-validation.md), and
architecture in [architecture.md](architecture.md). Historical phase records
remain in Git history; this file is the concise current implementation state.
