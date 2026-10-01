# Phases 0–13 audit and Phase 14 readiness

Audited on 2026-10-01 against `main` at
`bea1905a3b907797cc4bd751ab189cd826019e64` ("Phase 13 Complete").
The corrected report Action is version `0.1.1`.

## Result

Independent implementation defects are repaired and covered by regression
tests. Phase 13's calculation implementation exists, but its business
acceptance does not: 13A's verified definitions and 13I's real completed month
are absent. Phase 14 has not been implemented because its accepted workbook
structure also depends on that evidence (14A).

This corrects the previous implementation-status claim that every Phase 13
exit criterion had been met. Synthetic tests demonstrate the declared
arithmetic; they cannot establish that the declarations match the business.
The original phase records remain historical evidence, not current acceptance.

## Purpose and architecture

ForgeXL replaces repeated spreadsheet cleanup and the monthly sales-rep
reporting process with reusable, auditable local Actions. The final monthly
workflow combines sales history, sample history and ownership as of the chosen
month to calculate and distribute a report for every applicable rep.

| Layer | Responsibility and principal files |
| --- | --- |
| Browser workbench | Generic Action selection, upload slots, previews, audit and downloads. `src/components/workbench/`, `src/lib/api.js`. No business calculations. |
| Same-origin transport | `src/app/forge-api/[...path]/route.js` streams requests to the local backend. It does not parse data or calculate reports. |
| HTTP boundary | FastAPI under `backend/app/api/`; validates requests and serves metadata, tables and artifact bytes. |
| Run pipeline | `services/runner.py` resolves uploads/library inputs, validates, calls the Action, collects results and records provenance. Runs and artifacts are process memory and are lost on restart. |
| Action contract | `actions/base.py`, `models/schemas.py` and the registry. An Action receives `{slot_id: DataFrame}` and returns tables, metrics and optional byte artifacts. It does not open source files or library versions. |
| Parsing | `services/parser.py`: Polars CSV, fastexcel XLSX and the declared openpyxl fallback. Exact headers, refusal of duplicate columns and ambiguous worksheets. Monthly CSV text identifiers bypass numeric inference. |
| Persistent sources | `models/library.py`, `services/data_library.py`: separate Parquet/JSON Data Library, immutable UUID versions, monthly supersessions and staged atomic publication of each version. This is not a report archive. |
| Ingestion | `models/source_schemas.py`, `services/reporting_period.py`, `services/ingestion.py`: validate a reporting cycle before writes; bootstrap history into monthly partitions; keep sales/samples separate and ownership period-specific. A later storage failure reports earlier successful commits rather than rolling them back. |
| Resolution | `services/input_resolution.py`: resolve selectors once, record every exact version and combine history before the Action. Optional period relationships and recorded date interpretation belong here, while provenance is available. |
| Report calculations | `models/report_spec.py`, `services/monthly_report.py`, `actions/monthly_sales_rep_report.py`: one shared prepared model, snapshot-derived rep roster, shared company calculations and twelve output tables. |
| Presentation and delivery | `services/export.py`, `services/workbook.py`, `models/artifact.py`, `services/archive.py`: pure rendering of already-calculated values, safe filenames, individual downloads and ZIP. Generic infrastructure exists; the report Action currently returns tables only. |

Phases 0–8 established the local workbench and its in-memory runtime. Phases
9–12 added persistent sources, ingestion, library-backed inputs and optional
artifacts. Phase 13 connected those inputs to the provisional report engine.
There is no need for a new database, job queue, per-rep Action or calculation
code in the frontend to implement Phase 14.

## Repaired defects

