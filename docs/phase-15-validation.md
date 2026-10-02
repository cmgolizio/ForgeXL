# Phase 15 — Monthly workflow validation

Date: 2026-10-01. Base: merged Phase 14, `f472266`. The build plan remains
the source of truth. Phase 15 engineering is implemented; production business
acceptance, Excel for Mac opening and a live-browser check remain open.
V1 follow-through on 2026-10-02 adds chunked history, stable two-server startup,
explicit result release and transport guards. Its complete fix/blocker record
and current test evidence are in [v1-finalization.md](v1-finalization.md).

## Delivery against the build plan

| Requirement | Implemented behavior / evidence |
| --- | --- |
| 15A — monthly UI | `/monthly-reports`: reporting month, three source slots, date-format choices, Validate and Generate; linked from the Action workbench. |
| 15B — trust summary | Backend-derived row counts, period/schema/ownership checks, roster, coverage, errors and warnings. Upload review does not commit. Warnings require explicit consent. |
| 15C — logical cycle | Validate → commit exact reviewed sources → save source receipt → existing Action Run → individual XLSX and named ZIP. Saved sources and generated reports are separate states. Partial commit failures identify saved IDs; render failures retain a retryable receipt. |
| 15D — rerun | Saved-source validation and generation require no new files. A consumed validation token cannot generate twice. |
| 15E — old period | Previous cycle uses exact history version sets and its assignment snapshot. Current-version selection is explicit and captured in a new receipt. Action-version changes are disclosed. |
| 15F — production acceptance | Available private sources were checked, but their conflicting snapshot is refused before committing the month. Full company credit/sample coverage and Excel for Mac acceptance remain outstanding. No completed-company acceptance claim. |
| 15G — duplicates / corrections | Repeated upload rejected; correction requires current version and reason, supersedes instead of appending, and preserves the original cycle. Assignment correction and restart replay covered. |
| 15H — performance | Three fresh company-sized synthetic runs measure parsing, validation, commit, historical loading, calculations, XLSX, ZIP and full cycle independently. Results below and in `phase-15-benchmark.json`. |
| 15I — practical use | One-time history panel; recurring upload → validate/review → generate → ZIP. Saved periods and receipt-based retry avoid reuploading. |

## Audit repairs to earlier phases

| Finding | Repair / regression evidence |
| --- | --- |
| Report warnings appeared in Data Quality but were omitted from the Run validation/audit warning summary. | Optional `ActionResult.warnings`, propagated by the generic runner; the monthly Action returns its prepared warnings. Proof Actions retain empty defaults. |
| Multipart failures outside Starlette's `MultiPartException` could leave partial memory buffers open. | Parser closes buffers for validation errors, disconnects and cancellation. Existing limit, duplicate-field, truncated-stream and disconnect cleanup tests pass. |
| Data Library `OSError` details could disclose physical paths to an API caller. | Public errors record exception class instead of OS exception text; forced permission failure regression confirms no path disclosure. |
| Calendar period validation accepted year `0000`. | Refused at the shared period boundary. Monthly workflow also requires enough calendar range for two years of comparisons. |
| Mixed-type Excel fallback metadata was omitted from source-ingestion warnings. | Preflight/history validation exposes `MIXED_COLUMN_TYPES` before saving. |
| Two Phase 6 tests required the entire repository `data/` directory to be absent, conflicting with Phase 9's persistent library. | Proof Run check now verifies existing business files stay unchanged; configuration check tests removed Run-storage constants. |
| Architecture/README still claimed there was no library-backed Action or workflow API. | Documentation now describes the implemented report, confirmed assignment contract and Phase 15 boundary. |

No business formulas or report Action version changed. No existing source
version is migrated or rewritten. Run/result/artifact schemas and their routes
remain compatible; the seven monthly routes are additive.

## HTTP contract

All browser calls use the existing `/forge-api` same-origin proxy. FastAPI
stays on loopback. Multipart bodies use the existing bounded memory parser;
JSON request failures use the structured workbench error envelope.

| Method | Backend route | Input / result |
| --- | --- | --- |
| GET | `/api/monthly/catalog` | Dataset/version metadata, available periods and receipts; no table rows. |
| POST | `/api/monthly/validate` | Multipart `period`, optional three source files, transaction `<dataset>.date_format`, deliberate `<dataset>.replaces` and `reason`; returns review. |
| POST | `/api/monthly/validate-saved` | JSON `period`, optional `cycle_id`; returns exact-cycle or current-source review. |
| POST | `/api/monthly/generate` | JSON `validation_id`, `acknowledge_warnings`; returns commit/generation state, receipt and successful Run manifest. |
| POST | `/api/monthly/discard` | JSON `validation_id`; releases that pending monthly or history upload review. |
| POST | `/api/monthly/history/validate` | Multipart `dataset_id`, `source_file`, optional `date_format`, explicit `skip_existing=true`/`false`; sales/sample history only. |
| POST | `/api/monthly/history/commit` | JSON review token and warning consent; committed IDs or explicit partial failure. |
| POST | `/api/runs/{run_id}/discard` | Release finished Run tables/downloads only; saved source versions and cycle receipts remain. |

