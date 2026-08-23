# Implementation Status

Last Updated: 2026-08-23
Current Phase: None
Last Completed Phase: Phase 3 — Upload, Parsing, Run Execution, Storage, and
Export Pipeline

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
    src/app/page.jsx               minimal Phase 1 page
    src/app/globals.css            Tailwind import + theme tokens
    src/app/favicon.ico
    src/components/backend/BackendStatus.jsx
                                   client component, /health indicator
    public/.gitkeep

Unchanged by Phase 2. (The Phase 1 entry above recorded these as `.js` at
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
          example_passthrough.py   placeholder Action (delete in Phase 4)
        api/
          __init__.py
          actions.py          GET /api/actions
          runs.py             POST /api/runs, retrieval, preview, downloads
        models/
          __init__.py
          schemas.py          every Pydantic schema
        services/
          __init__.py
          storage.py          Run dirs, safe filenames, upload limit, manifest
          parser.py           parse_tabular_file: CSV + XLSX
          runner.py           the generic Run pipeline
          export.py           Parquet + CSV + XLSX artifacts
          preview.py          paginated reads of the internal Parquet
      tests/
        __init__.py
        conftest.py           isolated runs dir, registry and client fixtures
        helpers.py            make_action(), CSV/XLSX builders, upload helpers
        test_actions.py       Action contract + registry
        test_api.py           /health and /api/actions
        test_schemas.py       manifest / preview serialisation
        test_storage.py       Run dirs, path safety, upload limit, manifest
        test_parser.py        CSV, XLSX, worksheet ambiguity, engine fallback
        test_runner.py        the pipeline, validation, failed Runs
        test_export.py        Parquet/CSV/XLSX round trips
        test_preview.py       paging limits and Parquet-sourced previews
        test_runs_api.py      the Run endpoints and their status codes

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
                            400 malformed request (no action_id)
                            404 unknown Action
                            413 upload over MAX_UPLOAD_BYTES
                            422 validation failure
                            500 Action raised

    GET  /api/runs/{run_id}
                        ->  200 RunManifest | 404

    GET  /api/runs/{run_id}/outputs/{output_id}/preview?offset=&limit=
                        ->  200 PreviewResponse (default 100, max 500)
                            400 offset/limit out of range
                            404 unknown Run or output

    GET  /api/runs/{run_id}/outputs/{output_id}/download/csv
    GET  /api/runs/{run_id}/outputs/{output_id}/download/xlsx
                        ->  200 file attachment | 404

Every error body has the shape build plan section 22 specifies:

    {"error": {"code": "...", "message": "...", "details": {...}}}

FastAPI's own `/docs`, `/redoc` and `/openapi.json` are present by default;
the OpenAPI schema now documents `ActionDefinition`, `ActionInput`,
`ActionOutput` and `ActionListResponse`.

Registered Actions (1):

    example_passthrough  0.1.0  "Example Passthrough (Placeholder)"
      input  source_file       .csv .xlsx   no required columns
      output passthrough_data  csv, xlsx

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

| Path                    | Status                                                      |
| ----------------------- | ----------------------------------------------------------- |
| `src/app/`              | Exists (plan sketches root `app/`; `src/` retained per 1.1) |
| `src/components/`       | Exists (`backend/BackendStatus.jsx`)                        |
| `lib/`                  | Missing — Phase 5.1 (`src/lib/api.js`)                      |
| `backend/app/`          | Exists (`main.py`, `config.py`)                             |
| `backend/app/api/`      | Exists (`actions.py`, `runs.py`)                            |
| `backend/app/actions/`  | Exists (`base.py`, `registry.py`, placeholder Action)       |
| `backend/app/models/`   | Exists (`schemas.py`)                                       |
| `backend/app/services/` | Exists (storage, parser, runner, export, preview)           |
| `backend/tests/`        | Exists (9 test modules); `fixtures/` arrives in Phase 4     |
| `data/runs/`            | Exists (`.gitkeep`; run artifacts git-ignored)              |
| `scripts/`              | Exists (`dev-backend.sh`)                                   |
| `public/`               | Exists (`.gitkeep`; starter demo SVGs removed)              |
| `.env.example`          | Exists                                                      |
| `.env.local`            | Not present — not required (frontend default fallback)      |

### Repository / Git

    Remote:         https://github.com/cmgolizio/ForgeXL
    Current branch: claude/phase-3-pipeline-b7o3qw

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

### Frontend lint

    npm run lint          ->  exit 0, no errors, no warnings
                              (re-run in Phase 3: still clean)

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

8.  **`example_passthrough` is a placeholder and must be removed in Phase 4.**
    Registered under build plan 2.5 so `GET /api/actions` could be verified
    against real data. It is a working, deterministic Action (it returns its
    input unchanged), not a broken stub, but it is not one of the two proof
    Actions. Phase 4 must delete
    `backend/app/actions/example_passthrough.py`, drop its import and its
    entry from `ACTION_REGISTRY`, and register `exact_duplicate_remover` and
    `product_master_builder` instead. Two tests assert only that the
    application registry is non-empty with unique IDs, so they will keep
    passing after the swap.

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

None of the above blocks Phase 4.

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

No architectural conflicts were found. Framework, router, language, styling,
backend framework, data engine and lockfile all match the build plan. Nothing
from §4 (Non-Goals) is present: no Docker, no database, no DuckDB, no auth, no
cloud service, no AI functionality, no job queue, no TypeScript, no plugin
loader, no dynamic execution from disk. Uploads are not proxied through
Next.js — the browser calls FastAPI directly, as §5 requires.

---

## Next Phase

**Phase 4 — Proof Actions and Accuracy Tests**

Not started. Scope, per build plan Phase 4:

- **4A Exact Duplicate Remover** — `backend/app/actions/exact_duplicate_remover.py`
  per build plan section 26: one `source_file` slot, no required columns,
  remove rows that are exact duplicates across every column, preserve the
  first occurrence, preserve column order and retained-row order. Output
  `deduplicated_data`. Metrics `input_rows`, `output_rows`,
  `duplicates_removed`. No trimming, casing, normalisation or fuzzy matching.
- **4B Product Master Builder** — `backend/app/actions/product_master_builder.py`
  per build plan section 27: one `sales_file` slot requiring exactly `SKU`,
  `Vintage`, `Supplier`, `Producer`, `Selection`, `Volume`; select those six
  columns in that order, remove exact duplicate combinations, preserve
  first-occurrence order. Output `product_master`. Metrics `input_rows`,
  `output_rows`, `duplicate_product_rows_removed`. Accented text must remain
  accented.
- **4C Negative tests** — missing SKU, misspelled Supplier, empty file,
  unsupported extension; each must fail clearly with no partial output.
- **4D Excel round trip** — generate a known XLSX fixture, run each Action,
  download the generated XLSX, read it back in test code and verify columns,
  row count and values. Same for CSV.
- **4E Extensibility check** — confirm no frontend file was modified and that
  `GET /api/actions` exposes both definitions.

Phase 4 must also **delete `backend/app/actions/example_passthrough.py`** and
remove its import and registry entry, replacing it with the two real Actions
(Known Issue 8). The Phase 3 tests do not depend on it: they build their own
throwaway Actions through `tests/helpers.make_action` and the
`registered_actions` fixture, so the swap will not break them.

The Phase 3 pipeline is what both Actions will run on. Adding each one should
require only its module, a registry entry and tests — no change to
`storage.py`, `parser.py`, `runner.py`, `export.py`, `preview.py`,
`api/runs.py` or any frontend file. That expectation is the thing Phase 4
tests.

Do not begin Phase 5.
