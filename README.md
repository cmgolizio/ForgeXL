# ForgeXL — Local Data Workbench

A local web application for running reusable, deterministic data-processing
**Actions** over spreadsheets.

Pick an Action, upload the CSV or XLSX files it asks for, run it, review the
result in the browser, and download it as CSV or Excel. Everything happens on
the machine running ForgeXL: no cloud service, no database, no account, and no
business data is sent to an external service.

Adding a new Action means writing one backend module, registering it, and
adding tests. **No frontend file changes** — the Action selector, the upload
slots, the results view and the export buttons are all generated from the
backend's own Action metadata.

- Authoritative plan: [`docs/build-plan.md`](docs/build-plan.md)
- How it is actually built: [`docs/architecture.md`](docs/architecture.md)
- Phase-by-phase record, known issues, deviations:
  [`docs/implementation-status.md`](docs/implementation-status.md)
- V1 completion evidence, remaining gates and operating checklist:
  [`docs/v1-finalization.md`](docs/v1-finalization.md)
- The monthly sales-rep report's business definitions:
  [`docs/monthly-sales-rep-report-spec.md`](docs/monthly-sales-rep-report-spec.md)

---

## Prerequisites

| Tool    | Version            | Notes                                    |
| ------- | ------------------ | ---------------------------------------- |
| Node.js | 20.9 or newer      | Current verification: 24.19.0          |
| npm     | ships with Node    | Use `npm ci` for the locked dependency tree. |
| Python  | 3.10 or newer      | Current locked environment: 3.12.14    |
| Git     | any recent version |                                          |

Nothing else. No Docker, no database server, no Excel installation, and no
credentials — ForgeXL has no secrets and needs none.

---

## Initial setup

Two installs, one for each half of the application. Run both from the project
root.

```bash
# 1. Frontend dependencies (exactly what package-lock.json pins)
npm ci

# 2. Backend virtual environment and dependencies
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
```

That is the whole setup. There is no database to create, no migration to run,
no directory to make and no environment file to copy — `.env.example` documents
optional overrides only, and every setting has a working local default.

On Windows the virtual environment's interpreter is
`backend\.venv\Scripts\python.exe`; the project targets macOS and Linux.

---

## Starting the application

```bash
npm run doctor      # checks setup without reading/writing business data
npm run build       # once after setup, and after updating code
npm start           # starts BOTH production servers
```

For normal use, `npm start` supervises both servers without a development reload
worker, waits for the backend through the web proxy, and streams their logs.
An occupied port is refused without killing its owner. Control-C stops both.
On Mac, `npm run start:open` also opens your default browser after readiness;
after setup, `ForgeXL.command` offers the same launcher from Finder. Finder
double-click behavior still needs verification on a Mac. This is a shell
launcher, not a signed native application. Then open:

| Service                | URL                        |
| ---------------------- | -------------------------- |
| **ForgeXL (use this)** | http://127.0.0.1:3000      |
| FastAPI backend        | http://127.0.0.1:8000      |
| Interactive API docs   | http://127.0.0.1:8000/docs |

The browser only ever talks to port 3000. It addresses the backend as the
same-origin path `/forge-api/...`, which Next.js forwards to FastAPI, so the
browser never learns FastAPI's address at all.

Both servers bind to `127.0.0.1` — the application is deliberately not reachable
from other machines. `FORGEXL_WEB_PORT` can change the normal web port.
For development with live reloading, use `npm run dev` instead. Do not run
development and production servers on the same ports simultaneously.

### Testing from a second computer on the same network

```bash
npm run dev:lan
```

This binds **only** Next.js to all interfaces and prints the LAN URLs to open
from the other machine. FastAPI stays on `127.0.0.1` in every script. The second
computer needs nothing but a browser: no Node, no Python, no checkout. Use this
only on a trusted local network.

---

## Using it

1. Choose an Action.
2. Upload the file each input slot asks for — click or drag and drop.
3. **Run Action**.
4. Review the metrics, the audit summary and the paginated preview.
5. Download the result as CSV or Excel.

