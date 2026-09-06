# ForgeXL — Architecture

**Status:** current as of Phase 9 — the first phase of the post-POC expansion.
Phases 0–8 built and validated the proof of concept; Phase 9 added the
persistent Data Library described in §5a and changed nothing else.
**Authority:** `docs/build-plan.md` remains the architectural source of truth.
This document records what was _built_, not what may be built later.

ForgeXL is a local data workbench. A user opens it in a browser, picks a
reusable Action, uploads one or more spreadsheets, runs the Action, reviews the
result, and downloads it as CSV or Excel. Everything happens on the machine
running ForgeXL, and — since Phase 6 — **running an Action happens entirely in
memory.**

Since Phase 9 there is one thing ForgeXL does keep: a local **Data Library** of
business data (sales history, sample history, account ownership snapshots) that
outlives a Run and is deliberately not part of a Run at all. §5a describes it.
Nothing in the Run path below reads or writes it.

---

## 1. The V1 request path

```text
Browser
    ↓  same-origin request to /forge-api/...
Next.js  (127.0.0.1:3000, or 0.0.0.0:3000 for LAN testing)
    ↓  transport-only proxy — the body is streamed through unread
FastAPI  (127.0.0.1:8000, never LAN-exposed)
    ↓  uploaded bytes, held in memory
parser   (Polars CSV / fastexcel / openpyxl fallback)
    ↓  named Polars DataFrames, one per Action input slot
Action Registry
    ↓
Action Engine  (deterministic, DataFrame-in / DataFrame-out)
    ↓  result DataFrames, retained by the Run
preview  ·  metrics  ·  audit  ·  export
    ↓  CSV / XLSX bytes generated per request
HTTP download
```

Nothing in that path reads or writes a file.

### Why the proxy exists

The browser never learns FastAPI's address. It addresses ForgeXL as
`/forge-api/...` on whatever host served the page, and the Route Handler at
`src/app/forge-api/[...path]/route.js` forwards the request to FastAPI. That is
what lets a second laptop on the same trusted LAN use ForgeXL with nothing but
a browser: it reaches Next.js, and Next.js reaches a FastAPI that stays bound
to `127.0.0.1` (build plan Phase 6, architectural rules 7–13).

The handler is transport and nothing else. It **streams** the request body
through without reading it — there is no second spreadsheet implementation in
Node, and no CSV or XLSX is ever parsed outside Python. Anything that reads
that body (a log line, a size check, a `formData()` call) reintroduces
truncation on large uploads; see Known Issue 66 in
`docs/implementation-status.md`.

`src/lib/backend-origin.js` is the only module in the repository that knows
FastAPI's host and port. It carries `import "server-only"`, so importing it
from a client component is a build error, and none of its variables is a
`NEXT_PUBLIC_` one.

---

## 2. Layers and what each one owns

| Layer                    | Module                                 | Owns                                                                              |
| ------------------------ | -------------------------------------- | --------------------------------------------------------------------------------- |
| Transport (browser side) | `src/lib/api.js`                       | Every backend path the browser uses. All same-origin.                             |
| Transport (server side)  | `src/app/forge-api/[...path]/route.js` | Streaming proxy to FastAPI. No parsing, no business logic.                        |
| HTTP surface             | `backend/app/api/`                     | Routing, multipart intake, status codes, structured errors.                       |
| Upload intake            | `backend/app/api/upload_form.py`       | Bounded, memory-only multipart parsing; the upload limit, enforced while reading. |
| Pipeline                 | `backend/app/services/runner.py`       | The generic Run lifecycle. Every Action goes through it.                          |
| Parsing                  | `backend/app/services/parser.py`       | Bytes → Polars DataFrame. CSV, XLSX, worksheet-ambiguity rules.                   |
| Run state                | `backend/app/services/run_store.py`    | Where a Run lives. Five methods, one implementation in V1.                        |
| Persistent data          | `backend/app/services/data_library.py` | Versioned business datasets that outlive a Run. Seven methods. See §5a.           |
| Results                  | `backend/app/services/results.py`      | Measuring a result table: schema, row counts, columns added/dropped.              |
| Preview                  | `backend/app/services/preview.py`      | Paginated slices of a retained result frame.                                      |
| Export                   | `backend/app/services/export.py`       | CSV/XLSX bytes from a result frame, generated per request.                        |
| Action contract          | `backend/app/actions/base.py`          | What an Action is: metadata plus `run(inputs)`.                                   |
| Action registry          | `backend/app/actions/registry.py`      | Lookup by ID. No `if/elif` chain, no plugin loader, nothing loaded from disk.     |
| Actions                  | `backend/app/actions/<action>.py`      | The transformation, and nothing else.                                             |

