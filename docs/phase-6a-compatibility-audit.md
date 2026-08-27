# Phase 6A — Compatibility Audit and Contract Freeze

Produced by the Phase 6A session. Audit only: **no runtime behaviour was
changed.** Its job is to record exactly what the Phase 0/1–5 implementation
depends on before Phase 6B–6I change how files and run state move through the
system, so the migration cannot silently damage completed work.

Authoritative plan: `docs/build-plan.md`, "Phase 6A — Compatibility Audit and
Contract Freeze". Read that section alongside this file.

Repository state audited: branch `claude/forgexl-phase-6a-x5hz9j`, working tree
clean at commit `259615d`. Backend suite before this phase: **311 passed**.

---

## 1. Method

Every search Phase 6A prescribes was run against the whole repository
(excluding `node_modules/`, `backend/.venv/` and `.git/`):

- directory and artifact concepts — `data/`, `runs/`, `uploads/`, `inputs/`,
  `working/`, `exports/`, `manifest.json`, `tmp/`, `temp/`
- path and I/O concepts — `file_path`, `filepath`, `input_path`, `output_path`,
  `run_path`, `export_path`, `Path(`, `open(`, `.write_*`, `.read_text`,
  `mkdir`, `unlink`, `os.*`, `is_file`, `FileResponse`, `pathlib`
- frontend networking — `localhost:8000`, `127.0.0.1:8000`, `API_BASE_URL`,
  `NEXT_PUBLIC_*`, `/api/`

Not every hit is a defect. Each is classified with one of:

| Class            | Meaning                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------ |
| **MIGRATE**      | The normal request path requires the persistent filesystem. Phase 6B–6F must change it.                                  |
| **RESHAPE**      | Logic is sound and path-independent, but its signature or plumbing carries a path. Keep the logic, change the interface. |
| **KEEP**         | Filesystem use that is not part of spreadsheet processing (dev tooling, config root, VCS rules). Leave alone.            |
| **TEST-COUPLED** | A test that asserts the on-disk model. Must be rewritten alongside the code it covers.                                   |
| **UNTOUCHED**    | No filesystem dependency. Phase 6 must not rewrite it.                                                                   |

---

## 2. Feasibility check performed during the audit

The migration assumes CSV/XLSX can be parsed from memory and exports generated
into memory with the dependencies already pinned in `backend/requirements.txt`.
That was verified by execution rather than assumed, because a failure here
would be an architectural conflict to report, not an implementation detail:

| Capability                                                           | Result                                                                 |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `pl.read_csv(bytes)`                                                 | works                                                                  |
| `pl.read_csv(io.BytesIO)`                                            | works                                                                  |
| `fastexcel.read_excel(bytes)`                                        | works                                                                  |
| `fastexcel.read_excel(io.BytesIO)`                                   | **fails** — `InvalidParametersError: source must be a string or bytes` |
| `openpyxl.load_workbook(io.BytesIO, read_only=True, data_only=True)` | works                                                                  |
| `pl.DataFrame.write_csv(io.BytesIO)`                                 | works                                                                  |
| `pl.DataFrame.write_excel(workbook=io.BytesIO)`                      | works (6,150-byte workbook)                                            |

**Consequence for Phase 6C:** the in-memory parser must hold the upload as
`bytes` and hand fastexcel the `bytes` directly, wrapping in `io.BytesIO` only
for the openpyxl fallback. No architectural conflict exists; no dependency
change is required.

---

## 3. Filesystem dependency inventory

### 3.1 `backend/app/config.py`

| Location                                          | What                                                                                                                  | Class                                             |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| L16, L23 `PROJECT_ROOT`                           | Repository root, derived from `__file__`. Used by `main.py` for uvicorn `reload_dirs` only — never on a request path. | **KEEP**                                          |
| L25–33 `DATA_DIRECTORY`, `FORGEXL_DATA_DIRECTORY` | Root for generated data. Exists solely for the run-directory model.                                                   | **MIGRATE** (becomes unused; removal is Phase 6I) |
| L35 `RUNS_DIRECTORY`                              | `<data>/runs`. The anchor of the entire on-disk model.                                                                | **MIGRATE**                                       |
| L42–52 `HOST`, `PORT`, `MAX_UPLOAD_BYTES`         | No filesystem involvement. `MAX_UPLOAD_BYTES` stays the upload limit in the in-memory model.                          | **UNTOUCHED**                                     |
| L60–76 CORS origins                               | No filesystem involvement. Phase 6G may extend the origin list for LAN testing.                                       | **UNTOUCHED**                                     |

