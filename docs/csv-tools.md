# CSV tools — 2026-10-06

## Operation

1. From the existing action cards, choose **Combine CSV files** or **Filter a CSV**.
2. Choose the source CSV. For Combine, choose one or more additional CSVs.
   Select again to add more; use the arrows to set append order and Remove to
   remove an individual file. Duplicate filenames are allowed and remain separate.
3. Wait for Python to inspect the files. Columns come from the uploaded header,
   never a monthly-report schema or browser file parser.
4. Add conditions if needed. Choose **Match all conditions** (AND) or **Match any
   condition** (OR). Text matching is case-sensitive unless **Ignore case** is
   checked for that condition. Between includes both endpoints. For dates,
   select the actual format in the column and enter comparison dates in that format.
5. Click **Combine files** or **Apply filters**. Filter requires at least one
   condition. Combine permits no filters and always removes exact duplicates.
   Filter retains repeated rows unless **Remove exact duplicate rows** is checked.
6. Review rows received, exact duplicates removed when enabled, rows excluded by
   filters when configured, final count, and the paginated preview. **Download CSV**
   downloads the complete result. Zero matches succeed and download a header-only CSV.
7. **Clear files and results**, changing files/action, or leaving the page
   releases inspected data. Run details also offers result release. Reinspection
   after expiry keeps valid selected files and filter settings.

A failed inspection offers Retry inspection. Correctable processing errors keep
files/options and offer Retry processing; edit filters without reuploading.
Changing append order reuses retained row segments without parsing or uploading.
Adding/replacing/removing files inspects the changed collection. Changed files,
order, action, or filters invalidate old results; abandoned/late responses never
replace newer inputs. Processing controls lock to prevent duplicate submission.
States describe inspection or processing; no percentage is invented.

## Exactness and parsing

Source first, then additional files in displayed order. Every column's **exact
header name** must match. Header sets in different orders are compatible:
additional rows are aligned to source column order. Missing/extra columns stop
inspection with filename, file position, and exact missing/extra names.
Repeated headers are refused; a single empty header is preserved as empty.

An exact duplicate has equal **parsed CSV fields in every column**. Keep the
first occurrence and retained row order; remove within-file and cross-file
repeats. Equivalent quoting does not create a distinction. No trimming, case
changes, accents removal, rounding, selected-column matching, or fuzzy matching.
`000123` differs from `123`; `1.00` differs from `1`; `Customer` differs from
`customer`; a trailing space is significant. Quoted and unquoted empty fields
both have the parsed value `""`.

`parser.parse_csv_text` is a narrowly scoped policy used only by these tools:
strict UTF-8 or UTF-8 BOM decoding, strict CSV quote framing and record widths,
then explicit Polars String columns. Unicode, signs, decimal/date text, very
large identifiers, commas, escaped quotes, whitespace, CR/LF/CRLF inside quoted
fields, and original column order survive preview/export. A physical empty line
is one blank field in a one-column CSV; in a wider CSV it is a short-record
error. No malformed records are skipped. Unsupported encodings, NUL bytes,
unclosed quotes, and text after a closing quote are refused. The dialect is
comma-delimited CSV with double quotes; no delimiter/encoding guessing.

Logical fields are preserved, not original quote style or line endings. CSV
export is the existing Polars byte renderer and existing attachment naming.
The generic inferred parser, old duplicate remover, product master, monthly
source parser, and monthly business rules are unchanged.

## Filter contract

Each condition names `column`, `kind`, and `operator`.

| Kind | Operators | Values |
| --- | --- | --- |
| `text` | `equals`, `not_equals`, `contains`, `not_contains`, `starts_with`, `ends_with` | String `value`; empty text is allowed. |
| `text` | `is_any_of`, `is_none_of` | Nonempty `values` array of exact strings; selected values may themselves be empty. |
| `blank` | `is_blank`, `is_not_blank` | No values. |
| `number` | `equals`, `not_equals`, `gt`, `gte`, `lt`, `lte`, `between` | String `value`; `upper` for between. |
| `date` | `on`, `before`, `after`, `between` | String `value`; `upper` for between; explicit `date_format`. |

