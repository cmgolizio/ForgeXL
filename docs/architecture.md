# ForgeXL — Architecture

> Current correction (2026-10-02): monthly reports consume only sales/sample
> history, derive reps/account context from invoice activity, and accept
> multi-year uploads directly in the report form. No assignment upload is
> exposed. New two-source receipts use schema 2; schema-1 receipts remain
> readable without loading their snapshots. See [usability-fixes.md](usability-fixes.md).
> Historical phase sections below retain the original snapshot design as
> implementation history; the current report specification supersedes it.


**Status:** engineering implemented through Phase 15 on 2026-10-01. The configured report
contract defines six sections. Source
reconciliation and automated acceptance are recorded in
[phase-14-validation.md](phase-14-validation.md); complete company credits/sample
coverage and manual Excel for Mac opening remain pending.
Phases 0–8 built the proof of concept; Phase 9 added the Data Library (§5a),
Phase 10 ingestion (§5b), Phase 11 library-backed inputs (§5c), Phase 12 generic
artifacts/rendering (§5d), Phases 13–14 the report and workbook batch (§5e),
and Phase 15 the monthly workflow and durable source receipts (§5f).
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
Since Phase 11 an Action input slot may be filled from it — §5c — and that is a
**read**: a Run still writes nothing, to the library or anywhere else.

Since Phase 12 an Action can also produce **artifacts**: finished files such as
a formatted report workbook, beside the result tables it has always produced.
§5d describes them. They live in the Run's memory and are downloaded from it;
nothing about them reaches the filesystem either.

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

| Layer                    | Module                                     | Owns                                                                              |
| ------------------------ | ------------------------------------------ | --------------------------------------------------------------------------------- |
| Transport (browser side) | `src/lib/api.js`                           | Every backend path the browser uses. All same-origin.                             |
| Transport (server side)  | `src/app/forge-api/[...path]/route.js`     | Streaming proxy to FastAPI. No parsing, no business logic.                        |
| HTTP surface             | `backend/app/api/`                         | Routing, multipart intake, status codes, structured errors.                       |
| Upload intake            | `backend/app/api/upload_form.py`           | Bounded, memory-only multipart parsing; the upload limit, enforced while reading. |
| Pipeline                 | `backend/app/services/runner.py`           | The generic Run lifecycle. Every Action goes through it.                          |
| Parsing                  | `backend/app/services/parser.py`           | Bytes → Polars DataFrame. CSV, XLSX, worksheet-ambiguity rules.                   |
| Run state                | `backend/app/services/run_store.py`        | Where a Run lives. Five methods, one implementation in V1.                        |
| Persistent data          | `backend/app/services/data_library.py`     | Versioned business datasets that outlive a Run. Seven methods. See §5a.           |
| Input resolution         | `backend/app/services/input_resolution.py` | A dataset reference → immutable version(s) → a DataFrame. See §5c.                |
| Monthly ingestion        | `backend/app/services/ingestion.py`        | The three recurring source files → validated, versioned library commits. §5b.    |
| Report calculations      | `backend/app/services/monthly_report.py`   | The monthly report's arithmetic, against the definitions in `report_spec`. §5e.  |
| Results                  | `backend/app/services/results.py`          | Measuring a result table: schema, row counts, columns added/dropped.              |
| Preview                  | `backend/app/services/preview.py`          | Paginated slices of a retained result frame.                                      |
| Export                   | `backend/app/services/export.py`           | CSV/XLSX bytes from a result frame, generated per request.                        |
| Report rendering         | `backend/app/services/workbook.py`         | Already-calculated report data → a formatted workbook. Calculates nothing. §5d.   |
| Archiving                | `backend/app/services/archive.py`          | A Run's artifacts → one ZIP, built in memory with flat entry names. §5d.          |
| Action contract          | `backend/app/actions/base.py`              | What an Action is: metadata plus `run(inputs)`.                                   |
| Action registry          | `backend/app/actions/registry.py`          | Lookup by ID. No `if/elif` chain, no plugin loader, nothing loaded from disk.     |
| Actions                  | `backend/app/actions/<action>.py`          | The transformation, and nothing else.                                             |