`FORGEXL_DATA_DIRECTORY` is documented in `.env.example` and therefore has a
small public surface. It has no consumer once run state lives in memory; the
documentation must be updated in the same phase the constant is removed.

### 3.2 `backend/app/services/storage.py` — the on-disk model

The whole module exists to implement the run-directory tree. It is the largest
single migration target.

| Symbol                                                                                                                   | Line    | What it does                                                                                       | Class                                                        |
| ------------------------------------------------------------------------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `SAFE_ID_PATTERN`                                                                                                        | 35      | Token check applied before an ID becomes a path component.                                         | **RESHAPE** — still useful for worksheet/filename generation |
| `STORED_UPLOAD_STEM`, `stored_filename_for`                                                                              | 38, 191 | Generated on-disk upload name.                                                                     | **MIGRATE** — no upload is stored                            |
| `MANIFEST_FILENAME`                                                                                                      | 40      | `manifest.json`.                                                                                   | **MIGRATE**                                                  |
| `_INPUTS_DIRNAME` / `_WORKING_DIRNAME` / `_EXPORTS_DIRNAME`                                                              | 42–44   | `inputs/`, `working/`, `exports/`.                                                                 | **MIGRATE**                                                  |
| `_COPY_CHUNK_BYTES`                                                                                                      | 47      | 1 MiB copy chunk.                                                                                  | **MIGRATE**                                                  |
| `BinarySource` (Protocol)                                                                                                | 50      | Chunked-read interface an upload must satisfy.                                                     | **RESHAPE** — replaced by in-memory upload bytes             |
| `StoredUpload`                                                                                                           | 62      | Describes a preserved upload on disk.                                                              | **RESHAPE** — becomes an in-memory upload record             |
| `RunPaths` (+ `inputs`, `working`, `exports`, `manifest_path`, `input_directory`, `working_artifact`, `export_artifact`) | 74–112  | Every path in the system. Nothing else builds one.                                                 | **MIGRATE** — deleted, replaced by the Run Store             |
| `_safe_id`                                                                                                               | 115     | Guard for path components.                                                                         | **RESHAPE**                                                  |
| `new_run_id`                                                                                                             | 122     | `uuid4()` string — the Run ID convention.                                                          | **UNTOUCHED** (build plan 6B.7: preserve it)                 |
| `parse_run_id`                                                                                                           | 127     | Canonical-UUID validation of a client-supplied ID.                                                 | **UNTOUCHED** — still the right guard for a Run Store key    |
| `runs_directory`                                                                                                         | 145     | Reads `config.RUNS_DIRECTORY` at call time.                                                        | **MIGRATE**                                                  |
| `run_paths`                                                                                                              | 154     | ID → paths.                                                                                        | **MIGRATE**                                                  |
| `create_run`                                                                                                             | 160     | `mkdir` of the four directories.                                                                   | **MIGRATE** — becomes `RunStore.create_run()`                |
| `run_exists`                                                                                                             | 172     | Manifest file present?                                                                             | **MIGRATE** — becomes a Run Store lookup                     |
| `extension_of`                                                                                                           | 180     | Lowercase extension of an uploaded filename, directory components discarded. Pure string function. | **UNTOUCHED**                                                |
| `store_upload`                                                                                                           | 202     | Chunked copy to disk plus the `MAX_UPLOAD_BYTES` check.                                            | **MIGRATE** — the size check survives, the copy does not     |
| `display_filename`                                                                                                       | 261     | Basename for a user-facing message. Pure string function.                                          | **UNTOUCHED**                                                |
| `_human_size`                                                                                                            | 270     | Byte count for a message. Pure.                                                                    | **UNTOUCHED**                                                |
| `write_manifest`                                                                                                         | 283     | Atomic temp-file + `os.replace` JSON write, with `fsync`.                                          | **MIGRATE** — no manifest file exists in V1                  |
| `read_manifest`                                                                                                          | 308     | Loads and validates `manifest.json`.                                                               | **MIGRATE** — becomes `RunStore.get_run()`                   |

### 3.3 `backend/app/services/parser.py`