| Defect before the audit | Corrected behavior and regression coverage |
| --- | --- |
| Blank product dimensions split the same product into separate rows across comparison windows; account counts could become zero. | Grouped joins match null keys and product sorting breaks ties with the complete product key. Six blank-dimension cases verify the merged values and counts. |
| A blank supplier lost its rep revenue during company-versus-rep comparison. | The comparison join matches the blank supplier and preserves its revenue and share. |
| NaN/infinite monetary or quantity values passed validation and produced unusable totals; entirely null measures were misclassified. | Blank measures fail as missing; non-finite values fail as `NON_NUMERIC_MEASURE` in both sales and samples. Numeric/text cases are covered. |
| Integer-to-float conversion could silently change a large source value; combining boolean and numeric months could turn true/false into monetary values. | History concatenation rejects precision-losing integer conversions and boolean-to-number coercion. Report measure preparation rejects unsafe integers. |
| Typed spreadsheet account identifiers could crash the ownership join. | Both sides use the declared text identifier representation in working copies. Original frames remain unchanged. |
| An unexpected source `_owner` column could override ownership from the assignment snapshot. | Reserved internal columns are removed only from prepared copies before deriving dates, measures and ownership. A conflicting injected owner cannot change totals. |
| An Action validation hook ran after required-column validation failed and could turn a schema refusal into a 500. | Hooks run only after generic validation passes. The original structured schema errors are retained. |
| An ownership snapshot for another month could be used; combining multiple snapshots mixed ownership states. | The report declares period relationships; the runner refuses mismatched sample/ownership periods and resolution refuses merged snapshots. |
| History Run manifests recorded all versions, but no selector could replay the exact set after a month was corrected. | `versions:<id>,<id>,...` loads immutable recorded versions, including superseded ones, in period order. Duplicate IDs or two versions for one month are refused. |
| Ingestion accepted an explicitly chosen date format but discarded it, so the later report rejected the same dates as ambiguous. | Optional `date_column` / `date_format` metadata persists through reload and bootstrap. Opted-in report slots interpret a working copy before concatenation. The real Action is exercised with ambiguous US-format source text. |
| CSV inference stripped leading zeroes from source identifiers despite the text schema. | Monthly ingestion declares schema text columns before parsing; customer, invoice, SKU and vintage identifiers retain their source spelling. Ordinary upload Actions retain their previous parsing defaults. |
| Numeric-looking CSV headers with different spelling could be treated as duplicates. | The separate header probe reads literal text; `001` and `1` remain distinct while genuine repeats are still refused. |
| A storage error during a monthly cycle escaped without the structured record of files already committed. | `ReportingCycleImport` retains committed version IDs and identifies the failed and remaining datasets for a `DataLibraryError` at any of the three commits. |
| Workbook capacity checks omitted titles, subtitles, spacing, totals and heading text, allowing silent truncation. | Rendering budgets every presentation row and checks source headers, renamed headers, titles, subtitles and totals text. Boundary round trips verify the last total row is present. |
| A 150-character Unicode artifact filename could exceed a filesystem's 255-byte component limit. | Construction validates both character and UTF-8 byte limits; the helper truncates at a complete code point while preserving the extension and accents. |

The Action's version is raised from `0.1.0` to `0.1.1` to distinguish corrected
calculations in Run provenance. Its seven provisional rules remain provisional.
The contract tests explicitly pin the two additive ActionInput policy fields,
their inactive defaults for proof Actions, and the report's active policies.
Routes, outputs, proof-Action behavior and manifest schema version remain fixed.

## Existing saved data and exact replays

No stored version is migrated in place. The new optional date metadata defaults
to absent when old JSON is loaded. Unambiguous legacy dates remain readable;
an ambiguous legacy text column still needs an explicit interpretation.

To repair a legacy monthly version, reimport its original source with the
verified `date_format`, name the old version in `replaces`, and supply a
`reason`. Identical bytes are allowed only for an explicit change to missing
or different interpretation metadata; an unchanged interpretation remains a
duplicate. The new version supersedes the old one and preserves the raw parsed
frame. An original CSV identifier already stored as a number cannot recover
its leading zeroes from Parquet; reimport the original source as a deliberate
replacement if needed.

To replay a Run, group its `library_inputs` by slot. Name **every** recorded
history ID in that slot's `versions:` selector and use `version:` for its
snapshot. A `period:` or `history:` selector deliberately reads live corrected
data. Replay identifies the same source state; the Action version also matters
when comparing results from before and after a calculation correction. Run
manifests are in memory, so callers must retain them before a backend restart
if they need to reproduce an earlier Run.

## Required business decision

Build plan 13A says: **"If the existing report does not establish a rule
clearly, document the ambiguity and resolve it before implementation."**
It requires the verified Excel report, existing Power Query logic, accepted
definitions and manually checked results from a completed month. None is
present in this repository. The current fixture has twelve sales rows and
three sample rows across four months; its expected figures are hand-worked
synthetic arithmetic, not a previously accepted business month (13I).