Optional text `ignore_case` defaults false and uses Unicode casefold on temporary
comparison values only. Contains is literal text, never regex. Date formats are
exact `YYYY-MM-DD`, `MM/DD/YYYY`, or `DD/MM/YYYY` (including padding); the latter
two are explicit interpretations, never inferred from ambiguous dates. Impossible
calendar dates fail. Numbers accept finite signed integer/decimal text and
scientific notation, with no surrounding whitespace or grouping separators.
Decimal comparisons avoid binary-float rounding, including large identifiers.
Endpoints must be ordered. Source output stays string-valued.

**Blank rules:** only the empty string is blank; whitespace-only text remains
text. Text operators compare blank as `""`, including negative operators and
membership. An empty contains pattern matches every text field; its negative
matches none. Numeric/date conditions return false for blanks, including numeric
`not_equals`. Use a separate blank condition with OR to include them. Every
populated value in a typed condition's column must parse before processing,
regardless of AND/OR or another condition excluding that row. Unreadable values
stop with the column and up to five examples; none are silently converted to null.

Options use `{match: "all"|"any", remove_duplicates: boolean, conditions: [...]}`.
Defaults are all/false/empty. Combine's effective duplicate setting is always
true. Maximum 50 conditions, 100 membership values per condition, and 10,000
characters per supplied comparison value. Unknown fields/operators/columns,
wrong value types, unused endpoints, invalid values/formats, and inappropriate
ignore-case controls are rejected. Typed comparisons use temporary values in
Polars expressions; no helper/audit column is written into results.

## HTTP and Action integration

Every browser request uses `/forge-api`; Next.js remains a streaming,
transport-only proxy. FastAPI and Python services own upload intake, parsing,
sessions, header alignment, options validation and processing. Actions receive
prepared frames and validated per-request configuration, and never parse files
or access persistent business data.

| Method | Backend path | Contract |
| --- | --- | --- |
| GET | `/api/actions` | Adds discovered `combine_csv`/`filter_csv`, version 1.0.0, with `/csv-tools?action=...` workflow links. Input `max_files` declares ordered multi-file capability, default 1 on old slots. |
| POST | `/api/csv/inspect` | Multipart single `action_id`, single `combine_source`, repeatable file-only `combine_additional`; or single `filter_source`. Returns session UUID, headers, received rows, all source metadata and remaining lifetime. |
| POST | `/api/csv/reorder` | JSON `{session_id, order: [0, ...]}`; permutation of all retained file positions, source first. Returns a new inspected session and invalidates the old token, without reupload/parse. |
| POST | `/api/csv/runs` | JSON `{session_id, action_id, options}`. Reuses prepared strings; returns a normal ephemeral Run manifest. |
| POST | `/api/csv/discard` | JSON `{session_id}`. Idempotently releases preparation. |
| GET/POST | Existing Run routes | Standard manifest, paginated preview, complete CSV attachment, and explicit result discard. |

The existing `/api/runs` callers retain one upload per slot and reject repeats;
configurable CSV actions direct clients to inspection/configuration instead.
`read_run_form` defaults retain duplicate-field protection. Only CSV inspection
opts into the append-field repetition and aggregate/count bounds. Text fields
never stand in for dataset references or options in CSV inspection. Options are
strict JSON/Pydantic configuration in a separate request, not multipart filenames
or library references. Malformed CSV requests have structured workbench errors.

`Action.run(inputs)` stays unchanged. Default `run_configured(inputs, options)`
accepts only empty options and delegates to the old implementation. CSV actions
override it; registered instances have no per-request state. `execute_run` gains
keyword-only service-prepared frames/metadata and options, reusing normal Run
lifecycle, failure audits, results, preview, and export. Header-only prepared
inputs are allowed only on this validated path. CSV results advertise CSV only;
existing output format behavior remains unchanged.