The separation that matters most: **the runner owns all generic machinery, and
an Action owns only its transformation.** Adding an Action means writing one
module, registering it, and adding tests. No route, no service and no frontend
file changes, because the entire UI is generated from `GET /api/actions`.

---

## 3. The processing boundary

```text
named uploaded inputs            named library dataset references
    ↓  parser                        ↓  input resolution
    └───────────────┬────────────────┘
                    ↓
named DataFrame(s)          {slot_id: pl.DataFrame}
    ↓  Action Registry
Action.run(inputs)
    ↓
result DataFrame(s)         {output_id: pl.DataFrame}
artifacts (since Phase 12)  (Artifact, ...)   — finished files, §5d
```

An Action receives a mapping of frames keyed by its own declared input slot
IDs, and returns a mapping of frames keyed by its declared output IDs, plus its
own metrics. It never sees a path, a filename, an `UploadFile`, an HTTP object,
or — since Phase 11 — a dataset version. Both registered Actions were already
written this way and needed no conversion during the Phase 6 migration, and
none during Phase 11 either.

The two arrows into that mapping are the point of §5c: an Action cannot tell
which of them filled a slot.

A Run may produce one primary result table and any number of secondary ones. An
Action that needs only one declares only one.

Since Phase 12 it may also return artifacts. That half of the boundary is
optional in the strongest sense — the field defaults to empty, so an Action
written before the phase means exactly what it meant then, and neither
registered Action produces one.

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
  accident. `LIBRARY_DIRECTORY` (§5a) is the one place the backend writes, and
  nothing in the Run path writes there — since Phase 11 a Run may _read_ a
  committed version, which changes nothing about this list.

**Restarting the backend clears run history.** This is authorised behaviour
(build plan Phase 6, rules 14–15), not corruption. A Run created before the
restart returns a clean structured `UNKNOWN_RUN` / HTTP 404 from every route
that names it — retrieval, preview, both per-output downloads and the whole-Run
workbook. The frontend renders that as a readable message; it does not crash,
show a traceback, or print `[object Object]`. A page already showing a result
keeps showing it, because that result is in the browser, not on the server.

**Memory cleanup.** A Run's result frames and artifacts remain in memory until
explicit release or restart; there is no automatic eviction policy. Both result
screens expose **Release preview and downloads**, calling additive
`POST /api/runs/{run_id}/discard`. Running Actions cannot be released. The route
removes only that finished Run from `RunStore`, never persistent versions or
cycle receipts. Its links subsequently return 404. Monthly saved cycles can
regenerate results; generic proof Actions need their original uploads again.

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

| Dataset               | Kind       | Meaning                                                         |
| --------------------- | ---------- | --------------------------------------------------------------- |
| `sales_history`       | `history`  | Monthly sales. Versions accumulate across periods.              |
| `sample_history`      | `history`  | Monthly samples. A dataset of its own, never folded into sales. |
| `account_assignments` | `snapshot` | Rep/account ownership **as of** one reporting period.           |

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
no separate index file: a dataset _is_ a directory holding `dataset.json`, and
a version _is_ a directory holding `version.json`. The layout is the catalogue,
so there is no catalogue that can disagree with the data it describes.

`config.LIBRARY_DIRECTORY` is the only location the backend is configured to
write, overridable with `FORGEXL_LIBRARY_DIRECTORY`. It is resolved once, at
construction, so the library a caller reaches never depends on the process
working directory. **No path from it is ever exposed through the API** — callers
name a dataset and a version by their logical IDs.

### Three invariants

- **A committed version is immutable.** Nothing rewrites one. Correcting a
  month means committing a _new_ version that names the old one in
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

### What reaches it

Phase 9 built the storage layer. Phase 10 added ingestion, and Phase 15's
workflow invokes its validated commit rules and partitions initial history.
Only these explicit source-saving operations write business data. The report
Action and generic Run pipeline do not write sources. The workflow also saves
small provenance receipts as described in §5f. Importing modules creates no
library or source version.

Phase 11 input resolution reads selected immutable sources for a Run, and the
Phase 15 catalog/review reads metadata and source frames. Actions receive only
the resolved working frames.

