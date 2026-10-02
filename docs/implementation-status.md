# Implementation Status

Last updated: 2026-10-02. Latest engineering phase: **Phase 15 — Production
Monthly Workflow**. The workflow and automated checks are implemented.
**Production business acceptance, live-browser verification and manual Excel
for Mac opening remain outstanding.** These are not marked completed.
V1 follow-through completed stable two-server startup, multi-chunk history,
explicit overlap/partial-save recovery, preview retry, origin write guards and
user-controlled result-memory release. See [v1-finalization.md](v1-finalization.md)
for the full fix/blocker inventory, operating checklist and provisional evaluation.

## Current behavior

Open `/monthly-reports` from the home page. Initial setup validates company
sales/sample history and saves monthly partitions. The recurring cycle chooses
a month, reviews sales, samples and an assignment snapshot, then generates all
rep reports. Saved sources can be reused without uploading. Corrections name
the current immutable version and a reason; duplicate uploads are refused.
History may arrive in several complete-month chunks. Stored-month overlap
blocks by default; explicit missing-month selection shows exact skipped months
and requires warning consent. Partial saves identify month/version pairs for
safe same-file resumption. No history row merging or implicit replacement.

Validation shows backend-derived row counts, period/schema/ownership checks,
rep roster, errors, warnings and missing history. Warnings need explicit
consent. A review expires after 15 minutes and can generate only once. Changes
in live sources or the installed Action require revalidation.

Generation commits reviewed inputs, saves a durable source-selection receipt,
then executes `monthly_sales_rep_report` version `0.2.0`. A partial source-save
failure reports committed IDs. Workbook failure keeps the valid sources and
receipt for retry. The UI distinguishes source saving from report generation.

A saved cycle can be rerun with its exact historical sales/sample versions and
assignment snapshot, including after corrections or restart. Choosing current
versions is explicit and records a new cycle. Action-version changes are
qualified because a receipt preserves sources, not executable code. Source
versions and receipts persist; Runs, previews and artifact bytes stay in memory.
Both result screens can explicitly release a finished Run's in-memory results
without removing sources or receipts. There is no automatic eviction.

One successful report Run returns nineteen calculation/preview tables and a
six-sheet workbook for every applicable rep, plus a period-named ZIP. Performance
follows invoice salesperson. Signed sales/sample credits stay in their separate
datasets. Snapshot ownership supplies roster/account context without moving
historical performance. Idle snapshot reps remain included.

The six sheets cover monthly samples, R12 samples, R12 account sales, monthly
supplier sales and ratios, R12 product/account quantities, and current/prior R12
account comparisons. Missing R12 calendar months blank unavailable totals and
growth. Calendar coverage does not prove that all transactions were supplied.
Workbook presentation remains shared and separate from calculations.

## Phase progression and audit repairs

| Phase | Implemented foundation |
| --- | --- |
| 0–8 | Local workbench, reusable Actions, in-memory Runs, preview/export/audit, streaming transport and failure checks. |
| 9 | Persistent Data Library with immutable history/snapshot versions. |
| 10 | Source schemas, date interpretation, monthly ingestion and initial history partitioning. |
| 11 | Library-backed Action inputs and exact source-version provenance. |
| 12 | Optional artifacts, shared workbook rendering, safe names and ZIP downloads. |
| 13 | Report preparation, dynamic roster, company/rep calculations and supporting tables. |
| 14 | Six workbook views, literal footer values, complete rep batch and period-named ZIP. |
| 15 | Dedicated monthly UI/API, preflight trust summary, deliberate corrections, saved-source reruns, durable receipts and retry recovery. Production/manual acceptance remains open. |

This audit additionally repairs omitted report warnings in Run summaries,
partial-upload buffer cleanup, filesystem-path disclosure in library errors,
year-zero periods, omitted mixed-Excel-type ingestion warnings, stale tests
requiring no persistent library, and outdated architecture/README statements.
Earlier precision, attribution, history merging, period matching, partial import,
worksheet capacity and filename regressions remain covered.

The two proof Actions retain their original behavior. New result warnings have
an empty default. Generic Run/result/artifact schemas and routes remain
compatible; seven workflow routes and a finished-Run discard route are additive. Business formulas and report
Action version did not change. No stored source version is migrated in place.

## Verification

- Clean locked setup followed by `npm run verify:v1`: **2,302 backend tests,
  9 frontend DOM interaction tests and 5 startup/transport tests passed**,
  no failures/skips/xfails. One existing upstream Starlette/httpx deprecation
  warning. The environment's npm proxy-setting warning and locked development
  ESLint 9 support deprecation are disclosed in the completion record.
- Pyright: **0 errors, 0 warnings**.
- ESLint and the Next.js production build: passed.
- Actual production Next.js proxy → FastAPI → isolated disk → XLSX/ZIP harness:
  passed, including both proof Actions with CSV/XLSX inputs/downloads, history
  chunks/overlap consent, cross-origin denial, valid same-origin browser writes,
  golden company revenue, three workbooks/eighteen sheets, result release,
  disconnected-backend 502 and exact workbook replay after restarting FastAPI
  from a complete library backup restored in a different directory.
- Actual `npm start`: both servers ready, page/catalog correct, duplicate launch
  refused without stopping its owner, shutdown releases both ports, no business
  data written on startup. `npm run doctor` and `pip check`: passed.
- Correction, duplicate refusal, original-cycle replay, token expiry/discard,
  warning consent, explicit date interpretation, partial commits, receipt-write
  failures and render-failure retry have HTTP regressions.
- Company-size synthetic benchmark: 24 months, 72,000 sales rows, 6,000 sample
  rows, 900 accounts, 1,500 products, 50 suppliers and 15 rep workbooks.
  Three fresh follow-through runs: **2.78 seconds median** for validate/save/
  generate/ZIP, range **2.61–3.09 seconds**. Stage details in `v1-benchmark.json`;
  original Phase 15 measurements remain in `phase-15-benchmark.json`.
- Workbench benchmark: five repetitions per size/format. At 100k rows, median
  whole Run 11.6 ms CSV / 670.8 ms XLSX; full-size XLSX export separately
  2.85 / 2.75 seconds. Preview paging and result release verified. These Linux
  measurements are not target Mac/LAN promises.
- Prior Phase 15 private-source reconciliation repeated **16,355 independent grouped
  value comparisons** successfully. That workflow validation refused the
  conflicting ownership snapshot before committing the reporting month.
  Private files were unavailable in this fresh checkout; those comparisons were
  not repeated for this follow-through and do not establish company acceptance.

## Outstanding acceptance

The supplied validation sources still lack complete company credit/sample
coverage and contain an ownership conflict requiring a corrected snapshot.
Complete Phase 15F with complete sources, independent production spot-checks,
and Microsoft Excel for Mac opening. No Excel repair/display check is claimed
from programmatic reopening.

Live-browser verification is also pending. Prior-session local socket/cloud
loopback failures are recorded in Phase 15 evidence. In this fresh session,
automation's browser daemon exited during startup and an official Chrome build
exited 139 even on a direct blank-page launch. DOM interactions and the production HTTP proxy were verified,
but native browser layout, file chooser and downloads need a normal browser.

Placement previews and the true-zero sample-month policy retain provisional
warnings. Placements remain outside the six workbook sheets. No new phase was
invented beyond the build plan's Phase 15.

See [Phase 15 validation](phase-15-validation.md),
[V1 completion record](v1-finalization.md),
[report specification](monthly-sales-rep-report-spec.md),
[source schemas](monthly-source-schemas.md), and
[architecture](architecture.md). Historical records remain in Git history.