The separation that matters most: **the runner owns all generic machinery, and
an Action owns only its transformation.** Adding an Action means writing one
module, registering it, and adding tests. No route, no service and no frontend
file changes, because the entire UI is generated from `GET /api/actions`.

---

## 3. The processing boundary

```text
named uploaded inputs
    ↓  parser
named DataFrame(s)          {slot_id: pl.DataFrame}
    ↓  Action Registry
Action.run(inputs)
    ↓
result DataFrame(s)         {output_id: pl.DataFrame}
```

An Action receives a mapping of parsed frames keyed by its own declared input
slot IDs, and returns a mapping of frames keyed by its declared output IDs,
plus its own metrics. It never sees a path, a filename, an `UploadFile`, or an
HTTP object. Both registered Actions were already written this way and needed
no conversion during the Phase 6 migration.

A Run may produce one primary result table and any number of secondary ones. An
Action that needs only one declares only one.

---

## 4. Run lifecycle and state

A Run is a frozen dataclass (`backend/app/models/run.py`), not a directory. It
holds its ID, the Action it ran, timestamps, input metadata, validation
results, output metadata, the Action's metrics, any error — and the result
frames themselves.

```text
running ──→ succeeded
        └─→ failed
```

A Run is recorded as `running` the moment it starts, so an interrupted Run is
visibly one that never finished. Every outcome, failure included, leaves the
Run recorded with its full validation results: evidence of a failed Run is
never destroyed. A failed Run carries **no** result at all, so a half-finished
transformation can never be mistaken for a usable one.

State lives behind the `RunStore` interface — `create_run`, `get_run`,
`update_run`, `delete_run`, `list_runs`. The runner depends on that interface,
never on a dictionary. V1's implementation is `InMemoryRunStore`.

Because the result frames travel _with_ the Run, forgetting the Run is what
releases the memory: `delete_run(run_id)` drops the store's reference and the
frames become unreachable. This is pinned by a `weakref` test and was verified
in Phase 6I against a 300,000-row result.

---

## 5. V1 persistence behaviour

> ForgeXL V1 processes uploaded spreadsheet data ephemerally. Uploaded files
> and generated exports are not required to persist on the ForgeXL server
> after processing. Run history stored only in process memory may be lost when
> the backend restarts.

Concretely, in the shipped V1:

- An upload is read into memory and parsed from there. It is never written.
- No run directory, no `inputs/`, no `working/`, no `exports/` is created.
- No `manifest.json` is written; the manifest is derived from the Run and
  returned by the API.
- No internal Parquet file is written. This deliberately supersedes build plan
  §28, under the Phase 6 rule that its architectural rules override earlier
  conflicting instructions.
- CSV and XLSX bytes are generated from the retained result frame at download
  time and released with the response.
- The backend has **no configured data directory at all.** Phase 6I removed
  `DATA_DIRECTORY` and `RUNS_DIRECTORY` along with the last of the on-disk
  model, so there is no setting that could reintroduce a write location by
  accident.

**Restarting the backend clears run history.** This is authorised behaviour
(build plan Phase 6, rules 14–15), not corruption. A Run created before the
restart returns a clean structured `UNKNOWN_RUN` / HTTP 404 from every route
that names it — retrieval, preview, both per-output downloads and the whole-Run
workbook. The frontend renders that as a readable message; it does not crash,
show a traceback, or print `[object Object]`. A page already showing a result
keeps showing it, because that result is in the browser, not on the server.

**What this costs.** A Run's result frames stay in memory for the life of the
process, and nothing evicts them (Known Issue 40). For a local single-user
proof of concept that is acceptable; a retention policy is Phase 7J's decision,
and the capability to release a Run already exists.

---

## 5a. The Data Library (Phase 9)

Everything in §5 is about a **Run**, and it is still true. The Data Library is
a different thing, and the build plan states the difference as a rule:

```text
RunStore     ->  temporary execution/runtime state
Data Library ->  persistent business datasets used across Runs
```

They share no module, no record and no directory. A Run still writes nothing;
the library is written only when something is deliberately committed to it.

### What it holds

| Dataset               | Kind       | Meaning                                                   |
| --------------------- | ---------- | --------------------------------------------------------- |
| `sales_history`       | `history`  | Monthly sales. Versions accumulate across periods.        |
| `sample_history`      | `history`  | Monthly samples. A dataset of its own, never folded into sales. |
| `account_assignments` | `snapshot` | Rep/account ownership **as of** one reporting period.     |