---

## 5b. Monthly ingestion (Phase 10)

The layer between the parser and the Data Library. It is what protects stored
business data from malformed, duplicated, partial or period-mismatched uploads.

```text
uploaded bytes
    ↓  app/services/parser.py            the Run pipeline's parser, unchanged
Polars DataFrame
    ↓  app/models/source_schemas.py      canonical schemas          (10A)
    ↓  app/services/reporting_period.py  which month, from the data (10B)
    ↓  app/services/ingestion.py         rows, duplicates, commit   (10C–10G)
app/services/data_library.py             versioned Parquet          (9C)
```

**It uses the Run pipeline's parser, deliberately.** One implementation reads
an ingested file and an uploaded one, so the extension rules, the
worksheet-ambiguity refusal and the duplicate-column refusal apply identically
to both and cannot drift apart.

**Nothing in Phase 9 changed to accommodate it.** No model, interface or stored
record gained a field; the only addition to `data_library.py` is an
`ensure_dataset` wrapper matching the module's existing convention. Ingestion
is built entirely on `commit_version` and the derived queries the library
already offered.

### The four properties that make it safe

- **The month comes from the data, never the filename.** `Invoice Date` decides
  it for sales and samples. A file named `September Sales.csv` holding August
  rows is August, and a monthly import that was told "September" refuses it.
- **Ambiguity is refused, not resolved.** A date column that two declared
  formats read differently — `03/04/2026` is 4 March or 3 April — is reported
  and an explicit format is required. Guessing would move rows into the wrong
  month, which is the failure the layer exists to prevent.
- **Everything is checked before anything is written.** All three files in a
  monthly cycle are validated first, so "September sales committed but the
  September ownership snapshot silently failed" cannot happen by a file being
  wrong. A refusal leaves the library exactly as it was.
- **The stored frame is the parsed source frame.** No column renamed, added,
  reordered, repaired or dropped after parsing. CSV columns declared as text
  in the source schema bypass numeric inference to preserve identifiers such
  as `000123`; typed spreadsheet cells retain their original parser behavior.
  The month, date range, parser engine, source hash and chosen date
  interpretation are metadata _on the version_, never written into rows.

### Two entry points

| Path                 | Shape                                         | Rule                                            |
| -------------------- | --------------------------------------------- | ----------------------------------------------- |
| Historical bootstrap | one file, many months → one version per month | One-time, into an empty dataset (10G)           |
| Recurring monthly    | three files, one month                        | Validate all three, then commit all three (10F) |

After bootstrap the ordinary recurring cycle adds one reporting period at a
time and reuses stored history. The Phase 15 UI also supports later historical
chunks of complete months; this is an orchestration extension, not a change to
the one-time Phase 10 bootstrap entry point. Explicit overlap skipping never
updates an existing month (§5f).

A duplicate is the same bytes committed for the same month. The period is part
of that match because identical bytes mean different things for the two dataset
kinds: a history file's bytes decide its month, while an unchanged ownership
snapshot is legitimately byte-identical from one month to the next.

Correcting a committed month is deliberate and separate: commit a replacement
naming the version it supersedes and why (9D). The old version stays readable,
so the report built from it can still be reproduced.

New versions record optional `date_column` / `date_format` metadata as a pair;
older JSON records remain readable without them. An explicit replacement can
record a missing or corrected interpretation of the same source bytes, with a
reason. An unchanged interpretation is still a duplicate. No existing version
is rewritten or guessed into a new format.

The accepted schemas, every refusal and every warning are documented in
[`monthly-source-schemas.md`](monthly-source-schemas.md). The
assignment identity columns and optional context fields are confirmed;
invoice-performance rules are documented separately in the report contract.

### Workflow API added in Phase 15

Phase 10's Python ingestion entry points remain the source of validation and
commit rules. Phase 15 adds HTTP orchestration for reviewing uploads, saving
history, generating reports and selecting recorded cycles. See §5f.

---

## 5c. Library-backed Action inputs (Phase 11)

An Action input slot declares where its data comes from. There are two
sources, and an Action written before Phase 11 declares neither and means the
first one:

| `source`  | filled by                          | slot also declares    |
| --------- | ---------------------------------- | --------------------- |
| `upload`  | a file submitted with the Run      | `accepted_extensions` |
| `library` | one committed Data Library version | `dataset_id`          |

The two are mutually exclusive on the model itself: a library slot that
accepted file extensions, or an upload slot that named a dataset, is refused
when the Action is declared rather than when it runs.

```text
POST /api/runs
    action_id      = monthly_report
    sales_file     = <uploaded file>          an upload slot
    sales_history  = "period:2026-09"         a library slot
        ↓  app/services/input_resolution.py
    sales_history  -> version 4f27d4bb-…      one immutable version
        ↓  load_version
    sales_history  -> pl.DataFrame
        ↓
    Action.run({"sales_file": …, "sales_history": …})
```

**Which dataset is read is declared by the Action; which version is chosen per
Run.** The client never names a dataset, so no client-supplied string decides
what gets opened. It names versions using these forms:

| reference              | means                                              |
| ---------------------- | -------------------------------------------------- |
| `latest`               | the live version with the greatest reporting month |
| `period:2026-09`       | the live version for that month                    |
| `version:<version id>` | that exact version, superseded or not              |
| `versions:<id>,<id>,...` | that exact set, including superseded versions     |
| `history`              | every live version, oldest month first             |
| `history:2026-09`      | every live version through that month              |

`history` was added in Phase 13; `versions:` was added in the audit to replay
its recorded input sets after corrections. Both name a **set**.
Build plan 13B requires a report Action's inputs to "include the historical
information required by the report specification", and the year-over-year and
year-to-date windows of 13C span more months than one version holds — the
library stores one version per month by construction.

A `history` slot resolves to every live version in period order, loads each
one, and hands the Action their rows as one frame. The bounding month **must
itself have a live version**, or the Run fails with `UNKNOWN_DATASET_VERSION`
before the Action starts: a bound that quietly fell back to an earlier month
would mean a report built for August while its Run said September.

Months whose exports carry different columns are refused with
`INCONSISTENT_DATASET_VERSIONS` rather than reconciled — merging them would
mean inventing values for one month or dropping a column from the other. Months
that differ only in *type*, which a CSV month and a workbook month can, are
merged when the types are compatible. Integer-to-float widening is checked
for a loss of precision; boolean-to-number coercion is also refused with
`INCONSISTENT_DATASET_VERSIONS`. An exact set contains at most one version per month; snapshot
datasets cannot combine multiple versions.

An input slot may declare `interpret_dates=True`. Resolution applies each
version's recorded format to its own working frame before concatenation,
allowing CSV text dates and spreadsheet date cells in the same report. The
raw loaded frames and stored versions remain unchanged. A slot may also name
another slot with `period_matches`: the runner compares their newest resolved
periods before invoking Action validation. The monthly report requires its
sample history and ownership snapshot to end at the selected sales month.

`latest` is the greatest **month**, not the most recent commit. The two differ
exactly when an old month is corrected: restating March after June was imported
commits a March version last, and answering "latest" with March would be wrong.

### Reproducible Runs

`version:` and `versions:` are fixed; every other form moves. Build plan 11D allows a
moving form at selection and forbids one at execution, so resolution happens
once, before the Action runs, and the Run records **both**: `requested`
(`latest`) and `version_id` (what that resolved to). A Run therefore says what
it was asked for and what it actually read.

A slot that read a span of months records **one entry per month**, each naming
its own period, source file and hash. Recording the months individually rather
than summarising them is what keeps build plan 11C's promise: every identity a
later Run would have to name back is written down.

The consequence is the reason the phase exists. Committing a newer version, or
superseding the one a Run used, cannot change what that Run says it used — the
record is a resolved ID, not a question. Re-running with
`version:<recorded id>` for a snapshot and
`versions:<recorded id>,<recorded id>,...` for each history slot reproduces the
original source state, and a superseded version stays loadable by ID forever.

### What did not change

