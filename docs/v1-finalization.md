# V1 completion record and acceptance gates

Date: 2026-10-02. Scope: the existing build plan through Phase 15, including
remaining practical-use and POC evaluation work. This follows the Phase 0–14
audit and Phase 15 implementation on `codex/phase-15-monthly-workflow` / draft
PR #4. No new phase, cloud deployment, database, queue, authentication system
or native desktop packaging was introduced. Do not treat engineering delivery
as completed company-data or Mac acceptance.

## What was completed or fixed

| Item | Delivered change and evidence |
| --- | --- |
| Phase 15 monthly workflow | Dedicated `/monthly-reports` UI and seven additive workflow API routes; reviewed source saving, reporting, explicit corrections, exact-version saved cycles and retry after workbook failure. Full details in `phase-15-validation.md`. |
| Historical files supplied in several chunks | History review now accepts multiple complete months even after the first import. A second three-month export no longer fails merely because it contains multiple months. Each selected month remains a separate immutable version. |
| Overlap and partial-save recovery | Default refusal names stored months. Explicit **Import missing months only** shows skipped months and imported rows and requires warning consent. It skips whole months, never merges rows or implicitly replaces data. `committed_periods` identifies partial saves, so the same original file can resume safely. Empty/all-covered imports, invalid dates, stale and expired reviews have regressions. |
| Competing UI submissions | History operations disable monthly controls and reporting-period/mode changes. Existing in-flight refs prevent double clicks between renders. Review changes invalidate permission to generate. |
| Production startup previously started only Next.js | `npm start` now supervises FastAPI and Next.js, checks dependencies/build/ports, waits for proxy health, disables development reload, and stops owned children on signal or child failure. Occupied ports are refused without killing other servers. Both defaults remain loopback. |
| Practical Mac entry point | Executable `ForgeXL.command` invokes `npm run start:open` after one-time setup. Mac browser opening happens only after readiness. This is a small shell launcher, not a signed/native application; Finder verification is still pending. |
| Setup diagnosis and reproducibility | Read-only `npm run doctor`, pinned Python transitive dependency lock, pinned Pyright in the npm lock, and `npm run verify:v1` for the full repeatable automated verification. Runtime/dependency imports do not create or inspect the business library. |
| Cross-origin browser writes | Next.js checks browser Origin/Host and fetch metadata before forwarding a POST. FastAPI independently guards direct browser writes. Legitimate same-origin requests still work at custom web ports; origin-less local CLI use remains supported. This is not authentication or public-deployment hardening. |
| IPv6 loopback configuration | Backend-origin construction brackets `::1` correctly. Production preflight refuses an explicit upstream origin that differs from the backend binding. No network exposure is widened. |
| Preview recovery and UI errors | Retry a transient preview failure without regenerating reports; an application error boundary offers reset/navigation without printing raw errors or local paths. Accessible warning-consent labels keep separate history/correction checkboxes unambiguous. |
| In-memory results could not be released from the UI | Both result screens offer **Release preview and downloads** via `POST /api/runs/{run_id}/discard`. Running Actions cannot be discarded. Finished result frames/artifact bytes are released; persistent versions and receipts remain untouched and saved reports regenerate afterwards. No automatic eviction was added. |
| Earlier-phase report warnings | Optional Action result warnings propagate into Run validation and audit summaries instead of appearing only in Data Quality. Existing proof Actions retain empty defaults. |
| Earlier-phase upload cleanup | Multipart parser closes partially received buffers on validation errors, disconnects and cancellation. Upload size checks remain before disk spooling. |
| Earlier-phase error disclosure | Data Library filesystem failures expose exception classes instead of physical paths. Structured failures and no-traceback behavior remain covered. |
| Earlier-phase period/type issues | Year `0000` is rejected; monthly comparisons require enough calendar range. Mixed-type Excel fallback warnings reach ingestion review instead of disappearing. |
| Stale architecture/tests/docs | Updated route-freeze inventory for the additive cleanup route, loopback tests for the supervised launcher, and prior tests that conflicted with the persistent library. Corrected comments claiming snapshot ownership supplies invoice performance; README no longer claims monthly ingestion has no UI. Updated operating, backup/restore and acceptance instructions. |