| Symbol                                                                                                               | Line    | Class         | Note                                                                                                                                    |
| -------------------------------------------------------------------------------------------------------------------- | ------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `parse_tabular_file(path: Path, extension)`                                                                          | 82      | **RESHAPE**   | Signature takes a `Path`; dispatch logic is sound. Becomes bytes-first.                                                                 |
| `_parse_csv(path)`                                                                                                   | 115     | **RESHAPE**   | `pl.read_csv(path)` → `pl.read_csv(data: bytes)`. Error handling unchanged.                                                             |
| `_parse_xlsx(path)`                                                                                                  | 142     | **RESHAPE**   | Engine-fallback ordering and the never-retry-a-structural-refusal rule are behaviour to preserve exactly.                               |
| `_parse_xlsx_with_fastexcel(path)`                                                                                   | 177     | **RESHAPE**   | `fastexcel.read_excel(str(path))` → `fastexcel.read_excel(payload_bytes)` (see §2).                                                     |
| `_parse_xlsx_with_openpyxl(path)`                                                                                    | 206     | **RESHAPE**   | `load_workbook(path, ...)` → `load_workbook(io.BytesIO(payload), ...)`.                                                                 |
| `_fastexcel_sheet_has_data`, `_openpyxl_sheet_has_data`, `_frame_from_rows`, `_select_data_worksheet`, `_human_list` | 191–313 | **UNTOUCHED** | Operate on reader objects and rows, not paths. The worksheet-ambiguity rule (build plan §17) must survive byte-for-byte in its wording. |
| `ParsedFile`, `SUPPORTED_EXTENSIONS`, `ENGINE_*`                                                                     | 36–80   | **UNTOUCHED** | `ParsedFile` already carries a DataFrame, not a path.                                                                                   |

`ParsedFile` is the boundary object Phase 6D needs and it already exists in the
right shape. This is the single most important finding for keeping 6D small.

### 3.4 `backend/app/services/export.py`

| Symbol                                                         | Line  | Class         | Note                                                                                                                                                                            |
| -------------------------------------------------------------- | ----- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `write_output(paths, output_id, frame)`                        | 46    | **MIGRATE**   | Writes three files. Becomes on-demand in-memory generation (6F).                                                                                                                |
| `write_parquet`                                                | 65    | **MIGRATE**   | Parquet exists only as the preview source; 6E previews from the result DataFrame, so this becomes unnecessary. Build plan §28 is superseded by the Phase 6 architectural rules. |
| `write_csv`                                                    | 72    | **RESHAPE**   | `frame.write_csv(BytesIO)` verified working.                                                                                                                                    |
| `write_xlsx`                                                   | 79    | **RESHAPE**   | `frame.write_excel(workbook=BytesIO)` verified working.                                                                                                                         |
| `_worksheet_name`                                              | 90    | **UNTOUCHED** | 31-character Excel truncation; still needed by 6F.5.                                                                                                                            |
| `WrittenOutput`, `EXPORT_FORMATS`, `CSV_FORMAT`, `XLSX_FORMAT` | 25–43 | **UNTOUCHED** | `formats` is a public manifest field. `PARQUET_FORMAT` becomes unused.                                                                                                          |

### 3.5 `backend/app/services/preview.py`

| Symbol                                                      | Line   | Class         | Note                                                                                                  |
| ----------------------------------------------------------- | ------ | ------------- | ----------------------------------------------------------------------------------------------------- |
| `read_preview(paths, output_id, ...)`                       | 65     | **MIGRATE**   | `pl.scan_parquet` of `working/<id>.parquet`. Becomes a slice of the retained result DataFrame (6E.2). |
| `validate_offset`, `validate_limit`                         | 38, 47 | **UNTOUCHED** | Pure range checks. The refuse-rather-than-clamp rule is a public contract.                            |
| `DEFAULT_PREVIEW_LIMIT`, `MAX_PREVIEW_LIMIT`, `PreviewPage` | 21–35  | **UNTOUCHED** | 100 / 500 are public contract values.                                                                 |

### 3.6 `backend/app/services/runner.py`

The pipeline logic is largely path-independent; the _plumbing_ is not.