- **The Action contract.** `Action.run(inputs)` still takes
  `{slot_id: pl.DataFrame}`. An Action cannot tell the two sources apart, and
  `test_contract_freeze.py` refuses a Data Library import inside an Action
  module (build plan 11B).
- **The HTTP surface.** No route was added. A library-backed slot is a text
  field beside the uploaded files in the existing `POST /api/runs` form, and
  `FROZEN_ROUTES` is byte-identical.
- **The two proof Actions.** Both are still upload-backed, asserted in the
  frozen inventory (build plan 11A).
- **Validation.** A stored version is held to the same required-column and
  emptiness checks as an upload. Being stored earns no trust.
- **The Run pipeline writes nothing.**

### Browser selection

The dedicated Monthly Reports screen chooses periods and exact source cycles.
The generic Action workbench retains its metadata-driven library-input display
and links to Monthly Reports. Library-backed Actions still receive only
resolved DataFrames; the workflow does not add filesystem access to an Action.

---

## 5d. Rich artifacts (Phase 12)

ForgeXL has always had one kind of result: a table. Phase 12 adds a second,
because a finished report is not a DataFrame.

```text
tabular output    a Polars frame. Previewed, paged, exported as CSV or XLSX.
artifact          finished bytes. Downloaded as they are, under their own name.
```

Build plan 12A: "Do not pretend a finished workbook containing layout,
formatting, multiple report sections, and presentation logic is merely another
DataFrame." Modelling one as an output would have given it a column schema it
does not have, a preview endpoint that could not render it, and a CSV export
that would throw its formatting away.

### What an artifact is

`app.models.artifact.Artifact` is a frozen dataclass: an ID, a label, a
filename, a media type, a coarse type and the bytes. `ArtifactMetadata` is the
same thing without the bytes, and it is what the manifest carries — the same
split `OutputMetadata` already makes for a table. **No field holds a path**,
because there is none: an artifact lives in the Run's memory for as long as the
Run Store keeps the Run, travelling inside `RunResult` beside the result
frames, so forgetting the Run releases both.

### Where it comes from

```text
Action.run(inputs)
    ↓  ActionResult(outputs=..., artifacts=(...))
runner._collect_artifacts
    ↓  metadata onto the Run, bytes into RunResult
GET /api/runs/{run}/artifacts/{artifact}/download
GET /api/runs/{run}/artifacts/download/zip
```

An Action renders its workbooks through `app.services.workbook`, which owns the
spreadsheet engine — multiple worksheets, styled headers, currency,
percentage, integer, decimal and date formats, measured column widths, row
heights, frozen panes, filters, Excel tables, conditional formatting and a
totals row. It is the sanctioned alternative to an Action importing
`xlsxwriter`, which the contract freeze forbids.

Two rules bound it, and both are in the module's own docstring:

- **It calculates nothing.** A sheet is handed a frame and, if it wants one, a
  totals row whose values the caller has already worked out. The layer chooses
  fonts, widths and number formats, never a number (build plan 12D).
- **It writes no formula.** A totals row holds literal values, not
  `=SUBTOTAL(...)`. A formula would mean the file showed one number and stored
  another, and a reader that does not evaluate formulas — Polars, openpyxl, a
  preview pane — would show a third thing.

It is not a second workbook writer: the workbook options, the capacity check of
build plan 7B and the worksheet-naming rules of 6F.5 all come from
`app.services.export`, so there is one set of rules rather than two.

### Names, and why two kinds

An Action producing one file per sales rep derives both an ID and a filename
from data, and the two are treated differently on purpose:

|                                                | derived by            | on a collision |
| ---------------------------------------------- | --------------------- | -------------- |
| artifact **ID** — an internal handle in a URL  | `artifact_ids()`      | numbered apart |
| artifact **filename** — what the user receives | `artifact_filename()` | the Run fails  |

A filename is what the user asked for, so renaming one behind their back would
hand them a file called something they did not choose (build plan §3.3). An ID
is a handle nobody reads, so two rows reducing to the same token get distinct
ones — the same split `worksheet_names()` already makes.

An ID folds accents to ASCII because it is a token; a filename keeps them
exactly, because it is a name. `Château Réal` becomes the ID `chateau-real` and
the file `Château Réal - September 2026.xlsx`, and the download header carries
that name through RFC 6266's `filename*` parameter.

