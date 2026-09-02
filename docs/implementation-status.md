# Implementation Status

Last Updated: 2026-08-30
Current Phase: None
Last Completed Phase: Phase 6E — Results, Preview, Metrics, and Audit Data

> **Build plan note.** `docs/build-plan.md` was revised in commit `259615d`
> ("changed build plan. Updated architecture"). Phase 6 is no longer
> "Results, Preview, Audit Summary, and Export UX" numbered 6.1–6.9; it is now
> **"Filesystem-Independent Runtime, Results, Export, and Testing"**, split
> into subphases **6A–6I**. The older Phase 6 scope survives inside 6E
> (results/preview/metrics/audit) and 6F (export). Entries written before that
> revision, and Known Issue 16 in particular, refer to the superseded
> numbering.

This file is the durable cross-thread project state required by
`docs/build-plan.md` §33. Every Phase must update it.
`docs/build-plan.md` remains the authoritative architectural source of truth.

---

## Completed

### Phase 0 — Repository Audit and Build Contract

- Read `docs/build-plan.md` in full (3,821 lines).
- `docs/implementation-status.md` did not exist; created by that session.
- Inspected repository state before making any change (`pwd`, `ls -A`,
  `find . -maxdepth 2 -type f`, `find . -maxdepth 3 -type d`, `git status`,
  `git branch --show-current`, `git log`, `git ls-files`, `git check-ignore`).
- Inspected `package.json`, `package-lock.json`, `jsconfig.json`,
  `next.config.mjs`, `postcss.config.mjs`, `eslint.config.mjs`, `.gitignore`,
  `README.md`, `AGENTS.md`, `CLAUDE.md`, and every file under `src/`.
- Verified installed tool versions (Node.js, npm, Python 3, Git).
- Reviewed `.gitignore` and added the ignore rules required by §0.5.
- No application code was written. Phase 1 was not started.

**Audit conclusions**

1. A Next.js App Router project already exists and matches the build plan's
   required stack: JavaScript (no TypeScript), Tailwind CSS, ESLint.
   It must not be recreated (Phase 1.1).
2. The project uses a `src/` directory (`src/app/`). Build plan §10 sketches a
   root-level `app/`, but Phase 1.1 states: _"Do not add a `src/` folder unless
   the repository already uses one. Prefer the simplest existing convention."_
   The repository already uses one, so `src/` is retained. See
   **Deviations From Build Plan**.
3. The frontend was still the unmodified Create Next App starter. Cleaning it
   up is Phase 1.2 and was deliberately not done in Phase 0.
4. No backend existed. No `backend/`, `data/`, `components/`, `lib/`, or
   `scripts/` directory existed yet.
5. `node_modules/` was not installed; no npm install had been run.
6. No architectural conflict with `docs/build-plan.md` was found. One minor
   configuration conflict was found and corrected (`.env.example` was being
   ignored by `.gitignore`).

---

### Phase 1 — Application Foundation and Local Runtime

All ten sub-steps were implemented and verified.

**1.1 Frontend foundation.** The existing Next.js 16.3.2 App Router project
was kept, not recreated. Confirmed App Router, plain JavaScript, Tailwind v4,
ESLint. `npm install` was run (367 packages, 0 vulnerabilities). No TypeScript
was introduced; the repository still contains zero `.ts`/`.tsx` files.

**1.2 Starter noise removed.** `src/app/page.js` was replaced with a minimal
page rendering "Local Data Workbench" and "Local data-processing proof of
concept." `src/app/layout.js` metadata was changed from `"Create Next App"` to
the project title/description. The five unreferenced Create Next App demo
assets in `public/` were deleted (verified unreferenced by grep first);
`public/.gitkeep` keeps the directory in place. `src/app/globals.css` now uses
the configured Geist font stack instead of the starter's hardcoded
`Arial, Helvetica, sans-serif`, which contradicted the font variables that
`layout.js` sets up. No Action UI was built.

**1.3 Backend virtual environment.** `backend/.venv` created with the local
Python 3.11.15. pip upgraded 24.0 → 26.2.1. Installed: fastapi, uvicorn,
python-multipart, polars, fastexcel, openpyxl, xlsxwriter, pytest, httpx.
Resolved versions pinned in `backend/requirements.txt`.

**1.4 Backend application.** `backend/app/main.py` defines a minimal FastAPI
app with `GET /health` returning `{"status": "ok"}`. No Action, Run, upload, or
registry code was written — that is Phase 2/3.

**1.5 CORS.** Exact origins only: `http://127.0.0.1:3000` and
`http://localhost:3000`. No wildcard. Methods limited to GET and POST;
credentials disabled.

**1.6 Backend configuration.** `backend/app/config.py` centralizes
`PROJECT_ROOT`, `DATA_DIRECTORY`, `RUNS_DIRECTORY`, `HOST`, `PORT`,
`MAX_UPLOAD_BYTES` (250 MB) and `ALLOWED_FRONTEND_ORIGINS`. No constant is
duplicated elsewhere: `main.py` and `scripts/dev-backend.sh` both take host and
port from this module.

**1.7 Environment files.** `.env.example` created, documenting
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` for the frontend and the
optional `FORGEXL_*` backend overrides. `.env.local` was **not** left in the
repository: the frontend falls back to `http://127.0.0.1:8000` when the
variable is unset, so no local env file is required. A temporary `.env.local`
was created during verification to prove the override path works, then removed.

**1.8 Combined development startup.** `npm run dev` starts both services
together through `concurrently` (added as a devDependency):

    dev      concurrently --names web,api ... "npm:dev:web" "npm:dev:api"
    dev:web  next dev --hostname 127.0.0.1 --port 3000
    dev:api  bash scripts/dev-backend.sh

`scripts/dev-backend.sh` refuses to start with a clear message if
`backend/.venv` is missing, then runs `python -m app.main` from `backend/`,
which calls `uvicorn.run(...)` using the host/port from `config.py`.
`npm start` was also pinned to `127.0.0.1` so no npm script binds to
`0.0.0.0`.

**1.9 Health display.** `src/components/BackendStatus.js` is a client component
that fetches `${NEXT_PUBLIC_API_BASE_URL}/health` directly from the browser
(not proxied through Next.js) and renders "Backend Connected" or
"Backend Unavailable", with a neutral "Checking backend…" state while the
request is in flight. The request is aborted on unmount.

**1.10 Verification.** See **Tests** below. Lint, production build, backend
import, `/health`, CORS behaviour, loopback binding and both frontend health
states were each verified by execution, not by inspection.

---

### Phase 2 — Backend Data Engine and Action Contract

All eight sub-steps were implemented and verified.

**2.1 Module structure.** Created `backend/app/actions/`, `models/`,
`services/`, `api/`, each with an `__init__.py` carrying a docstring that says
what belongs there. `services/` is intentionally empty of implementation —
Phase 3 fills it.

**2.2 Schemas.** `backend/app/models/schemas.py` defines every structure the
build plan lists, as Pydantic v2 models:

| Build plan concept       | Model                                                        |
| ------------------------ | ------------------------------------------------------------ |
| Action input             | `ActionInput`                                                |
| Action output definition | `ActionOutput`                                               |
| Action definition        | `ActionDefinition` (+ `ActionListResponse`)                  |
| validation issue         | `ValidationIssue` (+ `ValidationSummary`)                    |
| input metadata           | `InputMetadata`                                              |
| output metadata          | `OutputMetadata`                                             |
| Run manifest             | `RunManifest` (+ `ActionReference`, `RunStatus`, `RunError`) |
| preview response         | `PreviewResponse`                                            |

`ActionInput`, `ActionOutput` and `ActionDefinition` are `frozen=True`: they
are declared once as module-level constants and must not be mutated. The
manifest and preview models are not frozen because a Run rebuilds them as it
progresses. `MANIFEST_SCHEMA_VERSION = 1` is stamped automatically.

Action _execution_ was deliberately kept out of Pydantic, as build plan 2.2
permits: dataframes are never serialised directly, so `ActionResult` is a
plain frozen dataclass.

**2.3 Action contract.** `backend/app/actions/base.py` defines `Action`, an
`abc.ABC` whose subclasses declare `id`, `version`, `name`, `description`,
`inputs` and `outputs` as class attributes and implement `run()`. It also
provides `definition()` (builds the `ActionDefinition` the API returns) and an
overridable `validate()` hook matching the `definition / validate(...) /
run(...)` sketch in build plan §24. `ActionResult` carries the output
dataframes keyed by output ID plus the Action's metrics.

No plugin loader exists and nothing is ever executed from disk: Actions are
imported Python, as build plan 2.3 requires.

**2.4 Registry.** `backend/app/actions/registry.py` provides `ActionRegistry`
with `register()`, `list_actions()` and `get_action()`, a single application
instance `ACTION_REGISTRY`, and module-level `list_actions()` /
`get_action()` that read it. `get_action()` returns `None` for an unknown ID
and never falls back to a default or a near match. `register()` raises
`DuplicateActionIdError` (a `ValueError`) rather than overwriting, and raises
`ValueError` on a blank ID.

`ActionRegistry` is a class rather than a bare module dict specifically so
tests can build isolated registries instead of mutating and resetting global
state. There is no `if action_id == ...` chain anywhere.

**2.5 Placeholder Action.** `backend/app/actions/example_passthrough.py`
registers `example_passthrough` — the smallest Action that genuinely exercises
the contract, returning its input unchanged. It exists so `GET /api/actions`
could be verified against real data (2.7) rather than an empty list. It is
version `0.1.0` and its display name says "(Placeholder)". **Phase 4 must
delete this module** and register `exact_duplicate_remover` and
`product_master_builder` in its place. No transformation logic belonging to
either real Action was written.

**2.6 Actions API.** `backend/app/api/actions.py` exposes
`GET /api/actions` on an `APIRouter(prefix="/api")`, returning
`ActionListResponse`. `main.py` mounts it. `main.py` otherwise changed only
its module docstring and gained one import.

**2.7 Developer-level verification.** No frontend code was written or
modified. The endpoint was verified over real HTTP with `curl`, through the
generated OpenAPI schema, and by a real headless-Chromium `fetch()` issued
from `http://127.0.0.1:3000`. See **Tests** below.

**2.8 Tests.** 48 tests across three files, all passing. See **Tests**.

---

### Phase 3 — Upload, Parsing, Run Execution, Storage, and Export Pipeline

All sixteen sub-steps were implemented and verified.

#### Repository repair performed first

Phase 3 could not begin until a defect in the committed Phase 2 state was
fixed. Commit `90dd7e8` ("phase 2 complete") wrote the entire backend Python
package into **`src/app/`** — the Next.js App Router directory — instead of
`backend/app/`, and named `schemas.py` as `schema.py`. Consequences:

- `backend/app/` contained only `__init__.py`, `config.py` and the Phase 1
  `main.py`; `actions/`, `api/`, `models/` and `services/` were absent.
- The whole backend suite failed at collection with
  `ModuleNotFoundError: No module named 'app.models'` — 0 of 48 tests ran.
- Every intra-package import (`from app.models.schemas import ...`) was
  unresolvable, which is the source of the **"Import ... could not be
  resolved"** warnings reported at the start of this session.

This was a misplacement, not a design decision: the moved modules import
`from app import config`, which resolves only under `backend/`, and
`docs/implementation-status.md` already documented `backend/app/...` as their
location. The files were restored with `git mv` (history preserved), and
`src/app/main.py` — a strict superset of the Phase 1 `backend/app/main.py`,
adding only the router import and `include_router` — replaced it. Verified
immediately afterwards: **48 Phase 2 tests passed** and `npx pyright` reported
**0 errors**, resolving the reported import warnings. No file content was
edited during the repair; only locations changed.

**3.1 Storage service.** `backend/app/services/storage.py` owns Run UUIDs, the
`inputs/ working/ exports/` tree, upload preservation, atomic manifest writes
and artifact lookup by logical ID. `RunPaths` is the only thing that builds a
path; the API never supplies one. Slot and output IDs must match
`SAFE_ID_PATTERN` before they contribute to a path, and `parse_run_id()`
accepts only the canonical string form of a UUID — a traversal-shaped or
truncated ID raises `UnknownRunError` before the filesystem is touched.
`runs_directory()` reads `config.RUNS_DIRECTORY` at call time rather than at
import, so tests redirect it at one place and never touch the real
`data/runs`.

**3.2 Safe filenames.** Every upload is stored as `source<ext>` inside its own
slot directory. The client's filename is recorded as `original_filename`
metadata and is used for nothing else. `extension_of()` strips any directory
component before reading the suffix, so `../../evil.csv` yields `.csv`.

**3.3 Upload limit.** `store_upload()` copies in 1 MiB chunks and checks the
running total against `MAX_UPLOAD_BYTES`, raising `UploadTooLargeError` (413).
A partial file from a rejected upload is deleted before the error propagates.
Starlette's `max_part_size` was investigated and does **not** apply to file
parts (only to non-file fields), so the limit is enforced here; Starlette
spools file parts to disk, so an oversized upload never becomes a memory
error.

**3.4-3.6 Parser service.** `backend/app/services/parser.py` exposes
`parse_tabular_file(path, extension)` returning a `ParsedFile` (dataframe,
`parser_engine`, `worksheet`, and row/column/columns metadata).

- CSV via Polars. `try_parse_dates` is deliberately left off, so date-shaped
  text stays text and no value is silently retyped (§3.3).
- XLSX via fastexcel/calamine, read through `ExcelReader.load_sheet(...)`
  rather than `pl.read_excel`, because the latter defaults to
  `drop_empty_rows=True` / `drop_empty_cols=True` and would silently drop data
  (§3.3 forbids that).
- Worksheet ambiguity (§17): a sheet counts as a data sheet if it holds any
  cells. Exactly one -> used and recorded. Zero -> "contains no data". Two or
  more -> `AmbiguousWorkbookError` naming the sheets, with the message §17
  specifies. A header-only sheet counts as a valid dataset with zero rows.
- openpyxl is the compatibility fallback and the engine that actually
  succeeded is what the manifest records (§6.2). A _structural_ refusal —
  no data sheet, or several — is never retried with the fallback, since the
  fallback would reach the same conclusion.
- Both engines read stored values. Neither evaluates formulas nor runs macros.

**3.7 Generic validation.** The runner checks, per slot: required slot
present, extension accepted, file parsed, dataset non-empty, required columns
present. Column comparison is exact and case-sensitive — `Sales Person` is
reported against `Salesperson`, and `Sku` against `SKU`, rather than guessed.
All slot problems in one request are collected and reported together.

**3.8 Runner service.** `backend/app/services/runner.py` owns the whole
generic workflow. It is framework-independent: it takes `PendingUpload`
objects (a filename plus a readable stream), so the API and the tests drive
the identical pipeline. Actions reproduce none of it.

**3.9 Failed Runs.** Once a Run directory exists, every outcome writes a
manifest. A failure records `status = failed`, the structured error, the full
validation error list, and the inputs that were uploaded. The directory and
the preserved upload are retained. Verified on disk for `MISSING_INPUT`,
`UNSUPPORTED_EXTENSION`, `MISSING_COLUMNS`, `FILE_TOO_LARGE` and
`AMBIGUOUS_WORKBOOK`.

**3.10 Export service.** `backend/app/services/export.py` writes each output
as `working/<id>.parquet`, `exports/<id>.csv` and `exports/<id>.xlsx`. Polars
writes all three directly; the dataframe is never converted to Python objects
on the way out.

**3.11 Manifest writing.** Written at `running` and again at `succeeded` or
`failed`. `write_manifest()` writes a temporary file in the same directory,
`fsync`s it and `os.replace`s it over the destination, so an interrupted
process cannot leave half-written JSON. The temp file is removed in a
`finally`.

**3.12-3.16 Runs API.** `backend/app/api/runs.py` adds `POST /api/runs`,
`GET /api/runs/{run_id}`, the preview endpoint and the two download
endpoints. `POST` reads the multipart form through
`async with request.form()`, so uploaded streams stay open until the runner
has copied them. Files are submitted under their Action slot IDs, never as one
anonymous list. Downloads use `FileResponse` with a filename derived from the
output ID, not from the upload.

**Error boundary.** `backend/app/errors.py` defines the structured error
taxonomy and `main.py` registers one `WorkbenchError` handler that renders
`{"error": {code, message, details}}` with the matching status. Tracebacks are
logged locally and never returned. Status codes follow §22: 400 malformed
request, 404 unknown Action/Run/Output, 413 too large, 422 validation failure,
500 unexpected.

**No frontend file was created or modified in Phase 3.** The `npm run build`
route list is unchanged (`/` and `/_not-found`).

---

### Phase 4 — Proof Actions and Accuracy Tests