If the uploaded data does not satisfy the Action, the Run fails with a plain
explanation of what was wrong — a missing column is named, not guessed at — and
no partial result is presented as a successful one.

---

## Current Actions

| Action                      | Input slot    | Required columns                                                | Output              |
| --------------------------- | ------------- | --------------------------------------------------------------- | ------------------- |
| **Exact Duplicate Remover** | `source_file` | none — any table                                                | `deduplicated_data` |
| **Product Master Builder**  | `sales_file`  | `SKU`, `Vintage`, `Supplier`, `Producer`, `Selection`, `Volume` | `product_master`    |

Both are deterministic: the same input always produces the same output. Neither
trims, re-cases, normalises, fuzzy-matches or infers anything. Column names are
matched **exactly** — `Sku` is not `SKU`, and `Supplier Name` is not
`Supplier`.

### CSV tools

Choose **Combine CSV files** or **Filter a CSV** on the existing action cards.
Upload the source; Combine also takes ordered additional files. Add filters,
click **Combine files** / **Apply filters**, preview, then **Download CSV**.
Combine removes exact parsed-field duplicates first; Filter keeps repeated rows
unless you enable duplicate removal. Combine and filter can run together.

CSV tools preserve every field as text, including leading zeros, large IDs,
decimal/date text, whitespace, Unicode and quoted newlines. Headers must match
exactly, in any column order. CSV data never updates stored monthly history.
Default bounds: 250 MiB/file, 500 MiB/request, 20 files including source; prepared
sessions expire after 15 minutes. Filtering and order changes reuse inspection.
See [CSV operation, contracts, limits and tests](docs/csv-tools.md).

### Monthly Sales Rep Report

This Action reads stored sales history and sample history. One successful Run generates a six-sheet workbook for
every applicable rep, individual downloads and a monthly ZIP.

| Field | Value |
| --- | --- |
| Action ID | `monthly_sales_rep_report` |
| Version | `0.4.0` |
| Inputs | `sales_history`, `sample_history` |
| Results | Nineteen preview/export tables plus all six-sheet rep workbook artifacts. |

Performance follows the salesperson on the invoice. Net sales include signed
credits/returns; sample credits reduce sample quantities separately. The six
accepted sections cover monthly samples, R12 samples, R12 account sales,
monthly supplier sales and percentages, R12 product/account quantities, and
current-versus-prior R12 account comparisons. Missing interior R12 months
produce blank unavailable totals with explicit notes.

The report contract establishes these six
sections. Supplementary placements and the true-zero sample-month policy
remain provisional, so Data Quality retains `PROVISIONAL_REPORT_RULES`.
The [specification](docs/monthly-sales-rep-report-spec.md) records exact rules;
[Phase 14 validation](docs/phase-14-validation.md) records independent source
checks and the remaining company-data and manual Excel for Mac acceptance.

1. Open ForgeXL and choose **Monthly Sales Rep Report**.
2. Choose the **Report month**. Upload company **Sales data** and **Sample
   data** as CSV or XLSX. Either file can contain one month or several years.
   Saved data for the month can be reused by leaving its upload empty.
3. Click the large **Generate reports** button. ForgeXL checks the sources
   automatically. If it displays errors, correct them; if it displays warnings,
   review and acknowledge them, then click the same button to continue.
4. Click **Download all reports (ZIP)**, or download individual Excel workbooks.
   Expand **Preview report data** to spot-check totals.

No account assignment upload is required or offered. Reports follow the
salesperson on each invoice. Reps with sales/sample activity in current R12
receive workbooks; assignment-only idle reps do not.

New months are saved automatically, and exactly unchanged stored months are
reused without double-counting. Conflicting stored months block generation
before any writes. To correct one month, upload only that month, expand its
**File requirements & date options**, select **Replace saved month**, and
explain the correction. Previous versions remain available.

Choose **Use saved data** to regenerate reports without uploads. The most
recent report’s exact source selection is the default; an earlier report or
current corrected data can be selected explicitly. If workbook generation
fails, click **Retry using saved data**. If saving fails partway, keep/reselect
the same files and try again; successful months are reused safely. Source data
and receipts survive restart. Preview/download bytes are regenerated on demand.
Old report receipts remain readable; the app warns when the installed Action
version changes the report contract.