| Symbol                                                                                                  | Line    | Class         | Note                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------- | ------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PendingUpload(filename, stream)`                                                                       | 55      | **RESHAPE**   | Already framework-independent. `stream` becomes `payload: bytes`.                                                                                                                                                         |
| `RunOutcome(manifest, paths)`                                                                           | 67      | **RESHAPE**   | Drop `paths`; return the logical run.                                                                                                                                                                                     |
| `execute_run`                                                                                           | 74      | **MIGRATE**   | Calls `storage.create_run()` (L100) and `storage.write_manifest` (L109, L141, and via `_finalize_failed`). Orchestration moves onto the Run Store; the stage ordering and the failure contract are behaviour to preserve. |
| `_store_and_check_slots`                                                                                | 162     | **RESHAPE**   | Required-slot and extension checks are pure; only `storage.store_upload` (L212) is filesystem.                                                                                                                            |
| `_parse_inputs`                                                                                         | 220     | **RESHAPE**   | Calls `parser.parse_tabular_file(upload.path, ...)` (L232).                                                                                                                                                               |
| `_validate_datasets`                                                                                    | 239     | **UNTOUCHED** | Emptiness + exact required-column comparison. No filesystem.                                                                                                                                                              |
| `_execute_action`                                                                                       | 280     | **UNTOUCHED** | Calls `action.run(frames)` and wraps a crash. Already DataFrame-first.                                                                                                                                                    |
| `_persist_outputs`                                                                                      | 294     | **MIGRATE**   | Calls `export.write_output(paths, ...)` (L309). Becomes result retention + metadata.                                                                                                                                      |
| `_finalize_failed`                                                                                      | 324     | **RESHAPE**   | Writes a failed manifest (L349). The rule "a failed Run keeps a usable error record" survives; the directory does not.                                                                                                    |
| `_frames_by_slot`, `_input_metadata`, `_unexpected_slot_warnings`, `_now`, `_elapsed_ms`, `_human_list` | 358–428 | **UNTOUCHED** | Pure, except `_input_metadata` populating `stored_filename` (see §4.4).                                                                                                                                                   |

### 3.7 `backend/app/api/runs.py`

| Endpoint / symbol                | Line     | Class         | Note                                                                                                                                                                       |
| -------------------------------- | -------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/runs`                 | 43       | **RESHAPE**   | `async with request.form()` then `PendingUpload(..., stream=value.file)` (L70–72). Becomes `await value.read()` → bytes. The form-field-per-slot contract must not change. |
| `GET /api/runs/{run_id}`         | 77       | **RESHAPE**   | `storage.read_manifest` → Run Store.                                                                                                                                       |
| `GET .../preview`                | 88       | **RESHAPE**   | `storage.run_paths(...)` (L108) → Run Store.                                                                                                                               |
| `GET .../download/csv` / `/xlsx` | 121, 127 | **MIGRATE**   | `FileResponse` from `export_artifact` (L178–191). Becomes an in-memory `Response` with `Content-Type` / `Content-Disposition` (6F.6).                                      |
| `_require_output`                | 138      | **UNTOUCHED** | Answers from the manifest, never from the filesystem.                                                                                                                      |
| `_DOWNLOAD_MEDIA_TYPES`          | 33       | **UNTOUCHED** | Correct media types for both formats.                                                                                                                                      |

### 3.8 Modules with **no** filesystem dependency

These are the Phase 0/1–5 components Phase 6 must leave alone:

- `backend/app/actions/base.py` — the Action contract. Already DataFrame-first.
- `backend/app/actions/registry.py` — registry and lookups.
- `backend/app/actions/exact_duplicate_remover.py` — see §5.
- `backend/app/actions/product_master_builder.py` — see §5.
- `backend/app/api/actions.py` — `GET /api/actions`.
- `backend/app/models/schemas.py` — no path is ever built or read. Two fields
  _describe_ the old model (see §4.4).
- `backend/app/errors.py` — the error taxonomy. `MissingArtifactError` is
  filesystem-flavoured in wording only; its code and status stay valid for "the
  result is no longer held in memory", which is exactly the V1 restart case.
- `backend/app/main.py` — app composition, CORS, the single error handler,
  `/health`. The one path (`reload_dirs`) is dev-server configuration.

### 3.9 Frontend

No frontend file touches a server filesystem concept. The frontend's Phase 6
work is a _network_ change (6G), not a filesystem one.

| Location                          | What                                                                                                                                                                          | Class                                         |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `src/lib/api.js` L13, L22–24      | `DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"` and `API_BASE_URL` from `NEXT_PUBLIC_API_BASE_URL`. The **only** place in the browser bundle that knows the FastAPI address. | **MIGRATE** (6G.1/6G.2 → `/forge-api`)        |
| `src/lib/api.js` L62, L78, L83    | The three endpoint paths (`/api/actions`, `/api/runs`, `/health`).                                                                                                            | **RESHAPE** — prefix changes, paths do not    |
| `next.config.mjs`                 | No `rewrites()` today.                                                                                                                                                        | **MIGRATE** — 6G.3 adds the proxy             |
| `.env.example` L14–17             | Documents `NEXT_PUBLIC_API_BASE_URL` as the direct FastAPI URL.                                                                                                               | **MIGRATE** — 6G                              |
| `package.json` `dev:web`          | `next dev --hostname 127.0.0.1`                                                                                                                                               | **MIGRATE** — 6G.6 needs a LAN-exposable host |
| `src/components/**`, `src/app/**` | Consume Action metadata and the manifest only. No URL, no path.                                                                                                               | **UNTOUCHED**                                 |