### The batch archive

`app.services.archive` bundles a Run's artifacts into one ZIP, in memory.
Entry names are re-checked against the flat-filename rule on the way in — the
model already refused a separator, a traversal name or a control character when the
artifact was built, and the archive writer does not rely on someone else having
checked (build plan 12F). Entries are stamped with the Run's own completion
time rather than with "now", so re-downloading a bundle returns the same bytes.

### What did not change

- **The Action contract.** `ActionResult.artifacts` defaults to empty. Every
  Action written before Phase 12 is valid and unchanged, and the contract
  freeze asserts that neither proof Action produces an artifact. Phase 14
  adds an optional bundle filename without changing those defaults.
- **`MANIFEST_SCHEMA_VERSION` stays at 2.** `RunManifest.artifacts` defaults to
  empty, so a manifest written before this phase still validates.
- **The Run pipeline writes nothing.** An artifact is bytes an Action produced,
  held in memory and handed back; no file is created at any point.
- **The tabular side.** Preview, per-output export and the whole-Run workbook
  are untouched, and an artifact-producing Run uses all of them normally.

### Current artifact delivery

The Monthly Sales Rep Report produces every rep workbook in one successful
Run. The existing generic Generated Files section can render their metadata,
and the existing routes download one artifact or all of them in a ZIP. A named
bundle travels as optional runtime result state; the route uses it instead of
its generic fallback. Neither the manifest schema nor the proof Actions change.

Artifacts are not persisted. They live and die with their Run, exactly as
result frames do. The Data Library remains an immutable source/history store,
not a report archive.

---

## 5e. The monthly report and workbook batch (Phases 13–14)

The report reads three resolved DataFrames and delegates preparation,
calculation and presentation to separate pure services:



| Module | Responsibility |
| --- | --- |
| `models/report_spec.py` | Rule confidence, source roles, windows, outputs and validation severity. |
| `services/monthly_report.py` | One period/prepared model, invoice rep identities, dynamic roster, shared company calculations and twelve supplementary tables. |
| `services/report_views.py` | Six accepted views and precomputed footer amounts/ratios. |
| `services/report_workbooks.py` | Per-rep slicing, period/coverage notes, formatting policy and safe names. No business calculations. |
| `services/workbook.py` | Pure generic rendering into XLSX bytes; capacity checks, numeric formats, literal cells, filters and frozen headers. |
| `actions/monthly_sales_rep_report.py` | Thin Action contract and delegation; returns tables, all workbooks and the month-specific ZIP name. |

[monthly-sales-rep-report-spec.md](monthly-sales-rep-report-spec.md) records the
exact six worksheets, formulas, coverage and acceptance scope.

### One reporting period and seven calendar windows

The greatest month in the selected sales data defines the period. Bounding
`history:YYYY-MM` requires that month to exist. Seven inclusive windows cover
the reporting month, prior month, same month last year, both YTD periods, and
current/prior R12. Month-presence checks include interior gaps. Accepted R12
views blank unavailable totals and growth; supplementary previews retain
explicitly qualified available-row sums.

### Invoice performance and snapshot context

Invoice salesperson is the configured performance identity, superseding the
previous account-owner performance assumption. Transactions retain their
`Sales Person`; the month-specific snapshot contributes current ownership and
account-list/roster context. It never moves historical performance to a new
owner. Snapshot reps plus reps active in current-R12 sales/samples receive
workbooks. Idle snapshot reps retain zero-activity reports; unknown active reps
are warned about and included.

### Prepared once, company once

Preparation and company aggregations are shared across all reps. Company
supplier amounts are calculated once and joined into accepted supplier views.
Footer percentages are ratios of precomputed footer amounts, not sums of row
percentages. Sources remain unchanged, including subcent prices, fractional
quantities, accents and original identifiers.

### Fail or qualify