One pending review per workflow service supports this local, single-user scope.
Tokens expire after 15 minutes and are single-use; new review replaces the old
one. Wrong/obsolete tokens do not destroy a newer review. Restarting loses
pending review data and Runs, while committed sources and receipts persist.
Historical chunks save selected whole months individually. Overlapping stored
months block validation by default. Explicit missing-month selection returns
`skipped_periods` and `imported_row_count`, adds a warning requiring consent,
and never changes stored months. After a partial save, `committed_periods`
identifies exactly what succeeded; revalidate the same file with missing-month
selection to resume safely. All-covered files cannot create empty imports.
Schema/date errors are not bypassed by skipping. The workflow does not promise
rollback or infer completeness of a month from its presence.

## Automated verification

`backend/tests/test_monthly_workflow.py` covers the real registered Action through
HTTP: independent golden sales/credit controls, three workbooks/eighteen sheets,
ZIP membership, warning consent, saved-source replay, supersession, duplicate
refusal, date interpretation, ownership errors, stale/expired/discarded tokens,
Action-version warnings, corrupted receipt identity, render failure, partial
commit/receipt failure, and sanitized filesystem errors.

`tests/frontend/monthly.test.mjs` uses React with a DOM model for nine interaction
stories: upload and review/consent, saved-cycle choice, failed render retry,
explicit correction/discard, history setup, failed revalidation, overlap
review/consent, history/monthly busy coordination and preview retry. The generated
result story also checks explicit release. These tests do not establish real
browser layout, native file chooser or download
behavior. `esbuild` and `happy-dom` are development-only dependencies.

The separate production HTTP harness starts actual Next.js and FastAPI against
an isolated temporary library. It verifies page/navigation responses, multipart
history and monthly requests through the streaming proxy, JSON generation,
ZIP response headers/content, golden company revenue, and byte-identical
workbook replay after restarting FastAPI. Three workbooks/eighteen sheets reopen
with openpyxl. V1 follow-through extends it with proof-Action CSV/XLSX download
controls, origin checks, chunk overlap review and exact replay from a restored
full-library backup in a separate directory. Only synthetic inputs enter it.

Commands:

```bash
npm test
npx --no-install pyright
npm run lint
npm run build
npm run benchmark:monthly
cd backend && .venv/bin/python -m tests.benchmarks.workflow_http
```

Final command outcomes are recorded in `implementation-status.md`. The one
backend warning is the existing upstream Starlette/httpx deprecation.

## Repeated company-size performance

Synthetic CSV input: 24 history months, 72,000 signed sales rows, 6,000 signed
sample rows, 900 accounts, 1,500 distinct products, 50 suppliers and 15 reps. Each repetition starts fresh; history
bootstrap is timed separately. The recurring cycle uploads 606,559 bytes and
produces 15 workbooks in an 850,099-byte ZIP. These are local Linux measurements,
not a Mac/LAN timing promise. Source construction is outside the measured cycle.

| Stage | Median ms | Min–max ms |
| --- | ---: | ---: |
| Parsing monthly files | 3.54 | 3.36–4.07 |
| Source/report validation | 238.99 | 237.62–248.35 |
| Historical loading | 119.29 | 118.96–121.57 |
| Persistent source/receipt commit | 14.18 | 13.02–14.38 |
| Report table calculations | 617.90 | 611.93–618.23 |
| XLSX generation | 1,455.14 | 1,446.60–1,485.46 |
| ZIP generation | 26.20 | 25.66–27.76 |
| Full validate/save/generate/ZIP cycle | 2,626.06 | 2,610.67–2,646.98 |
| One-time historical setup | 370.84 | 352.68–374.07 |

Stage wrappers measure real services without double-counting parsing inside
source validation. Validation includes report preparation in preflight and the
Run; loading includes both. Full HTTP total also includes orchestration, lossless
merging, date interpretation and metadata. No timing threshold is asserted by
tests. Missing source completeness cannot be inferred from benchmark speed.

## Remaining acceptance work

Private-source checks repeated the earlier 16,355 independent grouped-value
comparisons successfully. The new workflow imported reviewed historical
partitions in an isolated temporary store, then rejected the supplied monthly
snapshot with `AMBIGUOUS_ACCOUNT_OWNERSHIP`; the new month was not committed.
The snapshot must be corrected by a business owner. Other-rep/all-period credit
and sample coverage remains incomplete. Real company records and private
findings were not committed to this repository.

Complete 15F after receiving complete company sources and the corrected
snapshot: run a real cycle through the UI, download/extract the ZIP, independently
spot-check rep/company/supplier/sample/placement previews, restart, rerun, and
compare the resulting values. Open representative outputs in Microsoft Excel
for Mac and verify no repair prompt, six readable tabs, numeric amounts/ratios,
filters and frozen headers. Existing provisional placement and true-zero sample
month policies remain explicitly qualified.

The original Phase 15 live-browser verification was blocked: local Chrome failed
its required socket operation (`Operation not permitted`); the cloud browser
refuses the loopback URL (`ERR_BLOCKED_BY_CLIENT`). No visual browser acceptance
or Mac Excel acceptance is marked passed. These checks need a normal browser
and Excel on the operating machine; no public deployment was made.
The fresh 2026-10-02 retry and current verification evidence are documented in
[v1-finalization.md](v1-finalization.md); it also remains unverified.