**Recorded retroactively during the Phase 5 session.** Phase 4 was implemented
and committed (`584fea3`, "added a couple premade 'example' actions and test
files for them") but the session that did the work did not update this file, so
the entry below records what the Phase 5 session **verified by execution**,
not a narrative of how it was built. See **Known Issues** item 15.

Verified present and passing at the start of Phase 5:

- `backend/app/actions/exact_duplicate_remover.py` implements build plan
  section 26: one `source_file` slot, no required columns,
  `unique(keep="first", maintain_order=True)` across every column, metrics
  `input_rows` / `output_rows` / `duplicates_removed`. No trimming, casing or
  normalisation.
- `backend/app/actions/product_master_builder.py` implements section 27: one
  `sales_file` slot requiring exactly `SKU`, `Vintage`, `Supplier`,
  `Producer`, `Selection`, `Volume`; selects those six in that fixed order,
  removes duplicate combinations, metrics `input_rows` / `output_rows` /
  `duplicate_product_rows_removed`.
- `backend/app/actions/example_passthrough.py` is **gone**, and
  `ACTION_REGISTRY` now holds exactly the two real Actions. Known Issue 8 is
  resolved.
- `backend/tests/fixtures/` exists, alongside `test_exact_duplicate_remover.py`,
  `test_product_master_builder.py` and `test_action_round_trip.py`.
- `cd backend && .venv/bin/python -m pytest` → **311 passed, 1 warning**
  (Phase 3 left 231; Phase 4 added 80).
- `GET /api/actions` returns both definitions with their input slots, required
  columns and outputs (4E).
- No frontend file had been modified: at the start of Phase 5 the only
  frontend files were the Phase 1 set, and `npm run build` still listed only
  `/` and `/_not-found`.

---

### Phase 5 — Dynamic Frontend Action Runner

All ten sub-steps were implemented and verified. This is the first Phase to
write frontend application code since Phase 1.

**5.1 API utility.** `src/lib/api.js` is the only module in the frontend that
knows a backend URL or an endpoint path. It exports `API_BASE_URL` (from
`NEXT_PUBLIC_API_BASE_URL`, falling back to `http://127.0.0.1:8000`, with
trailing slashes trimmed), `fetchActions()`, `createRun()`, `fetchHealth()` and
the `ApiError` class. Every request goes from the browser straight to FastAPI;
nothing is proxied through a Next.js Route Handler (build plan section 5).

`src/components/backend/BackendStatus.jsx` was refactored to call
`fetchHealth()` instead of holding its own copy of the base URL — it was the
one place a backend URL was still duplicated. Its rendered behaviour is
unchanged.

`src/lib/formatters.js` holds the three pure display helpers the upload slot
needs: `formatFileSize`, `fileExtension` (mirrors the backend's
`extension_of`, so `../../evil.csv` yields `.csv`) and `joinWithOr` (phrases a
list the same way the backend's own validation messages do).

**5.2 Loading Actions.** `ActionRunner` requests `GET /api/actions` on mount
with an `AbortController`, and renders three distinct outcomes: "Loading
Actions…", an "Actions Unavailable" panel carrying the reason, or the populated
interface.

**5.3 Action selector.** `ActionSelector.jsx` renders a native `<select>`
populated exclusively from the API response. Selecting an option stores the ID;
the full Action metadata object is resolved from the loaded list and drives
everything below it.

**5.4 Action description.** `ActionDescription.jsx` shows name, description and
`Version 1.0.0`. The Action ID is deliberately not displayed — it is an
internal identifier, not something a user needs (build plan 5.4).

**5.5 Dynamic input slots.** `ActionRunner` maps over `selectedAction.inputs`
and renders one `FileUploadSlot` per entry. The slot component receives the
input ID, label, description, `required` flag, `accepted_extensions` and
`required_columns` and renders from those alone. **There is no branch on any
Action ID anywhere in the frontend** — verified by grep and by the
extensibility test below.

**5.6 Drag-and-drop.** `FileUploadSlot.jsx` uses only native browser APIs: a
visually-hidden `<input type="file">` paired with a `<label>` that carries the
`onDragOver` / `onDrop` handlers, so clicking to browse, keyboard focus and
dropping all work with no upload dependency. The chosen file's name, extension
and formatted size are shown, with a **Remove** button; choosing another file
replaces the current one. The file input's value is cleared after each change
so the same file can be re-chosen after a removal.

**5.7 Client-side preliminary validation.** Before submission the UI confirms
an Action is selected, that every required slot holds a file, and that each
file's extension is one that slot accepts. A rejected file is not stored and
produces a per-slot message. This is convenience only — the backend re-checks
all of it and stays authoritative (verified: the backend still returns 422
`UNSUPPORTED_EXTENSION` when the same file is posted directly).

**5.8 Run submission.** `createRun()` builds `FormData`, appends `action_id`,
then appends each file under **the Action's own input slot ID**. Keys are never
renamed in the frontend. Confirmed on disk: Runs submitted through the browser
produced `inputs/source_file/source.csv` and `inputs/sales_file/source.xlsx`,
and the manifests recorded `slot_id` `source_file` / `sales_file`.

**5.9 Running state.** While a Run executes: the Action selector, every file
input and the Remove buttons are disabled, and the Run button is disabled and
reads "Processing…". A `useRef` guard blocks a second submission slipping
through between the click and the re-render. **No progress percentage is
displayed** — the real progress is unknown, so the indicator says
"Processing…" rather than inventing a number (build plan 5.9).

**5.10 Error display.** `ApiError` normalises the backend's error contract
(section 22) into a list of issues: a single failure becomes one issue; a Run
that failed several checks at once (`VALIDATION_FAILED` with
`details.issues`) becomes one issue per check. `RunStatus.jsx` renders each
issue's `message`, and lists `details.missing_columns` by name when present.
Nothing is ever stringified blindly — `IssueColumns` renders only entries that
are genuinely strings, so a differently-shaped `details` payload cannot become
`[object Object]`. Tracebacks never arrive (the backend does not send them) and
are never rendered.

**Frontend state model (build plan section 30).** `ActionRunner` derives one
of the seven named states — `loading_actions`, `idle`, `ready`, `running`,
`success`, `validation_error`, `server_error` — and publishes it as
`data-workbench-state` on its root element. A failed Run is classified as
`validation_error` for the statuses the backend uses for a problem with the
uploaded data (422, 413) and `server_error` otherwise, so a backend fault is
never blamed on the user's file.

**Run result presentation is deliberately minimal.** On success the UI confirms
"Run Successful" and names the Action. Metrics, validation summary, the
paginated preview, Run ID display, the export buttons and "Start New Run" are
Phase 6 (build plan 6.1-6.9) and were **not** built.

---

### Phase 6A — Compatibility Audit and Contract Freeze

All six items of build plan "Phase 6A" were carried out. Phase 6A is
**defensive**: it changed **no runtime behaviour**. Not one line of
`backend/app/**` or `src/**` was modified. Two files were added — an audit
document and a test module — and nothing else in the repository changed.

**1. Repository audit.** Every search the phase prescribes was run across the
whole repository (excluding `node_modules/`, `backend/.venv/`, `.git/`):
`data/`, `runs/`, `uploads/`, `inputs/`, `working/`, `exports/`,
`manifest.json`, `tmp/`, `temp/`, and `file_path` / `filepath` / `input_path` /
`output_path` / `run_path` / `export_path` / `Path(` / `open(` / `.write_*` /
`mkdir` / `unlink` / `os.*` / `is_file` / `FileResponse` / `pathlib`, plus the
frontend networking search for `localhost:8000` / `127.0.0.1:8000`. Every hit
was classified rather than assumed wrong, using five classes: **MIGRATE**,
**RESHAPE**, **KEEP**, **TEST-COUPLED**, **UNTOUCHED**.

**2. Filesystem dependencies by category.** All fifteen categories the build
plan lists were examined. The result, in short: the filesystem is confined to
four service modules (`storage.py`, `export.py`, `preview.py`, and the plumbing
inside `runner.py`), the `Path`-typed edges of `parser.py`, and the download
half of `api/runs.py`. `api/actions.py`, `actions/*`, `models/schemas.py`,
`errors.py` and `main.py` have no request-path filesystem dependency at all.
**No frontend file touches a server filesystem concept** — the frontend's
Phase 6 work is the same-origin network change of 6G, not a filesystem change.

**3. Public contracts identified and frozen.** Action IDs, versions, names,
registration order, input-slot IDs, accepted extensions, required columns,
output IDs/labels/formats, metric key names, the Action/registry contract, the
seven HTTP routes, the field names of all thirteen Pydantic models, the full
error-code → HTTP-status table, `RunStatus` values, `MANIFEST_SCHEMA_VERSION`,
the preview limits, the Run ID convention and the frontend's `FormData`
contract. Recorded in `docs/phase-6a-compatibility-audit.md` §4 and pinned by
tests.

**4. Path-coupled Actions: there are none.** Both registered Actions were
inspected and classified **DataFrame-compatible**:

| Action                    | Classification       |
| ------------------------- | -------------------- |
| `exact_duplicate_remover` | DataFrame-compatible |
| `product_master_builder`  | DataFrame-compatible |

Both declare `run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult`,
and neither module imports `os`, `io`, `pathlib`, an Excel engine or any
`app.services.*` module, nor calls `open()`. Build plan **6D.2 ("refactor
filesystem-coupled Actions") therefore has no work to do on the Actions
themselves** — 6D is entirely about the runner, parser and export plumbing
around them.

**5. Tests protecting existing behaviour.** Added
`backend/tests/test_contract_freeze.py` — **84 tests**, all passing. It is
deliberately filesystem-independent (it never uses the `runs_dir` /
`run_paths` fixtures, which disappear with the on-disk model), so it must keep
passing unchanged through 6B–6I and is the regression signal for the whole
migration. It covers the five areas the build plan names — Action registration,
Action input validation, Action execution, deterministic output, error handling
— plus the public HTTP/schema surface and the DataFrame-first classification of
item 4. The 311 pre-existing tests were **preserved unchanged**; none was
weakened, skipped or deleted.

**6. No migration performed.** No abstraction was introduced, no in-memory
upload path was scaffolded, no Run Store was created. Phase 6B was not begun.

**Feasibility verified rather than assumed.** Because a failure here would be
an architectural conflict to report rather than an implementation detail, the
in-memory capabilities 6C/6F depend on were checked by execution against the
pinned dependency versions:

| Capability                                                        | Result                                                                 |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `pl.read_csv(bytes)` / `pl.read_csv(BytesIO)`                     | works                                                                  |
| `fastexcel.read_excel(bytes)`                                     | works                                                                  |
| `fastexcel.read_excel(BytesIO)`                                   | **fails** — `InvalidParametersError: source must be a string or bytes` |
| `openpyxl.load_workbook(BytesIO, read_only=True, data_only=True)` | works                                                                  |
| `pl.DataFrame.write_csv(BytesIO)`                                 | works                                                                  |
| `pl.DataFrame.write_excel(workbook=BytesIO)`                      | works                                                                  |

Consequence for Phase 6C: the parser must hold the upload as `bytes` and pass
`bytes` to fastexcel, wrapping in `io.BytesIO` only for the openpyxl fallback.
**No architectural conflict exists and no dependency change is required.**

**Deliverable.** `docs/phase-6a-compatibility-audit.md` — the full inventory,
the contract freeze, the Action classification, and §7's explicit two-column
migration list: which components need modification (with the subphase that
touches each) and which completed Phase 0/1–5 components must remain untouched.

### Phase 6B — Introduce Runtime and Storage Abstractions

All seven items of build plan "Phase 6B" were implemented and verified. Phase
6B separates **what a Run is** from **where its state is kept**. Run state now
lives in a Run Store; `data/runs/<run-id>/manifest.json` is no longer written
or read. Uploads, Parquet and exports are still on disk — those are 6C, 6D and
6F. No frontend file was touched.

**6B.1 Logical Run model.** `backend/app/models/run.py` defines `Run`, a frozen
dataclass carrying the run ID, the `ActionReference` (Action ID, version and
name), `status`, `created_at`, `updated_at`, `started_at`, `completed_at`,
`duration_ms`, input metadata, the validation summary, output (result)
metadata, Action metrics and the error. **No field holds a filesystem path**,
and a test asserts that structurally rather than by inspection.

It is a dataclass rather than a Pydantic model for the reason Phase 2 gave for
`ActionResult`: this is runtime state, not API-facing data, and from 6D/6E it
will carry Polars frames that Pydantic cannot validate. Pydantic keeps its
place at the boundary — `Run.to_manifest()` renders the **unchanged**
`RunManifest`, so the API shape frozen in Phase 6A is now _derived from_
runtime state instead of _being_ it. `updated_at` is deliberately not in the
manifest: it is runtime bookkeeping, and adding a field would change a frozen
contract for no caller's benefit.

A Run is never edited in place. A stage derives the next state with
`with_changes(...)` — which stamps `updated_at` unless given one — and hands
that to the store, so a stored Run cannot be modified behind the store's back.

**6B.7 Run IDs preserved.** `new_run_id()` and `parse_run_id()` moved from
`services/storage.py` to `models/run.py` unchanged: the convention is still
`str(uuid.uuid4())`, still validated as the canonical string form of a UUID,
still raising `UnknownRunError` for a malformed, truncated or traversal-shaped
ID. They moved because run identity belongs to the Run, not to the filesystem
module that 6C-6I dismantles. `storage.py` imports both from there and still
uses them, so `storage.new_run_id` / `storage.parse_run_id` resolve exactly as
before and no existing call site changed.

**6B.2 Run Store abstraction.** `backend/app/services/run_store.py` defines
`RunStore`, an `abc.ABC` with exactly the five methods the build plan names:
`create_run(run)`, `get_run(run_id)`, `update_run(run)`, `delete_run(run_id)`
and `list_runs()`. Anything wider would leak the storage medium into the
callers the abstraction exists to protect, so a test asserts that those five —
and only those five — are abstract.

Semantics: `get_run` and `update_run` raise `UnknownRunError` (404, unchanged
contract) for an unknown or malformed ID; `create_run` raises
`DuplicateRunIdError` (a `ValueError`, mirroring `DuplicateActionIdError`)
rather than overwriting; `delete_run` returns a bool and treats an unknown or
malformed ID as "already gone" rather than an error; `list_runs` returns runs
oldest first.

**6B.3 `InMemoryRunStore`.** One dictionary in the backend process, guarded by
a `threading.Lock` because Uvicorn runs synchronous endpoints in a thread pool,
so two Runs really can touch the store at once. The lock protects the
check-then-write pairs; the Runs themselves are frozen values.

**6B.4 No persistent infrastructure.** Nothing was added — no PostgreSQL,
SQLite, Redis, Supabase, S3, DuckDB, ORM or migration tool. `package.json` and
`backend/requirements.txt` are byte-identical to their committed state. A test
parses the module's own imports and fails if any of eleven database, cache or
object-store packages appears.

**6B.5 Business logic depends on the interface.** `ACTION_REGISTRY` set the
convention and the store follows it: a single application instance,
`RUN_STORE`, plus module-level `create_run` / `get_run` / `update_run` /
`delete_run` / `list_runs` that read it at call time. The runner and the API
call those functions; neither ever touches a dictionary. A test proves
replaceability by implementing a second `RunStore`, assigning it, and asserting
the five calls land on it — which is exactly what a future `PersistentRunStore`
would do.

**6B.6 Run deletion.** Two levels, because a Run's state is not all in one
place yet. `run_store.delete_run(run_id)` forgets the record.
`runner.delete_run(run_id)` is the lifecycle-level call: it forgets the record
**and** removes the Run's directory through the new
`storage.delete_run_directory()`, so deleting a Run really releases the state
it holds rather than orphaning the user's uploaded file on disk. The directory
half disappears with the last of the Run's on-disk files in 6C/6F.
`delete_run_directory` refuses anything that is not a direct child of the runs
directory, so a traversal-shaped ID deletes nothing; four tests cover that.

**The runner orchestrates through the store.** `services/runner.py` keeps its
stage ordering, its validation logic and its failure contract exactly as
Phase 3 wrote them. What changed is the plumbing: it records the Run when it
starts, then hands the store a new state after inputs are recorded and again at
success or failure. `storage.write_manifest()` is gone from all three places.
`RunOutcome` now carries the `Run`, with `.manifest` as a property rendering
it, so every existing caller and test that reads `outcome.manifest` is
unaffected. `execute_run` also gained one small robustness improvement:
`storage.create_run()` (the directory tree) moved inside the failure boundary,
so a directory that cannot be created now fails the Run cleanly instead of
escaping as an unstructured 500.

**The API serves run state from the store.** `api/runs.py` replaced its three
`storage.read_manifest(run_id)` calls with `run_store.get_run(run_id)`.
`GET /api/runs/{run_id}`, the preview endpoint and both downloads behave
identically — same responses, same status codes, same 404 for malformed,
unknown and traversal-shaped IDs. **No route was added**: build plan 6B does
not ask for one, and the frozen route inventory would have caught it.

**What this changes for a user.** Run state is process memory in V1, so
restarting the backend clears run history — build plan Phase 6 rules 14 and 15
explicitly allow this, and it was verified by execution (below). Nothing in the
current UI regressed: the frontend never calls `GET /api/runs/{id}`, and a full
browser run still works.

**Known Issue 20 (`InputMetadata.stored_filename`) is not yet due.** 6B does
not change upload handling: every upload is still written to
`inputs/<slot-id>/source<ext>`, so the field still records a real generated
filename and the manifest shape is still correct. The decision belongs to 6C,
where the upload stops reaching disk. Deciding it early would have meant
changing a frozen schema for a reason that does not exist yet.

---

### Phase 6C — In-Memory Upload and Spreadsheet Parsing

All nine items of build plan "Phase 6C" were implemented and verified. Uploaded
spreadsheets no longer reach the filesystem at any point: the bytes go from the
multipart request into a memory buffer, from there into a Polars DataFrame, and
the buffer is released as soon as the frame exists. No frontend file was
touched.

**Two repository defects were repaired first.** Neither was caused by this
phase; both had to be fixed before Phase 6C could begin, and both are recorded
under **Known Issues** (31 and 32).

1. **Phase 6B's backend code had been committed into `src/app/`** — the Next.js
   App Router directory — instead of `backend/app/`, exactly as Phase 2 had been
   (Known Issue 10). `backend/app/` therefore had no `models/run.py` and no
   `services/run_store.py`, and `api/runs.py`, `services/runner.py` and
   `services/storage.py` were still their Phase 3/6A versions. The whole suite
   failed at collection — **0 of 457 tests ran**
   (`ImportError: cannot import name 'run_store' from 'app.services'`). Repaired
   with `git mv` (history preserved, no file content edited); the suite then
   reported **457 passed**.
2. **`backend/tests/test_runner.py` had been overwritten with a byte-identical
   copy of `tests/test_run_store.py`**, destroying the entire runner pipeline
   test module. The 457 count was inflated by 34 duplicated Run Store tests, so
   real unique coverage was **423**, with Phase 3's runner tests gone. The
   Phase 6A version (28 tests) was recovered from commit `f481552` and carried
   forward through 6B (read the Run Store, not `manifest.json`) and 6C.

**6C.1 Named input slots preserved.** The multipart contract is unchanged:
`action_id` plus one file field per slot ID. `PendingUpload` still keys on the
slot ID and `_read_and_check_slots` still walks `action.inputs`, so an upload
stays bound to its logical slot from the request to the Action. A test drives a
two-slot Action with a CSV in `first` and an XLSX in `second` and asserts each
frame arrived where it belongs.

**6C.2/6C.3 Uploads read into memory.** `storage.store_upload()` is replaced by
`storage.read_upload()`, and `StoredUpload` by `LoadedUpload`, which carries
`payload: bytes` instead of `path: Path` and derives `size_bytes` by counting
the bytes received rather than trusting a header. Nothing is written and
nothing is reopened. The consequences were followed through rather than left
half-done: `RunPaths.inputs` and `RunPaths.input_directory()` are gone, the
`inputs/` directory is no longer created, and `_INPUTS_DIRNAME` is gone with
them. Verified over real HTTP across twelve Runs: **0 `inputs/` directories, 0
`source.*` files, 0 `manifest.json` files** written.

Because a slot ID no longer contributes to any path, the `_safe_id` check that
guarded `input_directory` disappeared with it. That is a reduction in attack
surface, not a loss of one: the test that asserted the guard now asserts the
stronger fact — a hostile slot ID (`../escape`, `a/b`) is carried as a
dictionary key and writes nothing anywhere.

**6C.4 Basic upload properties validated.** Per slot: required slot present,
extension accepted, **file not empty**, and size within `MAX_UPLOAD_BYTES`.
The required _number_ of inputs is what the per-slot required check already
enforces. All slot problems are still collected and reported together — a test
posts an unsupported extension in one slot and an empty file in the other and
asserts both come back in one response.

**The limit is enforced during the read, not after it.** `read_upload` measures
each chunk _before_ keeping it, so the buffer never grows past the limit and an
oversized upload cannot become a memory error. This is the point where build
plan 3.3's guarantee could quietly have been lost by moving to memory, so it is
pinned by a test that feeds a 64 MB stream against a 2 MB limit and asserts the
stream was read at most one chunk past the limit. Verified over HTTP: a
168-byte file against a 64-byte limit returns **413 FILE_TOO_LARGE**.

**6C.5 The MIME type is not trusted.** It never was and still is not read:
`PendingUpload` carries only a filename and a stream. The extension chooses the
reader; parsing decides the outcome. Verified over HTTP both ways — a valid CSV
declared `application/octet-stream` succeeds, and workbook bytes named
`.csv` and declared `text/csv` are refused with `PARSE_ERROR`.

**6C.6 CSV parsed from memory.** `pl.read_csv(payload)` reads the bytes
directly. `try_parse_dates` is still off, so date-shaped text is still text.

**6C.7 XLSX parsed from memory.** `fastexcel.read_excel(payload)` takes the
bytes unwrapped; `openpyxl.load_workbook(io.BytesIO(payload), ...)` takes a
buffer. This asymmetry is not arbitrary — Phase 6A proved by execution that
fastexcel accepts `bytes` but **rejects** `BytesIO`, and this session
re-verified it against the pinned versions before writing any code. **No
workbook is written out to be reopened**, which a test pins by running the
fallback engine with the process CWD redirected to an empty directory and
asserting the directory stays empty.

The worksheet-selection logic, the engine-fallback recording and the section 17
refusal wording are **unchanged**. The refusal message is now asserted verbatim
by its own test, because it is public contract.

**6C.8 Input metadata preserved.** Every field build plan 6C.8 lists is still
recorded: original filename, input slot, extension, byte size, worksheet,
row count, column count, column names and parser engine. `InputMetadata` is
byte-for-byte the same shape — see the `stored_filename` decision below.

**6C.9 Understandable errors.** All eight categories the build plan names are
distinguishable, verified over real HTTP:

| Build plan 6C.9 case       | Code                    | Verified message                                       |
| -------------------------- | ----------------------- | ------------------------------------------------------ |
| Missing required input     | `MISSING_INPUT`         | "Sales File is required."                              |
| Unsupported format         | `UNSUPPORTED_EXTENSION` | "Sales File must be .csv or .xlsx. sales.json is not…" |
| Empty file                 | `EMPTY_FILE` **(new)**  | "empty.csv is empty."                                  |
| Unreadable CSV             | `PARSE_ERROR`           | "The uploaded CSV file could not be read…"             |
| Unreadable XLSX            | `PARSE_ERROR`           | "The uploaded Excel workbook could not be read…"       |
| Malformed workbook         | `PARSE_ERROR`           | both engines named in `details`                        |
| Expected worksheet missing | `AMBIGUOUS_WORKBOOK`    | the section 17 wording, verbatim                       |
| File exceeds upload limit  | `FILE_TOO_LARGE`        | "sales.csv is larger than the 64 bytes upload limit."  |

`EmptyUploadError` / `EMPTY_FILE` (422) is the one addition to the error
taxonomy. Before it, a zero-byte upload, an unreadable file and a header-only
file all reported `PARSE_ERROR` or `EMPTY_DATASET` in ways that told the user
little; the three are now distinct, and a test asserts that zero bytes and a
header-only file produce different codes. Adding a class does not disturb the
Phase 6A freeze, which pins the codes and statuses of the errors it lists
rather than asserting the set is closed. **No traceback reaches the browser** —
grepped for in every error body.

**The `stored_filename` decision (Known Issues 20 and 29), resolved: kept and
redefined.** The field records the generated name an input is known by, derived
from its extension alone. Nothing is written under it any more, but it is still
the evidence for the rule build plan section 16 actually states — that the
client's filename never became a name the application used. Dropping it was the
alternative, and it would have changed a manifest shape frozen in Phase 6A,
forced `MANIFEST_SCHEMA_VERSION` to 2, and required editing
`test_contract_freeze.py` — the one module whose value depends on passing
unchanged through the whole migration — for a field no caller reads. Build plan
Phase 6 rule 5 ("schemas must be preserved wherever possible") decides it.
`MANIFEST_SCHEMA_VERSION` stays **1**, and the docstrings in
`models/schemas.py` and `services/storage.py` now say precisely what the field
means.

**`storage.create_run()` still creates the run directory.** `working/` and
`exports/` are still needed until 6D/6F generate those in memory, so the call
stays exactly where Phase 6B put it — inside the failure boundary. A Run that
fails validation therefore leaves two empty directories behind. That is interim
state which disappears with 6F, and moving the call is not something 6C asks
for. Recorded as Known Issue 33.

**`test_contract_freeze.py` passed unchanged, again.** 84 tests, file not
modified — the Phase 6A freeze has now survived both 6B and 6C untouched, which
is the whole reason it exists.

---

### Phase 6D — Convert Action Execution to DataFrame-First Processing

All eight items of build plan "Phase 6D" were implemented and verified. The
Action Engine now consumes and produces **DataFrames end to end**: a Run reads
its uploads into memory (6C), parses them into named frames, executes the
Action, and keeps the result frames in the Run. **A Run touches the filesystem
at no point** — verified by execution over real HTTP across nine Runs, after
which the data directory held zero files and zero directories. No frontend file
was touched.

**Two repository defects were repaired first.** Neither was caused by this
phase, and the committed state could not run without the repair. Both are
recorded under **Known Issues** (37 and 38).

1. **`backend/tests/test_runner.py` had again been overwritten** — this time
   with a byte-identical copy of `tests/test_runs_api.py` (verified: identical
   md5 in commit `70c41b1`). This is Known Issue 32 recurring, for the second
   time and with a different donor file. The Phase 6C session's rebuilt runner
   module is not in the repository.
2. **`backend/tests/test_storage.py` was never updated by the Phase 6C
   commit.** It is byte-identical to its Phase 6B version, so it still asserts
   `storage.store_upload()` and `RunPaths.input_directory()` — both removed by
   Phase 6C. **The committed suite failed: 25 failed, 460 passed.**

Both modules were rewritten against the Phase 6C runtime and the suite was
brought to green (**485 passed**, pyright clean) **before any 6D code was
written**, so this phase started from a working, verified 6C baseline rather
than from a broken tree.

**The pre-refactor baseline was captured by execution, not assumed.** Before a
line of 6D code was written, both real Actions were run through the Phase 6C
pipeline against their committed fixtures, as CSV and as XLSX, and the
manifests, result frames and generated CSV/XLSX were saved. Build plan 6D's
"its output must match the pre-refactor deterministic output" is therefore
checked against a recorded artifact. See **Tests**.

**6D.1 The processing boundary.** `services/runner.py` implements exactly the
lifecycle the build plan draws:

    named uploaded inputs -> parser -> named DataFrame(s)
        -> Action Registry -> Action -> result DataFrame(s)

`storage.create_run()` is no longer called, so no run directory is created;
`RunOutcome` no longer carries `paths`, because a Run has no location. The
runner still imports `storage` — for `read_upload`, `extension_of` and
`display_filename`, none of which build a path.

**6D.2 Filesystem-coupled Actions: still none.** Phase 6A classified both
registered Actions **DataFrame-compatible** and 6C confirmed it; 6D confirmed
it a third time by execution. Neither `exact_duplicate_remover.py` nor
`product_master_builder.py` was modified, and `test_contract_freeze.py` pins
that both declare `run(self, inputs)` and import no filesystem module.

**6D.3 Transformation logic stayed inside the Actions.** Nothing moved into
React, the upload handler, a FastAPI route, the parser or the export utility.
The freeze test's structural checks on the Action modules passed unchanged, and
no Action file appears in this phase's diff.

**6D.4 Registry behaviour preserved.** Action IDs, versions, discovery, schemas,
configuration and validation are untouched. `GET /api/actions` returns exactly
what it returned before.

**6D.5 One or more result tables.** `models/run.py` gains `RunResult` — the
tables an Action produced, keyed by output ID in the Action's **declaration
order**, with `primary` (the first declared output), `secondary` (everything
else, usually empty) and `table(output_id)`. A single-output Action needs no
ceremony: `RunResult.of({"result": frame})` and `result.primary`. `tables` is
wrapped in a `MappingProxyType`, so a caller cannot add or drop a result table
behind the Run's back — the same rule the frozen `Run` already followed.

The runner builds it by walking `action.outputs`, so **the Action decides which
table is primary, by declaring it first**. An Action that declares an output and
does not produce it still fails the Run.

**6D.6 Run lifecycle status.** The build plan says "use existing equivalent
status names if already established". `RunStatus` — `running`, `succeeded`,
`failed` — is established, is public API, and is pinned by
`test_contract_freeze.py`. It was **kept unchanged**. The build plan's example
list maps onto it: `created`/`validating`/`ready` are all states inside a
synchronous Run that no caller can observe (there is no job queue — build plan
3.12), `running` is the recorded initial state, and `completed` is `succeeded`.
Adding statuses no client could ever see would have changed a frozen contract
for no caller's benefit.

**6D.7 Intermediate processing is ephemeral.** The Run keeps DataFrames,
Python objects and metadata — no intermediary spreadsheet is written anywhere:

| Was, until 6D                                | Is now                                                     |
| -------------------------------------------- | ---------------------------------------------------------- |
| `working/<id>.parquet` written per output    | the Action's own frame, held by the Run                    |
| `exports/<id>.csv` written per output        | CSV bytes rendered from that frame when a download asks    |
| `exports/<id>.xlsx` written per output       | XLSX bytes built in a memory buffer, same as above         |
| preview read the Parquet file back           | preview slices the retained frame                          |
| download served the file with `FileResponse` | download returns a `Response` carrying the generated bytes |

`services/export.py` is now byte-producing only (`to_csv_bytes`,
`to_xlsx_bytes`, `to_bytes`, `worksheet_name`); its four path-writing functions
are gone, and a test asserts they are gone rather than merely unused.
`services/preview.py` takes a DataFrame instead of a `RunPaths` + output ID.

**Removing Parquet was 6D's call, not 6E's, and here is why.** Phase 6C's notes
predicted 6E would remove it. Build plan 6D's completion criteria requires a Run
to execute "with no required `inputs/`, `working/`, `exports/` directory" —
`working/` is exactly where Parquet lived, so it could not survive this phase.
Build plan **section 28 (internal Parquet) is therefore superseded here** rather
than in 6E; Known Issue 24 already recorded the reversal as deliberate, and the
Phase 6 architectural rules state that they override conflicting earlier
instructions.

**The same reasoning, and its limit, for preview and download.** Once nothing is
written, a preview that reads Parquet and a download that serves a file both
break. Keeping them working meant re-pointing them at the retained frames. That
is the _mechanism_ 6E.2 and 6F.1/6F.2 describe, and it was implemented **only as
far as removing the filesystem requirement demanded** — the response contracts
are byte-identical to their pre-6D form. **What 6E and 6F still own is listed
under Next Phase and was deliberately not built.**

**6D.8 Failed Runs are cleaned up correctly.** A failed Run is recorded failed,
keeps its error and its full validation list, keeps the record of what was
uploaded — and carries **no result at all**: `_finalize_failed` sets
`outputs=()` and `result=None` explicitly. That is defensive rather than
load-bearing today (a Run cannot currently fail after its result is recorded),
which a control test proved, so the guarantee is pinned by a test that drives
the finalizer with a Run that _does_ carry a result. Deleting a Run releases its
frames: `runner.delete_run()` is now just `run_store.delete_run()`, because
everything a Run holds travels with the Run. A `weakref` test asserts the result
frame is genuinely unreachable after deletion, rather than assuming it.

**Known Issues resolved by this phase.** 24 (Parquet superseded — now actioned),
26 (a restart orphans run directories — none are created), 27 (two functions
called `create_run` — only `run_store.create_run` is called now), 33 (a failed
Run leaves two empty directories — no directory is created at all).

**`test_contract_freeze.py` passed unchanged, again.** 84 tests, file not
modified — the Phase 6A freeze has now survived 6B, 6C and 6D untouched, which
is the whole reason it exists.

---

### Phase 6E — Results, Preview, Metrics, and Audit Data

All seven items of build plan "Phase 6E" were implemented and verified. A Run
now describes what it produced and explains what it did, and the browser shows
all of it: metrics, the paginated preview, validation warnings and the audit
summary. **No filesystem behaviour changed** — nothing is written, and the run
still needs no directory. Export buttons were deliberately **not** built; that
is Phase 6F (see below).

**The repository was verified intact before any code was written.** The three
checks the previous session's _Next Phase_ prescribed were run in order: the
backend modules are under `backend/app/` and not `src/app/`; `md5sum` across
`backend/tests/*.py` printed no duplicate; and the suite reported **519
passed**. The class of defect recorded as Known Issues 10, 31, 32, 37 and 38
did **not** recur this time. `npx eslint .` and `npm run build` were also clean
before any change, so this phase started from a verified baseline.

**6E.1 Result metadata.** `backend/app/services/results.py` is the new,
single place that describes a result frame. It is a service rather than code
inside the runner because the runner, the preview and (later) the export all
need the same description of a table, and describing a result twice is how two
descriptions come to disagree.

`OutputMetadata` gains four fields, all **measured**, never inferred:

| Field             | What it is                                                 |
| ----------------- | ---------------------------------------------------------- |
| `column_schema`   | Each column's name, Polars dtype and a coarse `ColumnKind` |
| `input_row_count` | Rows the Run actually received, across every input         |
| `columns_added`   | Result columns that appeared in **no** input               |
| `columns_removed` | Input columns that are **not** in this result              |

The two column lists are set differences over real column names in
first-appearance order, so they are deterministic and mean the same thing for a
one-input Action and a three-input one. A reordered column is reported as
neither added nor removed, which a test pins.

**"Affected row count" is the Action's to state, not the runner's to guess.**
Build plan 6E.1 lists it and 6E.5 qualifies it with _"where supported by the
Action"_. A generic `input_rows - output_rows` labelled "rows affected" would
be a guess about semantics — an Action that rewrites 500 values in place
affects 500 rows and changes the row count by zero — and build plan section 3.3
forbids guessing. So `ActionResult` gained an optional
`rows_affected: int | None`, defaulting to `None`; both real Actions set it to
the count of duplicate rows they dropped, which is exactly what they affected.
An Action that states nothing reports `null`, and the audit shows `null` rather
than substituting a different fact. **Neither Action's metrics, ID, version,
inputs, outputs or transformation changed** — `test_contract_freeze.py`'s
frozen metric-key sets still pass untouched.

**6E.2/6E.3 Preview from the result frame.** Already true since 6D: the preview
slices the retained DataFrame and no temporary spreadsheet is generated to read
back. The 100-row default and 500-row maximum are unchanged, and the browser
requests exactly one page — verified in a real browser by recording the network
requests a "Next" click produced (one `?offset=100&limit=100`, nothing else).

**6E.4 Schema information.** `ColumnSchema` carries three things: `name`, the
Polars `dtype` verbatim (`Int64`, `String`, …) and `kind` — a small closed
enum (`number`, `text`, `boolean`, `temporal`, `other`). The dtype is evidence
of what the data is; the kind is what a table actually branches on. `Boolean`
is classified before numeric deliberately, so a true/false column is never
right-aligned as though it were a count, and an unrecognised type falls back to
`other` rather than to a guess. Both `OutputMetadata` and `PreviewResponse`
carry it, and the preview's schema describes the whole table rather than the
page, so column types cannot change as the user pages forward.

**6E.5 The audit summary.** `RunAudit` (with `AuditInput` and `AuditResult`)
assembles what build plan 6E.5 lists: the Action executed, the inputs used,
rows received, rows returned, rows affected, the result tables available,
validation warnings and errors, the Action's metrics, the execution status and
the duration.

It is **derived, not recorded**: `Run.to_audit()` builds it from the Run's own
fields and `Run.to_manifest()` carries it, so the audit can never drift out of
step with the manifest it sits in — a test asserts field-by-field that the two
agree. A Run explains itself in whatever state it is in: a `running` Run
reports zero rows received and no results, and a **failed** Run reports its
status, what it received, `null` for what it returned, and the errors that
stopped it (build plan 3.9's evidence rule, now visible to a reader).
`rows_returned` is the **primary** table's row count, not a total across
tables — a total would belong to no table — and every table is listed in
`results`.

**6E.6 Audit data stays out of the user's data.** Nothing in this phase writes
a value into a result frame, and that is asserted rather than assumed: three
tests check the result table's columns after a Run, compare the retained frame
byte-for-byte against what the Action returned, and confirm that describing a
result does not alter it.

**6E.7 The existing frontend was extended, not rebuilt.** Every Phase 5
component still exists and none was replaced. `ActionSelector`,
`ActionDescription`, `FileUploadSlot` and `RunButton` were **not touched at
all**. Four components were added and two files extended:

    src/components/workbench/ResultsSummary.jsx   new — counts, column changes,
                                                  the Action's own metrics
    src/components/workbench/DataPreview.jsx      new — the paginated table
    src/components/workbench/AuditSummary.jsx     new — the audit record
    src/components/workbench/OutputSelector.jsx   new — only when >1 result
    src/components/workbench/RunStatus.jsx        + validation warnings, and a
                                                  success line that now names
                                                  the row counts
    src/components/workbench/ActionRunner.jsx     + result-table state and the
                                                  four components above
    src/lib/api.js                                + fetchPreview()
    src/lib/formatters.js                         + formatCount, formatDuration,
                                                  formatMetricLabel, formatCell,
                                                  isBlankCell

**There is still no branch on any Action ID anywhere in the frontend.** The
output selector is built from the manifest's own `outputs`, the metric rows
from the Action's own metric keys (humanised for display, never renamed,
reordered or dropped), and the preview's alignment from the column kinds the
backend reported. Verified by running a two-output Action against the real UI
with no frontend change of any kind — see **Tests**.

**Preview values are shown exactly as they arrived.** No number is regrouped
with thousands separators, no string is trimmed and no blank is filled in — the
preview is the user's data (build plan section 3.3). Grouping separators appear
only on counts the application itself produced (`15,842 rows`). The single
display convention is that a `null` renders as a muted em dash, because an
empty cell and a cell holding an empty string are different facts and would
otherwise look identical.

**`MANIFEST_SCHEMA_VERSION` is now 2, and this is the one deliberate contract
change.** `OutputMetadata` gained four required fields and `RunManifest` gained
a required `audit`, so a version 1 manifest does not validate against the new
model — that is an incompatibility in the parse direction and the constant says
so. Nothing already frozen changed name, order or meaning; every addition sits
beside what was there. `PreviewResponse.rows` is still positional and
`columns` is still a plain list of names.

**`test_contract_freeze.py` was amended, for the first time since Phase 6A.**
It passed unchanged through 6B, 6C and 6D, which is what made it useful. Phase
6E is the phase build plan Phase 6 always intended to change the manifest, and
the freeze did exactly its job: it turned that change into a decision that had
to be written down. The amendment is confined to three schema field lists, the
version constant and one `PreviewResponse` construction; each amended entry
carries a comment saying what changed and why. **Every other assertion in the
module — the Action inventory, the route table, the error/status table, the
frozen metric keys, the preview limits, the determinism checks, the
DataFrame-first classification — is untouched and still passing.**

**What Phase 6E deliberately did not build.** Build plan 6E's completion
criteria require a completed Run to display status, metrics, preview,
validation warnings and the audit summary. **Export buttons are not among
them**, and Phase 6F owns export — including 6F.6, which changes the download
filename convention from `<output-id>.<format>` to
`forgexl-<action>-<timestamp>.<format>`. Building the buttons now would have
been scaffolding for a later phase against a filename convention that is about
to change. The download endpoints themselves work (6D re-pointed them at the
retained frames) and are unchanged by this phase. Recorded as Known Issue 47.

---

## Current Architecture

### Frontend

| Item             | State                                                                       |
| ---------------- | --------------------------------------------------------------------------- |
| Framework        | Next.js `16.3.2` (Turbopack)                                                |
| React            | `19.2.8` / react-dom `19.2.8`                                               |
| Router           | App Router (`src/app/`). No `pages/` directory.                             |
| Language         | Plain JavaScript. Zero `.ts`/`.tsx` files.                                  |
| Tailwind CSS     | v4 (`4.3.3`), CSS-first via `postcss.config.mjs` + `@import "tailwindcss";` |
| ESLint           | `9.39.5`, flat config extending `eslint-config-next/core-web-vitals`        |
| `src/` directory | In use. `jsconfig.json` maps `@/*` → `./src/*`.                             |
| React Compiler   | Enabled (`reactCompiler: true`)                                             |
| Fonts            | `next/font/google` — Geist and Geist Mono                                   |

Frontend files:

    src/app/layout.jsx             root layout, project metadata
    src/app/page.jsx               header + BackendStatus + ActionRunner
    src/app/globals.css            Tailwind import + theme tokens
    src/app/favicon.ico
    src/lib/api.js                 the ONLY module holding a backend URL
    src/lib/formatters.js          formatFileSize / fileExtension / joinWithOr
    src/components/backend/BackendStatus.jsx
                                   client component, /health indicator
    src/components/workbench/ActionRunner.jsx
                                   "use client" — owns all workflow state
    src/components/workbench/ActionSelector.jsx
                                   <select> populated from GET /api/actions
    src/components/workbench/ActionDescription.jsx
                                   name, description, version
    src/components/workbench/FileUploadSlot.jsx
                                   one slot; click or drag-and-drop
    src/components/workbench/RunButton.jsx
                                   Run / Processing…, disabled-state rules
    src/components/workbench/RunStatus.jsx
                                   running / success / structured errors
    public/.gitkeep

`page.jsx` stays a server component; `ActionRunner` is the client boundary, so
server-only and client-only code remain separated (build plan §15).
`ActionDescription` and `RunStatus` are pure presentation and carry no
`"use client"` directive of their own — they are pulled into the client bundle
by their importer.

(The Phase 1 entry above recorded the health indicator as `.js` at
`src/components/BackendStatus.js`; the paths shown here are the actual
repository state. Build plan §15 permits both `.js` and `.jsx`.)

### Backend

    backend/
      .venv/                  git-ignored virtual environment
      requirements.txt        pinned direct dependencies
      pytest.ini              testpaths=tests, pythonpath=.
      app/
        __init__.py
        config.py             all backend settings
        errors.py             structured error taxonomy (section 22)
        main.py               FastAPI app, CORS, routers, error handler, /health
        actions/
          __init__.py
          base.py             Action contract + ActionResult
          registry.py         ActionRegistry, ACTION_REGISTRY, lookups
          exact_duplicate_remover.py
          product_master_builder.py
        api/
          __init__.py
          actions.py          GET /api/actions
          runs.py             POST /api/runs, retrieval, preview, downloads
        models/
          __init__.py
          schemas.py          every Pydantic schema
          run.py              the logical Run, RunResult, run IDs   (6B/6D)
        services/
          __init__.py
          run_store.py        RunStore, InMemoryRunStore, RUN_STORE       (6B)
          storage.py          in-memory upload intake, safe filenames,
                              upload limit — plus the run-directory
                              helpers 6D left unused (6I removes)   (6C/6D)
          parser.py           parse_tabular_bytes: CSV + XLSX from memory (6C)
          runner.py           the generic Run pipeline, DataFrame-first   (6D)
          export.py           CSV/XLSX bytes from a result frame          (6D)
          preview.py          paginated slices of a result frame          (6D)
      tests/
        __init__.py
        conftest.py           isolated runs dir + Run Store, registry, client
        helpers.py            make_action(), CSV/XLSX builders, upload helpers
        fixtures/             hand-written Action fixtures (Phase 4)
        test_actions.py       Action contract + registry
        test_api.py           /health and /api/actions
        test_schemas.py       manifest / preview serialisation
        test_run_model.py     the logical Run and run IDs                 (6B)
        test_run_store.py     the five store operations, replaceability   (6B)
        test_storage.py       in-memory upload intake, path safety, upload
                              limit, deletion  (rewritten in 6D; see KI 38)
        test_parser.py        CSV, XLSX from bytes, worksheet ambiguity,
                              engine fallback                             (6C)
        test_runner.py        the pipeline, validation, failed Runs, results,
                              deletion   (rebuilt again in 6D; see KI 37)
        test_export.py        CSV/XLSX bytes round trips                  (6D)
        test_preview.py       paging limits and the column schema      (6D/6E)
        test_results.py       column kinds, added/removed columns,
                              describe_output                            (6E)
        test_audit.py         result metadata and the audit summary through
                              the pipeline; audit stays out of the data  (6E)
        test_runs_api.py      the Run endpoints and their status codes
        test_contract_freeze.py  the Phase 6A freeze (amended once, in 6E)
        test_exact_duplicate_remover.py / test_product_master_builder.py /
        test_action_round_trip.py                                (Phase 4)

Installed backend packages (resolved 2026-08-22):

    fastapi          0.141.1
    pydantic         2.13.4
    uvicorn          0.52.4
    python-multipart 0.0.32
    polars           1.43.2
    fastexcel        0.21.0
    openpyxl         3.1.5
    xlsxwriter       3.2.9
    pytest           9.1.1
    httpx            0.28.1

`pydantic` was added to `backend/requirements.txt` in Phase 2. It was already
installed as a FastAPI transitive dependency; it is now declared explicitly
because `app.models.schemas` imports it directly (build plan §15). The
resolved version did not change.

Backend configuration values (defaults in `backend/app/config.py`):

    HOST                     127.0.0.1
    PORT                     8000
    DATA_DIRECTORY           <repo root>/data
    RUNS_DIRECTORY           <repo root>/data/runs
    MAX_UPLOAD_BYTES         262144000  (250 MB)
    ALLOWED_FRONTEND_ORIGINS http://127.0.0.1:3000, http://localhost:3000

Each is overridable through a `FORGEXL_`-prefixed environment variable
(`FORGEXL_BACKEND_HOST`, `FORGEXL_BACKEND_PORT`, `FORGEXL_DATA_DIRECTORY`,
`FORGEXL_MAX_UPLOAD_BYTES`, `FORGEXL_ALLOWED_FRONTEND_ORIGINS`). The prefix
avoids collisions with the generic `HOST`/`PORT` variables that `next dev` and
other local tooling also read; build plan §20 names the settings, not the
variable names.

### API surface (current)

    GET  /health        ->  200 {"status": "ok"}
    GET  /api/actions   ->  200 {"actions": [ActionDefinition, ...]}

    POST /api/runs      ->  200 RunManifest
                            multipart: action_id + one file field per slot ID
                            Uploads are read into memory and parsed from
                            there (6C); the Action's result frames are held
                            by the Run. The whole pipeline writes nothing to
                            disk and needs no run directory at all (6D).
                            The manifest carries result metadata on every
                            output and a derived `audit` summary (6E);
                            `schema_version` is now 2.
                            400 malformed request (no action_id)
                            404 unknown Action
                            413 upload over MAX_UPLOAD_BYTES
                            422 validation failure — including the new
                                EMPTY_FILE for a zero-byte upload (6C)
                            500 Action raised

    GET  /api/runs/{run_id}
                        ->  200 RunManifest | 404
                            Served from the Run Store (in-process memory in
                            V1), not from a file. Restarting the backend
                            clears run history — build plan Phase 6 rules
                            14/15.

    GET  /api/runs/{run_id}/outputs/{output_id}/preview?offset=&limit=
                        ->  200 PreviewResponse (default 100, max 500)
                            Sliced from the Run's retained result frame (6D),
                            with the table's `column_schema` so a client can
                            render values by type (6E).
                            400 offset/limit out of range
                            404 unknown Run or output, or the Run no longer
                                holds its result (MISSING_ARTIFACT)

    GET  /api/runs/{run_id}/outputs/{output_id}/download/csv
    GET  /api/runs/{run_id}/outputs/{output_id}/download/xlsx
                        ->  200 attachment | 404
                            The bytes are generated from the retained result
                            frame for that request; no file is read or
                            written (6D). The filename convention is still
                            `<output-id>.<format>` — 6F.6 changes it.

Every error body has the shape build plan section 22 specifies:

    {"error": {"code": "...", "message": "...", "details": {...}}}

FastAPI's own `/docs`, `/redoc` and `/openapi.json` are present by default;
the OpenAPI schema now documents `ActionDefinition`, `ActionInput`,
`ActionOutput` and `ActionListResponse`.

Registered Actions (2):

    exact_duplicate_remover  1.0.0  "Exact Duplicate Remover"
      input  source_file         .csv .xlsx   no required columns
      output deduplicated_data   csv, xlsx

    product_master_builder   1.0.0  "Product Master Builder"
      input  sales_file          .csv .xlsx
             required columns    SKU, Vintage, Supplier, Producer,
                                 Selection, Volume
      output product_master      csv, xlsx

The Phase 2 placeholder `example_passthrough` was removed in Phase 4.

### Adding an Action (the architecture being proven)

1. Write `backend/app/actions/<action>.py` — subclass `Action`, declare
   metadata, implement `run()`.
2. Import it in `backend/app/actions/registry.py` and add it to
   `ACTION_REGISTRY`.
3. Add tests (and fixtures).

Nothing else in the backend changes, and — once Phase 5 exists — no frontend
file changes, because the UI is built entirely from `GET /api/actions`.

### npm scripts

    dev      concurrently -> dev:web + dev:api
    dev:web  next dev --hostname 127.0.0.1 --port 3000
    dev:api  bash scripts/dev-backend.sh
    build    next build
    start    next start --hostname 127.0.0.1 --port 3000
    lint     eslint

devDependencies gained `concurrently` `^10.0.5`. No other dependency was added.

### Directory status vs build plan §10

| Path                    | Status                                                       |
| ----------------------- | ------------------------------------------------------------ |
| `src/app/`              | Exists (plan sketches root `app/`; `src/` retained per 1.1)  |
| `src/components/`       | Exists (`backend/`, `workbench/` — 6 Phase 5 components)     |
| `src/lib/`              | Exists (`api.js`, `formatters.js`) — added in Phase 5        |
| `backend/app/`          | Exists (`main.py`, `config.py`)                              |
| `backend/app/api/`      | Exists (`actions.py`, `runs.py`)                             |
| `backend/app/actions/`  | Exists (`base.py`, `registry.py`, the two proof Actions)     |
| `backend/app/models/`   | Exists (`schemas.py`, `run.py`)                              |
| `backend/app/services/` | Exists (run_store, storage, parser, runner, export, preview) |
| `backend/tests/`        | Exists (15 test modules and `fixtures/`)                     |
| `data/runs/`            | Exists (`.gitkeep` only; nothing is written there — 6D)      |
| `scripts/`              | Exists (`dev-backend.sh`)                                    |
| `public/`               | Exists (`.gitkeep`; starter demo SVGs removed)               |
| `.env.example`          | Exists                                                       |
| `.env.local`            | Not present — not required (frontend default fallback)       |

### Repository / Git

    Remote:         https://github.com/cmgolizio/ForgeXL
    Current branch: claude/forgexl-phase-6d-dataframe-0dfm70
    Last commit:    70c41b1  "phase 6C fix"

Phase 6D's changes were committed and pushed to
`claude/forgexl-phase-6d-dataframe-0dfm70` at the end of the session. The commit
covers five backend modules (`models/run.py`, `services/runner.py`,
`services/export.py`, `services/preview.py`, `api/runs.py`), eight test modules
and this file. `test_contract_freeze.py` is deliberately **not** among them, and
neither are `package.json`, `package-lock.json`, `backend/requirements.txt` or
any file under `src/`. `data/runs/` holds only `.gitkeep`.

(The paragraph below records the Phase 6C session's own view of the tree.)

Phase 6C's changes are uncommitted; the user has not authorised a commit.
`git status` shows the relocation of the five misplaced Phase 6B modules from
`src/app/` to `backend/app/` (Known Issue 31), the seven backend modules and
seven test modules Phase 6C changed, and this file. `data/runs/` holds only
`.gitkeep`. `test_contract_freeze.py` is deliberately **not** in that list.

(The Phase 6B entry below recorded the branch and last commit as of that
session. Phase 6B was committed as `679fff4` after it was written.)

Commit history at start of Phase 2 (4 commits):

    58b37d2  phase 1 complete. Frontend and backend (Python) foundations …
    f7f987c  phase 0 complete
    a213587  added build plan file to brand new nextjs app
    7152074  Initial commit from Create Next App

Phase 2 changes are uncommitted; the user has not authorised a commit.

Verified with `git add -A --dry-run` that exactly the intended files would be
staged (16 paths: `backend/app/main.py` modified, plus the new `actions/`,
`api/`, `models/`, `services/`, `tests/` modules and `backend/pytest.ini`).
`backend/.venv/`, `__pycache__/`, `.pytest_cache/`, `.env.local` and
`data/runs/<run>/…` are all correctly ignored; `data/runs/.gitkeep` and
`.env.example` are correctly **not** ignored.

---

## Environment

Versions verified by direct command execution:

| Tool     | Command             | Version  |
| -------- | ------------------- | -------- |
| Node.js  | `node --version`    | v22.22.2 |
| npm      | `npm --version`     | 10.9.7   |
| Python 3 | `python3 --version` | 3.11.15  |
| Git      | `git --version`     | 2.43.0   |
| pip      | in `backend/.venv`  | 26.2.1   |

Working directory: `/home/user/ForgeXL`

Host platform of the implementation session: Linux (x86_64), inside a remote
ephemeral container — **not** macOS. The build plan assumes a Mac target. See
**Known Issues**.

Local addresses (verified running):

    Frontend  http://127.0.0.1:3000
    Backend   http://127.0.0.1:8000

---

## Tests

### Backend test suite (Phase 6E)

Environment note: this session also started in a **fresh ephemeral container** —
`backend/.venv/` and `node_modules/` did not exist. Both were recreated by
following the documented setup exactly (`python3 -m venv backend/.venv`,
`pip install -r backend/requirements.txt`, `npm install`); no undocumented step
was required and no dependency was added. `backend/requirements.txt`,
`package.json` and `package-lock.json` are byte-identical to their committed
state.

    cd backend && .venv/bin/python -m pytest   ->  568 passed, 1 warning

The committed state was **intact** this time: the three integrity checks the
previous session prescribed all passed before any edit (backend modules under
`backend/`, no duplicate test-module checksums, **519 passed**).

| Module                            |  6D |  6E | Change                                          |
| --------------------------------- | --: | --: | ----------------------------------------------- |
| `test_contract_freeze.py`         |  84 |  84 | **amended** — 3 field lists + version constant  |
| `test_storage.py`                 |  64 |  64 | unchanged                                       |
| `test_runs_api.py`                |  53 |  59 | +6 — result metadata, preview schema, audit     |
| `test_runner.py`                  |  51 |  51 | one case extended (a failure clears the effect) |
| `test_run_model.py`               |  37 |  37 | updated for the new required fields             |
| `test_parser.py`                  |  36 |  36 | unchanged                                       |
| `test_run_store.py`               |  34 |  34 | unchanged                                       |
| `test_product_master_builder.py`  |  34 |  34 | unchanged                                       |
| `test_actions.py`                 |  30 |  30 | unchanged                                       |
| `test_audit.py`                   |   — |  22 | **new** — the audit summary, end to end         |
| `test_exact_duplicate_remover.py` |  21 |  21 | unchanged                                       |
| `test_preview.py`                 |  17 |  21 | +4 — the page's column schema                   |
| `test_results.py`                 |   — |  17 | **new** — the result-metadata service           |
| `test_action_round_trip.py`       |  17 |  17 | unchanged                                       |
| `test_export.py`                  |  15 |  15 | unchanged                                       |
| `test_api.py`                     |  14 |  14 | unchanged                                       |
| `test_schemas.py`                 |  12 |  12 | updated for the new required fields             |
| **Total**                         | 519 | 568 | **+49**                                         |

**Nothing was weakened to pass.** The four modules whose assertions changed
(`test_contract_freeze.py`, `test_schemas.py`, `test_run_model.py`,
`test_runner.py`) changed because the manifest shape deliberately changed: each
now asserts the **new** shape exactly, with the same strictness as before —
`test_schema_field_names_are_frozen` is still an exact tuple equality, and
`test_manifest_carries_no_dataframe_rows` still asserts the complete key set of
an output rather than a subset. No test was skipped, deleted or loosened, and
no `xfail` was added.

**What the two new modules pin.**

`test_results.py` (17) — that a boolean is not classified as a number, that an
unclassified Polars type falls back to `other` rather than to a guess, that the
dtype is reported verbatim, that received columns keep first-appearance order,
that reordering columns is reported as neither added nor removed, and that
result metadata carries no row of the result (asserted by searching the
serialised metadata for the cell values).

`test_audit.py` (22) — the metadata and the audit through the real pipeline:
row counts, dropped and created columns, the schema, several result tables, an
Action that states no effect reporting `null` rather than a number, a failed
Run still explaining itself, a `running` Run reporting the state it is actually
in, the audit agreeing field-by-field with the manifest it sits in, and three
cases proving no audit value reaches the user's result table (build plan 6E.6),
including one that compares the retained frame byte-for-byte against what the
Action returned.

### Type checking (Phase 6E)

    npx pyright   ->  0 errors, 0 warnings, 0 informations

### Frontend static checks (Phase 6E)

    npx eslint .   ->  clean, 0 problems
    npm run build  ->  Compiled successfully; routes / and /_not-found

ESLint's `react-hooks/set-state-in-effect` rule caught a first draft of
`DataPreview` that reset its paging offset from an effect. It was not
suppressed: the offset reset now happens because `ActionRunner` gives the
component a `key` of `<run id>:<output id>`, so a different table is a
different component instance and starts at its first page. That is the React
idiom the rule points at, and it removed an effect rather than silencing one.

### Phase 6E end-to-end verification over real HTTP

Backend on `127.0.0.1:8000`, `next dev` on `127.0.0.1:3000`, both started the
documented way.

| Check                                          | Result                                                        |
| ---------------------------------------------- | ------------------------------------------------------------- |
| `POST /api/runs` (product_master_builder, CSV) | 200; `schema_version: 2`                                      |
| Result metadata on the output                  | `input_row_count: 75`, `row_count: 60`                        |
| `columns_removed`                              | `["Customer"]` — the sales column the Product Master drops    |
| `columns_added`                                | `[]`                                                          |
| `column_schema`                                | 6 entries; `Vintage` → `Int64`/`number`, rest `String`/`text` |
| Audit summary                                  | rows_received 75, rows_returned 60, **rows_affected 15**      |
| Audit metrics                                  | the Action's own three keys, verbatim                         |
| `GET .../preview?offset=0&limit=3`             | 3 rows, `total_rows: 60`, `column_schema` present             |
| `GET .../preview?offset=58&limit=100`          | 2 rows returned, `total_rows: 60`                             |
| Accented values through the whole pipeline     | `Château Léoville` / `Sélection Prestige` intact              |
| Files written anywhere under `data/`           | **0** — `data/runs/.gitkeep` only, before and after           |

### Phase 6E browser verification (real headless Chromium)

Driven with Playwright against the real dev servers — a real browser, a real
file input, real network requests. Screenshots saved outside the repository.

**Run 1 — Product Master Builder, 280-row CSV (250 unique products).**

| Check                           | Result                                                                                                                                              |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend indicator               | `Backend Connected`                                                                                                                                 |
| Workbench state                 | `success`                                                                                                                                           |
| Success line                    | "Product Master Builder read 280 rows and returned 250."                                                                                            |
| Results figures                 | Input Rows 280 · Output Rows 250 · Columns 6 · Execution 2 ms                                                                                       |
| Columns removed shown           | `Customer`                                                                                                                                          |
| Action metrics                  | "Duplicate product rows removed" listed with its value                                                                                              |
| Preview headers                 | SKU, Vintage, Supplier, Producer, Selection, Volume                                                                                                 |
| Accents in the preview          | `Château Léoville` / `Sélection Prestige` rendered intact                                                                                           |
| Alignment from column kinds     | `Vintage` right-aligned; every text column left-aligned                                                                                             |
| Rows rendered                   | 100 (the page size, not the 250-row result)                                                                                                         |
| Paging line                     | `Showing 1–100 of 250`                                                                                                                              |
| Click **Next**                  | `Showing 101–200 of 250`                                                                                                                            |
| Network requests for that click | **exactly one** — `?offset=100&limit=100` (build plan section 31)                                                                                   |
| Click **Previous**              | `Showing 1–100 of 250`                                                                                                                              |
| Audit summary on screen         | action + version, status, 280 received, 250 returned, 30 affected, duration, Run ID, the input file with its shape, the result table with its shape |
| `[object Object]` anywhere      | none                                                                                                                                                |
| Server paths anywhere           | none (`/home/`, `data/runs` absent from the rendered page)                                                                                          |
| Uncaught page errors            | none                                                                                                                                                |

**Run 2 — a validation failure, same session.** A zero-byte CSV posted to
Exact Duplicate Remover: state `validation_error`, panel reads
"Validation Failed / empty.csv is empty.", and **no results block, no preview
table and no audit panel are rendered** — a failed Run never shows a result
area.

**Run 3 — Exact Duplicate Remover, 140-row XLSX (20 duplicates).**

| Check                    | Result                                                     |
| ------------------------ | ---------------------------------------------------------- |
| Success line             | "Exact Duplicate Remover read 140 rows and returned 120."  |
| Figures                  | Input 140 · Output 120 · Columns 4 · Execution 3 ms        |
| Action metrics           | Input rows 140, Output rows 120, Duplicates removed 20     |
| Boolean column `InStock` | rendered `false` and **left**-aligned, not as a number     |
| `Vintage`                | right-aligned                                              |
| Output selector          | **not rendered** — the Action has one output               |
| Column-change rows       | **not rendered** — this Action adds and removes nothing    |
| Audit                    | 140 received, 120 returned, 20 affected, `book.xlsx` 140×4 |
| Uncaught page errors     | none                                                       |

**Run 4 — blank cells.** A CSV with an empty numeric cell and an empty text
cell: both render as a muted `—`, and the duplicate row is removed (3 → 2).
No value is otherwise reformatted.

**Run 5 — a two-output Action, to prove the UI needs no Action-specific code.**
A verification-only server (`scratchpad/temp_server.py`, **outside the
repository**) served the real application with one extra Action registered,
declaring two outputs. **No repository file was changed for this test.**

| Check                            | Result                                                                           |
| -------------------------------- | -------------------------------------------------------------------------------- |
| Output selector appears          | yes, with options "Kept Rows" and "Dropped Rows"                                 |
| Preview heading                  | `Preview — Kept Rows`, then `Preview — Dropped Rows`                             |
| Switching result tables          | headers change 7 columns → 2; `Showing 1–100 of 280` → `Showing 1–3 of 3`        |
| Requests on switch               | the new table's first page only                                                  |
| Results figures follow the table | Output Rows 3, Columns 2 for the secondary table                                 |
| Audit "Results Produced"         | "Kept Rows (primary) 280 rows · 7 columns" and "Dropped Rows 3 rows · 2 columns" |

**Run 6 — the validation-warnings panel.** The UI only ever submits the slots
an Action declares, so a real `UNEXPECTED_INPUT` warning cannot be produced by
clicking. A genuine warning was obtained from the backend first (posting an
undeclared `stray_file` field returned a manifest whose
`validation.warnings[0].code` is `UNEXPECTED_INPUT`), and that **real backend
manifest** was then served to the UI through request interception. The page
rendered an amber panel headed "Warning" reading "…does not use stray_file; the
file was ignored." beside — not instead of — the "Run Successful" panel, which
is build plan section 6.2's rule that a warning never fails a Run.

### Backend test suite (Phase 6D)

Environment note: this session also started in a **fresh ephemeral container** —
`backend/.venv/` and `node_modules/` did not exist. Both were recreated by
following the documented setup exactly (`python3 -m venv backend/.venv`,
`pip install -r backend/requirements.txt`, `npm install`); no undocumented step
was required and no dependency was added.

    cd backend && .venv/bin/python -m pytest   ->  519 passed, 1 warning

**Three baselines, because the committed state was broken.**

| State                                                  | Result                                |
| ------------------------------------------------------ | ------------------------------------- |
| As committed (`70c41b1`)                               | **25 failed, 460 passed**             |
| After repairing `test_runner.py` and `test_storage.py` | **485 passed** (the real 6C baseline) |
| After Phase 6D                                         | **519 passed**                        |

The 485 figure is what Phase 6C would have reported had its work been committed
intact. The Phase 6C entry claims 488; the two rebuilt modules are
reconstructions, not a recovery of that session's edits, so their individual
counts differ (see Known Issues 37 and 38).

| Module                            | 6C repaired |      6D | Change                                    |
| --------------------------------- | ----------: | ------: | ----------------------------------------- |
| `test_contract_freeze.py`         |          84 |      84 | **file not modified**                     |
| `test_storage.py`                 |          64 |      64 | rebuilt for 6C; untouched by 6D           |
| `test_runs_api.py`                |          49 |      53 | downloads/preview from memory; +2 outputs |
| `test_runner.py`                  |          38 |      51 | rebuilt for 6C; +13 for 6D                |
| `test_run_model.py`               |          26 |      37 | `RunResult` (+11)                         |
| `test_parser.py`                  |          36 |      36 | unchanged                                 |
| `test_run_store.py`               |          34 |      34 | unchanged                                 |
| `test_product_master_builder.py`  |          34 |      34 | reads the retained frame                  |
| `test_actions.py`                 |          30 |      30 | unchanged                                 |
| `test_exact_duplicate_remover.py` |          21 |      21 | reads the retained frame                  |
| `test_preview.py`                 |          15 |      17 | rewritten against frames                  |
| `test_action_round_trip.py`       |          17 |      17 | unchanged (already reads download bytes)  |
| `test_export.py`                  |          11 |      15 | rewritten against bytes                   |
| `test_api.py`                     |          14 |      14 | unchanged                                 |
| `test_schemas.py`                 |          12 |      12 | unchanged                                 |
| **Total**                         |     **485** | **519** | **+34**                                   |

The single warning is the third-party `StarletteDeprecationWarning` already
recorded as Known Issue 7.

**No test was weakened, skipped or deleted to make the suite green.** Every test
that asserted behaviour 6D removes was rewritten against the behaviour that
replaced it:

| Assertion that could no longer hold                                                                     | What it asserts now                                                         |
| ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `test_write_output_creates_all_three_artifacts`                                                         | `test_the_csv_export_round_trips` / `..._xlsx_export_is_a_real_workbook...` |
| `test_the_parquet_round_trips_with_its_schema_intact`                                                   | dropped with Parquet; the frame _is_ the working representation now         |
| `test_two_outputs_are_written_side_by_side`                                                             | `test_each_output_downloads_and_previews_its_own_table` (over HTTP)         |
| `test_an_unsafe_output_id_is_refused` (export path building)                                            | no path is built; the ID never reaches one                                  |
| every `preview.read_preview(paths, output_id, ...)` call                                                | `preview.read_preview(frame, ...)`                                          |
| `test_a_missing_parquet_artifact_is_reported`                                                           | `test_a_preview_whose_result_has_been_released_returns_404`                 |
| `test_the_preview_reads_parquet_not_the_csv_export`                                                     | `test_previewing_reads_nothing_from_disk`                                   |
| `test_a_download_whose_file_is_missing_returns_404`                                                     | `test_a_download_whose_result_has_been_released_returns_404`                |
| `test_the_uploaded_source_is_never_written_to_the_run_directory` (which still expected three artifacts) | `test_a_run_writes_nothing_to_the_filesystem` — the stronger fact           |
| `pl.read_parquet(outcome.paths.working_artifact(...))` in both Action modules                           | `outcome.result.table(...)` / `outcome.result.primary`                      |
| `_assert_failed_cleanly` checking two empty directories                                                 | checks `run.result is None` **and** that nothing was written at all         |

**The 6D completion criteria, as tests:**

| Build plan 6D | Test                                                                                                                                                                                                                                                                                   |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 6D.1          | `test_the_pipeline_needs_no_run_directory_at_all`, `test_a_successful_run_writes_nothing_anywhere`                                                                                                                                                                                     |
| 6D.2          | `test_the_action_run_signature_takes_named_frames`, `test_an_action_executes_with_no_filesystem_available` (both in the unchanged freeze module)                                                                                                                                       |
| 6D.3          | the freeze module's structural checks on both Action modules                                                                                                                                                                                                                           |
| 6D.4          | the frozen registry inventory in `test_contract_freeze.py`                                                                                                                                                                                                                             |
| 6D.5          | `test_an_action_with_two_outputs_produces_two_result_tables`, `test_each_output_downloads_and_previews_its_own_table`, `test_a_single_table_result_needs_no_ceremony` and 8 more in `test_run_model.py`                                                                                |
| 6D.6          | `RunStatus` unchanged, pinned by `test_run_status_values_are_frozen`                                                                                                                                                                                                                   |
| 6D.7          | `test_a_successful_run_keeps_its_result_frame`, `test_the_retained_frame_is_the_one_the_action_returned`, `test_generating_an_export_writes_nothing`, `test_previewing_reads_nothing_from_disk`, `test_the_service_no_longer_writes_artifacts`                                         |
| 6D.8          | `test_a_failed_validation_leaves_no_result`, `test_an_action_that_raises_leaves_no_result`, `test_an_action_that_produces_only_some_of_its_outputs_leaves_no_result`, `test_finalizing_a_failure_clears_any_result_already_recorded`, `test_deleting_a_run_releases_its_result_frames` |

**Control tests — the new tests were proved to catch regressions.** Eight
deliberate breakages were introduced one at a time, each reverted from a
scratch-directory backup immediately afterwards. The suite returned to 519
passed after the last revert and the tree was confirmed byte-identical to the
backup.

| Deliberate break                                   | Result                                                                                                                                                                                                |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| the runner writes the result frame to disk         | 5 failures across `test_runner.py` and `test_runs_api.py`                                                                                                                                             |
| the failure finalizer no longer clears the result  | 1 failure (`test_finalizing_a_failure_clears_any_result_already_recorded`)                                                                                                                            |
| the _last_ declared output becomes primary         | 2 failures (`test_run_model.py`, `test_runner.py`)                                                                                                                                                    |
| `delete_run` keeps a reference to the Run's frames | 2 failures (`test_deleting_a_run_releases_its_result_frames` and one more)                                                                                                                            |
| an empty preview page reports no columns           | 2 failures in `test_preview.py`                                                                                                                                                                       |
| the XLSX export drops its worksheet name           | 1 failure (`test_the_worksheet_is_named_after_the_output`)                                                                                                                                            |
| a declared output that was not produced is skipped | 1 failure (`..._produces_only_some_of_its_outputs_leaves_no_result`)                                                                                                                                  |
| every output serves the _primary_ table            | **0 failures — a real gap.** Two tests were added (`test_each_output_downloads_and_previews_its_own_table`, `test_each_output_downloads_its_own_worksheet`); the same break then produced 2 failures. |

Two controls were informative rather than merely confirmatory, and both changed
the work: the failure-finalizer break passed at first (the pipeline cannot
currently reach that state, so a direct finalizer test was added), and the
per-output download break passed at first (nothing covered a _secondary_
output's own bytes, so two HTTP tests were added).

### Deterministic-output comparison against the pre-refactor baseline (6D)

Build plan 6D requires that "its output must match the pre-refactor
deterministic output". Before any 6D code was written, both real Actions were
executed through the Phase 6C pipeline against their committed fixtures, as CSV
and as XLSX, and the manifest, the result frame (columns, dtypes, every row) and
the generated CSV/XLSX were saved to a scratch directory. After 6D the same four
cases were re-run with `FORGEXL_DATA_DIRECTORY` pointed at a path that **does not
exist**:

| Case                                  | Manifest  | Result frame | CSV bytes                 | XLSX round-trip            |
| ------------------------------------- | --------- | ------------ | ------------------------- | -------------------------- |
| `exact_duplicate_remover`, CSV input  | identical | identical    | identical (sha 5c399b39…) | same table, same worksheet |
| `exact_duplicate_remover`, XLSX input | identical | identical    | identical (sha 05bcadad…) | same table, same worksheet |
| `product_master_builder`, CSV input   | identical | identical    | identical (sha f45f1b78…) | same table, same worksheet |
| `product_master_builder`, XLSX input  | identical | identical    | identical (sha 132b9b4a…) | same table, same worksheet |

and the nonexistent data directory was still absent afterwards.

One field is normalised in the comparison and it is worth stating why:
`inputs[0].file_size_bytes` for the two **XLSX** cases. The XLSX _fixture_ is
rebuilt for each comparison and xlsxwriter stamps a creation time into the
workbook, so the uploaded file's compressed length can differ by a byte between
builds (5901 vs 5902 here). That is the fixture varying, not ForgeXL's output —
the comparison asserts the recorded size equals the length of the payload that
run actually received before normalising it, and both CSV cases, whose fixtures
are byte-stable, matched with nothing normalised.

### Type checking (Phase 6D)

    npx pyright   ->  43 files analyzed, 0 errors, 0 warnings, 0 informations

Run twice: once on the repaired 6C baseline and once after 6D.

### Frontend static checks (Phase 6D)

    npm run lint   ->  exit 0, no errors, no warnings
    npm run build  ->  exit 0, compiled successfully
                       Routes: ○ /   ○ /_not-found  (both static)

Unchanged from Phase 6C, as expected: Phase 6D wrote no frontend code. Zero
`.ts`/`.tsx` files are tracked by git (the five under `.next/` are Next.js build
output and are git-ignored).

### Phase 6D end-to-end verification over real HTTP

The backend was started with `FORGEXL_DATA_DIRECTORY` pointed at a scratch
directory, so the repository's `data/runs/` was never written to.

    GET  /health                        ->  {"status":"ok"}
    GET  /api/actions                   ->  200, both Actions
    POST /api/runs  (CSV)               ->  200 "succeeded", duration_ms 4,
                                            engine "polars-csv", 200 bytes,
                                            3 rows in -> 2 out,
                                            duplicate_product_rows_removed 1
    POST /api/runs  (XLSX)              ->  200 "succeeded", engine
                                            "fastexcel-calamine", worksheet
                                            "Sales", 3 rows -> 2 rows
    POST /api/runs  (dedupe, CSV)       ->  200, duplicates_removed 1
    GET  /api/runs/{id}                 ->  200, served from the Run Store
    GET  .../preview?limit=5            ->  200, "Château Réal" and
                                            "Bodegas Muñoz" intact
    GET  .../download/csv               ->  200, text/csv; charset=utf-8,
                                            attachment; filename="product_master.csv",
                                            accented values intact
    GET  .../download/xlsx              ->  200, `file` reports
                                            "Microsoft Excel 2007+"

The downloaded XLSX was re-read **through the application's own parser**: it
came back as worksheet `product_master` with the expected columns and rows, so
an export ForgeXL could not itself ingest would have failed here.

**Error cases, each over real HTTP:**

    empty.csv (0 bytes)          ->  422 EMPTY_FILE "empty.csv is empty."
    a,b\n1,2,3,4\n (ragged)      ->  422 PARSE_ERROR
    bad.json                     ->  422 UNSUPPORTED_EXTENSION
    no file field at all         ->  422 MISSING_INPUT "Sales File is required."
    wrong columns                ->  422 MISSING_COLUMNS, all six named
    unknown action               ->  404 UNKNOWN_ACTION
    GET /api/runs/{unknown uuid} ->  404
    GET /api/runs/not-a-uuid     ->  404
    GET /api/runs/..%2Fsecret    ->  404
    .../outputs/nope/preview     ->  404
    .../outputs/nope/download/csv->  404
    preview?limit=501            ->  400
    preview?offset=-1            ->  400
    download from unknown run    ->  404

**The decisive 6D check — what the backend wrote, after nine real HTTP Runs:**

    files under the data directory  ->  0
    run directories                 ->  0
    inputs/ directories             ->  0
    working/ directories            ->  0
    exports/ directories            ->  0
    *.parquet                       ->  0
    manifest.json                   ->  0
    repository data/runs/           ->  .gitkeep only

    server path or traceback in any response body  ->  0 occurrences
    server log: "Traceback" or "500 Internal"      ->  0 occurrences

**A larger result, to confirm paging really works off the retained frame:**

    50,000-row CSV -> product_master_builder
      ->  succeeded, duration_ms 16, 20,000 output rows,
          duplicate_product_rows_removed 30,000
      preview offset=0      ->  total 20000, first row SKU-0
      preview offset=19995  ->  total 20000, first row SKU-19995
      download/csv          ->  588,937 bytes
      download/xlsx         ->  509,881 bytes
      files written by that run  ->  0

(No timings are claimed as benchmarks — that is Phase 7G, on the real target
machine.)

    CORS   Origin http://127.0.0.1:3000    ->  echoed
           Origin http://evil.example.com  ->  no access-control headers
    Bind   LISTEN 127.0.0.1:8000 only; nothing on 0.0.0.0

**Restart behaviour (build plan Phase 6 rules 14/15) still holds:**

    backend stopped and restarted
      GET /api/runs/{earlier id}          ->  404 UNKNOWN_RUN
      GET .../download/csv of that run    ->  404
      GET /api/actions                    ->  200, both Actions registered
      POST /api/runs                      ->  200, a new Run succeeds normally
      orphaned files left behind          ->  0  (Known Issue 26 is resolved:
                                                 nothing is written to orphan)

### Phase 6D browser verification (real headless Chromium)

Phase 6D changed no frontend file, so this exists to prove the Phase 5 UI still
works end to end against the DataFrame-first pipeline. Playwright was installed
**outside** the repository, in the session scratchpad, against the pre-installed
Chromium at `/opt/pw-browsers/chromium-1194`. Both servers were the real ones,
started with the real `npm run dev`.

**19/19 checks passed:** page title; "Backend Connected"; the selector populated
from `GET /api/actions` with both Actions; state `idle` on load; `Version 1.0.0`
and the `Sales File` slot rendered from metadata; state `ready` after choosing a
file; **a real CSV Run through the UI reaching `success`**; "Run Successful"
shown; **a real XLSX Run through the UI reaching `success`**; the second Action
running without reloading the app; a bad file classified `validation_error` with
`Supplier` and `Volume` named; an empty file explained as "is empty"; no
`[object Object]`; no traceback; **no server path rendered anywhere**; the
browser posting directly to `127.0.0.1:8000/api/runs`; no uncaught page errors.

Backend log for the whole browser session: 3 × `POST /api/runs` 200, 2 × 422,
12 × `GET /api/actions` 200, 13 × `GET /health` 200 — **no traceback, no 500**.
The session wrote **0 files** under the data directory.

Both servers were stopped afterwards, ports 3000 and 8000 confirmed free, and
`data/runs/` holds only `.gitkeep`.

---

### Backend test suite (Phase 6C)

Environment note: this session also started in a **fresh ephemeral container** —
`backend/.venv/` and `node_modules/` did not exist. The venv was recreated by
following the documented setup exactly (`python3 -m venv backend/.venv`,
`pip install -r backend/requirements.txt`); `node_modules/` was already present.
No undocumented step was required and no dependency was added.

    cd backend && .venv/bin/python -m pytest   ->  488 passed, 1 warning

**Baseline.** The committed state could not run at all: 0 of 457 tests were
collected (Known Issue 31). After the relocation repair, 457 passed — but 34 of
those were the duplicated Run Store module (Known Issue 32), so real unique
coverage before this phase was **423**.

| Module                            | Before | After | Change                       |
| --------------------------------- | -----: | ----: | ---------------------------- |
| `test_runner.py`                  |    0\* |    45 | rebuilt (see Known Issue 32) |
| `test_storage.py`                 |     53 |    60 | upload half rewritten        |
| `test_parser.py`                  |     26 |    36 | rewritten against bytes      |
| `test_runs_api.py`                |     46 |    49 | upload assertions inverted   |
| `test_contract_freeze.py`         |     84 |    84 | **file not modified**        |
| `test_run_store.py`               |     34 |    34 | unchanged                    |
| `test_product_master_builder.py`  |     34 |    34 | one expectation updated      |
| `test_actions.py`                 |     30 |    30 | unchanged                    |
| `test_run_model.py`               |     26 |    26 | unchanged                    |
| `test_exact_duplicate_remover.py` |     21 |    21 | unchanged                    |
| `test_action_round_trip.py`       |     17 |    17 | reads downloads from bytes   |
| `test_preview.py`                 |     15 |    15 | unchanged                    |
| `test_api.py`                     |     14 |    14 | unchanged                    |
| `test_schemas.py`                 |     12 |    12 | unchanged                    |
| `test_export.py`                  |     11 |    11 | reads exports from bytes     |
| **Total (unique)**                |    423 |   488 | **+65**                      |

\* `test_runner.py` was on disk but held a copy of `test_run_store.py`; its 34
collected tests were the Run Store's, counted twice.

The single warning is the third-party `StarletteDeprecationWarning` already
recorded as Known Issue 7.

**No test was weakened, skipped or deleted to make the suite green.** Every
test that asserted behaviour 6C removes was rewritten against the behaviour
that replaced it:

| Assertion that could no longer hold                                  | What it asserts now                                                                                 |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `test_a_hostile_filename_never_escapes_its_slot_directory`           | `..._becomes_a_generated_name_and_writes_nothing` — the generated name, and nothing on disk         |
| `test_the_stored_bytes_match_the_upload_exactly`                     | `test_the_upload_is_held_in_memory_byte_for_byte`                                                   |
| `test_two_slots_are_stored_side_by_side`                             | `test_two_slots_are_read_independently`                                                             |
| `test_a_rejected_upload_leaves_no_partial_file`                      | `test_a_rejected_upload_is_not_retained_in_memory`                                                  |
| `test_input_directory_rejects_an_unsafe_slot_id`                     | `test_an_unsafe_slot_id_reaches_no_path_at_all` — the stronger fact                                 |
| `test_the_uploaded_source_is_preserved_under_a_generated_name` (API) | split into `..._is_never_written_to_the_run_directory` and `..._is_recorded_under_a_generated_name` |
| `test_a_hostile_upload_filename_cannot_write_outside_the_run` (API)  | `..._writes_nothing_anywhere`                                                                       |
| `test_a_completely_empty_file_fails_the_run` expecting `PARSE_ERROR` | expects `EMPTY_FILE` — the more specific code, not a looser one                                     |
| every `parse_tabular_file(path, ext)` call                           | `parse_tabular_bytes(payload, ext)`                                                                 |

**Control tests — the new tests were proved to catch regressions.** Five
deliberate breakages were introduced one at a time and reverted immediately
afterwards; the suite was re-run to 488 passed after the last revert and
`git status` confirmed the tree was byte-identical, with the one stray file the
first breakage wrote (`data/runs/leaked.bin`) removed.

| Deliberate break                                             | Result                                                                           |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `read_upload` writes the payload to disk after reading it    | 19 failures across `test_runner.py`, `test_runs_api.py`, the Action modules      |
| the limit is checked only after the whole stream is read     | 1 failure (`test_the_limit_stops_the_read_before_the_whole_file_is_accumulated`) |
| an empty upload is accepted instead of refused               | 5 failures across runner, API and `test_product_master_builder.py`               |
| the openpyxl fallback writes a temp file instead of a buffer | 1 failure (`test_the_fallback_reads_the_same_bytes_without_a_temporary_file`)    |
| an upload is attached to the wrong slot                      | 1 failure (`test_each_slot_keeps_its_own_data`)                                  |

### Type checking (Phase 6C)

    npx pyright   ->  43 files analyzed, 0 errors, 0 warnings, 0 informations

### Frontend static checks (Phase 6C)

    npm run lint   ->  exit 0, no errors, no warnings
    npm run build  ->  exit 0, compiled successfully
                       Routes: ○ /   ○ /_not-found  (both static)

Unchanged from Phase 6B, as expected: Phase 6C wrote no frontend code.

### Phase 6C end-to-end verification over real HTTP

The backend was started with `FORGEXL_DATA_DIRECTORY` pointed at a scratch
directory, so the repository's `data/runs/` was never written to.

    GET  /health                        ->  {"status":"ok"}
    GET  /api/actions                   ->  200, both Actions
    POST /api/runs  (CSV, 6C.6)         ->  200 "succeeded", duration_ms 34,
                                            engine "polars-csv", worksheet null,
                                            size 168 = the file's real length,
                                            3 rows in -> 2 out,
                                            duplicate_product_rows_removed 1
    POST /api/runs  (XLSX, 6C.7)        ->  200 "succeeded", engine
                                            "fastexcel-calamine", worksheet
                                            "Sales", 3 rows in -> 2 out
    GET  .../preview?limit=5            ->  200, "Château Réal" and
                                            "Bodegas Muñoz" intact
    GET  .../download/csv               ->  200, accented values intact
    GET  .../download/xlsx              ->  200, `file` reports "Microsoft Excel 2007+"

**Error cases (6C.9), each over real HTTP:**

    empty.csv (0 bytes)          ->  422 EMPTY_FILE "empty.csv is empty."
    a,b\n1,2,3,4\n (ragged)       ->  422 PARSE_ERROR "…CSV file could not be read…"
    "not a workbook".xlsx        ->  422 PARSE_ERROR "…Excel workbook could not be read…"
    two-data-sheet workbook      ->  422 AMBIGUOUS_WORKBOOK, section 17 wording
    sales.json                   ->  422 UNSUPPORTED_EXTENSION
    no file field at all         ->  422 MISSING_INPUT "Sales File is required."
    SKU,Vintage only             ->  422 MISSING_COLUMNS ["Supplier","Producer",
                                                          "Selection","Volume"]
    168 bytes vs a 64-byte limit ->  413 FILE_TOO_LARGE
    grep for a traceback in any error body  ->  none

**MIME type is not trusted (6C.5):**

    valid CSV declared application/octet-stream  ->  succeeded, polars-csv
    XLSX bytes named .csv declared text/csv      ->  422 PARSE_ERROR

**On-disk result after twelve Runs (the decisive 6C check):**

    inputs/ directories created  ->  0
    source.csv / source.xlsx     ->  0
    manifest.json                ->  0
    files actually written       ->  only working/<id>.parquet and
                                     exports/<id>.{csv,xlsx}, and only for
                                     the Runs that succeeded

    CORS   Origin http://127.0.0.1:3000    ->  echoed
           Origin http://evil.example.com  ->  no access-control headers
    Bind   LISTEN 127.0.0.1:8000 only; nothing on 0.0.0.0

**Restart behaviour (build plan Phase 6 rules 14/15) still holds:**

    backend stopped and restarted
      GET /api/runs/{earlier id}  ->  404 UNKNOWN_RUN
      GET /api/actions            ->  200, both Actions still registered
      POST /api/runs              ->  200, a new Run succeeds normally

### Phase 6C browser verification (real headless Chromium)

Phase 6C changed no frontend file, so this exists to prove the Phase 5 UI still
works end to end against the in-memory pipeline. Playwright was installed
**outside** the repository, in the session scratchpad, against the pre-installed
Chromium at `/opt/pw-browsers/chromium-1194`. Both servers were the real ones.

**16/16 checks passed:** page title; "Backend Connected"; the selector populated
from `GET /api/actions` with both Actions; `Version 1.0.0` and the `Sales File`
slot rendered from metadata; state `ready` after choosing a file; **a real CSV
Run through the UI reaching `success`**; **a real XLSX Run through the UI
reaching `success`**; an empty file classified `validation_error` with "is
empty" shown to the user; no `[object Object]`; no traceback on the page; the
browser posting directly to `127.0.0.1:8000/api/runs`; no uncaught page errors.

The browser session wrote **0 `inputs/` directories, 0 `source.*` files and 0
manifests** — only the three exports and Parquet files its three successful Runs
produced.

Both servers were stopped afterwards, ports 3000 and 8000 confirmed free, and
`data/runs/` holds only `.gitkeep`.

---

### Backend test suite (Phase 6B)

Environment note: this session also started in a **fresh ephemeral container**
— `backend/.venv/` and `node_modules/` did not exist. Both were recreated by
following the documented setup exactly (`python3 -m venv backend/.venv`,
`pip install -r backend/requirements.txt`, `npm install`), with no undocumented
step required and no dependency added.

    cd backend && .venv/bin/python -m pytest   ->  455 passed, 1 warning

    Before Phase 6B                                395 passed
    tests/test_run_store.py   (new)                 34
    tests/test_run_model.py   (new)                 26
    tests/test_runner.py      28 -> 32              +4
    tests/test_storage.py     57 -> 53              -4

    tests/test_contract_freeze.py    84  unchanged, and the file itself was
                                         not modified — the Phase 6A freeze
                                         passes untouched through 6B, which is
                                         the whole point of it
    tests/test_runs_api.py           46  (unchanged)
    tests/test_product_master_builder.py 34  (one line: reads the Run Store
                                         instead of the manifest file)
    tests/test_actions.py            30  (unchanged)
    tests/test_parser.py             26  (unchanged)
    tests/test_exact_duplicate_remover.py 21  (unchanged)
    tests/test_action_round_trip.py  17  (unchanged)
    tests/test_preview.py            15  (unchanged)
    tests/test_api.py                14  (unchanged)
    tests/test_schemas.py            12  (unchanged)
    tests/test_export.py             11  (unchanged)

The single warning is the third-party `StarletteDeprecationWarning` already
recorded as Known Issue 7.

**No test was weakened, skipped or deleted to make the suite green.** Nine
tests in `test_storage.py` asserted behaviour that no longer exists, because
the file they asserted against no longer exists. Each was rewritten against
the store that replaced it, not dropped:

| Removed from `test_storage.py`                           | Where its intent now lives                                                                          |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `test_run_exists_is_false_until_a_manifest_is_written`   | `test_a_run_is_unknown_until_it_is_created`                                                         |
| `test_run_exists_reports_false_for_a_malformed_id`       | `test_a_malformed_id_is_never_recorded`                                                             |
| `test_write_manifest_produces_readable_json`             | `test_to_manifest_carries_every_recorded_field`, `test_the_manifest_serialises`                     |
| `test_rewriting_a_manifest_replaces_it_atomically`       | `test_update_replaces_the_recorded_state`                                                           |
| `test_read_manifest_round_trips_what_was_written`        | `test_update_round_trips_everything_it_was_given`                                                   |
| `test_read_manifest_raises_for_an_unknown_run`           | `test_get_run_raises_for_an_unknown_run`                                                            |
| `test_read_manifest_raises_for_a_malformed_run_id`       | `test_get_run_raises_for_a_malformed_id` (4 cases)                                                  |
| `test_write_manifest_leaves_no_temporary_file_behind`    | `test_a_rejected_update_leaves_the_store_unchanged` — the in-memory form of "no half-written state" |
| `test_read_manifest_raises_when_the_manifest_is_corrupt` | `test_a_run_cannot_be_mutated` — state cannot be corrupted behind the store's back                  |

Five new tests replaced them in `test_storage.py` itself, covering
`delete_run_directory` including the two traversal-shaped refusals.

`test_runner.py` kept every test it had; three that read `manifest.json` from
disk now read the Run Store, and three new ones cover run recording and
lifecycle deletion.

**Control tests — the new tests were proved to catch regressions.** Four
deliberate breakages were introduced one at a time and reverted immediately
afterwards; `git status` confirmed the tree was byte-identical after each:

| Deliberate break                                  | Result                                                                                                            |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `InMemoryRunStore.create_run` silently overwrites | 1 failure (`test_a_duplicate_run_id_is_rejected_not_silently_overwritten`)                                        |
| A failed Run is not written back to the store     | 4 failures (`..._keeps_its_directory_and_its_record`, `..._records_every_validation_error`, and both crash cases) |
| `new_run_id` returns `"run-<uuid>"`               | 8+ failures across `test_run_model.py` and `test_run_store.py`                                                    |
| A `run_path` field is added to the `Run` model    | 1 failure (`test_no_run_field_holds_a_filesystem_path`)                                                           |

### Type checking (Phase 6B)

    npx pyright   ->  43 files analyzed, 0 errors, 0 warnings, 0 informations

Four more files than Phase 6A's 39: `app/models/run.py`,
`app/services/run_store.py`, `tests/test_run_model.py`, `tests/test_run_store.py`.

### Frontend static checks (Phase 6B)

    npm run lint   ->  exit 0, no errors, no warnings
    npm run build  ->  exit 0
                       ✓ Compiled successfully in 6.7s
                       Routes: ○ /   ○ /_not-found  (both static)

Unchanged from Phase 6A, as expected: Phase 6B wrote no frontend code.

### Phase 6B end-to-end verification over real HTTP

The backend was started with `FORGEXL_DATA_DIRECTORY` pointed at a scratch
directory, so the repository's `data/runs/` was never written to.

    GET  /health                       ->  {"status":"ok"}
    GET  /api/actions                  ->  200, both Actions
    POST /api/runs (product_master)    ->  200, "succeeded", duration_ms 33,
                                           parser_engine "polars-csv",
                                           3 input rows -> 2 output rows,
                                           metrics duplicate_product_rows_removed 1
    GET  /api/runs/{id}                ->  200, byte-identical to the POST body
                                           (served from the Run Store)
    GET  .../preview?limit=5           ->  200, 2 rows, "Château Réal" intact
    GET  .../download/csv              ->  200, accented values intact
    GET  .../download/xlsx             ->  200, `file` reports "Microsoft Excel 2007+"
    POST /api/runs (missing columns)   ->  422 MISSING_COLUMNS naming all four
    GET  /api/runs/{failed id}         ->  200, status "failed", the full
                                           validation error list, and the
                                           uploaded filename still recorded
    GET  /api/runs/{unknown uuid}      ->  404 UNKNOWN_RUN
    GET  /api/runs/not-a-uuid          ->  404
    GET  /api/runs/..%2Fsecret         ->  404
    GET  .../outputs/nope/preview      ->  404

    manifest.json written anywhere      ->  0 files

On-disk layout in the scratch directory was `inputs/sales_file/source.csv`,
`working/product_master.parquet` and `exports/product_master.{csv,xlsx}` —
**and no `manifest.json`**. That is the expected interim state: run state moved
to memory in 6B, and the remaining files leave in 6C, 6D and 6F.

**Restart behaviour (build plan Phase 6 rules 14/15), verified:**

    backend stopped and restarted
      GET /api/runs/{earlier id}  ->  404 UNKNOWN_RUN
      GET /api/actions            ->  200, both Actions still registered
      POST /api/runs              ->  200, a new Run succeeds normally

    CORS   Origin http://127.0.0.1:3000    ->  echoed
           Origin http://evil.example.com  ->  no access-control headers
    Bind   LISTEN 127.0.0.1:8000 only; nothing on 0.0.0.0

### Phase 6B browser verification (real headless Chromium)

Phase 6B changed no frontend file, so this exists to prove the Phase 5 UI still
works end to end against the new runtime. Playwright was installed **outside**
the repository, in the session scratchpad, against the pre-installed Chromium.
Both servers were the real ones.

**13/13 checks passed:** page title; "Backend Connected"; the selector
populated from `GET /api/actions` with both Actions; `Version 1.0.0` and the
`Sales File` slot rendered from metadata; state `ready` after choosing a file;
a real Run through the UI reaching state `success` and showing "Run Successful";
a second Run with a bad file classified `validation_error` with `Supplier` and
`Volume` named; no `[object Object]`; the browser talking to
`127.0.0.1:8000/api/runs` directly; no uncaught page errors.

Both servers were stopped afterwards, ports 3000 and 8000 confirmed free, and
`data/runs/` still holds only `.gitkeep`.

---

### Backend test suite (Phase 6A)

Environment note: this session started in a **fresh ephemeral container** —
`backend/.venv/` and `node_modules/` did not exist. Both were recreated by
following the documented setup exactly (`python3 -m venv backend/.venv`,
`pip install -r backend/requirements.txt`, `npm install`), with no undocumented
step required. That is an incidental clean-setup confirmation; the formal
clean-setup test is Phase 8.1.

    cd backend && .venv/bin/python -m pytest   ->  395 passed, 1 warning

    Before Phase 6A                                311 passed
    tests/test_contract_freeze.py (new)             84 passed

    tests/test_storage.py            57  (unchanged)
    tests/test_runs_api.py           46  (unchanged)
    tests/test_product_master_builder.py 34  (unchanged)
    tests/test_actions.py            30  (unchanged)
    tests/test_runner.py             28  (unchanged)
    tests/test_parser.py             26  (unchanged)
    tests/test_exact_duplicate_remover.py 21  (unchanged)
    tests/test_action_round_trip.py  17  (unchanged)
    tests/test_preview.py            15  (unchanged)
    tests/test_api.py                14  (unchanged)
    tests/test_schemas.py            12  (unchanged)
    tests/test_export.py             11  (unchanged)
    tests/test_contract_freeze.py    84  (added by Phase 6A)

No existing test was modified, weakened, skipped or deleted. The single warning
is the third-party `StarletteDeprecationWarning` already recorded as Known
Issue 7.

**What the contract freeze pins** (`docs/phase-6a-compatibility-audit.md` §4):

| Area (build plan 6A.5)  | Coverage                                                                                                                                     |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Action registration     | Frozen inventory and registration order; ID uniqueness; `get_action` misses on 5 near-match forms; duplicate-ID and blank-ID rejection       |
| Action input validation | Slot IDs, labels, `required`, accepted extensions and required columns (exact tuple **and order**) for both Actions; metadata immutability   |
| Action execution        | `run()` receives frames keyed by slot ID and returns one frame per declared output ID; exact metric key sets; instances hold no state        |
| Deterministic output    | Repeat execution identical; input frame never mutated; accents, blanks, nulls and near-duplicates survive; first-occurrence and column order |
| Error handling          | Full 15-row code → HTTP-status table; response body shape; single- vs multi-issue `RunValidationError`; no traceback in any rendered error   |
| Public HTTP surface     | The 7-route inventory read from the generated OpenAPI schema; `/health`; `GET /api/actions` against the frozen table; CORS is not wildcard   |
| Schema freeze           | Field names of all 13 models; `RunStatus` values; `MANIFEST_SCHEMA_VERSION == 1`; preview 100/500 and refuse-don't-clamp; 250 MB limit       |
| DataFrame-first (6A.4)  | Both Action modules structurally free of filesystem imports and `open()`; each executes with `DATA_DIRECTORY` pointing nowhere               |

**Control tests — the freeze was proved to actually catch regressions**, not
merely to pass. Three deliberate breakages were introduced one at a time and
each was reverted immediately afterwards:

| Deliberate break                                          | Result                                                                                         |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `exact_duplicate_remover.version` `1.0.0` -> `1.0.1`      | 3 failures (`..._frozen_identity`, `..._declared_metadata`, `..._serves_the_frozen_inventory`) |
| `import os` / `Path` / `open()` added to an Action module | 2 failures (`..._imports_a_filesystem_module`, `..._opens_or_executes_anything`)               |
| `RunManifest.duration_ms` renamed to `elapsed_ms`         | 1 failure (`test_schema_field_names_are_frozen[RunManifest]`)                                  |

`git status` after each revert confirmed the tree was byte-identical to its
committed state.

### Type checking (Phase 6A)

    npx pyright   ->  39 files analyzed, 0 errors, 0 warnings, 0 informations

Control test: appending `_control: int = "not an int"` to
`tests/test_contract_freeze.py` reproduced a `reportAssignmentType` error at
that line, confirming pyright is genuinely analysing the new module rather than
skipping it. The line was removed and pyright re-verified clean.

### Frontend static checks (Phase 6A)

    npm run lint   ->  exit 0, no errors, no warnings
    npm run build  ->  exit 0
                       ✓ Compiled successfully in 5.3s
                       Routes: ○ /   ○ /_not-found  (both static)

Unchanged from Phase 5, as expected: Phase 6A wrote no frontend code.

### Phase 6A end-to-end confirmation over real HTTP

Run to confirm the audited runtime still behaves exactly as documented — i.e.
that Phase 6A changed nothing. The backend was started with
`FORGEXL_DATA_DIRECTORY` pointed at a scratch directory, so the repository's
`data/runs/` was never written to.

    GET  /health                     ->  {"status":"ok"}
    GET  /api/actions                ->  200, both Actions
    POST /api/runs                   ->  200, status "succeeded", duration_ms 50,
                                         parser_engine "polars-csv",
                                         input 3 rows -> output 2 rows
    GET  .../preview?limit=5         ->  200, 2 rows, positional lists,
                                         "Château Réal" intact
    GET  .../download/csv            ->  200, accented values intact
    POST /api/runs (unknown action)  ->  404
    manifest path-leakage grep       ->  0 occurrences of /home/, /tmp/ or data/runs

On-disk layout in the scratch directory was the documented one
(`manifest.json`, `inputs/sales_file/source.csv`,
`working/product_master.parquet`, `exports/product_master.{csv,xlsx}`) —
i.e. the model Phase 6B–6F will replace. The backend was stopped afterwards,
port 8000 confirmed free, and `data/runs/` still holds only `.gitkeep`.

### Backend test suite (Phase 3)

    cd backend && .venv/bin/python -m pytest       ->  231 passed, 1 warning

    tests/test_actions.py    26 passed   Action contract + registry
    tests/test_api.py        10 passed   /health and GET /api/actions
    tests/test_schemas.py    12 passed   manifest / preview serialisation
    tests/test_storage.py    57 passed   Run dirs, path safety, limit, manifest
    tests/test_parser.py     26 passed   CSV, XLSX, ambiguity, engine fallback
    tests/test_runner.py     28 passed   pipeline, validation, failed Runs
    tests/test_export.py     11 passed   Parquet/CSV/XLSX round trips
    tests/test_preview.py    15 passed   paging limits, Parquet-sourced preview
    tests/test_runs_api.py   46 passed   Run endpoints and status codes

The 48 Phase 2 tests still pass unchanged; Phase 3 added 183.

Every test that touches storage runs against its own temporary runs directory
(`conftest.py` redirects `config.RUNS_DIRECTORY`), so the suite never reads or
writes the real `data/runs`.

**Build plan Phase 3 testing list, and the tests that prove each:**

| Phase 3 requirement               | Tests                                                                                                                                            |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| supported file accepted           | `test_a_supported_file_is_accepted`, `test_an_xlsx_upload_is_accepted`                                                                           |
| unsupported extension rejected    | `test_an_unsupported_extension_returns_422`, `test_unsupported_extensions_are_rejected` (8 cases), `test_an_unsupported_upload_is_never_stored`  |
| oversized file rejected           | `test_an_oversized_upload_returns_413`, `test_an_oversized_upload_is_rejected`, `test_a_rejected_upload_leaves_no_partial_file`                  |
| unknown Action rejected           | `test_an_unknown_action_returns_404`                                                                                                             |
| missing required input rejected   | `test_a_missing_required_input_returns_422`, `test_a_missing_required_input_fails_the_run`                                                       |
| missing required columns rejected | `test_missing_required_columns_return_422_with_the_missing_names`, `test_column_comparison_is_exact`, `test_column_comparison_is_case_sensitive` |
| Run directory created             | `test_create_run_creates_the_full_directory_tree`, `test_each_run_gets_its_own_isolated_directory`                                               |
| source preserved                  | `test_the_source_upload_is_preserved_byte_for_byte`, `test_the_uploaded_source_is_preserved_under_a_generated_name`                              |
| manifest created                  | `test_the_manifest_records_the_run_end_to_end`, `test_the_manifest_on_disk_matches_what_was_returned`                                            |
| Parquet output created            | `test_write_output_creates_all_three_artifacts`, `test_the_parquet_round_trips_with_its_schema_intact`                                           |
| CSV output created                | `test_the_csv_export_round_trips`, `test_a_downloaded_csv_reads_back_with_the_expected_data`                                                     |
| XLSX output created               | `test_the_xlsx_export_is_a_real_workbook_that_round_trips`, `test_the_xlsx_download_returns_a_real_workbook`                                     |
| preview returns limited rows      | `test_the_preview_returns_only_the_requested_rows`, `test_only_the_requested_rows_are_returned`                                                  |
| invalid Run ID returns 404        | `test_a_malformed_run_id_returns_404` (4 cases), `test_an_unknown_run_id_returns_404`                                                            |
| invalid output returns 404        | `test_an_unknown_output_returns_404`, `test_a_traversal_shaped_output_id_returns_404` (3 cases)                                                  |

Beyond the required list, the suite also proves: failed Runs retain their
directory, manifest, uploaded file and full error list (3.9); the manifest
contains no dataframe rows and no filesystem paths (sections 11 and 23); the
Excel engine fallback is exercised and recorded (section 6.2); worksheet
ambiguity is refused and never retried with the fallback (section 17); an
Action that raises or omits a declared output fails cleanly without leaking a
traceback; accented text survives every hop; and a hostile upload filename
cannot write outside its Run directory.

### Phase 3 end-to-end verification over real HTTP

Backend started with the real `scripts/dev-backend.sh`; requests issued with
`curl` against `http://127.0.0.1:8000`.

    POST /api/runs (CSV, Origin: http://127.0.0.1:3000)
                          ->  200, status "succeeded", duration_ms 42,
                              inputs[0].parser_engine "polars-csv",
                              stored_filename "source.csv"
    GET  /api/runs/{id}   ->  200
    GET  .../preview?limit=2
                          ->  200, 2 of 3 rows, columns ["a","b"]
    GET  .../download/csv ->  200, text/csv,
                              content-disposition: attachment;
                              filename="passthrough_data.csv"
                              body matched the uploaded data exactly
    GET  .../download/xlsx
                          ->  200, `file` reports "Microsoft Excel 2007+"

    Error paths:
      unknown action      ->  404 UNKNOWN_ACTION
      missing input       ->  422 MISSING_INPUT
      unsupported ext     ->  422 UNSUPPORTED_EXTENSION
      no action_id        ->  400 INVALID_REQUEST
      malformed run id    ->  404 UNKNOWN_RUN
      oversized upload    ->  413 FILE_TOO_LARGE
                              "sample.csv is larger than the 8 bytes upload
                               limit." (FORGEXL_MAX_UPLOAD_BYTES=8)
      multi-sheet .xlsx   ->  422 AMBIGUOUS_WORKBOOK, message matching the
                              wording of build plan section 17

### Run directory layout on disk (build plan section 11)

    data/runs/<run-id>/
      manifest.json
      inputs/source_file/source.csv     preserved upload, generated name
      working/passthrough_data.parquet  internal representation
      exports/passthrough_data.csv
      exports/passthrough_data.xlsx

Failed Runs were confirmed retained on disk with `status: failed` and a
populated `error.code` for `FILE_TOO_LARGE`, `MISSING_INPUT`,
`UNSUPPORTED_EXTENSION` and `AMBIGUOUS_WORKBOOK`. `git check-ignore` confirmed
run artifacts are ignored while `data/runs/.gitkeep` is not. The Run
directories created during verification were removed afterwards, leaving
`data/runs/` as it was found.

The single warning is third-party and is recorded under **Known Issues**.

`backend/pytest.ini` sets `testpaths = tests` and `pythonpath = .`, so the
suite runs with `backend/` as rootdir and imports `app.*` without any
`sys.path` manipulation inside test files.

**Build plan 2.8 requirements, and the tests that prove each:**

| 2.8 requirement                                  | Test                                                                                                                                                                       |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Actions can register                             | `test_action_can_register`, `test_registry_accepts_actions_at_construction`                                                                                                |
| `list_actions` returns registered Actions        | `test_list_actions_returns_registered_actions_in_registration_order`, `test_list_actions_is_empty_for_an_empty_registry`                                                   |
| `get_action` returns the correct Action          | `test_get_action_returns_the_matching_action`                                                                                                                              |
| Unknown Action returns the expected result       | `test_get_action_returns_none_for_an_unknown_id`, `test_get_action_never_guesses_a_near_match` (7 cases)                                                                   |
| Duplicate IDs rejected, not silently overwritten | `test_duplicate_action_id_is_rejected_not_silently_overwritten`, `test_duplicate_action_id_is_rejected_at_construction`, `test_duplicate_action_id_error_is_a_value_error` |

The duplicate-ID test asserts more than "an exception was raised": it also
confirms the first Action is still the one registered afterwards and that the
registry did not grow.

`test_get_action_never_guesses_a_near_match` is parametrised over `""`,
`"ALPHA"`, `" alpha"`, `"alpha "`, `"alph"`, `"alpha_extra"` and
`"../alpha"` — case, whitespace, truncation, extension and a traversal-shaped
ID must all miss rather than resolve.

Registry tests build their own `ActionRegistry` instances from a throwaway
Action (`tests/helpers.make_action`), so they neither depend on nor mutate the
application registry. Two tests do assert against the real one: that it holds
at least one Action, and that its IDs are unique.

### Phase 5 browser verification (real headless Chromium)

Both servers were started with the real `npm run dev`; the browser drove the
actual UI at `http://127.0.0.1:3000`. Playwright was installed **outside** the
repository, in the session scratchpad, and launched against the pre-installed
Chromium. Nothing was stubbed or mocked — every Run below hit the real backend
and produced a real Run directory.

**34/34 checks passed.**

| Build plan | Check                                                            | Result                                                                        |
| ---------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 5.1        | Every backend request goes browser -> `127.0.0.1:8000` directly  | pass — no request to a Next.js route                                          |
| 5.2        | Selector populated from `GET /api/actions`                       | pass — `["Select Action","Exact Duplicate Remover","Product Master Builder"]` |
| 5.3        | Options come from the API, not a hardcoded list                  | pass                                                                          |
| 5.4        | Name + `Version 1.0.0` shown; internal ID not shown              | pass                                                                          |
| 5.5        | Exact Duplicate Remover renders only `Source File`               | pass                                                                          |
| 5.5        | Product Master Builder renders only `Sales File`                 | pass                                                                          |
| 5.5        | Required columns rendered from metadata                          | pass — `SKU, Vintage, Supplier, Producer, Selection, Volume`                  |
| 5.6        | Filename, extension and size shown                               | pass — `sales.csv · .csv · 162 B`                                             |
| 5.6        | File can be removed before the Run                               | pass — state returns to `idle`                                                |
| 5.6        | Drag-and-drop accepts a dropped file                             | pass — synthetic `DragEvent` with a `DataTransfer`                            |
| 5.7        | Run disabled with no Action selected                             | pass                                                                          |
| 5.7        | Run disabled with no file chosen                                 | pass                                                                          |
| 5.7        | Unsupported extension refused client-side                        | pass — "Sales File must be .csv or .xlsx."                                    |
| 5.7        | Run enabled only with Action + required file                     | pass                                                                          |
| 5.8        | Run posted to `POST /api/runs`                                   | pass                                                                          |
| 5.8        | Files arrive under the Action's own slot IDs                     | pass — `inputs/source_file/`, `inputs/sales_file/` on disk                    |
| 5.9        | "Processing…" shown while running                                | pass                                                                          |
| 5.9        | Action selector disabled while running                           | pass                                                                          |
| 5.9        | No fake progress percentage                                      | pass — no `NN%` anywhere                                                      |
| 5.9        | Six rapid clicks submitted exactly one Run                       | pass — 1 `POST /api/runs`                                                     |
| 5.10       | Missing columns listed by name                                   | pass — Supplier, Producer, Selection, Volume                                  |
| 5.10       | No `[object Object]` rendered                                    | pass                                                                          |
| 5.10       | No stack trace rendered                                          | pass                                                                          |
| §30        | `idle` -> `ready` -> `running` -> `success` / `validation_error` | pass — read from `data-workbench-state`                                       |
| —          | XLSX upload runs through the same dynamic slot                   | pass                                                                          |
| —          | Switching Action clears the previous Run and files               | pass                                                                          |
| —          | A second Action runs without reloading the app                   | pass                                                                          |
| —          | No uncaught page errors across the whole session                 | pass                                                                          |

The 19 MB / 400,000-row CSV fixture was also run through the browser
end-to-end and succeeded; it is what the duplicate-submission check ran
against, so the button really was hammered while a Run was genuinely in
flight. (No timings are recorded here — benchmarking is Phase 7G.)

Backend log for the whole session: four `POST /api/runs` 200, one
`POST /api/runs` 422, `GET /api/actions` 200, `GET /health` 200. No traceback,
no 500, no unhandled exception.

### Phase 5 extensibility acceptance test (build plan section 32)

Build plan section 32 calls this "a critical acceptance test", so it was run
against a real third Action rather than reasoned about.

A temporary Action (`tmp_three_slot_probe`, version `9.9.9`) declaring **three**
input slots — `current_sales` (required, `.csv`/`.xlsx`, requires column `SKU`),
`historical_sales` (required, **`.csv` only**) and `assignments`
(**optional**) — was registered in the backend. **Not one frontend file was
touched.**

**11/11 checks passed:**

    New Action appears in the selector                        pass
    Three declared inputs render three upload areas           pass
    Three independent file inputs exist                       pass
    Required slots labelled Required, optional one Optional   pass
    Per-slot accepted extensions come from metadata           pass
    Version 9.9.9 shown from metadata                         pass
    Run disabled with no files                                pass
    Run still disabled with 1 of 2 required slots filled      pass
    Run enabled with both REQUIRED slots filled               pass
      (the optional slot is correctly not waited on)
    .xlsx refused for the .csv-only slot, .csv accepted       pass
      ("Historical Sales must be .csv.")
    No uncaught page errors                                   pass

The probe Action and its registry entry were **removed afterwards**;
`git checkout backend/app/actions/registry.py` restored the registry, and
`git status` confirms the backend is byte-identical to its committed state. The
registry again holds exactly `ExactDuplicateRemoverAction` and
`ProductMasterBuilderAction`.

This is the requirement build plan §3.2 describes as "extremely important" and
§8.5 re-tests at handoff: adding an ordinary Action requires a backend module,
a registry entry and tests — and no frontend change at all.

### Frontend static checks (Phase 5)

    npm run lint          ->  exit 0, no errors, no warnings
    npm run build         ->  exit 0
                              ✓ Compiled successfully in 509ms
                              Routes: ○ /   ○ /_not-found  (both static)

The route list is unchanged from Phase 3: Phase 5 added components and library
modules, not routes. The page remains statically prerendered — the Action list
is fetched in the browser at runtime, so no build-time backend call exists.

### Backend suite (unchanged by Phase 5)

    cd backend && .venv/bin/python -m pytest   ->  311 passed, 1 warning

Re-run after the extensibility probe was removed: still 311 passed. Phase 5
added no backend code and changed no backend file.

### Run artifacts created during verification

Runs created while verifying were confirmed on disk with the expected layout —

    data/runs/<run-id>/
      manifest.json
      inputs/<slot-id>/source.csv        preserved upload, generated name
      working/<output-id>.parquet
      exports/<output-id>.csv
      exports/<output-id>.xlsx

— including a `status: failed` directory retained for the 422 validation
failure. All of them were removed afterwards, leaving `data/runs/` holding only
`.gitkeep`, as it was found.

### Frontend lint

    npm run lint          ->  exit 0, no errors, no warnings
                              (re-run in Phase 3 and Phase 5: still clean)

### Frontend production build

    npm run build         ->  exit 0
                              ✓ Compiled successfully in 5.9s
                              Routes: ○ /   ○ /_not-found  (both static)

    Unchanged from Phase 2 — Phase 3 added no frontend code, and the route
    list proves it.

### Backend import

    backend/.venv/bin/python -c "import app.main"   (cwd = backend/)
                          ->  imports cleanly, no warnings
                              routes: /openapi.json /docs
                                      /docs/oauth2-redirect /redoc /health

### Backend configuration

    config defaults       ->  HOST 127.0.0.1, PORT 8000,
                              DATA_DIRECTORY /home/user/ForgeXL/data,
                              RUNS_DIRECTORY /home/user/ForgeXL/data/runs,
                              MAX_UPLOAD_BYTES 262144000,
                              ORIGINS [127.0.0.1:3000, localhost:3000]
    env override          ->  FORGEXL_BACKEND_PORT=8123 -> PORT 8123
                              FORGEXL_ALLOWED_FRONTEND_ORIGINS=... honoured
                              defaults restored when unset

### Combined startup (re-verified in Phase 3)

    npm run dev           ->  [api] Uvicorn running on http://127.0.0.1:8000
                              [web] - Local: http://127.0.0.1:3000
                              [web] ✓ Ready in 398ms
                              [api] Application startup complete.
                              GET http://127.0.0.1:8000/health   -> {"status":"ok"}
                              GET http://127.0.0.1:8000/api/actions -> 200
                              GET http://127.0.0.1:3000/          -> 200

### Combined startup (Phase 1 record)

    npm run dev           ->  [web] ✓ Ready in 390ms
                              [web] - Local: http://127.0.0.1:3000
                              [api] Uvicorn running on http://127.0.0.1:8000
                              [api] Application startup complete.

### GET /health

    curl http://127.0.0.1:8000/health
                          ->  HTTP/1.1 200 OK
                              content-type: application/json
                              {"status":"ok"}

### CORS

    Origin: http://127.0.0.1:3000   ->  200, access-control-allow-origin:
                                        http://127.0.0.1:3000
    Origin: http://localhost:3000   ->  200, access-control-allow-origin:
                                        http://localhost:3000
    Origin: http://evil.example.com ->  200, NO access-control-* headers
                                        (origin not echoed, no wildcard)
    OPTIONS preflight (allowed)     ->  200, allow-methods: GET, POST
                                        allow-origin: http://127.0.0.1:3000

### Loopback binding

    /proc/net/tcp         ->  LISTEN 127.0.0.1:3000
                              LISTEN 127.0.0.1:8000
                              (nothing on 0.0.0.0)
    curl http://192.0.2.2:3000/     ->  connection refused
    curl http://192.0.2.2:8000/health -> connection refused
                              (192.0.2.2 = this host's non-loopback address)

### Frontend reaches backend (real browser, headless Chromium)

    Backend running:
      title            "Local Data Workbench"
      h1               "Local Data Workbench"
      tagline          "Local data-processing proof of concept."
      indicator        "Backend Connected"
      requests made    GET http://127.0.0.1:8000/health   (direct, not proxied)
      console errors   none

    Backend stopped:
      indicator        "Backend Unavailable"
      console errors   only the browser's own
                       "net::ERR_CONNECTION_REFUSED" resource log lines;
                       no uncaught page errors

### GET /api/actions over real HTTP (Phase 2.7)

    curl -i http://127.0.0.1:8000/api/actions
                          ->  HTTP/1.1 200 OK
                              content-type: application/json
                              content-length: 584
                              {"actions":[{"id":"example_passthrough", …}]}

Full body parsed with `json.tool`: one Action, with `id`, `version`, `name`,
`description`, `inputs` and `outputs`; the single input slot reports
`id=source_file`, `label="Source File"`, `required=true`,
`accepted_extensions=[".csv",".xlsx"]`, `required_columns=[]`; the single
output reports `id=passthrough_data`, `label="Passthrough Data"`,
`formats=["csv","xlsx"]`.

### OpenAPI schema (definitions are serialisable)

    curl http://127.0.0.1:8000/openapi.json
      paths    ->  ['/api/actions', '/health']
      schemas  ->  ['ActionDefinition', 'ActionInput',
                    'ActionListResponse', 'ActionOutput']
      /api/actions 200 -> $ref #/components/schemas/ActionListResponse

### CORS on /api/runs (Phase 3)

    Origin: http://127.0.0.1:3000   ->  access-control-allow-origin:
                                        http://127.0.0.1:3000
    Origin: http://localhost:3000   ->  access-control-allow-origin:
                                        http://localhost:3000
    Origin: http://evil.example.com ->  no access-control-* headers at all
                                        (origin not echoed, no wildcard)

### Loopback binding (Phase 3)

    /proc/net/tcp         ->  0100007F:1F40 state 0A  (127.0.0.1:8000 LISTEN)
                              nothing bound to 0.0.0.0
    http://192.0.2.2:8000/health    ->  connection refused
                              (192.0.2.2 = this host's non-loopback address)

### CORS on /api/actions

    Origin: http://127.0.0.1:3000   ->  200, access-control-allow-origin:
                                        http://127.0.0.1:3000
    Origin: http://localhost:3000   ->  200, access-control-allow-origin:
                                        http://localhost:3000
    Origin: http://evil.example.com ->  200, NO access-control-* headers
    OPTIONS preflight (allowed)     ->  200, allow-methods: GET, POST
                                        allow-origin: http://127.0.0.1:3000
                                        max-age: 600

### Browser reaches /api/actions (real headless Chromium)

Loaded `http://127.0.0.1:3000/`, then issued the cross-origin request from the
page context — the same call the Phase 5 Action selector will make. **No
frontend application code was written or changed for this check**; Playwright
was installed outside the repository, in the session scratchpad.

    page origin      http://127.0.0.1:3000
    fetch status     200
    action ids       ['example_passthrough']
    input slots      ['source_file (Source File) .csv/.xlsx']
    output ids       ['passthrough_data']
    page errors      none

### Combined startup after mounting the router

    npm run dev           ->  [web] ✓ Ready in 376ms
                              [web] - Local: http://127.0.0.1:3000
                              [api] Uvicorn running on http://127.0.0.1:8000
                              [api] Application startup complete.
                              [api] "GET /api/actions HTTP/1.1" 200 OK

    /proc/net/tcp         ->  LISTEN 127.0.0.1:3000
                              LISTEN 127.0.0.1:8000
    http://192.0.2.2:8000/api/actions  ->  connection refused
    http://192.0.2.2:3000/             ->  connection refused

### Type checking

    npx pyright           ->  0 errors, 0 warnings, 0 informations
                              (all backend source and test files, including
                               every module added in Phase 3)

Control test: appending a deliberately invalid assignment to
`backend/app/actions/registry.py` reproduced a `reportAssignmentType` error,
confirming pyright is actually analysing the new modules rather than skipping
them. The file was restored and re-verified clean.

### Environment variable plumbing

    .env.local with NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8123
      -> next dev reports "- Environments: .env.local"
      -> browser requests http://127.0.0.1:8123/health
      -> confirms the documented override in .env.example is real
    (.env.local removed afterwards; default fallback is what ships)

---

## Known Issues

1.  **Next.js telemetry is enabled by default.** `npx next telemetry status`
    reports `Enabled`. This is Next.js's own anonymous build/usage telemetry to
    Vercel; it carries no uploaded data and no application data, so it does not
    violate build plan §8's rule about transmitting uploaded data. It is
    nonetheless an outbound network call from a deliberately local-only
    project. It was **not** changed in Phase 1, because disabling it via
    `next telemetry disable` writes to machine-global config outside this
    repository. Phase 7K ("verify no remote analytics/data calls") should make
    an explicit decision; the repo-local option is exporting
    `NEXT_TELEMETRY_DISABLED=1` in the dev scripts.
2.  **Two `/health` requests per page load in development.** React Strict Mode
    (on by default in `next dev`) invokes effects twice. Expected dev-only
    behaviour, not a bug; a production build issues one request.
3.  **Implementation host is Linux, not macOS.** The build plan targets a Mac
    (§3.4 benchmark hardware, Phase 1.8 "Mac-compatible development script").
    `scripts/dev-backend.sh` uses only portable POSIX/bash constructs and the
    standard `backend/.venv/bin/python` layout, so it should run unchanged on
    macOS, but this has not been executed there. Phase 7 performance numbers
    must be produced on the real target machine to mean anything.
4.  **`README.md` is still the Create Next App default.** It documents
    `app/page.js` (this project uses `src/app/page.js`) and
    `http://localhost:3000` rather than the canonical `http://127.0.0.1:3000`,
    and it does not yet describe backend setup. Rewriting it is Phase 8.2.
5.  **`backend/requirements.txt` pins direct dependencies only.** Transitive
    dependency versions are left to pip. This is reproducible for the packages
    the project actually chose but is not a full lockfile. If exact
    reproducibility becomes necessary, a `pip freeze` lock can be added later.
6.  ~~**No backend test suite yet.**~~ Resolved in Phase 2. `backend/tests/`
    now holds 48 tests, including regression coverage for `/health` and for the
    CORS behaviour of `/api/actions`, which Phase 1 could only verify manually.

7.  **`StarletteDeprecationWarning` from the FastAPI test client.** Running the
    suite prints:

        Using `httpx` with `starlette.testclient` is deprecated;
        install `httpx2` instead.

    Emitted by Starlette 1.6.0 at import of `starlette.testclient`, because
    `httpx` 0.28.1 is installed rather than the newer `httpx2`. It is a
    third-party notice, not a defect in this project's code, and it affects
    only the test client — never the running application. It is deliberately
    **not** suppressed (build plan 7A). `httpx` is the dependency build plan
    §6.2 names, so switching to `httpx2` is a dependency decision that belongs
    to Phase 7A rather than to Phase 2.

8.  ~~**`example_passthrough` is a placeholder and must be removed in
    Phase 4.**~~ Resolved in Phase 4 and re-verified in Phase 5.
    `backend/app/actions/example_passthrough.py` no longer exists, its import
    and registry entry are gone, and `ACTION_REGISTRY` holds exactly
    `ExactDuplicateRemoverAction` and `ProductMasterBuilderAction`. The two
    tests that assert only that the application registry is non-empty with
    unique IDs still pass, as predicted.

9.  ~~**The Action contract is defined but has never executed inside a
    Run.**~~ Resolved in Phase 3. The contract now drives the real pipeline:
    `Action.run()`, `Action.validate()` and `ActionResult` are exercised
    through `POST /api/runs`, and `RunManifest` / `PreviewResponse` are
    produced from real Runs rather than from synthetic round-trips. The
    contract's shape held: no change to `base.py`, `registry.py` or
    `schemas.py` was needed to build the pipeline on top of it.

**Added in Phase 3:**

10. **Phase 2 was committed into the wrong directory; repaired here.**
    Commit `90dd7e8` placed the backend Python package under `src/app/`
    instead of `backend/app/`, and named `schemas.py` as `schema.py`. The
    entire backend suite failed at collection (0 of 48 tests ran) and every
    intra-package import was unresolvable — the cause of the reported
    "Import ... could not be resolved" warnings. Repaired with `git mv` at the
    start of this session; see the Phase 3 entry under **Completed**. Nothing
    outstanding, but the earlier Phase 2 notes in this file describe a state
    that did not exist on disk until the repair.

11. **The upload limit is enforced after the request body has been received.**
    Starlette's `max_part_size` bounds only non-file form fields, so the
    250 MB limit is enforced while the runner copies each file into its Run
    directory. Starlette spools file parts to disk rather than memory, so an
    oversized upload cannot become a memory error, and the response is a clean
    413 — but the bytes do reach the machine before being rejected. Enforcing
    it earlier would mean rejecting on `Content-Length`, which would wrongly
    reject a legitimate multi-file Action whose files are individually under
    the limit but collectively over it. Revisit in Phase 7 if it matters.

12. **A workbook whose second sheet holds even one cell is refused.**
    `_select_data_worksheet` treats any sheet containing cells as a data
    sheet, so a workbook with a data sheet plus a one-cell "Notes" tab raises
    `AMBIGUOUS_WORKBOOK`. This is the conservative reading of build plan
    section 17 ("Accuracy is more important than pretending to support every
    workbook") and is what the section's own error message tells the user to
    fix. A worksheet-selection UI is the intended later replacement.

13. **`POST /api/runs` executes synchronously.** As build plan 3.12 requires
    — no job queue. A large upload therefore holds the request open for the
    duration of the Run. Phase 7G/7H will measure how long that actually is.

14. **Performance has not been measured.** No benchmark fixtures were
    generated and no timing was recorded beyond incidental `duration_ms`
    values on tiny files. That is Phase 7G-7I, and must be done on the real
    target machine.

**Added in Phase 5:**

15. **Phase 4 was implemented but never recorded in this file.** Commit
    `584fea3` added both proof Actions, their fixtures and their tests, and
    removed the placeholder Action, but the session that did it did not update
    `docs/implementation-status.md` — the file still read
    "Last Completed Phase: Phase 3" and "Phase 4 … Not started" at the start of
    the Phase 5 session. Phase 5 verified the Phase 4 work by execution (311
    tests passing, both definitions served by `GET /api/actions`, no frontend
    modification) and wrote the retroactive entry under **Completed**. That
    entry is a record of what was _verified_, not of how the work was done, and
    it does not claim the Phase 4 sub-steps were re-executed. Nothing is
    outstanding, but Phase 4's own testing narrative (4A-4E, and in particular
    the accented-text and Excel round-trip assertions) is described only by the
    tests themselves.

16. ~~**The Run result view is intentionally minimal.**~~ Mostly resolved in
    Phase 6E. On success the UI now shows the row counts, the columns the
    result added or dropped, the Action's own metrics, output selection when a
    Run made more than one table, the paginated preview with type-aware
    alignment, validation warnings, the audit summary and the Run ID. Two
    pieces of the original list remain and are **not** 6E's: the CSV/XLSX
    download buttons belong to Phase 6F (Known Issue 47), and "Start New Run"
    was never a build-plan requirement — choosing another Action or replacing
    a file already clears the previous result. (The last sentence of the
    original entry is also now obsolete: since Phase 6D the backend writes no
    export to `data/runs/<run-id>/exports/`, because no such directory is
    created.)

17. **Client-side extension checking duplicates a backend rule.** Build plan
    5.7 requires the convenience check, so the accepted-extension list is
    evaluated in two places. The frontend copy is driven entirely by the
    `accepted_extensions` the backend sent for that slot, so the two cannot
    disagree about _policy_ — but `fileExtension()` in
    `src/lib/formatters.js` is a second implementation of the backend's
    `extension_of()`, and the two would have to be changed together if the rule
    for deriving an extension from a filename ever changed. The backend remains
    authoritative: a file that slips past the browser is still refused with 422.

18. **`ActionRunner` holds all workflow state in one component.** Six `useState`
    hooks plus a `useRef` guard, with no reducer, context or state library. At
    Phase 5's size this is the simpler option and keeps the data flow readable
    in one file. If Phase 6 adds output selection and preview paging on top, a
    `useReducer` may become the clearer expression; that is a refactor to judge
    then, not a defect now.

19. **Two `/api/actions` requests per page load in development.** The same
    React Strict Mode double-invocation already recorded as Known Issue 2 for
    `/health`. Dev-only; the aborted first request is harmless and a production
    build issues one.

**Added in Phase 6A:**

20. ~~**`InputMetadata.stored_filename` and `MANIFEST_SCHEMA_VERSION` need an
    explicit decision in Phase 6B/6D.**~~ **Resolved in Phase 6C: the field is
    kept and redefined** as the generated name an input is known by, derived
    from its extension alone. It is still the evidence for build plan section
    16's actual rule — that the client's filename never became a name the
    application used. `MANIFEST_SCHEMA_VERSION` stays `1`,
    `test_contract_freeze.py` passes unmodified, and the docstrings in
    `models/schemas.py` and `services/storage.py` state the new meaning. See
    the Phase 6C entry for why dropping it was rejected. The original text
    follows.

    **`InputMetadata.stored_filename` and `MANIFEST_SCHEMA_VERSION` need an
    explicit decision in Phase 6B/6D.** `stored_filename` records the generated
    on-disk name (`source.csv`) and nothing is stored on disk in V1. The
    options are to drop the field — a manifest shape change, so
    `MANIFEST_SCHEMA_VERSION` must be bumped from 1 — or to keep it as the
    logical name of the in-memory input. The frontend does not read it today,
    so either is safe for the UI. It must not be dropped silently: the freeze
    test `test_schema_field_names_are_frozen[RunManifest]` will fail, which is
    the point.

21. **`FORGEXL_DATA_DIRECTORY` becomes a documented setting with no consumer.**
    `.env.example` documents it and `config.DATA_DIRECTORY` / `RUNS_DIRECTORY`
    derive from it. Once run state lives in memory, nothing reads either
    constant. `.env.example` must be corrected in the same phase the constants
    are removed (6I), or the file will document a setting that does nothing.

22. **Roughly half the existing backend suite is coupled to the on-disk model
    and will need rewriting during 6B–6H.** `test_storage.py` (57),
    `test_runner.py` (28), `test_parser.py` (26), `test_preview.py` (15) and
    `test_export.py` (11) assert directories, manifests on disk, stored uploads
    or Parquet files; parts of `test_runs_api.py`, `test_action_round_trip.py`
    and the two Action test modules do the same. That is expected work, not a
    defect — but the rule is **rewrite them against the new runtime, never
    delete or skip one to make the suite green**. `test_actions.py`,
    `test_api.py`, `test_schemas.py` and the new `test_contract_freeze.py` are
    already filesystem-free and must keep passing untouched throughout.

23. **The build plan's Phase 6 was renumbered after Phase 5 was written.** See
    the note at the top of this file. Known Issue 16 and the Phase 5 entry
    refer to "build plan Phase 6 (6.1-6.9)", which no longer exists under that
    numbering; that scope now lives in 6E and 6F. Nothing is wrong in the
    codebase — only the cross-references in the older entries are stale, and
    they are left as written rather than rewritten after the fact.

24. **Phase 6 will supersede build plan section 28 (internal Parquet).**
    Section 28 requires the preview to read `working/<output-id>.parquet`.
    Phase 6E.2 requires the preview to be built from the result DataFrame and
    explicitly forbids generating a temporary spreadsheet to read back. The
    Phase 6 architectural rules state that they override any earlier build-plan
    instruction that conflicts with them, so Parquet becomes unnecessary. This
    is recorded because it is a real, deliberate reversal of an earlier
    requirement, not an oversight.

**Added in Phase 6B:**

25. **Run history no longer survives a backend restart.** Run state is process
    memory in V1, so `GET /api/runs/{id}` returns 404 for a Run created before
    the last restart, and `data/runs/<run-id>/manifest.json` is no longer
    written or read. This is deliberate and is what build plan Phase 6 rules 14
    and 15 authorise ("Run state may be stored in memory for V1", "Restarting
    the FastAPI development server may clear V1 run history"). It supersedes the
    manifest-as-file half of build plan §9.5, §11 and §23 — the manifest itself
    is unchanged and is still what the API returns, it is simply derived from
    the Run rather than read from disk. Recorded here because it is a real,
    deliberate reversal of an earlier requirement, not an oversight. The
    replacement, when persistence is wanted, is a `PersistentRunStore`
    (Known Issue 28).

26. **A backend restart orphans the run directories left on disk.** Uploads,
    Parquet and exports are still written under `data/runs/<run-id>/` until
    6C/6D/6F, but the run record that made them reachable is gone after a
    restart, so nothing can serve or delete them. `runner.delete_run()` cleans
    up a Run it still knows about; it cannot clean up one the process has
    forgotten. This disappears entirely once 6F removes on-disk exports — the
    files it orphans are the files that stop being written. No action is needed
    in 6C-6E beyond not making it worse.

27. **Two functions are called `create_run`.** `run_store.create_run(run)`
    records run state; `storage.create_run(run_id)` makes the directory tree
    the upload and the exports still need. They are always called through their
    module prefix, and the second disappears with the on-disk model, so
    renaming a working Phase 3 function purely for the duration of the
    migration was judged worse than the ambiguity — build plan Phase 6 rule 6
    ("do not rewrite functioning Phase 0/1-5 functionality solely to conform to
    a new naming convention"). The one place they appear together carries a
    comment.

28. **`InMemoryRunStore` grows without bound within one process.** Nothing
    evicts a Run, and V1 has no reason to: the process is a local development
    server, a Run's record is metadata measured in kilobytes, and
    `delete_run()` exists for a caller that wants one gone. It becomes a real
    question only when 6D/6E start retaining result DataFrames in the run —
    that is where a retention policy belongs, alongside 6D.8's "allow memory
    associated with abandoned processing to be released".

29. ~~**Known Issue 20 is deferred to 6C, not resolved.**~~ Resolved in
    Phase 6C — see Known Issue 20. Original text:

    **Known Issue 20 is deferred to 6C, not resolved.**
    `InputMetadata.stored_filename` still records a real generated on-disk name
    because 6B did not change upload handling. 6C is where the upload stops
    reaching disk and the field must either be dropped (bumping
    `MANIFEST_SCHEMA_VERSION`) or redefined as the logical name of the
    in-memory input.

30. **Known Issue 22 is partly worked off.** Of the roughly half of the suite
    coupled to the on-disk model, 6B rewrote the run-state part:
    `test_storage.py` (57 -> 53) and `test_runner.py` (28 -> 32). The parts
    coupled to stored uploads, Parquet and exports — `test_parser.py`,
    `test_preview.py`, `test_export.py` and the on-disk parts of
    `test_runs_api.py`, `test_action_round_trip.py` and the two Action test
    modules — are still ahead, in 6C, 6D, 6E and 6F.

**Added in Phase 6C:**

31. **Phase 6B was committed into the wrong directory; repaired here.**
    Commit `679fff4` ("phase 6B complete") wrote `models/run.py`,
    `models/__init__.py`, `services/run_store.py`, `services/__init__.py`,
    `services/runner.py`, `services/storage.py` and `api/runs.py` under
    **`src/app/`** — the Next.js App Router directory — instead of
    `backend/app/`. This is Known Issue 10 recurring identically for Phase 6B.
    `backend/app/` had no Run model and no Run Store, and the whole suite failed
    at collection with
    `ImportError: cannot import name 'run_store' from 'app.services'` — **0 of
    457 tests ran**. Repaired at the start of this session with `git mv`, so
    history is preserved and no file content was edited; the suite then reported
    457 passed. Nothing is outstanding, but the Phase 6B entry above describes a
    state that did not exist under `backend/` until this repair, and **any
    future session must check that new backend modules landed under
    `backend/app/` before reporting a phase complete** — this has now happened
    twice.

32. **`backend/tests/test_runner.py` was destroyed by the Phase 6B commit and
    has been rebuilt.** The file was overwritten with a byte-identical copy of
    `tests/test_run_store.py` (verified: identical md5), so Phase 3's entire
    runner pipeline module was gone and the 457-test count was inflated by 34
    Run Store tests collected twice. The Phase 6B entry's claim of
    "`test_runner.py` 28 -> 32" describes work that is not in the repository.
    The Phase 6A version (28 tests, commit `f481552`) was recovered and carried
    forward: through 6B (outcomes read from the Run Store rather than
    `manifest.json`, plus run-recording and lifecycle-deletion coverage) and
    through 6C (uploads in memory), reaching **45 tests**. The rebuild is a
    reconstruction from the 6A source plus the documented 6B intent, **not** a
    recovery of the 6B session's actual edits, which were never committed.

33. **A failed Run still creates two empty directories.**
    `storage.create_run()` builds `working/` and `exports/` before the inputs
    are read, so a Run that fails validation leaves both behind empty — and,
    since 6C, with nothing else in the Run directory at all. Moving the call
    later would be a behaviour change 6C does not ask for, and both directories
    disappear when 6F generates exports in memory. Harmless interim state; no
    action needed in 6D or 6E beyond not making it worse.

34. **An upload is held in memory at up to `MAX_UPLOAD_BYTES`.** That is the
    architecture Phase 6 mandates, and the read is bounded so the buffer never
    exceeds the limit — but with the 250 MB default, a single upload can hold
    250 MB of process memory, and `bytes(buffer)` briefly doubles that at the
    moment the payload is finalised. The runner releases the payloads as soon as
    they have become dataframes, which keeps the peak to the parse itself rather
    than the Run's whole lifetime. Phase 7G-7I is where the real figure should
    be measured on the target machine; if it matters, the lever is
    `FORGEXL_MAX_UPLOAD_BYTES`, which is already configurable.

35. **Known Issue 11 has changed shape.** The upload limit is still enforced
    after the request body has reached the machine (Starlette's `max_part_size`
    still bounds only non-file fields), but it is now enforced while the runner
    reads the part into memory rather than while copying it to disk. Starlette
    still spools file parts, so an oversized upload still cannot become a memory
    error, and the response is still a clean 413. The underlying trade-off is
    unchanged; only the location of the check moved.

36. **Known Issue 22 is further worked off.** Of the roughly half of the suite
    coupled to the on-disk model, 6C rewrote the upload and parsing part:
    `test_parser.py` (26 -> 36, now entirely byte-based), the upload half of
    `test_storage.py` (53 -> 60) and the upload assertions in
    `test_runs_api.py` (46 -> 49). `test_export.py` and `test_action_round_trip.py`
    now read spreadsheets back from bytes rather than paths, though the
    artifacts they read are still written to disk. What remains coupled is the
    **output** side — `test_preview.py` (Parquet), `test_export.py` (the three
    written artifacts) and the download half of `test_runs_api.py` — which is
    6E's and 6F's work.

**Added in Phase 6D:**

37. **`backend/tests/test_runner.py` was destroyed by the Phase 6C commit and
    has been rebuilt — the second time this has happened to this file.**
    Commit `70c41b1` ("phase 6C fix") left `test_runner.py` byte-identical to
    `tests/test_runs_api.py` (verified: identical md5 in the commit itself, not
    only on disk). Known Issue 32 recorded the same failure for Phase 6B, where
    the donor was `test_run_store.py`. The Phase 6C entry's claim of
    "`test_runner.py` … reaching 45 tests" describes work that is not in the
    repository. The Phase 6A version (28 tests, commit `f481552`) was recovered
    and carried forward through 6B (outcomes read from the Run Store), 6C
    (uploads in memory) and 6D (results in memory), reaching **51 tests**. It is
    a reconstruction from the 6A source plus the documented 6B/6C intent, **not**
    a recovery of those sessions' actual edits, which were never committed —
    which is why its count differs from the 45 the 6C entry claims.

    **This class of defect has now occurred four times** (Known Issues 10, 31,
    32 and this one). Any future session must, before writing any code, verify
    that (a) new backend modules landed under `backend/app/` and not `src/app/`,
    and (b) no two test modules are byte-identical:

        cd backend/tests && md5sum *.py | awk '{print $1}' | sort | uniq -d

    An empty result is the passing condition.

38. **`backend/tests/test_storage.py` was never updated by the Phase 6C
    commit, and the committed suite therefore failed.** The file in `70c41b1`
    is byte-identical to its Phase 6B version, so it still asserted
    `storage.store_upload()` and `RunPaths.input_directory()` — both removed by
    Phase 6C. `cd backend && .venv/bin/python -m pytest` on the committed state
    reported **25 failed, 460 passed**. The Phase 6C entry's claim of
    "`test_storage.py` 53 -> 60, upload half rewritten" describes work that is
    not in the repository. The module was rewritten in this session against the
    Phase 6C in-memory intake (`read_upload`, `LoadedUpload`, and the stronger
    "a hostile slot ID reaches no path at all" assertion), reaching **64 tests**,
    and the suite was brought to green **before any Phase 6D code was written**.

39. **`services/storage.py` now holds dead runtime code.** `create_run()`,
    `RunPaths` and its `working` / `exports` / `working_artifact` /
    `export_artifact` members, `run_paths()`, `runs_directory()` and
    `delete_run_directory()` are no longer called by anything in `app/` — the
    pipeline builds no path at all. They were **deliberately left in place**:
    build plan **6I.1** explicitly owns removing "run-directory creation …
    working-directory creation … export-directory creation … path-building
    helpers", and removing them in 6D would be doing a later phase's work.
    `tests/test_storage.py` still covers them, so they cannot rot silently in
    the meantime. `config.DATA_DIRECTORY` and `RUNS_DIRECTORY` are likewise
    now read only by this dead code and by the test fixtures — see Known
    Issue 21, whose `.env.example` correction is due in the same phase.

40. **A Run's result frames stay in memory for the life of the process.**
    Known Issue 28 predicted this would become a real question "when 6D/6E start
    retaining result DataFrames in the run" — it now has. `InMemoryRunStore`
    evicts nothing, so a session that runs twenty 50,000-row Actions holds all
    twenty result frames until the backend restarts or `delete_run()` is called.
    Nothing calls `delete_run()` today: no route exposes it (build plan 6B asks
    for the capability, not an endpoint). For a local single-user POC this is
    acceptable and is what build plan Phase 6 rules 14/15 authorise, and 6D.8's
    actual requirement — that the memory _can_ be released — is met and pinned
    by a `weakref` test. A retention policy or an eviction rule is a real
    decision for Phase 7J ("Memory/Architecture Review"), which is where the
    measurement belongs.

41. **Build plan section 28 (internal Parquet) is now superseded in code, not
    just on paper.** Known Issue 24 recorded the reversal as deliberate and
    predicted 6E would carry it out. 6D's completion criteria ("no required
    `working/` directory") forced it a phase earlier, so `working/<id>.parquet`
    is no longer written and `pl.scan_parquet` no longer appears in the
    backend. Nothing is outstanding; the cross-reference in Known Issue 24 and
    in the Phase 6C notes is simply a phase off.

42. **Preview and download changed mechanism in 6D rather than in 6E/6F.**
    `docs/phase-6a-compatibility-audit.md` §7.1 assigns `preview.read_preview`
    to 6E and `export.py` / the download route to 6F. Once 6D stopped writing
    files, leaving those two reading files would have broken two working
    endpoints, so both were re-pointed at the retained frames. **Only the
    mechanism moved**: the `PreviewResponse` shape, the 100/500 limit rules, the
    download media types and the `<output-id>.<format>` filename are all
    byte-identical to their pre-6D behaviour. What 6E and 6F still own is listed
    under **Next Phase**. Recorded because it is a real, deliberate departure
    from the Phase 6A migration schedule, not an oversight.

43. ~~**A failed Run still creates two empty directories** (Known Issue 33).~~
    Resolved in Phase 6D: no directory is created at all.

44. ~~**A backend restart orphans the run directories left on disk**
    (Known Issue 26).~~ Resolved in Phase 6D, exactly as that issue predicted:
    the files it orphaned are the files that stopped being written.

45. ~~**Two functions are called `create_run`** (Known Issue 27).~~ Resolved in
    Phase 6D: `storage.create_run()` is no longer called by anything, so
    `run_store.create_run()` is the only one in the pipeline.

46. **Known Issue 22 is now fully worked off.** The half of the suite that was
    coupled to the on-disk model has been rewritten against the new runtime, in
    four stages: 6B (run state), 6C (uploads and parsing), and 6D (the output
    side — `test_export.py` 11 -> 15, `test_preview.py` 15 -> 17, and the
    download/preview half of `test_runs_api.py`). Nothing in the suite now reads
    or writes a Run artifact. `test_actions.py`, `test_api.py`,
    `test_schemas.py` and `test_contract_freeze.py` remain untouched throughout,
    as that issue required.

**Added in Phase 6E:**

47. **Results cannot be exported from the browser yet.** Known Issue 16's
    remaining half. The results view now shows the status, the metrics, the
    paginated preview, validation warnings and the audit summary — but not the
    CSV/XLSX download buttons. That is deliberate: build plan 6E's completion
    criteria list five things a completed Run must display and export is not
    among them, and **Phase 6F owns export**, including 6F.6, which changes the
    download filename from `<output-id>.<format>` to
    `forgexl-<action>-<timestamp>.<format>`. Building the buttons in 6E would
    have been scaffolding for a later phase against a convention about to
    change. The two download endpoints themselves work and were not touched.

48. **`test_contract_freeze.py` is no longer "unchanged since 6A".** It was
    amended once, in this phase, for the manifest-shape change build plan
    6E.1/6E.4/6E.5 requires. Three schema field lists, the
    `MANIFEST_SCHEMA_VERSION` assertion and one `PreviewResponse` construction
    changed; every other assertion is untouched. Recorded because the module's
    value came from passing unmodified, and a future session must be able to
    see that the change was a decision rather than drift. **6F, 6G, 6H and 6I
    should leave it unchanged again** — none of them has a reason to alter a
    frozen contract.

49. **`MANIFEST_SCHEMA_VERSION` is 2, and nothing reads a version 1 manifest.**
    The bump is honest — the new required fields mean an old manifest would not
    validate — but it has no consumer, because Phase 6B stopped writing
    `manifest.json` and no manifest outlives the process. The constant is
    documentation of a shape change, not a migration path. It becomes load
    bearing only if run history is ever persisted (the `PersistentRunStore`
    extension point 6I.8 records).

50. **The audit duplicates facts the manifest already carries.**
    `audit.action`, `audit.status`, `audit.metrics`, `audit.warnings`,
    `audit.errors` and `audit.duration_ms` restate manifest fields, and
    `audit.inputs` restates part of `manifest.inputs`. That is what build plan
    6E.5 asks for — an assembled explanation — and the duplication is made safe
    by deriving the audit in `Run.to_audit()` rather than storing it, with a
    test asserting the two agree field by field. It does make the response
    body larger; the cost is a few hundred bytes on a metadata payload that
    never contains rows.

51. **`rows_affected` is only as good as the Action that reports it.** The
    runner cannot verify the figure — it is the Action's own claim about what
    it did, exactly as the `metrics` dict is. A test proves the _measured_
    metadata (row counts, columns, schema) cannot be corrupted by an Action
    that reports a wrong count, but a wrong `rows_affected` would be displayed
    as given. Both real Actions compute it from the same two heights the
    freeze test already checks for internal consistency.

52. **Two preview requests per page in development.** React Strict Mode invokes
    effects twice in `next dev`, so selecting a result table fires the preview
    fetch twice. The same dev-only behaviour as Known Issues 2 and 19; a
    production build issues one request. Observed directly during the browser
    verification.

53. **A very wide result relies on horizontal scrolling alone.** The preview
    table scrolls inside its own `overflow-x-auto` box, which is what build
    plan section 31 requires, but there is no column freezing or column
    selection — a 60-column result means a lot of scrolling. Deliberately out
    of scope: section 31 also says not to add a spreadsheet component to the
    POC.

None of the above blocks Phase 6F.

---

## Deviations From Build Plan

1. **`src/` directory retained** (carried forward from Phase 0). Build plan §10
   sketches root-level `app/`, `components/` and `lib/`. This repository was
   created by Create Next App with `src/app/`, and `jsconfig.json` maps
   `@/*` → `./src/*`. Phase 1.1 explicitly instructs: _"Do not add a `src/`
   folder unless the repository already uses one. Prefer the simplest existing
   convention."_ Accordingly Phase 1 placed the health indicator at
   `src/components/BackendStatus.js`, and Phase 5's `lib/api.js` should become
   `src/lib/api.js`. Layout only; no architectural effect.

2. **`.gitignore` carries a `!.env.example` negation** (from Phase 0). Not
   listed in §0.5, but required so §20 / Phase 1.7 (`.env.example` committed as
   documentation) is achievable. Verified working.

3. **The startup script is `scripts/dev-backend.sh`, not `scripts/dev.sh`.**
   §10 sketches a single `scripts/dev.sh`. Phase 1.8 permits "an npm script or
   Mac-compatible development script", explicitly suggests `concurrently`, and
   §8.3 states the target workflow is `npm run dev`. The combined launcher is
   therefore the `dev` npm script, and the shell script is named for what it
   actually does — start the backend — rather than being misleadingly called
   `dev.sh` while starting only one service. `npm run dev` remains the single
   command a user runs.

4. **Backend environment variables are `FORGEXL_`-prefixed.** §20 lists the
   settings to centralize (`HOST`, `PORT`, `DATA_DIRECTORY`,
   `MAX_UPLOAD_BYTES`, `ALLOWED_FRONTEND_ORIGINS`) and those are the constant
   names inside `config.py`. The environment variables that override them are
   prefixed to avoid colliding with the generic `HOST`/`PORT` variables that
   `next dev` and other local tooling read from the same shell.

5. **Create Next App demo assets deleted.** `public/file.svg`, `globe.svg`,
   `next.svg`, `vercel.svg` and `window.svg` were removed under Phase 1.2
   ("Remove Starter Noise") after grep confirmed nothing references them.
   `public/.gitkeep` preserves the directory §10 expects. `src/app/favicon.ico`
   was kept — replacing the icon is not Phase 1 work.

6. **`pyrightconfig.json` added** (after Phase 1, at the user's request).
   Not part of §10's structure and not application code — editor tooling only,
   with no runtime effect. It fixes two false errors the language server
   reports against correct code: fastapi/uvicorn "could not be resolved" (the
   type checker looking at the system interpreter instead of `backend/.venv`),
   and `from app import config` -> "unknown import symbol" (Pylance's
   `autoSearchPaths` treats `./src` as a Python source root, so the Next.js
   `src/app/` directory shadows the real `backend/app` package).

   The config declares an execution environment rooted at `backend/`, which
   mirrors what `scripts/dev-backend.sh` does to `sys.path` at runtime by
   running `python -m app.main` from that directory, and points `venvPath` /
   `venv` at `backend/.venv`. Editor-agnostic: any pyright-based language
   server reads it (Pylance, Neovim, Zed, the pyright CLI). A VS Code-only
   `.vscode/settings.json` was added first and then removed in favour of this.

   Verified by running `npx pyright`: 3 files analyzed, 0 errors. A control
   test (temporarily adding a genuinely bad import) reproduced both of the
   original error messages, confirming the checks are active rather than the
   config silently skipping the backend.

   One VS Code caveat: `venvPath`/`venv` are honoured by the pyright CLI, but
   Pylance uses the interpreter selected in VS Code, so
   **Python: Select Interpreter** -> `./backend/.venv/bin/python` is still
   needed there. The import-path half of the fix applies everywhere.

**Added in Phase 2:**

7. **`ActionRegistry` is a class, not a bare module-level dict.** §25 sketches
   `ACTION_REGISTRY = {action.id: action, ...}` with `list_actions()` and
   `get_action(action_id)` functions. Those exist and are what the API calls;
   `ACTION_REGISTRY` is simply an `ActionRegistry` instance rather than a raw
   dict. The reason is testing: build plan 2.8 requires proving that duplicate
   IDs are rejected and that lookups miss correctly, and instantiable
   registries let each test build an isolated one instead of mutating a global
   dict and resetting it afterwards. No behavioural difference; the class is
   ~40 lines with three methods.

8. **`backend/tests/` has an `__init__.py` and a `helpers.py`.** §10 sketches
   `tests/` containing `fixtures/` and the test modules. The package marker
   plus `pythonpath = .` makes `from tests.helpers import make_action` an
   unambiguous absolute import rather than relying on pytest's implicit
   `sys.path` insertion. `fixtures/` was **not** created: it would be an empty
   untracked directory until Phase 4 supplies real fixture data.

9. **`InputMetadata` carries a `worksheet` field.** The manifest sketch in §23
   does not list it, but §3.6 requires recording "the worksheet and parser
   engine used" for XLSX inputs. §23 states that "exact internal
   implementation may differ slightly if justified". Null for CSV inputs.

10. **`PreviewResponse.rows` is a list of positional lists, not a list of
    objects.** §21 requires the response to contain `columns`, `rows`,
    `offset`, `limit` and `total_rows`, without fixing the row encoding.
    Positional rows aligned to `columns` avoid repeating every column name on
    every row, and sidestep the ambiguity a dataset with duplicate column
    names would create in an object encoding. Phase 3 will materialise rows
    as lists when reading Parquet.

11. **`pydantic` was added to `backend/requirements.txt`.** §6.2's dependency
    list does not name it (it arrives with FastAPI), but §15 mandates Pydantic
    models for API-facing structured data and `app.models.schemas` now imports
    it directly. A direct import should be a declared dependency. The resolved
    version is unchanged (2.13.4); nothing was upgraded or downgraded.

12. **One `# pyright: ignore` in test code.** `npx pyright` initially reported
    three errors, all in the new tests. Two were fixed properly: an
    `Action | None` lookup was bound to a variable and null-checked, and a test
    that relied on Pydantic coercing tuples into `list[list[Any]]` was deleted
    rather than kept against the declared type. The third remains: a test
    instantiates a deliberately abstract Action to prove `TypeError` is raised,
    so that line carries a narrow `# pyright: ignore[reportAbstractUsage]` with
    a comment — the static error _is_ the assertion. No error in application
    code was suppressed; pyright reports 0 errors.

**Added in Phase 3:**

13. **`backend/app/errors.py` is a new module not sketched in section 10.**
    Build plan section 15 requires "Python exceptions internally … converted
    into structured API errors at the boundary", and section 22 fixes the
    error shape. Putting the taxonomy in one `app`-level module lets
    `main.py` convert every internal failure with a single handler instead of
    a long `except` chain spread across the API modules, and lets a service
    raise the right error without importing FastAPI. It is shared vocabulary
    rather than a service, so it sits beside `config.py` rather than under
    `services/`.

14. **Three test modules beyond the three section 10 sketches.** Section 10
    lists `test_actions.py`, `test_parser.py` and `test_api.py`.
    `test_parser.py` exists as named; the Run endpoints went into
    `test_runs_api.py` rather than swelling `test_api.py`, and
    `test_storage.py`, `test_runner.py`, `test_export.py` and
    `test_preview.py` each cover one service. Section 15 asks for small files;
    one 230-test module would not be that. `conftest.py` holds the shared
    fixtures. `fixtures/` still does not exist — test data is generated in
    process by `helpers.csv_bytes` / `helpers.xlsx_bytes`, so there are no
    binary blobs in the repository; Phase 4 may add real fixture files.

15. **XLSX is read through fastexcel directly, not `pl.read_excel`.**
    `pl.read_excel` defaults to `drop_empty_rows=True` and
    `drop_empty_cols=True`, which would silently discard data and violate
    build plan section 3.3 ("never silently drop rows"). Calling
    `ExcelReader.load_sheet(...).to_polars()` uses the same calamine engine
    the build plan prefers, with no implicit row or column removal.

16. **A submitted form field the Action does not declare produces a warning,
    not an error.** The build plan does not say what to do with an unexpected
    input slot. Ignoring it silently would hide a frontend/backend mismatch;
    failing the Run would be harsher than the situation warrants. It is
    recorded as an `UNEXPECTED_INPUT` warning in the manifest, and warnings
    never fail a Run (section 6.2).

17. **An over-large preview `limit` is refused, not clamped.** Build plan
    section 21 sets the maximum at 500 without saying what to do above it.
    A silently clamped page would misreport what the caller received, so
    `limit=501` returns 400 with the maximum in `details`.

**Added in Phase 5:**

18. **Frontend files live under `src/lib/` and `src/components/workbench/`.**
    Build plan §10 sketches root-level `lib/api.js`, `lib/formatters.js` and a
    flat `components/` directory. The `src/` prefix is the deviation already
    recorded as item 1 and carried forward. Within `src/components/`, the six
    Phase 5 components are grouped in a `workbench/` subdirectory, matching the
    `backend/BackendStatus.jsx` grouping Phase 1 established, rather than being
    scattered at the top level. File names are otherwise exactly those §10
    lists. Layout only; no architectural effect.

19. **An extra component, `ActionRunner.jsx`, is not in §10's list.** §10 names
    the presentational components; something has to own the state that connects
    them. Putting that in `ActionRunner` rather than in `page.jsx` keeps
    `page.jsx` a server component, so the client boundary is one explicit file
    instead of the whole page (build plan §15, "keep server-only and
    client-only code separated"). It contains no Action-specific logic.

20. **`ResultsSummary.js`, `DataPreview.js` and `ExportButtons.js` were not
    created.** §10 lists them among the eventual components, but they implement
    build plan Phase 6 (6.1, 6.4, 6.7), and Phase 5's exit criteria explicitly
    defer result presentation: "Result preview/export refinement is Phase 6."
    Creating empty or placeholder versions now would be scaffolding for a Phase
    that has not been authorised.

21. **`src/lib/formatters.js` holds three helpers, not a general formatting
    library.** §10 lists the file; Phase 5 only needs file-size rendering,
    extension extraction and list phrasing, so only those exist. Number and
    duration formatting arrive when Phase 6 needs them.

22. **The frontend has no automated test suite.** Build plan Phase 5 specifies
    no frontend tests, and §37's definition of done requires "frontend lint
    passes" and "frontend production build passes" rather than frontend unit
    tests. Phase 5 was verified instead by driving the real UI in a real
    browser against the real backend (34 checks, plus 11 extensibility checks).
    Those scripts live in the session scratchpad, outside the repository, so
    they are evidence rather than a committed regression suite — re-running
    them in a later session means rewriting them. If frontend regressions
    become a concern, adding a committed browser test is a Phase 7A decision.

23. **`BackendStatus.jsx` was modified in Phase 5.** It is Phase 1 code, but
    build plan 5.1 requires that backend URLs not be scattered across
    components, and it held the only other copy of the base URL. It now calls
    `fetchHealth()` from `src/lib/api.js`. Behaviour is unchanged; this is the
    only pre-existing frontend file Phase 5 touched other than `page.jsx`.

**Added in Phase 6A:**

24. **A new document, `docs/phase-6a-compatibility-audit.md`.** Build plan §10
    sketches `docs/` as holding `build-plan.md` and
    `implementation-status.md` only. Phase 6A's deliverable is explicitly
    "documented filesystem dependency points ... identified public contracts
    ... a clear list of components requiring migration", which is a reference
    document, not a status update. Folding a 300-line dependency inventory
    into this status file would bury it; keeping it separate lets 6I re-run the
    audit against it directly (build plan 6I.2). This file links to it and
    summarises its conclusions.

25. **The contract-freeze tests live in a new module rather than being spread
    across the existing ones.** Build plan §10 sketches three test modules and
    the suite already has twelve (Deviation 14). `test_contract_freeze.py`
    exists as one module because its defining property is that it is
    filesystem-independent and must survive 6B–6I unchanged — a property that
    only holds if the tests are kept together and away from the fixtures
    (`runs_dir`, `run_paths`) that disappear with the on-disk model.

**Added in Phase 6B:**

26. **Two modules §10 does not sketch: `app/models/run.py` and
    `app/services/run_store.py`.** Build plan §10 lists `models/schemas.py` and
    five services. Build plan 6B explicitly requires "a logical Run model" and
    "a Run Store abstraction" as separate concepts — the model is a value, the
    store is a service — so folding either into `schemas.py` or `storage.py`
    would have contradicted the phase that asked for them. §10 predates
    Phase 6 by several revisions; Phase 6's rules override it where they
    conflict.

27. **The logical Run is a frozen dataclass, not a Pydantic model.**
    `models/` otherwise holds Pydantic schemas. The Run is runtime state that
    is never serialised directly, and from 6D/6E it will carry Polars frames
    that Pydantic cannot validate — the same reasoning Phase 2 applied to
    `ActionResult` (build plan 2.2 permits it explicitly). Pydantic keeps the
    boundary: `Run.to_manifest()` returns the unchanged `RunManifest`.

28. **`new_run_id` and `parse_run_id` moved from `services/storage.py` to
    `models/run.py`.** The Phase 6A audit's §7.1 said to keep them; it did not
    say where. Run identity belongs to the Run, and `storage.py` is dismantled
    across 6C-6I, so leaving the ID convention inside it would have meant
    moving it later anyway. Neither function's behaviour changed by a
    character, and `storage.py` imports both — so `storage.new_run_id` and
    `storage.parse_run_id` still resolve and no existing call site changed.

29. **Two more test modules, `test_run_model.py` and `test_run_store.py`.**
    The same reasoning as Deviations 14 and 25: one module per unit under test.
    Both are deliberately filesystem-free, like `test_contract_freeze.py`, so
    they survive 6C-6I unchanged.

30. **`storage.delete_run_directory()` is a new function that deletes files.**
    Build plan 6B.6 asks for deletion that releases a Run's "associated runtime
    state". While uploads and exports are still on disk, forgetting only the
    record would leave the user's uploaded file orphaned, so
    `runner.delete_run()` removes both. The function refuses anything that is
    not a direct child of the runs directory, is called from exactly one place,
    and is not reachable over HTTP. It disappears with the on-disk model.

31. **Build plan section 28 (internal Parquet) was removed in Phase 6D, not
    6E.** Section 28 requires each output to be written as
    `working/<output-id>.parquet` and the preview to read it back. Build plan
    6D.7 forbids intermediary spreadsheet files and 6D's completion criteria
    requires a Run to execute "with no required … `working/` … directory", so
    Parquet could not survive this phase. The Phase 6 architectural rules state
    that they override any earlier build-plan instruction that conflicts with
    them. Known Issue 24 recorded the reversal as deliberate when Phase 6A
    found it; only the phase it happened in differs from the prediction.

32. **The preview and download _mechanisms_ moved in Phase 6D, ahead of the
    Phase 6A migration schedule.** `docs/phase-6a-compatibility-audit.md` §7.1
    assigns `preview.read_preview` to 6E and `services/export.py` plus the
    download route to 6F. Once 6D stopped writing files, leaving those two
    reading files would have broken two working endpoints, so both now read the
    Run's retained result frames. The change was held to exactly that: the
    `PreviewResponse` shape, the 100/500 limit rules, the download media types
    and the `<output-id>.<format>` filename are byte-identical to their pre-6D
    behaviour, and every deliverable 6E and 6F actually list is untouched (see
    **Next Phase**). Recorded as Known Issue 42.

33. **`RunStatus` was kept at three values rather than the six build plan 6D.6
    lists.** 6D.6 offers `created / validating / ready / running / completed /
failed` as an example and says explicitly: "Use existing equivalent status
    names if already established." `running` / `succeeded` / `failed` are
    established, are public API, and are pinned by `test_contract_freeze.py`.
    A Run executes synchronously with no job queue (build plan 3.12), so
    `created`, `validating` and `ready` are states no client can observe;
    `completed` is `succeeded`. Adding statuses nothing could ever report would
    have changed a frozen contract for no caller's benefit.

34. **`MANIFEST_SCHEMA_VERSION` moved from 1 to 2, and
    `test_contract_freeze.py` was amended (Phase 6E).** Build plan section 23
    fixes the manifest's approximate shape and Phase 6A froze it exactly.
    Build plan 6E.1, 6E.4 and 6E.5 require result metadata, column type
    information and an audit summary, none of which that shape could carry, so
    the manifest was extended: `OutputMetadata` gained four required fields,
    `RunManifest` gained a required `audit`, and `PreviewResponse` gained
    `column_schema`. **Nothing already frozen changed name, order or meaning.**
    The version constant was bumped because a version 1 manifest no longer
    validates. Recorded here and in Known Issues 48 and 49 because Phase 6A's
    whole purpose was to make this a decision rather than a side effect.

35. **`ActionResult` gained an optional `rows_affected` field (Phase 6E).**
    Build plan 6E.5 asks for "rows affected" and qualifies it with "where
    supported by the Action". Deriving it generically from two row counts would
    be a guess about what a transformation did, which build plan section 3.3
    forbids, so Actions state it or say nothing. It is optional and defaults to
    `None`, so an Action that ignores it is unaffected — the field is additive
    to the Action contract, not a new requirement on it. Both real Actions set
    it; neither changed its ID, version, inputs, outputs, metrics or
    transformation.

36. **One module §10 does not sketch: `app/services/results.py` (Phase 6E).**
    Build plan §10 lists five services. The result-metadata logic could have
    gone into `runner.py`, but the runner, the preview and (in 6F) the export
    all need the same description of a result frame, and describing a result
    twice is how two descriptions come to disagree. Same reasoning that placed
    `run_store.py` beside them in 6B.

37. **Four frontend components §10 does not sketch, and one it does under a
    different name (Phase 6E).** §10 lists `ResultsSummary`, `DataPreview` and
    `ExportButtons`. `ResultsSummary.jsx` and `DataPreview.jsx` exist under
    those names. `AuditSummary.jsx` and `OutputSelector.jsx` are additions
    required by build plan 6E.5 and by 6E.1's "available result tables", which
    §10 predates. `ExportButtons` was **not** built — see Known Issue 47.

No architectural conflicts were found. Framework, router, language, styling,
backend framework, data engine and lockfile all match the build plan. Nothing
from §4 (Non-Goals) is present: no Docker, no database, no DuckDB, no auth, no
cloud service, no AI functionality, no job queue, no TypeScript, no plugin
loader, no dynamic execution from disk, no heavyweight upload or component
library. Uploads are not proxied through Next.js — the browser calls FastAPI
directly, as §5 requires, which Phase 5 confirmed by watching the requests the
real browser actually made.

Phase 5 added no runtime dependency: the whole frontend is React, Tailwind and
native browser APIs (`fetch`, `FormData`, `File`, `DataTransfer`). `package.json`
is unchanged, and remained unchanged through 6A-6E — as did
`package-lock.json` and `backend/requirements.txt`.

---

## Next Phase

**Phase 6F — In-Memory CSV and XLSX Export**

Not started. Nothing for it was scaffolded, stubbed or prepared during
Phase 6E: no export button was added to the UI, no filename convention was
changed, `services/export.py` was not modified, and `test_export.py` was not
touched.

**Where 6F starts from.** The export _mechanism_ 6F.1 and 6F.2 describe already
exists — Phase 6D built it, because a Run stopped writing files and a download
that served a file would otherwise have broken (Known Issue 42).
`export.to_csv_bytes` / `to_xlsx_bytes` render the retained frame into a memory
buffer, and the download endpoints return those bytes. So 6F.1/6F.2 are to be
**confirmed**, not built.

**What Phase 6F actually owns**

- **6F.3 Excel compatibility.** Confirm by reopening a generated workbook, not
  by inspecting the writer. `test_action_round_trip.py` and `test_export.py`
  already do some of this — extend rather than duplicate.
- **6F.4 Multiple result tables in one workbook.** The XLSX download writes one
  worksheet per request today. `RunResult.secondary` and the manifest's
  `outputs` (and now `audit.results`) make the multi-table case straightforward.
  Decide deliberately whether a download of one output stays one worksheet and
  a new "whole run" download carries all of them, or whether the existing
  endpoint changes shape — the second is a frozen-route change.
- **6F.5 Collision-safe worksheet names.** `export.worksheet_name()` truncates
  to Excel's 31 characters and does nothing else; two outputs whose IDs share a
  31-character prefix would collide. Excel also forbids `: \ / ? * [ ]` and
  reserves `History`, none of which is handled.
- **6F.6 The ForgeXL filename convention.** Still `<output-id>.<format>`;
  6F.6 wants `forgexl-<action>-<timestamp>.<format>`. This is the reason the
  export buttons were **not** built in 6E (Known Issue 47) — build them in 6F,
  against the final convention, using the existing
  `src/components/workbench/*` components. Build plan §10 names the component
  `ExportButtons`.
- **6F.7 Release the export buffer.** Make the policy explicit rather than
  incidental: the bytes are generated per request and dropped with the
  response, and nothing retains a second copy.
- **6F.8 No server filesystem information in any response.** Already true and
  checked by execution in 6D and again in 6E; keep the check.
- **6F's own completion-criteria battery**: execute, request CSV, read the
  bytes, request XLSX, reopen it from memory, verify worksheet names, headers
  and representative values.

**What Phase 6E leaves in the tree for 6I**

- `services/storage.py` still keeps `create_run()`, `RunPaths`, `run_paths()`,
  `runs_directory()` and `delete_run_directory()` as **dead runtime code**, and
  `config.DATA_DIRECTORY` / `RUNS_DIRECTORY` and the `.env.example` line that
  documents them still exist. Build plan 6I.1 owns removing them (Known Issues
  21 and 39). **Do not remove them in 6F.**
- The `runs_dir` fixture in `tests/conftest.py` still exists so tests can assert
  the directory stays **empty**. Keep until 6I.
- Result frames still accumulate in `InMemoryRunStore` for the life of the
  process (Known Issue 40). 6E added no retention pressure of its own — it
  reads the frames a Run already holds. Measurement and any eviction policy
  belong to Phase 7J.
- Browser requests still go straight to `http://127.0.0.1:8000` via
  `NEXT_PUBLIC_API_BASE_URL`. The same-origin `/forge-api/*` namespace is
  **6G**, not 6F. `src/lib/api.js` is still the only frontend module that
  knows a backend URL, which is what will make 6G a one-file change.

**Before writing any code, verify the repository is intact.** The defect class
recorded as Known Issues 10, 31, 32, 37 and 38 (backend code committed into
`src/app/`, or a test module overwritten by a copy of another) has occurred
four times. It did **not** recur before this session, but check anyway. Run, in
order:

    ls backend/app/models backend/app/services backend/app/api   # not src/app/
    cd backend/tests && md5sum *.py | awk '{print $1}' | sort | uniq -d
    cd backend && .venv/bin/python -m pytest

The first must show the backend modules under `backend/`, the second must print
nothing, and the third must report **568 passed** before any new work begins.
If `backend/.venv/` or `node_modules/` is missing, the container is fresh:
recreate both exactly as `backend/requirements.txt` and `package.json`
document, and add nothing.

Do not begin Phase 6G.