Business arithmetic, invoice attribution, signed-credit handling, the six
workbook sections, nineteen output tables and report Action `0.2.0` are unchanged.
No stored source version was migrated, rewritten or deleted. Generic Run,
result and artifact response shapes remain compatible. The two proof Actions
still select/remove exact duplicate combinations without normalization.

## Verification evidence

`npm run verify:v1` passed: **2,302 backend tests, 9 frontend DOM stories and
5 startup/transport tests**, no failures, skips or xfails; Pyright 0 errors /
0 warnings, ESLint and the Next.js production build passed. Production HTTP and
actual startup/shutdown harnesses passed. `npm run doctor` and `pip check` passed.
One upstream Starlette/httpx deprecation and the environment's npm `http-proxy`
configuration warning are disclosed; neither is hidden by filtering.
The clean npm install also warns that the locked development-only ESLint 9
release is no longer supported. Upgrading its major and supported Node baseline
is follow-up tooling maintenance, not a runtime fix or a claimed security audit.
Commands run from the repository root unless stated otherwise:

```bash
npm ci
backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
npm run verify:v1
npm run benchmark:monthly
# Separate workbench parsing/export/preview/memory benchmark:
cd backend && .venv/bin/python -m tests.benchmarks.run
```

The production HTTP harness uses actual Next.js and FastAPI processes, an
isolated temporary library and synthetic fixtures, not mocked backend services.
It checks CSV/XLSX inputs and downloads for both proof Actions, structured schema
failure, origin denial and valid same-origin writes, monthly source saving,
nineteen-table report generation, three workbooks/eighteen sheets, an independent
995.00 company-revenue control, chunk overlap refusal/consent, explicit result
release, backend-disconnected 502 behavior and byte-identical report replay
after restart from a full-library backup restored to a different directory. The separate
startup harness runs actual `npm start`, verifies page/catalog/health, refuses a
duplicate launch while preserving the first, shuts down both owned ports and
checks that startup created no business library.

The workbench benchmark uses five repetitions at 10k/50k/100k rows for each
format. At 100k rows, median whole Run was 11.6 ms for CSV and 670.8 ms for XLSX.
Full-size XLSX export is separate: 2,848.2 ms from the CSV fixture and 2,745.9 ms
from the XLSX fixture. A 100-row preview was about 0.1 ms and serialized 8,394
bytes versus 8,392,800 bytes for the whole 100k-row table. Peak Python allocation
for the CSV Run was 11.95 MiB (not total OS/native Polars memory). The retained
result was alive while stored and no longer alive after `delete_run`.
These Linux measurements are not a Mac or LAN timing guarantee.

The fresh company-sized benchmark (`v1-benchmark.json`) ran three independent
stores with 24 months, 72,000 sales rows, 6,000 sample rows, 900 accounts,
1,500 products, 50 suppliers and 15 rep workbooks. Median validate/save/generate/
ZIP cycle: **2,778.325 ms**, range 2,610.953–3,089.751 ms. Median historical setup:
324.715 ms. Each ZIP was 850,099 bytes. This stage-timed benchmark uses an
in-process FastAPI HTTP client; actual Next.js transport is verified separately,
and production Mac/LAN time remains unmeasured.

The previously documented 16,355 independent private-source comparisons and
ownership-conflict refusal are evidence from the prior Phase 15 session. Private
source files were not available in this fresh checkout and those comparisons
were **not repeated** for this follow-through. No company records are committed.
The existing Starlette/httpx test-client deprecation is upstream; it is disclosed
instead of suppressed or addressed with an unrequested compatibility migration.

## What could not be completed