All sources are recorded **in effective order**, with separate generated input
identities even when filenames repeat. Manifest/audit `metrics.effective_options`
records the effective configuration, including the forced Combine deduplication
setting. Other metrics are input_rows, duplicates_removed, rows_excluded and
output_rows. Deduplication precedes filtering; excluded counts refer to the
post-deduplication frame. Failed Action Runs retain evidence but no partial result.
Neither CSV operation writes Data Library versions, monthly receipts, or disk
uploads/intermediates. Sources and prepared frames are ephemeral.

## Bounds and cleanup

| Environment variable | Default | Scope |
| --- | --- | --- |
| `FORGEXL_MAX_UPLOAD_BYTES` | 262,144,000 bytes (250 MiB) | Existing per-file bound, also applied to CSV. |
| `FORGEXL_CSV_MAX_FILES` | 20 | Total files including source, from 2 to 1000. |
| `FORGEXL_CSV_MAX_TOTAL_BYTES` | 524,288,000 bytes (500 MiB) | Aggregate uploaded file bytes per inspection. |
| `FORGEXL_CSV_SESSION_TTL_SECONDS` | 900 | Original inspection lifetime; reorder does not extend it. |
| `FORGEXL_CSV_MAX_SESSIONS` | 4 | Retained plus in-flight reserved inspection sessions. |
| `FORGEXL_CSV_MAX_RETAINED_BYTES` | 1,073,741,824 bytes (1 GiB) | Retained preparation budget, charged as max(upload bytes, estimated Polars size). |

Set overrides before starting the backend; discovery metadata reflects startup
file-count configuration. Budget is an estimate of retained data, not a hard OS
RSS ceiling: parsing/concatenation/export temporarily need additional memory.
Use smaller bounds on constrained machines. Processing is synchronous, without
queues, authentication or a database.

Multipart buffers remain memory-only and close on success, refusal, parse/Action
failure, cancellation and disconnect. Reservations cap simultaneous preparation.
Prepared sessions expire via daemon timers, as well as checks at access; explicit
discard releases immediately. A processing lease can finish using its local
frame reference if the preparation expires while running. Changing filters reuses
frames; changing append order replaces the token. Unknown, released, expired or
restarted sessions report reinspection instructions. Abandoned requests release
preparation and successful Runs; correctable errors retain valid preparation
for retry. Finished Runs retain existing explicit-release/restart lifetime;
preparation timers do not remove successful Run downloads.

## Verification

- `backend/tests/test_csv_tools.py`: exact fields/quoting, duplicate filename/order,
  reordered headers and mismatches, every filter operator, Unicode caseless and
  literal matching, blank/negative/AND/OR behavior, strict numeric/date validation,
  pipeline order, full/header-only exports, session retry/expiry/capacity/reorder,
  disconnect/cancellation, audit evidence and old parser/hook compatibility.
- `backend/tests/test_upload_form.py`: real multipart buffers, memory-only spools,
  single-field protections, CSV aggregate/count refusals, cancellation/disconnect
  and closure after malformed/successful multi-file intake.
- `tests/frontend/csv.test.mjs`: discovered choices, controls/order, conditional
  filter controls, gating, options requests, retries, stale response invalidation,
  duplicate click locks, metrics, download links, prepared reuse and clear/release.
- Production `workflow_http.py`: actual Next.js proxy → FastAPI → CSV download,
  including >100-row full output, text fidelity, Filter repeated rows, zero matches,
  attachment behavior, malformed options, and releases; existing proof/monthly
  and restart/backup stories remain in the same harness.

Final command results and browser limitations are recorded at the top of
[implementation-status.md](implementation-status.md). No production business
source was uploaded, changed, or reconciled by this feature's verification.