Other actions use the same three steps: choose action, upload the required
file(s), **Generate report**, then download Excel or CSV. **More options**
contains the standalone history-import tool for adding history without running
a report; it is not required for multi-year monthly uploads.

See [the specification](docs/monthly-sales-rep-report-spec.md) and
[the usability fix record](docs/usability-fixes.md) for current rules and checks.

### Supported file formats

|          | Formats                                               |
| -------- | ----------------------------------------------------- |
| Upload   | `.csv`, `.xlsx`                                       |
| Download | `.csv`, `.xlsx`                                       |
| Rejected | `.xlsm`, `.xlsb`, `.xls`, `.ods`, and everything else |

The macro-capable formats are refused by extension before any parser opens the
bytes, so no macro can run. Formulas in an `.xlsx` are read as their stored
values and never evaluated.

An `.xlsx` must contain **one** data worksheet. A workbook with more than one
plausible data sheet is refused with a message saying so rather than a sheet
being guessed. Maximum upload size is 250 MB per file, configurable via
`FORGEXL_MAX_UPLOAD_BYTES`.

---

## Where Runs are stored

**In the backend process's memory. Running an Action writes nothing to disk.**

Uploaded files are read into memory and parsed from there. Results stay as
in-memory dataframes. CSV and XLSX exports are generated when you click
download and released with the response. So are any **generated files** an
Action produces — a formatted report workbook, or the ZIP of all of them: those
are held as bytes by the Run and handed straight back. No upload, no
intermediate file, no export, no generated file and no manifest ever reaches
the filesystem.

The consequence: **restarting the backend clears Run history.** A link to an
earlier Run then returns a clean "run not found" message. This is intended V1
behaviour, not a fault — see [`docs/architecture.md`](docs/architecture.md) §5.
A result already open in the browser keeps displaying, because it is in the
browser rather than on the server.

After downloading, **Release preview and downloads** forgets that finished
Run and releases its result tables and workbook bytes. It never deletes stored
source versions or cycle receipts. Download links then return 404; rerun a saved
monthly cycle to recreate them. There is no automatic eviction of older Runs:
release finished results as you go, or restart the backend between sessions.

Nothing uploaded is sent anywhere. There is no telemetry, no analytics and no
outbound HTTP client in the running backend.

## Where the Data Library is stored

Separate from a Run, and the one thing ForgeXL does keep:
**`data/library/`**, on this machine, ignored by git in full.

The Data Library holds business data that has to outlive a Run — sales history
and sample history — as versioned Parquet files
with small JSON records beside them. Committed versions are immutable: a month
is corrected by committing a replacement that records what it replaces and why,
so an older report can still be reproduced from the data it was built from.
There is no database.

Older account-ownership snapshots remain stored for compatibility but are never
read by monthly reports or offered as uploads.

Change the location with `FORGEXL_LIBRARY_DIRECTORY`. The directory is created
when the first version is committed, not at startup, and it is safe to back up
by copying **after stopping both servers**. Include the entire directory,
including `.reporting-cycles`, to preserve exact replay. Do not copy only the
Parquet files or the current versions. Restore to a separate directory first
and point `FORGEXL_LIBRARY_DIRECTORY` there; verify catalog and a saved cycle
before replacing a working copy. No automatic backup, cloud sync or encryption
is provided. See [`docs/architecture.md`](docs/architecture.md) §5a.

**What fills it** is the monthly reporting workflow: sales and samples are
parsed, validated and partitioned by transaction month. All partitions validate
before any write. New months become immutable versions; unchanged stored
months are reused, and conflicting months require a deliberate correction.

The accepted columns and every refusal are documented in
[`docs/monthly-source-schemas.md`](docs/monthly-source-schemas.md). The implemented
Monthly Reports screen exposes both history ingestion and the recurring cycle.