| Gate / limitation | Why it remains open | What is needed to close it |
| --- | --- | --- |
| Complete-company source acceptance (15F) | Available prior validation data did not establish complete company signed-credit/sample coverage across both R12 windows and all reps. Calendar-month presence is not transaction completeness. | Complete exports plus independent company/rep/account/supplier/sample controls for the selected period. |
| Conflicting account snapshot | One account was assigned to multiple owners in prior supplied inputs. Choosing an owner would invent a business decision. | Business owner corrects the snapshot, then validate and generate again. |
| Real production UI cycle and restart replay | Automated golden HTTP/DOM stories do not prove production source completeness or human usability. | Run the corrected complete sources through the UI, independently reconcile, restart and rerun the recorded exact cycle. |
| Microsoft Excel for Mac | This environment has no Excel for Mac. Programmatic `openpyxl` reopening proves structural readability, not Excel display/repair behavior. | Open representative six-tab outputs in Excel on Mac; inspect values, formats, filters, frozen rows and absence of repair prompts. |
| Live browser/native interactions | In this follow-through, the browser automation daemon exited during startup. A normally verified official Chrome download also exited 139 on a direct headless blank-page launch, before visiting ForgeXL. Prior cloud-browser loopback refusal is separately recorded in Phase 15 evidence. Certificate verification was not disabled; no public deployment or privilege escalation was used. | A normal browser on the operating machine: layout, native file chooser, drag/drop, scrolling, downloads and retry behavior. |
| Finder launcher and target-machine timings | Linux cannot establish Finder/Terminal behavior, Mac runtime setup or Mac performance. | Follow startup checklist on Mac, double-click the launcher, stop/relaunch, and record timings. |
| Second-laptop LAN acceptance | HTTP proxy and Host/origin helper tests cover transport, not an actual remote laptop/network/browser. | Opt in with `npm run dev:lan` on a trusted network and test the browser-only workflow from the second laptop. |
| Supplementary placements / true-zero sample-month policy | These report rules remain explicitly provisional in the report specification. It is not appropriate to invent business semantics. | Business owner confirms the policies and acceptance controls. Placements stay outside the six workbook sheets. |

No remaining product gate was silently marked done. There is no public deployment,
credentials workflow, multi-user authorization or automatic retention/backup
promise. A saved exact-source receipt preserves source identities, not older
executable Action code; installing a different Action version is warned about.

## Manual acceptance and operation checklist

1. On the target Mac, use the README's `npm ci` and locked backend setup. Run
   `npm run doctor`, `npm run build`, then `npm start`. Confirm the monthly page
   and proxy health; Control-C should close both servers. Double-click
   `ForgeXL.command`, verify browser opening, stop and relaunch. A second launch
   must report occupied ports without stopping the first.
2. Test against a **new isolated absolute library directory** using
   `FORGEXL_LIBRARY_DIRECTORY` in the launch environment. Do not point a trial
   acceptance run at the sole production copy. Never commit private exports or
   downloaded business reports to git.
3. Import complete company sales/sample history covering both comparison
   windows in whole-month chunks. Review schema/date warnings and missing
   periods. Confirm that nonoverlapping chunks extend history, overlap refuses
   by default, and explicit skip leaves stored months unchanged. Do not use skip
   to repair an incomplete stored month: use deliberate monthly replacement.
4. Supply reporting-month sales, samples and the corrected complete assignment
   snapshot. Validate first; check detected reps, row counts, signed credits,
   missing history and ownership. Intentionally test a bad file and conflicting
   ownership in the isolated library; neither should commit the new cycle.
5. After warning review/consent, generate once. Confirm the UI distinguishes
   saved sources from generated reports, every applicable rep appears (including
   idle snapshot reps), and the ZIP contains one uniquely named workbook per rep.
   Download and inspect individual workbooks as well.
6. Independently calculate controls from the original exports, not ForgeXL's
   own tables: company/rep net sales and signed credits, sample quantities and
   credits, supplier shares, account/product R12 totals and prior/current
   comparisons. Check full-history totals and explicitly incomplete-history
   blanks. Review supplementary placement previews under their provisional rule.
