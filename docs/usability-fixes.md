# Saved-history conflict recovery — 2026-10-06

A differing multi-year upload no longer forces users to remove both files or
prepare 37 separate corrections merely to reuse the saved history. Conflicts
still block by default. Each affected source offers **Use saved sales months** or
**Use saved sample months**. This retains both uploads and clears the stale review;
click **Generate reports** to revalidate, review the ignored-differences warning,
acknowledge it, then generate. Only missing months are imported. Every saved
month, including the report month, keeps its current version and rows. This
choice deliberately ignores uploaded differences; it does not establish that
the saved data is complete or correct.

The same choice is available under **File requirements & date options**. Selecting
a new file clears that source's reuse decision. A correction and saved-month
reuse cannot be selected together. Every uploaded row still passes normal
schema/date/measure validation before reuse. Replacement remains an explicit
single-month/current-version/reason operation. Source changes after review still
invalidate generation, and receipts pin the exact saved/new versions used.

`POST /api/monthly/validate` accepts optional single text fields
`sales_history.use_saved_months` and `sample_history.use_saved_months`, each
`true` or `false` (default false). Unknown fields, malformed booleans, repetitions,
reuse without an upload and reuse combined with correction are rejected.
`HISTORY_DIFFERENCES_IGNORED` groups affected months with saved version IDs and
uploaded/saved row counts. Warning acknowledgment is required before generation.

Rep detection and historical comparisons now say **Not checked until source
errors are resolved** when preflight has not run. They no longer imply zero reps
or complete comparison coverage in that state. Missing Customer on sample
transactions remains a warning; sample rows stay stored as uploaded.

For an already-installed version, if the saved sales history is the intended
source and includes the report month: remove only the **Sales data** upload,
keep the **Sample data** upload, and generate. This uses saved sales history while
saving new sample months. **Use saved data** requires both sales and samples to
already be saved and is a different operation.

---

# CSV workflow extension — 2026-10-06

The existing action cards now link to **Combine CSV files** and **Filter a CSV**
through backend workflow metadata. Source/additional CSV controls show effective
order, add more files, individual removal and move arrows. Python supplies the
actual headers. Native filter controls support all/any, explicit case behavior,
text membership, blanks, numbers and explicitly formatted dates. One large
**Combine files**/**Apply filters** button processes; metrics, full CSV download,
and a paginated preview follow. Zero matches remain downloadable.

Inputs lock during processing; changed files/order/action/filters hide and release
stale results. Inspection and processing retry keep valid inputs. Filter changes
reuse inspected frames; append-order changes reorder retained segments without
reupload/parse. Clear files and results, leaving the page, expiry and backend
restart cleanly release preparation. Reinspection after expiry retains filters.
CSV processing is ephemeral and does not save/correct monthly business history.

See [csv-tools.md](csv-tools.md) for instructions, strict field/blank semantics,
contracts, limits and tests. Final verification and browser limitations are
recorded in [implementation-status.md](implementation-status.md).

---

# Guided workflow corrections — 2026-10-02

## Problem and resulting behavior

The previous UI separated history setup, monthly validation and report generation,
and required an Account Assignment snapshot. Ordinary monthly uploads were
restricted to one month, so a three-year sales export failed before generation.

The home page now starts with action cards. A selected action shows required
uploads, then one large Generate report button. The monthly page accepts sales
and sample files containing one month or multiple years. Account Assignment is
absent from the UI, monthly catalog and accepted monthly API fields. The report
uses transaction reps active in current R12 and transaction-derived account
context. This contract change advances the report Action from `0.2.0` to `0.3.0`.

Downloads appear prominently when generation finishes. Date interpretation,
corrections, standalone history import, saved-cycle selection, table previews,
audit details and result release remain available behind expandable controls.
Missing-file explanations, locked processing controls and connection retries
make the next required step explicit.

## History and compatibility

- Every uploaded transaction is validated before saving, even after the selected
  reporting cutoff. Explicit date interpretation is applied to validation and
  month partitioning without changing stored raw values.
- New months are saved as immutable monthly partitions. Exactly matching stored
  values/types/date interpretation are reused; row ordering does not create a
  difference. Conflicts fail before any writes. No row merging or silent correction.
- A report reads only months through its selected period. Later valid months in
  the same upload are saved but excluded from that cycle's receipt and calculations.
- Single-month corrections still require the current version and a reason.
  Partial saves and failed rendering preserve their recovery information.
- New schema-v2 receipts name sales and samples. Schema-v1 receipts keep their
  original immutable record but replay only sales/sample references with the
  changed-Action warning. A missing legacy assignment snapshot cannot block replay.
- The generic frontend discovers specialized routes through optional Action
  metadata. Future ordinary actions still render their input slots dynamically.

## Verification

All 2,306 backend tests, 17 frontend interaction tests and five startup/transport
tests passed. Pyright reports zero errors/warnings; lint and the production build
pass. Focused tests import 36 months through the ordinary monthly endpoint,
generate without assignment data, replay receipts, reuse unchanged history,
refuse conflicts, isolate the reporting cutoff and reject malformed later months.

Frontend interactions cover the clean single-button path, warning consent,
required-file gating, metadata-driven future inputs, changing actions, retained
multi-year files when changing the month, saved-source recovery, network retry,
stale review invalidation, unsupported files and duplicate-click locking.

The production HTTP harness verifies real Next.js proxy/FastAPI/file-store
behavior, both proof actions with CSV/XLSX, write-origin guards, exact golden
revenue `995`, two active rep workbooks with twelve total sheets, downloadable
ZIPs, result release, backend disconnect and replay after restoring a full backup
to a different directory. The startup harness confirms ready servers, owner-safe
duplicate-launch refusal, clean shutdown and no startup business writes.

Company-size repeated synthetic performance measurements are retained in
`usability-benchmark.json`. Existing monetary, supplier, date-window, export and
workbook regressions remain covered; snapshot-only roster/count expectations
were deliberately updated to the new invoice-derived contract.

## Remaining verification

Browser automation could not start in this execution environment. Direct Chrome
also fails because the runtime blocks its process-singleton socket. These are
environment blockers; no native browser layout or file-download interaction is
claimed. Perform a normal browser walkthrough and Excel for Mac opening during
acceptance. Private production sources were unavailable, so no new production
data reconciliation is claimed. Existing provisional placement/zero-sample
warnings remain visible and require acknowledgment.