The single frontend field read from a Run today is `manifest.action.name`
(`RunStatus.jsx`). Everything else in the manifest is currently unused by the
browser, so 6E can extend the UI without breaking anything already shipped.

### 3.10 Tests

`backend/tests/` holds 311 tests. Coupling to the on-disk model, by module:

| Module                            | Tests | Filesystem coupling                                                                                                                           |
| --------------------------------- | ----: | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_storage.py`                 |    57 | **TEST-COUPLED** — the module under test disappears                                                                                           |
| `test_runs_api.py`                |    46 | Mixed — endpoint contracts survive, on-disk assertions do not                                                                                 |
| `test_product_master_builder.py`  |    34 | Mixed — accuracy tests are frame-level; negative tests go through the pipeline                                                                |
| `test_actions.py`                 |    30 | **UNTOUCHED** — no filesystem                                                                                                                 |
| `test_runner.py`                  |    28 | **TEST-COUPLED** — asserts directories, manifests on disk                                                                                     |
| `test_parser.py`                  |    26 | **TEST-COUPLED** — every case writes a `tmp_path` file first                                                                                  |
| `test_exact_duplicate_remover.py` |    21 | Mixed — as above                                                                                                                              |
| `test_action_round_trip.py`       |    17 | Mixed — reads generated export files back                                                                                                     |
| `test_preview.py`                 |    15 | **TEST-COUPLED** — writes Parquet, then previews it                                                                                           |
| `test_api.py`                     |    14 | **UNTOUCHED**                                                                                                                                 |
| `test_schemas.py`                 |    12 | **UNTOUCHED**                                                                                                                                 |
| `test_export.py`                  |    11 | **TEST-COUPLED** — asserts three files exist                                                                                                  |
| `conftest.py`                     |     — | `runs_dir` / `run_paths` fixtures redirect `config.RUNS_DIRECTORY`; both disappear with the model                                             |
| `helpers.py`                      |     — | `csv_bytes` / `xlsx_bytes` already build fixtures **in memory** and need no change; `upload()` / `upload_file()` reshape with `PendingUpload` |

`tests/helpers.py` and `tests/fixtures/` already satisfy build plan 6H.8
("keep test data synthetic") and 6H.1 — no `.csv`/`.xlsx` blob is committed and
no OS file picker is involved anywhere in the suite.

### 3.11 Non-runtime filesystem references — leave alone

- `.gitignore` — `data/runs/*` / `!data/runs/.gitkeep`. Harmless once the
  directory is unused; removing it is optional Phase 6I cleanup.
- `data/runs/.gitkeep` — the tracked placeholder.
- `scripts/dev-backend.sh` — locates `backend/.venv` and `cd`s to `backend/`.
- `pyrightconfig.json`, `jsconfig.json` — editor/tooling paths.
- `docs/*.md` — prose.

---

## 4. Public contracts (frozen)

These are what Phase 6B–6I must preserve. Each row is pinned by a test in
`backend/tests/test_contract_freeze.py`.

### 4.1 Action identity and metadata

| Action                    | Version | Input slot               | Accepted        | Required columns                                                                       | Output              | Metrics keys                                                  |
| ------------------------- | ------- | ------------------------ | --------------- | -------------------------------------------------------------------------------------- | ------------------- | ------------------------------------------------------------- |
| `exact_duplicate_remover` | `1.0.0` | `source_file` (required) | `.csv`, `.xlsx` | —                                                                                      | `deduplicated_data` | `input_rows`, `output_rows`, `duplicates_removed`             |
| `product_master_builder`  | `1.0.0` | `sales_file` (required)  | `.csv`, `.xlsx` | `SKU`, `Vintage`, `Supplier`, `Producer`, `Selection`, `Volume` (exact, in that order) | `product_master`    | `input_rows`, `output_rows`, `duplicate_product_rows_removed` |

Both outputs declare `formats = ("csv", "xlsx")`. Registration order is
`exact_duplicate_remover` then `product_master_builder`, and that order is what
`GET /api/actions` returns and what the selector renders.

### 4.2 Registry and Action contract

- `ActionRegistry.register()` / `.list_actions()` / `.get_action()`, plus the
  module-level `list_actions()` / `get_action()` reading `ACTION_REGISTRY`.
- `get_action()` returns `None` for an unknown ID and never guesses a near match.
- `register()` raises `DuplicateActionIdError` (a `ValueError`) rather than
  overwriting, and `ValueError` on a blank ID.
- `Action` declares `id`, `version`, `name`, `description`, `inputs`, `outputs`
  and implements `run(inputs) -> ActionResult`; `definition()` and `validate()`
  are provided by the base class.
- `ActionResult(outputs: Mapping[str, pl.DataFrame], metrics: dict)`.
- `Action.run` receives inputs **keyed by input slot ID**.

### 4.3 HTTP surface

    GET  /health
    GET  /api/actions
    POST /api/runs                                             multipart: action_id + one file field per slot ID
    GET  /api/runs/{run_id}
    GET  /api/runs/{run_id}/outputs/{output_id}/preview         ?offset=&limit=
    GET  /api/runs/{run_id}/outputs/{output_id}/download/csv
    GET  /api/runs/{run_id}/outputs/{output_id}/download/xlsx

Phase 6G changes the _browser-side prefix_ (`/forge-api/...`), not these
server-side paths.

### 4.4 Response and manifest shapes

Field names, exactly as they are today:

- `ActionDefinition` — `id`, `version`, `name`, `description`, `inputs`, `outputs`
- `ActionInput` — `id`, `label`, `description`, `required`, `accepted_extensions`, `required_columns`
- `ActionOutput` — `id`, `label`, `description`, `formats`
- `ActionListResponse` — `actions`
- `ValidationIssue` — `code`, `message`, `details`, `slot_id`
- `ValidationSummary` — `passed`, `errors`, `warnings`
- `RunManifest` — `schema_version`, `run_id`, `status`, `action`, `created_at`,
  `started_at`, `completed_at`, `duration_ms`, `inputs`, `validation`,
  `outputs`, `metrics`, `error`
- `ActionReference` — `id`, `version`, `name`
- `InputMetadata` — `slot_id`, `original_filename`, `stored_filename`,
  `file_size_bytes`, `extension`, `parser_engine`, `worksheet`, `row_count`,
  `column_count`, `columns`
- `OutputMetadata` — `id`, `label`, `row_count`, `column_count`, `columns`, `formats`
- `RunError` — `code`, `message`, `details`
- `PreviewResponse` — `run_id`, `output_id`, `columns`, `rows`, `offset`,
  `limit`, `total_rows`; rows are **positional lists** aligned to `columns`
- `RunStatus` — `"running"`, `"succeeded"`, `"failed"`
- `MANIFEST_SCHEMA_VERSION == 1`

**Two fields describe the old model and need a decision in 6B/6D, not silent
removal:**

1. `InputMetadata.stored_filename` — the generated on-disk name (`source.csv`).
   Nothing is stored in V1. Options: drop it (a manifest schema change, so bump
   `MANIFEST_SCHEMA_VERSION`), or keep it as the logical name of the in-memory
   input. It is not read by the frontend today.
2. `RunManifest`'s docstring names `data/runs/<run-id>/manifest.json` as its
   home. Prose only; correct it when the writer goes.

Also note `MANIFEST_SCHEMA_VERSION` itself: if the shape changes
incompatibly, the constant exists precisely to be bumped.

### 4.5 Error taxonomy — code → HTTP status

| Code                    | Status | Raised for                                              |
| ----------------------- | -----: | ------------------------------------------------------- |
| `INTERNAL_ERROR`        |    500 | base `WorkbenchError`                                   |
| `INVALID_REQUEST`       |    400 | malformed request (no `action_id`; bad offset/limit)    |
| `UNKNOWN_ACTION`        |    404 | unregistered Action ID                                  |
| `UNKNOWN_RUN`           |    404 | unknown or malformed Run ID                             |
| `UNKNOWN_OUTPUT`        |    404 | Run has no such output, or not available in that format |
| `MISSING_ARTIFACT`      |    404 | the result/export is no longer available                |
| `FILE_TOO_LARGE`        |    413 | over `MAX_UPLOAD_BYTES`                                 |
| `INVALID_INPUT`         |    422 | base input-validation error                             |
| `MISSING_INPUT`         |    422 | required slot absent                                    |
| `UNSUPPORTED_EXTENSION` |    422 | extension the slot does not accept                      |
| `PARSE_ERROR`           |    422 | unreadable CSV/XLSX                                     |
| `AMBIGUOUS_WORKBOOK`    |    422 | more than one data worksheet                            |
| `EMPTY_DATASET`         |    422 | zero data rows                                          |
| `MISSING_COLUMNS`       |    422 | exact required-column comparison failed                 |
| `VALIDATION_FAILED`     |    422 | several validation issues at once (`details.issues`)    |
| `ACTION_FAILED`         |    500 | the Action raised on valid input                        |

Error body shape, unchanged:
`{"error": {"code": ..., "message": ..., "details": {...}}}`. A traceback is
never in it.

### 4.6 Behavioural contracts

- Run ID is the canonical string form of a `uuid4` (build plan 6B.7: preserve).
- Column comparison is exact and case-sensitive.
- `unique(keep="first", maintain_order=True)` semantics — first occurrence kept,
  original order preserved, column order preserved.
- Nothing is trimmed, re-cased, normalised or fuzzily matched; accents survive.
- Preview: default 100, maximum 500, an over-large limit is **refused (400)**,
  not clamped.
- A submitted field the Action does not declare is an `UNEXPECTED_INPUT`
  **warning**, never a failure.
- A failed Run still records `status = failed`, the structured error and the
  full validation error list.
- Every slot problem in one request is reported together, not one at a time.
- API responses contain no filesystem paths.

### 4.7 Frontend contract

- `FormData`: `action_id` plus one file field named with the Action's own slot
  ID, never renamed in the browser.
- `ApiError` normalises `{error: {code, message, details}}`, expanding
  `details.issues` into one issue per check.
- The UI reads `manifest.action.name` and, from `GET /api/actions`, `id`,
  `name`, `version`, `description`, `inputs[].{id,label,description,required,accepted_extensions,required_columns}`.
- `data-workbench-state` carries the seven build-plan §30 states.

---

## 5. Action classification (Phase 6A item 4)

Every currently registered Action was inspected.

| Action                    | Classification           | Evidence                                                                                                                                                                                                                        |
| ------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `exact_duplicate_remover` | **DataFrame-compatible** | `run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult`. Module imports only `polars`, `app.actions.base` and `app.models.schemas`. No `open`, no `Path`, no `pathlib`/`os`/`io` import, no call into `app.services.*`. |
| `product_master_builder`  | **DataFrame-compatible** | Identical: same signature, same imports, no filesystem symbol anywhere in the module.                                                                                                                                           |

**There are zero filesystem-coupled Actions.** Both receive parsed DataFrames
keyed by slot ID and return DataFrames keyed by output ID. Build plan 6D.2
("refactor filesystem-coupled Actions") therefore has **no work to do on the
Actions themselves** — 6D is entirely about the runner, parser and export
plumbing around them.

This is pinned by `test_contract_freeze.py`, which asserts the classification
structurally (module imports) _and_ behaviourally (each Action executes
correctly with `config.RUNS_DIRECTORY` pointed at a path that does not exist).

---

## 6. Tests protecting existing behaviour (Phase 6A item 5)

Phase 6A added `backend/tests/test_contract_freeze.py`. It is deliberately
**filesystem-independent**, so it must keep passing unchanged through 6B–6I and
is the regression signal for the whole migration. It covers the five areas the
build plan names:

| Area                    | What is pinned                                                                                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Action registration     | Registered inventory, IDs, versions, names, registration order, uniqueness; `get_action` miss behaviour; duplicate-ID rejection; blank-ID rejection       |
| Action input validation | Slot IDs, labels, `required`, accepted extensions, required columns (exact tuple and order) for both Actions                                              |
| Action execution        | `run()` receives frames keyed by slot ID, returns `ActionResult` with a frame per declared output ID, and reports exactly its documented metric keys      |
| Deterministic output    | Repeat execution produces identical frames and identical metrics; first-occurrence order, column order, accents, blanks and near-duplicates all preserved |
| Error handling          | Full code → HTTP-status table, `as_response_body()` shape, `RunValidationError` single-issue vs multi-issue behaviour, no traceback in any rendered error |
| (plus) Public surface   | HTTP route inventory, schema field names, `RunStatus` values, `MANIFEST_SCHEMA_VERSION`, preview limits, `GET /api/actions` payload                       |
| (plus) DataFrame-first  | Both Actions classified structurally and behaviourally, as in §5                                                                                          |

The 311 pre-existing tests were **preserved unchanged**; none was weakened,
skipped or deleted.

---

## 7. Migration list — what changes, what does not

### 7.1 Components requiring modification

| Component                                                                                                                                                                                         | Phase    | Change                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/storage.py`                                                                                                                                                                             | 6B       | Replaced by a logical Run model + `RunStore` / `InMemoryRunStore`. Keep `new_run_id`, `parse_run_id`, `extension_of`, `display_filename`, `_human_size`. |
| `services/runner.py`                                                                                                                                                                              | 6B, 6D   | Orchestrate through the Run Store; stage logic and failure contract preserved.                                                                           |
| `api/runs.py` (POST)                                                                                                                                                                              | 6C       | `UploadFile` → bytes; keep the slot-named multipart contract.                                                                                            |
| `services/parser.py`                                                                                                                                                                              | 6C       | `parse_tabular_file(payload: bytes, extension)`; fastexcel gets `bytes`, openpyxl gets `BytesIO`.                                                        |
| `config.py`                                                                                                                                                                                       | 6B/6I    | `DATA_DIRECTORY` / `RUNS_DIRECTORY` become unused; `MAX_UPLOAD_BYTES` stays.                                                                             |
| `services/runner.py` `_persist_outputs`                                                                                                                                                           | 6D/6E    | Retain result frames in the run instead of writing artifacts.                                                                                            |
| `services/preview.py` `read_preview`                                                                                                                                                              | 6E       | Slice the retained result frame; keep the limit rules.                                                                                                   |
| `models/schemas.py`                                                                                                                                                                               | 6B/6D/6E | Decide `InputMetadata.stored_filename`; add result/audit metadata fields 6E.1/6E.5 requires.                                                             |
| `services/export.py`                                                                                                                                                                              | 6F       | In-memory CSV/XLSX generation; drop Parquet.                                                                                                             |
| `api/runs.py` (downloads)                                                                                                                                                                         | 6F       | `FileResponse` → in-memory response with the `forgexl-<action>-<timestamp>` filename convention.                                                         |
| `src/lib/api.js`, `next.config.mjs`, `.env.example`, `package.json`                                                                                                                               | 6G       | Same-origin `/forge-api` + Next.js rewrite + LAN-exposable dev server.                                                                                   |
| `src/components/workbench/*`                                                                                                                                                                      | 6E       | Add results/metrics/preview/export UI on the existing components. Do not rebuild them.                                                                   |
| `tests/conftest.py`, `tests/helpers.py`                                                                                                                                                           | 6B–6H    | Replace the `runs_dir` / `run_paths` fixtures; `csv_bytes` / `xlsx_bytes` already work as-is.                                                            |
| `test_storage.py`, `test_export.py`, `test_preview.py`, `test_runner.py`, and the on-disk parts of `test_runs_api.py`, `test_parser.py`, `test_action_round_trip.py`, the two Action test modules | 6B–6H    | Rewritten against the new runtime. **Rewrite, never delete to make the suite green.**                                                                    |
| `README.md`, `docs/implementation-status.md`                                                                                                                                                      | 6I       | Document the final architecture and V1 persistence behaviour.                                                                                            |

### 7.2 Components that must remain untouched

| Component                                           | Why                                                                         |
| --------------------------------------------------- | --------------------------------------------------------------------------- |
| `actions/base.py`                                   | The Action contract is already DataFrame-first; 6D needs nothing from it.   |
| `actions/registry.py`                               | No filesystem; the registry contract is explicitly preserved by 6D.4.       |
| `actions/exact_duplicate_remover.py`                | DataFrame-compatible (§5).                                                  |
| `actions/product_master_builder.py`                 | DataFrame-compatible (§5).                                                  |
| `api/actions.py`                                    | `GET /api/actions` has no filesystem dependency.                            |
| `errors.py`                                         | The taxonomy is the public error contract.                                  |
| `main.py`                                           | Composition, CORS and the single error handler; only 6G may extend origins. |
| `services/parser.py` worksheet logic                | The build plan §17 ambiguity rule and its exact wording.                    |
| `services/preview.py` limit validation              | 100 / 500 / refuse-don't-clamp is public contract.                          |
| `test_actions.py`, `test_api.py`, `test_schemas.py` | Already filesystem-free; they must keep passing untouched.                  |
| `src/components/**`, `src/app/**`                   | No URL, no path. 6E extends; it does not rebuild.                           |

---

## 8. Conclusion

The Phase 6 migration is **narrower than it looks**. The filesystem is confined
to four service modules (`storage`, `export`, `preview`, and the plumbing in
`runner`), the `path`-typed edges of `parser`, and the download half of
`api/runs.py`. Everything the build plan calls the Action Engine — the base
contract, the registry and both registered Actions — is already DataFrame-first
and needs no change at all.

No architectural conflict was found. Nothing in Phase 6B–6I requires a new
dependency, a database, or a change to a build-plan technology choice.