7. Open representative active, idle and accented-name rep files in Excel for
   Mac. Verify six readable worksheets, no repair prompt, numeric rather than
   text amounts/ratios, meaningful blank unavailable values, totals/notes,
   filters, frozen headers and usable widths. Record discrepancies explicitly.
8. Release finished previews/downloads. Verify their old links return a readable
   not-found result while saved periods/cycle records remain. Restart and select
   the original recorded cycle. Compare workbook values and ZIP membership to
   the first run. In the isolated library, test an explicit corrected month:
   current-source reporting should change; original-cycle replay should not.
9. Record elapsed target-machine validation, generation and ZIP time with source
   size and rep count. Inspect responsive controls, keyboard labels, native
   picker, drag/drop and browser downloads. Optionally test a second laptop via
   `npm run dev:lan` on a trusted network; keep FastAPI private.
10. Stop both servers before backup. Copy the **entire** library, including
    `.reporting-cycles`, immutable old versions and all metadata. Restore to a
    different directory, launch with that directory, verify catalog and replay
    before replacing any working copy. Code/dependencies and report Action
    version should also be retained for reproducibility. There is no automated
    backup or encryption layer; manage access at the operating-system level.

Record date, OS/browser/Excel versions, code revision, Action version, dataset
version IDs, controls, timings and pass/fail for each gate. Keep private values
in a private acceptance record, not the public repository.

## Build-plan §35 evaluation and §36 decision

These are provisional engineering review scores, not claimed user acceptance.

| Category | Score / 10 | Basis / qualification |
| --- | ---: | --- |
| Ease of use | 8 | Single monthly review/generate/download flow; real user and Finder check pending. |
| Speed | 9 | Repeated synthetic measurements; target Mac/LAN timing pending. |
| Accuracy | 8 | Controlled signed-credit, attribution, R12 and replay tests; complete-company acceptance pending. |
| Error clarity | 9 | Structured named failures, trust summary, warnings and separate commit/report states. |
| Extensibility | 9 | Metadata-driven Actions and optional artifacts; proof Actions remain unchanged. |
| Maintainability | 9 | Explicit boundaries, pure business calculations, immutable sources, locked verification. |
| Local data privacy | 9 | Loopback defaults, no business-data egress, origin guards; no public deployment/auth promise. |
| Export quality | 8 | Literal values, shared six-sheet renderer and structural checks; Mac Excel acceptance pending. |
| UI quality | 6 | DOM behavior covered; visual/native-browser usability remains unverified. |
| Architectural potential | 9 | Clear Action, Run, library, receipt and presentation boundaries without extra infrastructure. |

Average: **8.4/10**, a convenience only. It does not override the incomplete
accuracy/input-completeness and human export/UI acceptance gates.

Decision: **REVISE / hold final V1 acceptance**, not GO and not a fundamental
STOP. The startup/history/cleanup revisions have been implemented; close the
listed production, policy and target-machine gates before declaring V1 accepted.
No new architecture is justified merely by the remaining manual checks.

## Changed areas

New files: `ForgeXL.command`, `scripts/check-setup.mjs`, `scripts/start.mjs`,
`backend/requirements.lock.txt`, `backend/app/api/request_guard.py`,
`backend/tests/test_history_chunks.py`, `backend/tests/test_browser_write_guard.py`,
`backend/tests/benchmarks/startup_http.py`, `src/lib/request-origin.js`,
`src/app/error.jsx`, `src/components/workbench/ReleaseRun.jsx`,
`tests/scripts/startup.test.mjs`, `tests/scripts/request-origin.test.mjs`,
this completion report and the current benchmark record.

Modified: history/monthly API, models and service; Run cleanup route;
backend startup and ingestion commentary; monthly import/report/review components;
preview and Action result screens; frontend API/origin helpers; npm manifests;
backend dependency instructions and development launcher; API-contract,
local-exposure, workflow, Run and frontend tests; production HTTP harness;
README, example configuration, architecture, implementation status and Phase 15
validation docs. Earlier Phase 15/audit file changes remain part of PR #4 and
are detailed in its validation record. No files or business data were deleted.
