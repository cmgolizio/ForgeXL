# ForgeXL — Local Data Workbench

A local web application for running reusable, deterministic data-processing
**Actions** over spreadsheets.

Pick an Action, upload the CSV or XLSX files it asks for, run it, review the
result in the browser, and download it as CSV or Excel. Everything happens on
the machine running ForgeXL: no cloud service, no database, no account, and no
uploaded data leaves the computer.

Adding a new Action means writing one backend module, registering it, and
adding tests. **No frontend file changes** — the Action selector, the upload
slots, the results view and the export buttons are all generated from the
backend's own Action metadata.

- Authoritative plan: [`docs/build-plan.md`](docs/build-plan.md)
- How it is actually built: [`docs/architecture.md`](docs/architecture.md)
- Phase-by-phase record, known issues, deviations:
  [`docs/implementation-status.md`](docs/implementation-status.md)

---

## Prerequisites

| Tool    | Version            | Notes                                    |
| ------- | ------------------ | ---------------------------------------- |
| Node.js | 20.9 or newer      | Next.js 16 requires it. Verified on 22.x |
| npm     | ships with Node    | Verified on 10.9                         |
| Python  | 3.10 or newer      | Verified on 3.11                         |
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
backend/.venv/bin/python -m pip install -r backend/requirements.txt
```

That is the whole setup. There is no database to create, no migration to run,
no directory to make and no environment file to copy — `.env.example` documents
optional overrides only, and every setting has a working local default.

On Windows the virtual environment's interpreter is
`backend\.venv\Scripts\python.exe`; the project targets macOS and Linux.

---

## Starting the application

```bash
npm run dev
```

One command starts both halves and streams their logs together. Then open:

| Service                | URL                        |
| ---------------------- | -------------------------- |
| **ForgeXL (use this)** | http://127.0.0.1:3000      |
| FastAPI backend        | http://127.0.0.1:8000      |
| Interactive API docs   | http://127.0.0.1:8000/docs |

The browser only ever talks to port 3000. It addresses the backend as the
same-origin path `/forge-api/...`, which Next.js forwards to FastAPI, so the
browser never learns FastAPI's address at all.

Both servers bind to `127.0.0.1` — the application is deliberately not reachable
from other machines.

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

**In the backend process's memory. Nothing is written to disk.**

Uploaded files are read into memory and parsed from there. Results stay as
in-memory dataframes. CSV and XLSX exports are generated when you click
download and released with the response. No upload, no intermediate file, no
export and no manifest ever reaches the filesystem, and the backend has no
configured data directory at all.

The consequence: **restarting the backend clears Run history.** A link to an
earlier Run then returns a clean "run not found" message. This is intended V1
behaviour, not a fault — see [`docs/architecture.md`](docs/architecture.md) §5.
A result already open in the browser keeps displaying, because it is in the
browser rather than on the server.

Nothing uploaded is sent anywhere. There is no telemetry, no analytics and no
outbound HTTP client in the running backend.

---

## Development commands

```bash
npm run dev          # start both servers on 127.0.0.1 (the normal command)
npm run dev:lan      # same, with Next.js reachable from the local network
npm run lint         # ESLint over the frontend
npm run build        # Next.js production build
npm start            # serve the production build on 127.0.0.1:3000
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
backend/app/services/    Parsing, the Run pipeline, results, preview, export
backend/tests/           The test suite and its synthetic fixture system
docs/                    Build plan, architecture, implementation status
scripts/                 Backend launcher and the LAN address helper
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
