# Implementation Status

Last Updated: 2026-08-22
Current Phase: None
Last Completed Phase: Phase 1 — Application Foundation and Local Runtime

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

    src/app/layout.js              root layout, project metadata
    src/app/page.js                minimal Phase 1 page
    src/app/globals.css            Tailwind import + theme tokens
    src/app/favicon.ico
    src/components/BackendStatus.js  client component, /health indicator
    public/.gitkeep

### Backend

    backend/
      .venv/                  git-ignored virtual environment
      requirements.txt        pinned direct dependencies
      app/
        __init__.py
        config.py             all backend settings
        main.py               FastAPI app, CORS, GET /health, dev entrypoint

Installed backend packages (resolved 2026-08-22):

    fastapi          0.141.1
    uvicorn          0.52.4
    python-multipart 0.0.32
    polars           1.43.2
    fastexcel        0.21.0
    openpyxl         3.1.5
    xlsxwriter       3.2.9
    pytest           9.1.1
    httpx            0.28.1

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

    GET /health   ->  200 {"status": "ok"}

FastAPI's own `/docs`, `/redoc` and `/openapi.json` are present by default.
No Action, Run, upload, preview or download endpoints exist yet.

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
| `src/components/`       | Exists (`BackendStatus.js`)                                 |
| `lib/`                  | Missing — Phase 5.1 (`src/lib/api.js`)                      |
| `backend/app/`          | Exists (`main.py`, `config.py`)                             |
| `backend/app/api/`      | Missing — Phase 2.6 / 3.12                                  |
| `backend/app/actions/`  | Missing — Phase 2                                           |
| `backend/app/models/`   | Missing — Phase 2.2                                         |
| `backend/app/services/` | Missing — Phase 3                                           |
| `backend/tests/`        | Missing — Phase 2.8 onward                                  |
| `data/runs/`            | Exists (`.gitkeep`; run artifacts git-ignored)              |
| `scripts/`              | Exists (`dev-backend.sh`)                                   |
| `public/`               | Exists (`.gitkeep`; starter demo SVGs removed)              |
| `.env.example`          | Exists                                                      |
| `.env.local`            | Not present — not required (frontend default fallback)      |

### Repository / Git

    Remote:         https://github.com/cmgolizio/ForgeXL
    Current branch: claude/phase-1-implementation-7n65lp

Commit history at start of Phase 1 (3 commits):

    f7f987c  phase 0 complete
    a213587  added build plan file to brand new nextjs app
    7152074  Initial commit from Create Next App

Phase 1 changes are uncommitted; the user has not authorised a commit.

Verified with `git add -A --dry-run` that only intended files would be staged.
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

No automated test suite exists yet — the first backend tests are Phase 2.8.
`pytest` and `httpx` are installed and ready. Phase 1 verification was
performed by executing the following, with the exact results recorded.

### Frontend lint

    npm run lint          ->  exit 0, no errors, no warnings   (run twice)

### Frontend production build

    npm run build         ->  exit 0
                              ✓ Compiled successfully in 6.5s
                              Routes: ○ /   ○ /_not-found  (both static)

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

### Combined startup

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

### Environment variable plumbing

    .env.local with NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8123
      -> next dev reports "- Environments: .env.local"
      -> browser requests http://127.0.0.1:8123/health
      -> confirms the documented override in .env.example is real
    (.env.local removed afterwards; default fallback is what ships)

---

## Known Issues

1. **Next.js telemetry is enabled by default.** `npx next telemetry status`
   reports `Enabled`. This is Next.js's own anonymous build/usage telemetry to
   Vercel; it carries no uploaded data and no application data, so it does not
   violate build plan §8's rule about transmitting uploaded data. It is
   nonetheless an outbound network call from a deliberately local-only
   project. It was **not** changed in Phase 1, because disabling it via
   `next telemetry disable` writes to machine-global config outside this
   repository. Phase 7K ("verify no remote analytics/data calls") should make
   an explicit decision; the repo-local option is exporting
   `NEXT_TELEMETRY_DISABLED=1` in the dev scripts.
2. **Two `/health` requests per page load in development.** React Strict Mode
   (on by default in `next dev`) invokes effects twice. Expected dev-only
   behaviour, not a bug; a production build issues one request.
3. **Implementation host is Linux, not macOS.** The build plan targets a Mac
   (§3.4 benchmark hardware, Phase 1.8 "Mac-compatible development script").
   `scripts/dev-backend.sh` uses only portable POSIX/bash constructs and the
   standard `backend/.venv/bin/python` layout, so it should run unchanged on
   macOS, but this has not been executed there. Phase 7 performance numbers
   must be produced on the real target machine to mean anything.
4. **`README.md` is still the Create Next App default.** It documents
   `app/page.js` (this project uses `src/app/page.js`) and
   `http://localhost:3000` rather than the canonical `http://127.0.0.1:3000`,
   and it does not yet describe backend setup. Rewriting it is Phase 8.2.
5. **`backend/requirements.txt` pins direct dependencies only.** Transitive
   dependency versions are left to pip. This is reproducible for the packages
   the project actually chose but is not a full lockfile. If exact
   reproducibility becomes necessary, a `pip freeze` lock can be added later.
6. **No backend test suite yet.** `pytest` is installed but `backend/tests/`
   does not exist; the build plan introduces tests in Phase 2.8. Consequently
   there is no automated regression guard on `/health` or the CORS
   configuration — both are currently covered only by the manual verification
   recorded above.

None of the above blocks Phase 2.

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

No architectural conflicts were found. Framework, router, language, styling,
backend framework, data engine and lockfile all match the build plan. Nothing
from §4 (Non-Goals) is present: no Docker, no database, no DuckDB, no auth, no
cloud service, no AI functionality, no job queue, no TypeScript. Uploads are
not proxied through Next.js — the browser calls FastAPI directly, as §5
requires.

---

## Next Phase

**Phase 2 — Backend Data Engine and Action Contract**

Not started. Scope, per build plan Phase 2:

- 2.1 Create `backend/app/actions/`, `models/`, `services/`, `api/` with
  `__init__.py` files.
- 2.2 `backend/app/models/schemas.py` — Pydantic schemas for Action
  definition, Action input, Action output definition, validation issue, input
  metadata, output metadata, Run manifest, preview response.
- 2.3 `backend/app/actions/base.py` — the Action contract (id, version, name,
  description, inputs, outputs, execution). No plugin loader, no dynamic
  execution from disk.
- 2.4 `backend/app/actions/registry.py` — `list_actions()`, `get_action(id)`;
  unknown IDs return no Action.
- 2.5 A minimal placeholder Action definition is acceptable for registry tests.
- 2.6 `backend/app/api/actions.py` — `GET /api/actions`, router mounted in
  `main.py`.
- 2.7 Developer-level frontend check only — do not build the Action selector.
- 2.8 Tests: registration, `list_actions`, `get_action`, unknown ID, and
  duplicate Action IDs rejected rather than silently overwritten.

Do not begin Phase 3.