Action validation fails with a structured 422 before artifacts are published
for unsafe measures, missing invoice reps/sales accounts, unreadable dates,
wrong/unknown invoice types, or mismatched input periods. Samples may have
blank customers. Unknown reps, source-schema additions, repeated source rows,
missing historical months and direct-call assignment conflicts qualify the
report. Library ingestion still refuses a conflicting ownership snapshot.
Remaining provisional placement/sample-period definitions appear in Data
Quality; placements are excluded from the accepted workbooks.

### Batch publication and presentation

One rendering policy produces six sheets per rep. Amounts and percentages
stay numeric; note rows and footer rows participate in capacity checks.
Filenames preserve the reporting period and safely resolve case-insensitive
collisions, character limits and UTF-8 byte limits. A fixed XLSX creation
property removes the rendering clock from byte reproducibility.

The runner publishes result tables and the entire artifact set together.
If any workbook fails rendering, the failed Run exposes no partial batch.
The generic artifact ZIP contains each workbook once and uses
`<Month Year> Sales Rep Reports.zip`; individual downloads remain available.

The batch is reached through the Phase 15 workflow or existing `POST /api/runs`
library selectors. Manual Excel for Mac acceptance and full-company
completed-month validation remain outstanding.

---

## 5f. Monthly workflow and source receipts (Phase 15)

`src/app/monthly-reports/page.jsx` exposes two paths: validate/import a reporting
month, or rerun saved sources. An initial history panel uses the same ingestion
rules to partition complete months from a multi-month file, including later
historical chunks. Overlap is refused unless the user deliberately chooses
missing months only and acknowledges the exact skipped months. Stored months
are never row-merged or implicitly corrected by that selection.

| Boundary | Owner | Responsibility |
| --- | --- | --- |
| Browser | `components/monthly/`, `lib/api.js` | File/period controls, review, explicit warning consent and correction intent, downloads and previews. No business arithmetic. |
| HTTP | `api/monthly.py` | Bounded memory multipart intake, structured request errors, metadata catalog and service calls in the thread pool. |
| Review | `services/monthly_workflow.py` | Existing ingestion checks, lossless date/merge policy and pure report preparation; one pending review with a 15-minute token. |
| History setup | `services/history_workflow.py` | Reviewed whole-month chunks; explicit overlap skipping, exact partial-save month IDs, no implicit correction. |
| Durable selection | `services/cycle_receipts.py` | Small JSON receipts under `data/library/.reporting-cycles/<period>/<cycle-id>.json`; exact immutable source IDs and Action version. |
| Generation | Existing runner / report Action | Resolve receipt selectors, calculate existing report tables, publish the complete in-memory workbook batch. |

Validation does not write sources. Generate consumes its token once, checks
that live sources and the installed Action still match the review, commits
validated monthly files, saves a receipt, then executes the existing Action.
Repeated files are refused. A replacement names the current version and a
nonblank reason; prior versions stay readable. Reusing sources requires no
upload. Receipt reruns intentionally retain superseded source IDs; current-source
reruns capture their selection in a new receipt. A changed Action version
produces a visible warning because source replay cannot restore older code.

A storage failure reports the already-committed source IDs and publishes no
reports. A rendering failure retains the valid sources and receipt for retry.
There is no cross-dataset rollback claim. Sources saved and reports generated
are distinct UI states. Downloads and Runs remain ephemeral, including after a
restart; receipts contain no results, artifacts, original uploads or local paths.

Each service retains at most one pending parsed review. New validation, input
discard or a consumed/expired token releases it; an idle expired review is
released on the next service interaction. This local workflow does not add
multi-user coordination, queues, database persistence or authentication.

Catalog responses contain metadata only. Review summaries include source row
counts, report month, roster, ownership/schema errors and calendar coverage.
Missing R12 months remain warnings with unavailable totals blank; calendar
coverage is explicitly not a guarantee of complete company exports.

See [phase-15-validation.md](phase-15-validation.md) for the route inventory,
failure/replay evidence, repeated timings and acceptance limits.

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
- **A dataset reference is refused, not interpreted** (`INVALID_DATASET_SELECTOR`
  / 422, added in Phase 11). `latest`, `period:YYYY-MM`,
  `version:<version id>`, `versions:<id>,<id>,...` and `history[:YYYY-MM]`
  are accepted forms; `current`, `newest` or
  a bare month is reported rather than matched to a near neighbour, the same
  way an unrecognised Action ID is. A reference also never names the dataset —
  the Action declares that — so no client string chooses what gets opened.