**What reads it** is an Action input slot that declares itself library-backed
(build plan Phase 11). Such a slot names the dataset it reads; the Run names
which version — `latest`, `period:2026-09`, or an exact `version:<id>` — and
that reference is resolved to one immutable version _before_ the Action runs.
The Run records the version it resolved to, so committing a newer month later
never changes what an earlier Run says it used, and naming that recorded
version reproduces the original result exactly.

The Action receives DataFrames keyed by its input slots and cannot tell an
uploaded one from a stored one. The monthly reporting screen selects the
report period and exact stored cycle. The generic Action screen links to it.

Running an Action still writes nothing — reading a stored version is a read,
and the two systems stay separate.

---

## Development commands

```bash
npm run dev          # start both development servers on 127.0.0.1
npm run dev:lan      # same, with Next.js reachable from the local network
npm run lint         # ESLint over the frontend
npm run build        # Next.js production build
npm start            # supervise both local production servers
npm run doctor       # dependency/runtime preflight; no business-data access
npm test             # backend + frontend DOM + startup/transport helper tests
npm run typecheck    # pinned Pyright; Python analysis, not TypeScript conversion
npm run verify:v1    # tests, typecheck, lint, build, production HTTP and startup
```

### Backend tests

```bash
cd backend && .venv/bin/python -m pytest
```

The suite is entirely synthetic: it builds its own CSV and XLSX files in
memory, so it needs no file picker, no sample workbook and no network.

### Performance benchmarks

Not part of the test suite — it takes minutes and asserts nothing.

```bash
cd backend && .venv/bin/python -m tests.benchmarks.run
```

Measured figures are recorded in
[`docs/implementation-status.md`](docs/implementation-status.md).

---

## Project layout

```text
src/app/                 Next.js App Router pages, and the /forge-api proxy route
src/components/          React components (plain JavaScript, no TypeScript)
src/lib/                 Frontend API paths and display formatters
backend/app/actions/     The Action contract, the registry, and each Action
backend/app/api/         FastAPI routes
backend/app/services/    Parsing, the Run pipeline, results, preview, export,
                         report rendering and archiving, the Data Library and
                         its ingestion and input resolution
backend/tests/           The test suite and its synthetic fixture system
docs/                    Build plan, architecture, implementation status
scripts/                 Setup check, production/development launchers, LAN helper
```

## Adding an Action

1. Write `backend/app/actions/<your_action>.py`: subclass `Action`, declare
   `id`, `version`, `name`, `description`, `inputs` and `outputs`, and
   implement `run(inputs)` — it receives parsed dataframes keyed by your input
   slot IDs and returns dataframes keyed by your output IDs.
2. Import it in `backend/app/actions/registry.py` and add it to
   `ACTION_REGISTRY`.
3. Add tests, and fixtures if it needs them.

Nothing in the frontend changes. An Action declaring three input slots renders
three upload areas on its own.

To read stored history instead of an upload, declare the slot with
`source=ActionInputSource.LIBRARY` and the `dataset_id` it reads. The runner
resolves the version and hands your `run(inputs)` an ordinary dataframe — an
Action never opens a Data Library file itself.

To produce **finished files** as well as tables — one formatted workbook per
sales rep, say — return them in `ActionResult.artifacts`. Render each with
`app.services.workbook`, which owns the spreadsheet engine and does the
formatting (worksheets, currency and percentage formats, column widths, frozen
panes, filters, conditional formats, totals) from report data you have already
calculated. Name each with `artifact_ids()` and `artifact_filename()` so the
IDs and filenames stay safe and collision-free. ForgeXL then lists the files
under the result, offers a download link for each and a ZIP of all of them —
again with no frontend change.

### Reusing saved history when a master differs

If monthly validation reports differing saved months, choose **Use saved sales
months** or **Use saved sample months** for that source. Keep your files selected,
click **Generate reports** to revalidate, acknowledge the warning, and generate.
Only missing months are imported; uploaded differences are ignored and existing
months keep their saved data. To actually correct a month, use a complete
single-month replacement with its current version and a reason. See
[the recovery contract](docs/usability-fixes.md).