| Open item | Evidence or decision required |
| --- | --- |
| `comparison_windows` | Accepted windows and history-coverage policy. The current code only warns when an entire window has no rows; absent interior months and genuine zero activity are not distinguished. Partial YTD sums are not proof of a complete year. |
| `known_invoice_types` | Actual invoice-type values and their accepted treatment. |
| `placement` / `placement_history` | Accepted customer/product identity, look-back and handling of incomplete history or blank product identifiers. |
| `sample_period` | Whether missing sample imports must fail, and how a true zero-sample month is represented. |
| `comparison_index` | The accepted company-versus-rep comparison formula. |
| `duplicate_source_rows` | Business definition of an accidental repeated source versus legitimate identical lines. |
| Ownership schema | Real account-assignment export, including exact headers and identifier types; currently `confirmed=False`. |
| Workbook structure | Accepted worksheet names, sections, displayed totals and ordering (14A). |

The preferred resolution is a verified workbook, its Power Query definitions
and matching sales/sample/ownership sources plus the history needed for its
comparisons. These may be sanitized consistently. Independently checked
representative expected values must accompany the completed month.

Alternatively, the owner can explicitly authorize the current provisional
calculation rules and limitations as the basis for Phase 14, together with the
proposed layout below. That enables provisional workbook generation; it does
not retroactively prove 13I or confirm the business rules. Record that decision
here, retain the provisional warnings, and label any unperformed acceptance
checks accurately.

## Proposed Phase 14 structure, subject to that decision

One sheet per existing calculation table lets the shared renderer preserve
numeric columns and keeps calculation changes out of presentation. This is a
proposal, not an accepted existing layout.

| Worksheet, in order | Calculation table |
| --- | --- |
| Summary | `rep_summary` |
| Company Summary | `company_summary` |
| Account Performance | `account_performance` |
| Supplier Performance | `supplier_performance` |
| Company Suppliers | `company_supplier_performance` |
| Supplier Comparison | `supplier_comparison` |
| Product Performance | `product_performance` |
| Placements | `placements` |
| Placement Detail | `placement_detail` |
| Samples | `samples` |
| Sample Detail | `sample_detail` |
| Data Quality | `data_quality` |

Per-rep sheets contain only that rep's rows, including a roster member with no
activity. Company tables and data-quality warnings are shared context in each
workbook. Names include the rep and reporting month, e.g.
`Beth Comeaux - September 2026.xlsx`; the batch is
`September 2026 Sales Rep Reports.zip`.

## Execution after the decision

1. **13A / 13I:** reconcile declarations with the provided evidence, settle
   coverage/ownership questions and pin independently checked values from the
   real month. If a provisional basis is authorized instead, record its scope
   and the outstanding business acceptance accurately.
2. **14A:** record the agreed worksheet/table mapping, ordering and totals in
   the report specification. Use the existing process's structure when supplied.
3. **14B:** describe all worksheets through the existing `Sheet` / `Column`
   renderer and shared formats. Currency, quantity, percentages, widths,
   headers, frozen panes, filters and literal precomputed totals follow one
   reusable policy. Add any required totals in the calculation layer.
4. **14C / 14D:** slice already-calculated tables by `Sales Rep`, render the
   complete dynamic roster in one Action Run and return collision-safe workbook
   artifacts beside the twelve previewable tables. Keep library access out of
   the Action and filesystem writes out of the Run.
5. **14E:** expose the purpose-named monthly ZIP through the generic artifact
   infrastructure; keep individual downloads available. Do not duplicate
   workbook calculation or rendering inside the archive layer.
6. **14F:** reopen every generated workbook programmatically, compare
   representative numeric values with its source DataFrames, verify sections,
   headers, names and archive membership, and exercise generic download routes.
   Manually open representative outputs in Microsoft Excel on Mac. That manual
   acceptance check has not been performed in this Linux environment.

Phase 15 remains separate: the dedicated validate/import/generate UI,
committed-versus-generated state, corrections/reruns/history, real month
acceptance and representative performance validation. Phase 14 does not need
to absorb those workflows.

## Verification

Baseline: 2,150 passing tests, clean pyright, ESLint and production build.
Final: **2,226 tests passed**, with one upstream Starlette/httpx deprecation
warning and zero failures/skips/xfails; pyright reports 0 errors/0 warnings;
ESLint and the production build pass. Full check details and the changed-file
inventory are recorded in `docs/implementation-status.md`. The existing
contract and end-to-end tests remain active; regression cases exercise the
failure conditions above and the actual report Action. Automated workbook
round trips cover the Phase 12 infrastructure fixes, not a claim that the
unimplemented Phase 14 business workbooks have passed acceptance.