- **An artifact filename is a flat name, never a path** (build plan 12F,
  added in Phase 12). Separators, traversal names, a leading dot, control characters,
  reserved Windows device names and trailing dots or spaces are all refused
  when the artifact is constructed, and checked again when it is written into
  the ZIP. Names are capped at 150 characters and 255 UTF-8 bytes, including
  the extension; the filename helper preserves accents while fitting both.
  A filename reaches a `Content-Disposition` header and a ZIP entry
  name, so it cannot be allowed to name a location in either.
- **A reporting month is read from data, never from a filename** (build plan
  10B). An uploaded file's name is metadata here too: it supplies the extension
  that chooses a parser and is recorded on the version, and it decides nothing
  about which month the rows belong to.
- **An ambiguous date column is refused, not resolved** (build plan 10B). Two
  declared formats that read a column differently mean the file cannot say
  which month it is, and an explicit choice is required. The same rule section
  17 applies to a workbook with two data sheets.
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
  nothing caught. `export.check_fits_worksheet` measures the result first,
  including header text. Rich rendering also reserves rows for titles,
  subtitles, spacing and supplied totals, and checks presentation text before
  writing. These checks prevent a polished workbook from silently losing its
  last rows or shortening a heading.
- **CORS is an exact allowlist** — `http://127.0.0.1:3000` and
  `http://localhost:3000`. Never a wildcard.
- **Browser writes are origin-checked before uploads are read.** The Next.js
  POST boundary rejects cross-site fetch metadata and mismatched Origin/Host;
  after checking it removes those browser-origin headers from the loopback hop.
  FastAPI independently rejects direct writes from browser origins outside its
  allowlist. Origin-less local CLI calls remain supported. This is defense
  against cross-origin browser writes, not authentication, authorization or
  general protection for a public/multi-user deployment.
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

Normal use is `npm run build` followed by `npm start`. `scripts/start.mjs`
checks dependencies/build/ports, starts both production servers, waits for
health through the proxy, and stops its owned children on exit or server failure.
It refuses occupied ports and non-loopback backend settings without stopping
other processes. `npm run doctor` checks setup without reading business data.
Mac `ForgeXL.command` invokes `npm run start:open`; it is not a native packaged
app. The default production web port is 3000, overridable with
`FORGEXL_WEB_PORT`. No background daemon, queue or cloud runtime is introduced.

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

An input slot that should read stored business data instead of an upload adds
two fields to its declaration — `source=ActionInputSource.LIBRARY` and
`dataset_id` — and nothing else. The Action's `run(inputs)` is identical either
way, and it must not import `app.services.data_library`: resolving a version is
the runner's job (§5c). Note that the browser has no version picker yet, so
such an Action is driven in-process or by naming the version in the request
form until build plan 15A builds one.

An Action that should produce finished files as well as tables returns them in
`ActionResult.artifacts`, rendering each with `app.services.workbook` and
naming each with `artifact_ids()` and `artifact_filename()` (§5d). Nothing else
changes: no route, no service and no frontend file, because the artifact list,
the per-file download links and the "Download All" bundle are all built from
the Run manifest.

---

## 10. Where to read more

| Question                                                                                          | Document                               |
| ------------------------------------------------------------------------------------------------- | -------------------------------------- |
| What must be built, and in what order                                                             | `docs/build-plan.md`                   |
| What has been built and verified, phase by phase                                                  | `docs/implementation-status.md`        |
| Which components were filesystem-coupled before Phase 6, and what the frozen public contracts are | `docs/phase-6a-compatibility-audit.md` |
| The exact columns of the three monthly source files, and every ingestion refusal and warning      | `docs/monthly-source-schemas.md`       |
| Every business definition the monthly sales-rep report is built on, and which are still provisional | `docs/monthly-sales-rep-report-spec.md` |
| Known issues, limitations and deviations                                                          | `docs/implementation-status.md`        |