The `snapshot` kind is the point of build plan 9E. An account owned by one rep
in September and another in November needs September's report to use
September's ownership, so ownership is stored per effective month rather than
as one mutable current file.

### Layout

```text
data/library/                      (git-ignored in full; configurable)
    sales_history/
        dataset.json               the dataset record
        versions/
            <version id>/          version id is a ForgeXL-generated UUID
                version.json       the version record
                data.parquet       the rows
    sample_history/…
    account_assignments/…
    .staging/                      transient; a commit in progress
```

Parquet plus small JSON records, as build plan 9C prefers. **No database was
added**, and none is needed to store a few dozen monthly tables. There is also
no separate index file: a dataset *is* a directory holding `dataset.json`, and
a version *is* a directory holding `version.json`. The layout is the catalogue,
so there is no catalogue that can disagree with the data it describes.

`config.LIBRARY_DIRECTORY` is the only location the backend is configured to
write, overridable with `FORGEXL_LIBRARY_DIRECTORY`. It is resolved once, at
construction, so the library a caller reaches never depends on the process
working directory. **No path from it is ever exposed through the API** — callers
name a dataset and a version by their logical IDs.

### Three invariants

- **A committed version is immutable.** Nothing rewrites one. Correcting a
  month means committing a *new* version that names the old one in
  `supersedes`, with a reason; the old version stays loadable by ID, which is
  what makes an old report reproducible. The interface has no `delete_version`
  and no `update_version`, so history cannot be rewritten by accident.
- **A commit is all-or-nothing.** Every check runs before the first byte is
  written; the files are then assembled in `.staging/` and published with a
  single `os.rename`. A reader sees either no such version or the whole of it.
  The dataset record is written the same way — temporary file, flush, replace.
- **Exactly one live version per period.** A second commit for a period that
  already has one is refused unless it explicitly supersedes it, so the library
  can never hold two versions of September with nothing to choose between them
  — the silent overwrite build plan 9D forbids, arrived at by addition.

### Identity

A dataset ID is a lowercase identifier declared in ForgeXL code; a version ID
is a ForgeXL-generated UUID, validated as one exactly as a Run ID is. Both are
checked for shape before they are used to build a path, so neither an uploaded
filename nor a client-supplied string can steer a read or a write out of the
library root (build plan 9B).

### Not yet wired to anything

Phase 9 built the layer and stopped there. Nothing imports it from a route, a
runner or the frontend, and `ensure_known_datasets()` is called deliberately
rather than at import, so a library that has never been written to stays
absent. Ingestion is Phase 10; library-backed Action inputs are Phase 11.

---

## 6. The extension point for future persistence

The DataFrame-first Action contract is deliberately independent of where
anything is stored. A future version may add implementations such as:

```text
PersistentRunStore        run history that survives a restart
ObjectStorage             uploaded or exported artifacts kept deliberately
Database-backed history   queryable past runs
```

Each of these is a new `RunStore` implementation or a new service beside it.
None of them requires changing:

- `Action.run(inputs) -> ActionResult` — the processing boundary
- any Action ID, version, input slot, required-column rule or output ID
- the runner's stage order or its failure contract
- the manifest, preview, or export shapes

The seam is a single module-level instance, `app.services.run_store.RUN_STORE`.
Swapping it is exactly the mechanism the test suite already uses to give each
test its own store, which is the practical proof that the abstraction holds.

**None of these three is implemented, and none of them should be** until there
is a concrete requirement for it. Build plan §7 applies: do not add
infrastructure before there is evidence it solves an actual problem.

Phase 9 is the first time that seam was used in anger, and it is worth
recording what it cost: **nothing above it changed.** The Data Library is a new
service beside the Run Store, with an interface of its own
(`app.services.data_library.DataLibrary`) and its own module-level instance
(`DATA_LIBRARY`) swapped the same way. `Action.run(inputs) -> ActionResult` is
untouched, no Action ID, version, input slot or output ID moved, the runner's
stages and failure contract are unchanged, and the manifest, preview and export
shapes are byte-for-byte what Phase 8 froze. That is the property §6 was
claiming, now demonstrated rather than asserted.

---

## 7. Safety rules the architecture enforces

- **A client filename is metadata, never a path.** Every upload is known
  internally as `source<ext>`; the browser's name is recorded and used nowhere
  else. Since Phase 6C no path is built from any client value at all, and the
  Data Library keeps the rule: a version's `source_filename` is recorded and
  its ID is a generated UUID.
