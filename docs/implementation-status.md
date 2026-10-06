# CSV tools completed — 2026-10-06

Implemented discovered **Combine CSV files** and **Filter a CSV** workflows in
the existing application. Ordered multi-file append, exact all-field keep-first
deduplication, optional subsequent filters, filter-only duplicate preservation,
strict string-field parsing, validated typed comparisons, preview/full CSV
exports, zero-match downloads, provenance/options audits, bounded prepared
sessions, reordering without reupload/parse, retries, stale-result invalidation,
and cleanup are integrated. No monthly schema/restriction or persistent-library
write is part of these operations. See [csv-tools.md](csv-tools.md) for operation,
contracts, defaults, precise blank/date rules, and configuration limits.

## CSV implementation verification

`npm run verify:v1` **passed** on this working tree:

- **2,406 backend tests** (100 added), **32 frontend DOM interaction tests**
  (15 added), and **5 startup/transport helper tests**, without skips/failures.
- Pyright **0 errors / 0 warnings**, ESLint, and Next.js production build.
- Actual production Next.js streaming proxy → FastAPI → CSV inspection,
  retained-file reordering, configured Runs, paginated preview, complete download
  beyond 100 rows, preserved textual values, header-only zero-match download,
  duplicate filenames, malformed-option refusal and explicit cleanup.
- Existing proof CSV/XLSX actions; monthly golden company control **995**,
  two rep workbooks/twelve worksheets, signed-credit/attribution/R12 regressions,
  history/consent/conflict behavior, result release, exact restart/backup replay,
  write-origin protection and disconnected-backend 502 checks.
- Production startup starts both servers, refuses duplicate launch without
  stopping its owner, releases ports on shutdown, and writes **0** business-data
  files at startup. All business-shaped fixtures use isolated temporary stores.

The available remote browser refused the local CSV page with
**`net::ERR_BLOCKED_BY_CLIENT`**. A real browser walkthrough could not be performed;
DOM and real HTTP transport checks are distinct and neither is described as
native layout/file-picker/download verification. A normal browser on the host
Mac should still be used for native acceptance. No deployment or merge was
performed, and no persistent company data was uploaded or modified.

The pre-existing Starlette/httpx deprecation and environment npm proxy warning
remain. No dependency changes, database, authentication, queue or cloud service
were introduced. No functional CSV implementation item remains open; native
browser acceptance is the outstanding verification limitation. Existing monthly
business/Excel acceptance gates below remain independent of this feature.

---

# Implementation Status

Last updated: 2026-10-02. Phase 15's monthly workflow now includes the user's
guided-workflow corrections. The current requirements at the top of
[build-plan.md](build-plan.md) supersede earlier assignment and single-month
screen requirements. See [usability-fixes.md](usability-fixes.md) for this change.
Production business acceptance, live-browser layout verification and manual
Excel for Mac opening remain outstanding.

## Current behavior

The home screen presents action cards. Every action follows choose action →
upload required files → a prominent Generate report button. Selecting an action
shows its file controls and a Change action control. Results emphasize downloads;
table previews, audit details and cleanup are expandable.

Monthly reports accept only sales history and sample history. Account assignment
uploads are not required or offered. Invoice Sales Person determines performance
and the roster of reps active in current R12. Account context comes from distinct
customer/rep pairs in that same window. Snapshot-only reps and accounts do not
affect reports. Each active rep receives a six-sheet workbook; the batch is
available as a period-named ZIP.

The same upload controls accept one month or multiple years. The selected report
month bounds report calculations. Every supplied month is validated, including
months later than that cutoff, before anything is saved. New complete months are
partitioned and saved; equivalent stored months are reused. Conflicting history
blocks all writes. A correction requires a single-month replacement naming the
current immutable version and a reason. No history row merging or implicit
replacement occurs. Partial saves identify month/version pairs for safe same-file
resumption.

Generate reports performs validation and then generation without a separate
review-button step. Errors pause for correction. Warnings require explicit
acknowledgment before that same button continues. Reviewed selections expire
after 15 minutes, generate once, and are invalidated by changed inputs, saved
versions or the installed Action.

Generation commits reviewed inputs, saves a durable two-source receipt, then
executes `monthly_sales_rep_report` version `0.3.0`. Workbook failure retains
saved sources and the receipt for retry. Existing schema-v1 three-source receipts
remain readable: only their exact sales/sample versions are loaded, with an
Action-version-change warning. Assignment snapshots are not loaded. No source
version is migrated in place.

Saved cycles replay exact historical sales/sample versions, including after
corrections or restart. Current corrected versions are an explicit alternative
that records a new cycle. Receipts preserve source selections, not executable
code. Sources and receipts persist; Runs, previews and artifact bytes remain in
memory and can be explicitly released without removing saved inputs.

The six sheets cover monthly samples, R12 samples, R12 account sales, monthly
supplier sales and ratios, R12 product/account quantities, and current/prior R12
account comparisons. Signed credits, revenue and quantity formulas, exact
identities, missing-history blanks and shared workbook presentation are retained.
Calendar coverage does not prove that all company transactions were supplied.

## Phase progression

| Phase | Implemented foundation |
| --- | --- |
| 0–8 | Local workbench, reusable Actions, in-memory Runs, preview/export/audit and streaming transport. |
| 9–11 | Persistent immutable sources, date/schema validation and exact source-version provenance. |
| 12–14 | Shared rendering, artifact downloads, report calculations and six workbook views. |
| 15 | Monthly workflow, receipts, deliberate corrections, warning consent and retry recovery. |
| Phase 15 corrections | Two-source reports, direct multi-year uploads and guided UI for all actions. |

The two proof Actions retain their processing behavior. Action metadata now has
an optional `workflow_path`, allowing dedicated workflows to be discovered by
the generic frontend. New ordinary Actions still render forms from their input
metadata. The Next.js/FastAPI/Polars architecture and local operation are retained.

## Verification

- **2,306 backend tests and 17 frontend DOM interaction tests passed**, with no
  skips or failures. Five startup/transport tests also passed.
- Pyright: **0 errors, 0 warnings**. ESLint and the production build passed.
- Multi-year regressions cover 36-month import/generation, exact replay, unchanged
  overlap without double counting, conflict refusal, report cutoff, invalid later
  months, removal of assignment inputs and legacy receipt compatibility.
- Actual production Next.js proxy → FastAPI → isolated disk → XLSX/ZIP checks
  passed: both proof Actions, same-origin write guards, golden company revenue
  `995`, two active rep workbooks/twelve sheets, result release, backend disconnect,
  and exact workbook replay after backup restoration and restart.
- Actual `npm start` passed: both servers ready, duplicate launch refused without
  stopping its owner, shutdown releases ports, no business data written at startup.
- Correction, token expiry/discard, warning consent, explicit date interpretation,
  partial commits, receipt-write failures and render-failure retry remain covered.
- Company-size synthetic benchmark: 24 months, 72,000 sales rows, 6,000 sample
  rows, 900 accounts, 1,500 products, 50 suppliers and 15 rep workbooks. Current
  repeated measurements are recorded in `usability-benchmark.json`. Linux timings
  are not target Mac/LAN promises. Earlier benchmark files remain historical.

One upstream Starlette/httpx deprecation warning and the environment's npm
proxy-setting warning remain. No dependency changes were needed.

## Outstanding acceptance

Native browser verification could not run here: browser automation's daemon
failed startup and a direct official Chrome launch was blocked by the runtime's
socket restrictions. DOM interactions and production HTTP were checked; native
browser layout, file chooser and download interaction require a normal browser.

Complete company sales/credit/sample coverage, independent production
spot-checks and Microsoft Excel for Mac opening remain acceptance work. Prior
private-source comparisons were not repeated in this checkout. An assignment
snapshot is no longer an input or acceptance prerequisite. Programmatic workbook
reopening does not establish an Excel repair/display check.

Placement previews and the true-zero sample-month policy retain provisional
warnings. Placements remain outside the six workbook sheets. Historical Phase 15
and V1 completion records describe their original versions; current behavior is
defined by the updated build plan, report specification and this status.