- **A dataset or version ID must parse before it becomes a path.** A dataset ID
  is a lowercase identifier and a version ID is a UUID; anything else is
  `UNKNOWN_DATASET` / `UNKNOWN_DATASET_VERSION` / 404 rather than a directory
  lookup.
- **A committed dataset version is never rewritten** (build plan 9D). The Data
  Library interface offers no way to delete or edit one, a period can only be
  re-committed as an explicit supersession with a reason, and a commit is
  staged and renamed into place so a partial one cannot be read as a version.
- **A Run ID must parse as a UUID** before it reaches anything. A
  traversal-shaped or truncated ID is `UNKNOWN_RUN` / 404.
- **No API response contains a local path.** Regression tests assert that
  `/Users/`, `/home/`, `/tmp` and `data/runs` appear in no response body.
- **The upload limit is enforced while receiving**, in the multipart parser,
  before Starlette can spool anything to disk. Over the limit is
  `FILE_TOO_LARGE` / 413.
- **Uploaded content is never executed.** No macro runs, no formula is
  evaluated, and no uploaded value reaches a shell command.
- **A workbook with more than one plausible data sheet is refused**, with the
  message build plan §17 specifies, rather than a sheet being guessed.
- **A header row that names two columns the same thing is refused**
  (`DUPLICATE_COLUMNS` / 422, added in Phase 7). Every parser resolves the
  clash by renaming the later column and carrying on, which is the silent
  rename build plan §3.3 forbids: an Action selecting that column would get the
  first one and drop the second's values with nothing said. The check reads the
  header as the file spells it, before any engine has renamed anything, and
  compares names exactly — `SKU` and `sku` are two names.
- **An export that cannot hold the data is refused, never truncated**
  (`EXPORT_TOO_LARGE` / 422, added in Phase 7). The XLSX format holds
  1,048,575 data rows, 16,384 columns and 32,767 characters in a cell; past any
  of those, xlsxwriter silently shortens the value or Polars raises an error
  nothing caught. `export.check_fits_worksheet` measures the result first and
  says which limit was exceeded, where, and that CSV has none of them.
- **CORS is an exact allowlist** — `http://127.0.0.1:3000` and
  `http://localhost:3000`. Never a wildcard.
- **Errors are structured**: `{"error": {"code", "message", "details"}}`. A
  Python traceback never reaches the browser.

---

## 8. Where things run

| Process | Address                             | Exposed to the LAN?          |
| ------- | ----------------------------------- | ---------------------------- |
| Next.js | `127.0.0.1:3000`, or `0.0.0.0:3000` | Only under `npm run dev:lan` |
| FastAPI | `127.0.0.1:8000`                    | No, in every script          |

`npm run dev` starts both on loopback. `npm run dev:lan` binds **only** Next.js
to `0.0.0.0` so a second laptop can reach it; FastAPI takes its host from
`config.HOST` in every script and stays on loopback.

No uploaded data is transmitted to any external service, and no source file
names one — Phase 7K sweeps for it in `tests/test_local_exposure.py`, along
with the loopback binding and the CORS allowlist above. The running backend
imports no HTTP client at all.

Next.js's own build telemetry was the one outbound call the stack made on its
own. Phase 7K disabled it in the repository: every npm script that runs
`next dev`, `next build` or `next start` exports `NEXT_TELEMETRY_DISABLED=1`.
The machine-global `next telemetry disable` was rejected because it writes
outside the repository, so it would fix one developer's machine and leave the
next checkout sending telemetry again.

---

## 9. Adding an Action

1. Write `backend/app/actions/<action>.py`: subclass `Action`, declare `id`,
   `version`, `name`, `description`, `inputs`, `outputs`; implement
   `run(inputs)`.
2. Import it in `backend/app/actions/registry.py` and add it to
   `ACTION_REGISTRY`.
3. Add tests and fixtures.

Nothing else changes. The Action selector, the description panel, the upload
slots, the results view and the export buttons are all built from
`GET /api/actions` and the Run manifest, so an Action with three input slots
renders three upload areas with no frontend edit. This is the property the
whole proof of concept exists to demonstrate.

---

## 10. Where to read more

| Question                                                                                          | Document                               |
| ------------------------------------------------------------------------------------------------- | -------------------------------------- |
| What must be built, and in what order                                                             | `docs/build-plan.md`                   |
| What has been built and verified, phase by phase                                                  | `docs/implementation-status.md`        |
| Which components were filesystem-coupled before Phase 6, and what the frozen public contracts are | `docs/phase-6a-compatibility-audit.md` |
| Known issues, limitations and deviations                                                          | `docs/implementation-status.md`        |
