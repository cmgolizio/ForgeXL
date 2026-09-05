````md
# Local Data Workbench — Proof of Concept Build Plan

## Document Purpose

This document is the authoritative implementation plan for the **Local Data Workbench Proof of Concept**.

It is written primarily for an AI coding agent such as **OpenAI Codex or Claude Code**, not as a beginner tutorial for a human developer.

The coding agent must treat this file as the project's architectural and implementation source of truth.

The objective is to build the proof of concept described below **incrementally, deterministically, and with minimal unnecessary complexity**.

Do not expand the scope merely because additional features would be useful.

Do not replace the architecture described here with a different architecture unless the user explicitly authorizes the change.

---

# 1. Product Purpose

The purpose of this project is to prove that a local web application can function as a reusable data-processing workbench.

The user must be able to:

1. Open the application locally on a Mac.
2. Select a predefined custom Action.
3. Upload one or more raw data files required by that Action.
4. Allow the application to validate the uploaded data.
5. Execute deterministic data-processing logic.
6. View the resulting data in the browser.
7. View useful information about what happened during the run.
8. Export resulting datasets as files.
9. Repeat the process with another Action without changing the frontend application.
10. Add future Actions without rebuilding the fundamental application architecture.

The POC is meant to answer one major question:

> Is this architecture fast, accurate, reliable, extensible, and pleasant enough to justify building the larger Data Workbench product?

---

# 2. POC Goal

At the end of this build, a user should be able to open:

    http://127.0.0.1:3000

and see a clean local application containing an Action selector.

At minimum, two Actions will exist:

1. Exact Duplicate Remover
2. Product Master Builder

The user will:

1. Select an Action.
2. Upload a CSV or XLSX file.
3. Run the Action.
4. See validation and execution results.
5. Preview the output.
6. Download the output as CSV.
7. Download the output as XLSX.
8. See basic run statistics such as:
   - input rows
   - output rows
   - rows removed where applicable
   - execution time
   - validation errors or warnings

The application must not require:

- cloud hosting
- an internet connection for normal operation after dependencies are installed
- a database server
- authentication
- Docker
- Excel
- Power Query
- third-party SaaS services

All uploaded company data and generated output must remain on the local machine.

---

# 3. POC Success Criteria

The POC is successful only if all of the following are true.

## 3.1 Functional

The user can:

- start the application locally
- select an Action
- upload supported data
- execute an Action
- receive understandable errors for invalid data
- preview results
- export results as CSV
- export results as XLSX
- run another Action without restarting the application

## 3.2 Architectural

Adding a third Action should require:

1. creating a new backend Action module
2. registering it with the Action registry
3. adding tests

It should **not** require modifying the frontend Action selector or manually constructing a new frontend form.

The frontend must generate the required upload controls dynamically from backend Action metadata.

This requirement is extremely important.

The Action architecture, not the sample Actions themselves, is the primary thing being proven.

## 3.3 Accuracy

The application must:

- never silently drop rows
- never silently rename required columns
- never silently substitute missing data
- never perform fuzzy matching
- never guess what a column represents
- never silently choose a semantically different field
- never silently convert invalid data into valid-looking data
- preserve uploaded source files unchanged
- report validation failures explicitly

All transformations must be deterministic.

The same Action version + same input data must produce the same logical output.

## 3.4 Performance

Performance must be measured rather than assumed.

Target benchmark:

- 100,000-row CSV
- one-input Action
- ordinary modern Mac hardware

Desired target:

- under 5 seconds for straightforward transformations

POC acceptance threshold:

- under 15 seconds for straightforward transformations

These are engineering targets, not promises.

Record actual measurements during Phase 7.

XLSX parsing may be slower than CSV and must be measured separately.

## 3.5 Usability

A nontechnical user should not need to:

- open Terminal after initial setup
- edit Python
- edit JavaScript
- understand APIs
- understand Polars
- understand the filesystem structure

The eventual normal workflow should be:

    Open application
    → Choose Action
    → Upload file
    → Run
    → Review
    → Export

---

# 4. Explicit Non-Goals for the POC

Do NOT build the following during this proof of concept:

- cloud hosting
- remote access
- multi-user support
- user accounts
- authentication
- permissions
- Supabase
- PostgreSQL
- MongoDB
- hosted databases
- Docker
- Kubernetes
- background workers
- Redis
- job queues
- AI-generated Actions
- natural-language Action creation
- visual workflow builders
- fuzzy product matching
- fuzzy customer matching
- data mapping interfaces
- scheduled jobs
- email delivery
- Excel add-ins
- Electron
- Tauri
- native Mac packaging
- automatic software updates
- collaboration features
- version history UI
- Action marketplace/plugin UI
- persistent master-data library
- DuckDB unless Phase 7 produces evidence that Polars alone is inadequate
- premature abstraction for hypothetical requirements

Do not add any of these simply because they may eventually be useful.

The POC should remain intentionally narrow.

---

# 5. Core Architecture

Use the following architecture.

    Browser
       │
       │ HTTP on localhost
       ▼
    Next.js frontend
    http://127.0.0.1:3000
       │
       │ direct requests
       ▼
    FastAPI backend
    http://127.0.0.1:8000
       │
       ▼
    Action Engine
       │
       ▼
    Polars
       │
       ├── CSV
       ├── XLSX
       └── Parquet internal working files
       │
       ▼
    Local filesystem
    /data/runs/

The browser communicates directly with FastAPI for data upload and results.

Do NOT proxy large uploaded files through Next.js Route Handlers.

There is no benefit to moving a large file:

    Browser → Next.js → FastAPI

when it can instead move:

    Browser → FastAPI

This keeps the POC simpler and avoids unnecessary file copying.

---

# 6. Technology Decisions

## 6.1 Frontend

Use:

- Next.js
- App Router
- React
- plain JavaScript
- Tailwind CSS

Do NOT use TypeScript.

Do NOT convert files to TypeScript.

Use `.js` and `.jsx`.

Use the current stable versions available when implementation occurs.

Do not hardcode an obsolete package version merely because this plan was written earlier.

Use the package lockfile for reproducibility.

---

## 6.2 Backend

Use:

- Python
- FastAPI
- Uvicorn
- Polars

Additional backend dependencies may include:

- python-multipart
- fastexcel
- xlsxwriter
- openpyxl
- pytest
- httpx

Purpose:

### FastAPI

Responsible for:

- HTTP API
- uploads
- Action discovery
- Run execution
- validation responses
- result metadata
- downloads

### Polars

Responsible for:

- CSV parsing
- XLSX parsing
- dataframe transformations
- deduplication
- selection
- exporting data
- internal Parquet output

### fastexcel

Preferred XLSX-reading engine.

### openpyxl

May be retained as a compatibility fallback for Excel workbooks that cannot be parsed properly by the preferred Excel engine.

Do not automatically switch engines while hiding that fact from the run metadata.

If fallback is used, record the parser engine used.

### xlsxwriter

Used for XLSX output.

---

# 7. Why DuckDB Is Not Included Yet

Do not install or introduce DuckDB during the initial POC.

Polars already provides the functionality required for:

- CSV processing
- Excel processing
- filtering
- grouping
- selection
- joins
- deduplication
- aggregation
- Parquet
- CSV export
- Excel export

DuckDB becomes useful later if the application develops requirements such as:

- persistent analytical datasets
- SQL-based Actions
- queries spanning many stored files
- very large repeated analytical workloads
- local data warehouse behavior
- persistent reference datasets

Do not add infrastructure before there is evidence that it solves an actual problem.

---

# 8. Local-Only Requirement

Both development servers must bind to:

    127.0.0.1

NOT:

    0.0.0.0

unless the user explicitly changes this requirement later.

Normal development endpoints:

    Frontend:
    http://127.0.0.1:3000

    Backend:
    http://127.0.0.1:8000

This prevents the POC from intentionally exposing the application to other machines on the local network.

No uploaded data may be intentionally transmitted to:

- OpenAI
- Anthropic
- Vercel
- Supabase
- Google
- analytics providers
- telemetry services
- external APIs
- remote logging services

Normal processing is entirely local.

---

# 9. Fundamental Domain Concepts

The implementation must use these concepts consistently.

---

## 9.1 Action

An Action is a reusable deterministic data-processing recipe.

Examples:

    Exact Duplicate Remover

    Product Master Builder

Eventually:

    Target Account YOY

    Winebow Follow-Up

    Product Matcher

    Supplier Performance

But only the first two are required for this POC.

An Action defines:

- ID
- version
- display name
- description
- required input slots
- accepted file extensions
- required columns
- output definitions
- processing logic

---

## 9.2 Input Slot

An Action does not merely request "some files."

It defines named input slots.

Example:

    sales_file

Future example:

    current_sales
    historical_sales
    assignments

This matters because future Actions may require multiple datasets with different purposes.

The frontend must build upload controls from these backend-defined input slots.

---

## 9.3 Run

A Run is one execution of one Action against one specific set of inputs.

Each Run receives a UUID.

Example:

    4f27d4bb-7464-4d04-a21b-....

A Run records:

- Action
- Action version
- timestamps
- input metadata
- validation
- output metadata
- metrics
- execution duration
- errors
- generated artifacts

---

## 9.4 Output

An Action may produce one or multiple outputs.

Each output should have:

- stable output ID
- label
- row count
- column count
- columns
- preview capability
- internal Parquet file
- CSV export
- XLSX export

---

## 9.5 Manifest

Every Run creates:

    manifest.json

The manifest is the audit record for that Run.

It must contain enough information to determine:

- what was run
- what input was used
- what Action version executed
- whether validation passed
- what output was produced
- how many rows were involved
- how long processing took
- whether any warning/error occurred

---

# 10. Expected Repository Structure

The project should ultimately resemble:

    /
    ├── app/
    │   ├── layout.js
    │   ├── page.js
    │   └── globals.css
    │
    ├── components/
    │   ├── ActionSelector.js
    │   ├── ActionDescription.js
    │   ├── FileUploadSlot.js
    │   ├── RunButton.js
    │   ├── RunStatus.js
    │   ├── ResultsSummary.js
    │   ├── DataPreview.js
    │   └── ExportButtons.js
    │
    ├── lib/
    │   ├── api.js
    │   └── formatters.js
    │
    ├── backend/
    │   ├── app/
    │   │   ├── __init__.py
    │   │   ├── main.py
    │   │   ├── config.py
    │   │   │
    │   │   ├── api/
    │   │   │   ├── __init__.py
    │   │   │   ├── actions.py
    │   │   │   └── runs.py
    │   │   │
    │   │   ├── actions/
    │   │   │   ├── __init__.py
    │   │   │   ├── base.py
    │   │   │   ├── registry.py
    │   │   │   ├── exact_duplicate_remover.py
    │   │   │   └── product_master_builder.py
    │   │   │
    │   │   ├── models/
    │   │   │   ├── __init__.py
    │   │   │   └── schemas.py
    │   │   │
    │   │   └── services/
    │   │       ├── __init__.py
    │   │       ├── parser.py
    │   │       ├── storage.py
    │   │       ├── runner.py
    │   │       ├── preview.py
    │   │       └── export.py
    │   │
    │   ├── tests/
    │   │   ├── fixtures/
    │   │   ├── test_actions.py
    │   │   ├── test_parser.py
    │   │   └── test_api.py
    │   │
    │   └── requirements.txt
    │
    ├── data/
    │   └── runs/
    │       └── .gitkeep
    │
    ├── docs/
    │   ├── build-plan.md
    │   └── implementation-status.md
    │
    ├── scripts/
    │   └── dev.sh
    │
    ├── public/
    │
    ├── .env.example
    ├── .env.local
    ├── .gitignore
    ├── package.json
    ├── package-lock.json
    └── README.md

Do not blindly create files that already exist.

Inspect the repository first.

Preserve existing useful configuration unless it conflicts with this architecture.

---

# 11. Runtime Data Structure

Each Run must have its own directory.

Example:

    data/
      runs/
        <run-id>/
          manifest.json

          inputs/
            sales_file/
              source.csv

          working/
            deduplicated_data.parquet

          exports/
            deduplicated_data.csv
            deduplicated_data.xlsx

Never expose absolute local filesystem paths through the API.

The browser should receive logical IDs and download endpoints instead.

---

# 12. Git Rules

Before changing anything:

    git status

The agent must inspect existing changes.

Do not overwrite unrelated work.

Do not run:

    git reset --hard

Do not delete unrelated files.

At the end of every Phase, report:

- files created
- files modified
- files deleted, if any
- test commands executed
- test results
- build results
- known issues
- deviations from this plan

---

# 13. AI Agent Execution Protocol

THIS SECTION IS MANDATORY.

Every time a new implementation thread begins, the coding agent must:

1.  Read this entire file:

    docs/build-plan.md

2.  Read:

        docs/implementation-status.md

    if it exists.

3.  Determine exactly which Phase the user requested.

4.  Inspect the current repository state before editing.

5.  Execute **only the requested Phase**.

6.  Do not begin the next Phase.

7.  Run every validation/test specified for the requested Phase.

8.  Update:

    docs/implementation-status.md

9.  Give the user a concise completion report.

10. Stop.

Do not continue into another Phase even if the next step appears obvious.

---

# 14. Architecture Change Rule

If implementation reveals that something in this plan is impossible, incompatible, or materially harmful:

DO NOT quietly redesign the architecture.

Instead:

1. verify the problem
2. document the exact issue
3. explain the smallest reasonable alternatives
4. stop
5. wait for user direction

Minor implementation details that do not change architecture may be resolved normally.

Examples of architecture changes requiring approval:

- switching away from FastAPI
- switching away from Next.js
- introducing a database
- introducing DuckDB
- introducing Docker
- introducing a cloud service
- switching from JavaScript to TypeScript
- proxying uploads through Next.js
- adding authentication
- changing local-only behavior

---

# 15. Coding Rules

## Frontend

Use plain JavaScript.

Do not create:

    .ts
    .tsx

files.

Use React components appropriately.

Keep server-only and client-only code separated.

Do not import Node-only modules into browser/client components.

Avoid unnecessary dependencies.

Do not add a large component library for this POC.

Use native HTML controls where they are sufficient.

---

## Backend

Use clear Python modules.

Avoid giant files.

Do not create an abstract framework around hypothetical future requirements.

Favor explicit functions and classes.

Use type hints where they improve correctness.

Use Pydantic models for API-facing structured data.

Use Python exceptions internally and convert them into structured API errors at the boundary.

---

# 16. Data Safety Rules

The backend must:

- preserve the original uploaded source file
- never modify the original upload
- use generated filesystem names where appropriate
- store the original filename only as metadata
- reject unsupported extensions
- prevent path traversal
- prevent user filenames from controlling filesystem destinations
- create isolated run directories
- never execute uploaded content
- never use uploaded filenames in shell commands
- never construct shell commands from Action IDs
- never execute macros in Excel files
- never treat workbook formulas as executable application code

Support initially:

    .csv
    .xlsx

Reject initially:

    .xlsm
    .xlsb
    .xls
    .ods
    .json
    .parquet

Additional formats may be added later deliberately.

---

# 17. Excel Safety Rule

For the POC, an XLSX input must contain one clear data worksheet.

Do not silently select an arbitrary worksheet from a workbook containing multiple plausible data sheets.

If workbook structure is ambiguous:

return a clear validation error.

Example:

    This workbook contains multiple worksheets.
    The POC currently requires a workbook containing one data worksheet.
    Save the required worksheet as its own workbook or CSV and try again.

This restriction may be replaced by a worksheet-selection UI later.

Accuracy is more important than pretending to support every workbook.

---

# 18. Maximum Upload Size

Define the upload limit in backend configuration.

Initial default:

    250 MB per uploaded file

The exact value must be configurable rather than scattered through application code.

Example configuration concept:

    MAX_UPLOAD_BYTES

A file exceeding the limit should receive a clear error.

Do not begin reading an arbitrarily large file entirely into browser memory merely to inspect it.

---

# 19. CORS

FastAPI should permit requests only from expected local frontend origins.

At minimum:

    http://127.0.0.1:3000
    http://localhost:3000

Do not configure:

    Access-Control-Allow-Origin: *

for the POC.

---

# 20. Environment Configuration

Create:

    .env.example

with documented values such as:

    NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000

Backend configuration should also centralize:

    HOST
    PORT
    DATA_DIRECTORY
    MAX_UPLOAD_BYTES
    ALLOWED_FRONTEND_ORIGINS

Sensible local defaults are acceptable.

Do not place secrets in the repository.

The POC should not actually require secrets.

---

# 21. API Contract

The core API should provide the following capabilities.

---

## GET /health

Purpose:

Verify backend availability.

Example logical response:

    {
      "status": "ok"
    }

---

## GET /api/actions

Purpose:

Return Action definitions.

The frontend must use this endpoint to populate the Action selector.

Example conceptual response:

    {
      "actions": [
        {
          "id": "exact_duplicate_remover",
          "version": "1.0.0",
          "name": "Exact Duplicate Remover",
          "description": "...",
          "inputs": [
            {
              "id": "source_file",
              "label": "Source File",
              "required": true,
              "accepted_extensions": [
                ".csv",
                ".xlsx"
              ],
              "required_columns": []
            }
          ]
        }
      ]
    }

Do not hardcode the Action list into the frontend.

---

## POST /api/runs

Content type:

    multipart/form-data

Request must contain:

    action_id

Files should be submitted using their Action input slot IDs.

Example:

    action_id = product_master_builder

    sales_file = <uploaded file>

This design allows future Actions to define multiple files.

The backend must:

1. validate Action ID
2. find the Action definition
3. validate required input slots
4. validate extension
5. create Run ID
6. create Run directory
7. preserve original input
8. parse data
9. validate required columns
10. execute Action
11. create internal Parquet output
12. create CSV export
13. create XLSX export
14. write manifest
15. return Run result

This endpoint may execute synchronously for the POC.

Do NOT build a job queue yet.

---

## GET /api/runs/{run_id}

Returns Run manifest information.

---

## GET /api/runs/{run_id}/outputs/{output_id}/preview

Query parameters:

    offset
    limit

Default:

    offset=0
    limit=100

Maximum preview limit:

    500

Do not serialize an entire 100,000-row dataset into frontend JSON.

Response should contain:

    columns
    rows
    offset
    limit
    total_rows

---

## GET /api/runs/{run_id}/outputs/{output_id}/download/csv

Return generated CSV.

---

## GET /api/runs/{run_id}/outputs/{output_id}/download/xlsx

Return generated XLSX.

---

# 22. HTTP/Error Behavior

Use meaningful HTTP status codes.

Examples:

    200
    successful request

    400
    malformed request

    404
    unknown Action, Run, or Output

    413
    uploaded file too large

    422
    input data failed validation

    500
    unexpected server error

Validation errors must be structured.

Concept:

    {
      "error": {
        "code": "MISSING_COLUMNS",
        "message": "The uploaded file is missing required columns.",
        "details": {
          "missing_columns": [
            "Supplier",
            "Volume"
          ]
        }
      }
    }

The UI should show this cleanly.

Do not expose Python tracebacks directly in the browser.

Tracebacks may be logged locally during development.

---

# 23. Run Manifest Schema

Each Run must create a manifest containing approximately:

    {
      "schema_version": 1,

      "run_id": "...",

      "status": "succeeded",

      "action": {
        "id": "product_master_builder",
        "version": "1.0.0",
        "name": "Product Master Builder"
      },

      "created_at": "...",
      "started_at": "...",
      "completed_at": "...",

      "duration_ms": 0,

      "inputs": [
        {
          "slot_id": "sales_file",
          "original_filename": "...",
          "stored_filename": "...",
          "file_size_bytes": 0,
          "extension": ".csv",
          "parser_engine": "...",
          "row_count": 0,
          "column_count": 0,
          "columns": []
        }
      ],

      "validation": {
        "passed": true,
        "errors": [],
        "warnings": []
      },

      "outputs": [
        {
          "id": "product_master",
          "label": "Product Master",
          "row_count": 0,
          "column_count": 0,
          "columns": [],
          "formats": [
            "csv",
            "xlsx"
          ]
        }
      ],

      "metrics": {},

      "error": null
    }

Exact internal implementation may differ slightly if justified.

Do not place actual dataframe rows inside the manifest.

---

# 24. Action Contract

All Actions must follow one consistent contract.

Each Action must provide metadata including:

    id

    version

    name

    description

    inputs

    outputs

Each Action must expose deterministic execution logic.

Conceptually:

    Action
      definition
      validate(...)
      run(...)

The runner, not individual Actions, should handle:

- filesystem creation
- preserving uploads
- generic file parsing
- generic required-column validation
- Parquet persistence
- generic CSV export
- generic XLSX export
- manifest writing

Actions should focus on the actual transformation.

This separation is important.

---

# 25. Action Registry

Create one central Action registry.

Concept:

    ACTION_REGISTRY = {
        action.id: action,
        ...
    }

Provide functions conceptually similar to:

    list_actions()

    get_action(action_id)

The API should query the registry.

Do not use enormous `if/elif` chains such as:

    if action_id == "x":
    elif action_id == "y":
    elif action_id == "z":

The Action registry is one of the major architectural pieces being validated by this POC.

---

# 26. Action 1 — Exact Duplicate Remover

ID:

    exact_duplicate_remover

Version:

    1.0.0

Display name:

    Exact Duplicate Remover

Purpose:

Prove that the system can accept a generic dataset and deterministically transform it without requiring domain-specific columns.

Input:

    source_file

Accept:

    CSV
    XLSX

Required columns:

    none

Behavior:

1. Load dataset.
2. Record input row count.
3. Remove rows that are exact duplicates across every column.
4. Preserve the first occurrence.
5. Preserve column order.
6. Preserve row order of retained first occurrences.
7. Record output row count.
8. Calculate:

   duplicates_removed =
   input_rows - output_rows

Output ID:

    deduplicated_data

Output label:

    Deduplicated Data

Metrics:

    input_rows
    output_rows
    duplicates_removed

Do NOT:

- trim field values
- uppercase field values
- normalize strings
- perform fuzzy comparisons
- reinterpret blanks
- combine near-duplicates

Only exact row duplicates are removed.

---

# 27. Action 2 — Product Master Builder

ID:

    product_master_builder

Version:

    1.0.0

Display name:

    Product Master Builder

Purpose:

Prove that Actions can enforce a specific schema and create a purpose-built output.

Input slot:

    sales_file

Accepted:

    CSV
    XLSX

Required columns exactly:

    SKU
    Vintage
    Supplier
    Producer
    Selection
    Volume

Behavior:

1. Validate all required columns exist.
2. Select only these columns.
3. Preserve this exact output column order:

   SKU
   Vintage
   Supplier
   Producer
   Selection
   Volume

4. Remove exact duplicate combinations across those six fields.
5. Preserve first-occurrence order.
6. Produce Product Master output.

Output ID:

    product_master

Output label:

    Product Master

Metrics:

    input_rows
    output_rows
    duplicate_product_rows_removed

Do NOT:

- normalize producer names
- normalize selection names
- strip accents
- infer missing Vintage
- infer missing Volume
- combine near matches
- perform fuzzy matching
- generate SKU values
- change company data

Those are separate future Actions/problems.

---

# 28. Internal Parquet Requirement

After an Action produces an output dataframe, store the working representation as:

    working/<output-id>.parquet

The preview API should read from this internal Parquet output.

Do not use CSV as the application's internal preview source.

Reasons:

- faster repeated reads
- schema preservation
- efficient slicing
- no need to rerun the Action merely to preview another page

The user-facing exports remain:

    CSV
    XLSX

---

# 29. Frontend Requirements

The interface should remain intentionally focused.

Initial page layout:

    --------------------------------------------------
    Local Data Workbench
    Run reusable data-processing Actions locally.
    --------------------------------------------------

    Action
    [ Select Action ▼ ]

    Action description

    Required Inputs

    [ Drop file here / Choose File ]

    File information

    [ Run Action ]

    --------------------------------------------------

After execution:

    Run Successful

    Action:
    Product Master Builder

    Input Rows:
    15,842

    Output Rows:
    1,247

    Execution:
    0.82 s

    --------------------------------------------------

    Preview

    | SKU | Vintage | Supplier | ... |
    | ... |

    Showing 1–100 of 1,247

    --------------------------------------------------

    [ Download CSV ]
    [ Download Excel ]

The visual design should be clean and professional but restrained.

Do not spend an entire Phase creating animations or decorative design.

Functionality and clarity matter more.

---

# 30. Frontend State Model

The application should distinguish these states:

    loading_actions

    idle

    ready

    running

    success

    validation_error

    server_error

The Run button must be disabled when:

- no Action is selected
- a required file is missing
- a Run is already executing

Do not allow accidental duplicate submissions from repeated button clicking.

---

# 31. Preview Requirements

The preview table must:

- show column headers
- display at most 100 rows initially
- support next/previous pagination
- request only the required page from the backend
- show total row count
- horizontally scroll when columns exceed viewport width

Do not render the entire output dataset.

Do not add a complex spreadsheet component during the POC.

A normal HTML table is sufficient.

---

# 32. File Upload UI

For each Action input definition returned by the backend:

create one upload slot automatically.

Example backend Action:

    inputs:
      sales_file

Frontend should render:

    Sales File

    [ Choose file ]

If a future Action has:

    current_sales
    historical_sales
    assignments

the frontend should automatically render three upload areas.

No Action-specific frontend code should be necessary.

This is a critical acceptance test.

---

# 33. Implementation Status File

Create:

    docs/implementation-status.md

during Phase 0.

Suggested structure:

    # Implementation Status

    Last Updated:
    Current Phase:
    Last Completed Phase:

    ## Completed

    ## Current Architecture

    ## Tests

    ## Known Issues

    ## Deviations From Build Plan

    None.

    ## Next Phase

Every Phase must update this file.

This gives future AI threads durable project context.

---

# 34. Estimated Build Effort

Accuracy and validation are the most important traits while completing the phases within this build-plan.

---

# ============================================================

# PHASE 0

# Repository Audit and Build Contract

# ============================================================

## Purpose

Understand the existing repository before modifying it and establish durable cross-thread project state.

## Difficulty

Low.

## Estimated Effort

0.5–1 hour.

## Instructions

### 0.1 Read Documentation

Read this entire file.

Do not skim sections merely because they appear to describe later Phases.

Understand the architectural constraints first.

---

### 0.2 Inspect Repository

Run commands appropriate to inspect:

    pwd
    ls
    find . -maxdepth 2 -type f
    git status
    git branch --show-current

Inspect:

    package.json

if present.

Determine:

- whether a Next.js project already exists
- current Next.js structure
- whether Tailwind exists
- whether TypeScript exists
- whether `/backend` exists
- whether `/docs` exists
- whether `/data` exists
- current dependencies
- current npm scripts
- existing environment files

Do not delete or overwrite existing functionality during this Phase.

---

### 0.3 Verify Machine Tools

Check:

    node --version
    npm --version
    python3 --version
    git --version

Do not install a different Node/Python version unless compatibility requires it.

Document the detected versions.

---

### 0.4 Create Status Document

Create:

    docs/implementation-status.md

using the format defined earlier.

Record:

- repository state
- detected runtimes
- project framework
- current branch
- whether architecture conflicts exist
- next Phase

---

### 0.5 Verify Ignore Rules

Inspect `.gitignore`.

Eventually, the following must not be committed:

    data/runs/*
    !data/runs/.gitkeep

    backend/.venv/

    .env.local

    __pycache__/
    .pytest_cache/

Do not remove useful existing ignore rules.

---

## Phase 0 Exit Criteria

Phase 0 is complete when:

- repository has been inspected
- environment has been inspected
- no unnecessary code has been changed
- implementation-status.md exists
- architectural conflicts have been identified

If a major conflict exists, stop and report it.

Do not begin Phase 1.

---

# ============================================================

# PHASE 1

# Application Foundation and Local Runtime

# ============================================================

## Purpose

Establish the Next.js frontend and FastAPI backend and prove they can run together locally.

## Difficulty

Low–Moderate.

## Estimated Effort

1–3 hours.

---

## 1.1 Frontend Foundation

If a valid Next.js App Router project already exists:

do not recreate it.

Confirm:

- App Router
- JavaScript
- Tailwind

If no project exists, create one using:

- Next.js
- App Router
- JavaScript
- Tailwind
- ESLint

Do NOT select TypeScript.

Do not add a `src/` folder unless the repository already uses one.

Prefer the simplest existing convention.

---

## 1.2 Remove Starter Noise

Replace default Next.js demonstration content with a minimal page.

Do not build the full interface yet.

Display:

    Local Data Workbench

    Local data-processing proof of concept.

This merely confirms the frontend works.

---

## 1.3 Backend Virtual Environment

Create:

    backend/.venv

using the local Python installation.

Example conceptual command:

    python3 -m venv backend/.venv

Upgrade pip inside the virtual environment.

Install the backend packages required for the initial foundation:

    fastapi
    uvicorn
    python-multipart
    polars
    fastexcel
    openpyxl
    xlsxwriter
    pytest
    httpx

Record resolved dependencies in:

    backend/requirements.txt

Use reproducible versions resolved at implementation time.

---

## 1.4 Backend Application

Create:

    backend/app/main.py

Implement a minimal FastAPI application.

Add:

    GET /health

returning:

    {
      "status": "ok"
    }

---

## 1.5 CORS

Configure exact local origins:

    http://127.0.0.1:3000
    http://localhost:3000

Do not use wildcard origins.

---

## 1.6 Backend Configuration

Create:

    backend/app/config.py

Centralize:

- backend host
- backend port
- data directory
- upload limit
- frontend origins

Avoid duplicating constants across modules.

---

## 1.7 Environment Files

Create:

    .env.example

Document:

    NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000

Create `.env.local` only if required locally.

Ensure `.env.local` is ignored.

---

## 1.8 Combined Development Startup

Normal operation should eventually use one command.

Provide an npm script or Mac-compatible development script that starts:

    Next.js
    FastAPI

together.

A reasonable implementation may use the lightweight npm development dependency:

    concurrently

Frontend:

    127.0.0.1:3000

Backend:

    127.0.0.1:8000

Do not bind either service to all interfaces.

---

## 1.9 Health Display

Have the frontend make one simple request to:

    /health

Display a small backend status indicator:

    Backend Connected

or:

    Backend Unavailable

Do not build Action functionality yet.

---

## 1.10 Verify

Verify:

    frontend starts

    backend starts

    GET /health works

    frontend reaches backend

    CORS works

Run:

    npm lint command appropriate to project

    npm build command

Verify Python application imports cleanly.

---

## Phase 1 Exit Criteria

The user can run one local startup command and open:

    http://127.0.0.1:3000

The frontend loads.

The backend loads.

The frontend confirms backend connectivity.

No Action implementation exists yet.

Update:

    docs/implementation-status.md

Stop.

Do not begin Phase 2.

---

# ============================================================

# PHASE 2

# Backend Data Engine and Action Contract

# ============================================================

## Purpose

Create the extensible backend foundation that Actions will use.

This is one of the most important Phases.

## Difficulty

Moderate.

## Estimated Effort

2–4 hours.

---

## 2.1 Create Backend Module Structure

Create:

    backend/app/actions/
    backend/app/models/
    backend/app/services/
    backend/app/api/

with appropriate `__init__.py` files.

---

## 2.2 Define API/Data Schemas

Create:

    backend/app/models/schemas.py

Define structured Pydantic schemas for:

- Action definition
- Action input
- Action output definition
- validation issue
- input metadata
- output metadata
- Run manifest
- preview response

Do not force Action execution itself into Pydantic abstractions if plain Python is clearer.

---

## 2.3 Define Base Action Contract

Create:

    backend/app/actions/base.py

Define the Action interface.

An Action must provide:

    id
    version
    name
    description
    inputs
    outputs

and execution behavior.

Keep it understandable.

Do not build a plugin loader.

Do not dynamically execute files from disk.

Actions are trusted application code.

---

## 2.4 Build Registry

Create:

    backend/app/actions/registry.py

Support:

    list_actions()

    get_action(action_id)

Unknown IDs must return no Action rather than guessing.

---

## 2.5 Add Temporary Example Definition

At this point only, it is acceptable to register a minimal placeholder Action definition if needed for testing the registry.

Do not implement final transformation logic yet unless it naturally belongs in this Phase.

---

## 2.6 Actions API

Create:

    backend/app/api/actions.py

Implement:

    GET /api/actions

Return registry definitions.

Mount router in:

    main.py

---

## 2.7 Frontend Verification Only

Do not build the final Action selector yet.

A developer-level check is enough:

- call `/api/actions`
- verify JSON
- verify definitions are serializable
- verify inputs are correctly represented

---

## 2.8 Registry Tests

Add tests proving:

- Actions can register
- list_actions returns registered Actions
- get_action returns correct Action
- unknown Action returns expected result
- duplicate Action IDs are rejected rather than silently overwritten

Duplicate IDs are an important correctness failure.

---

## Phase 2 Exit Criteria

The backend has:

- Action contract
- Action registry
- structured Action metadata
- GET /api/actions
- tests

No full Run pipeline is required yet.

Update implementation-status.md.

Stop.

Do not begin Phase 3.

---

# ============================================================

# PHASE 3

# Upload, Parsing, Run Execution, Storage, and Export Pipeline

# ============================================================

## Purpose

Build the generic machinery that accepts files and executes Actions.

This should work independently of any specific Action's business logic.

## Difficulty

Moderate–High.

## Estimated Effort

3–5 hours.

---

## 3.1 Storage Service

Create:

    backend/app/services/storage.py

Responsibilities:

- generate Run UUID
- create Run directory
- create inputs directory
- create working directory
- create exports directory
- safely store uploaded files
- write manifest atomically
- locate artifacts by logical ID

Never accept arbitrary filesystem paths from the API.

---

## 3.2 Safe Filenames

Uploaded filenames are metadata.

Generate safe internal filenames.

Do not use an arbitrary user filename as a trusted path.

Preserve:

    original_filename

inside manifest metadata.

---

## 3.3 Upload Limit

Enforce configured upload size.

Return:

    413

when exceeded.

Do not let huge uploads fail later with an obscure memory error.

---

## 3.4 Parser Service

Create:

    backend/app/services/parser.py

Implement generic:

    parse_tabular_file(...)

Support:

    CSV
    XLSX

Return:

- Polars dataframe
- parser metadata
- parser engine
- row count
- column count
- columns

---

## 3.5 CSV Parsing

Use Polars.

Handle ordinary CSV files.

If parsing fails:

return a clear parse error.

Do not attempt increasingly strange delimiter guesses without reporting them.

Automatic CSV parser detection supplied by the chosen library is acceptable, but actual parsing failure must not be hidden.

---

## 3.6 XLSX Parsing

Use the preferred high-performance Excel engine.

Validate workbook ambiguity.

The POC should reject workbooks where a safe single data worksheet cannot be determined.

Record the worksheet and parser engine used.

If using compatibility fallback:

record that fact.

---

## 3.7 Generic Validation

Before Action execution validate:

- required input slot exists
- extension supported
- dataset parsed successfully
- dataset is not empty unless Action explicitly permits emptiness
- required columns exist

Column comparisons for required schemas should be exact unless an Action explicitly defines otherwise.

Example:

    Sales Person

is not automatically equivalent to:

    Salesperson

The program should report the mismatch.

---

## 3.8 Runner Service

Create:

    backend/app/services/runner.py

Generic workflow:

    resolve Action
    ↓
    create Run
    ↓
    save input
    ↓
    parse input
    ↓
    validate input
    ↓
    execute Action
    ↓
    persist Parquet
    ↓
    create exports
    ↓
    finalize manifest

The runner must own generic mechanics.

Individual Actions should not reproduce this code.

---

## 3.9 Failed Runs

If a Run fails after its directory has been created:

retain the Run directory.

Write a manifest containing:

    status = failed

and useful error metadata.

Do not destroy all evidence of the failed Run.

---

## 3.10 Export Service

Create:

    backend/app/services/export.py

Given an output dataframe:

write:

    .parquet
    .csv
    .xlsx

Parquet belongs under:

    working/

CSV and XLSX belong under:

    exports/

---

## 3.11 Manifest Writing

Write manifest at meaningful state transitions.

At minimum:

    created/running

and:

    succeeded

or:

    failed

Prefer atomic replacement:

write temporary file, then rename.

Avoid leaving half-written JSON if the process is interrupted.

---

## 3.12 POST /api/runs

Create Run endpoint.

Use multipart form data.

The request includes:

    action_id

and Action-defined file fields.

Do not require the frontend to upload one generic unnamed list when the Action contains named input roles.

---

## 3.13 Run Retrieval

Implement:

    GET /api/runs/{run_id}

Return structured manifest.

---

## 3.14 Preview Service

Create:

    backend/app/services/preview.py

Read internal Parquet.

Support:

    offset
    limit

Return only requested rows.

Default:

    100

Maximum:

    500

---

## 3.15 Preview Endpoint

Implement:

    GET /api/runs/{run_id}/outputs/{output_id}/preview

Validate:

- Run exists
- output exists
- offset valid
- limit valid

---

## 3.16 Download Endpoints

Implement:

    /download/csv
    /download/xlsx

Use controlled server paths.

Set sensible response filenames.

---

## Phase 3 Testing

Test:

- supported file accepted
- unsupported extension rejected
- oversized file rejected
- unknown Action rejected
- missing required input rejected
- missing required columns rejected
- Run directory created
- source preserved
- manifest created
- Parquet output created
- CSV output created
- XLSX output created
- preview returns limited rows
- invalid Run ID returns 404
- invalid output returns 404

A simple temporary Action may be used if final Actions are not yet built.

---

## Phase 3 Exit Criteria

The generic pipeline exists.

It can execute an Action through the same infrastructure future Actions will use.

Update implementation-status.md.

Stop.

Do not begin Phase 4.

---

# ============================================================

# PHASE 4

# Proof Actions and Accuracy Tests

# ============================================================

## Purpose

Implement two real Actions and prove deterministic accuracy using controlled datasets.

## Difficulty

Moderate.

## Estimated Effort

2–4 hours.

---

# SUBPHASE 4A — Exact Duplicate Remover

Implement:

    exact_duplicate_remover.py

Follow its specification in Section 26 exactly.

Use Polars functionality that preserves retained row order.

Add fixture containing:

- unique rows
- exact duplicates
- null values
- repeated text
- repeated numbers

Manually define expected output.

Test exact equality.

Verify:

    input rows
    output rows
    duplicates removed

---

# SUBPHASE 4B — Product Master Builder

Implement:

    product_master_builder.py

Required exact columns:

    SKU
    Vintage
    Supplier
    Producer
    Selection
    Volume

Create controlled fixture with:

- repeated identical products
- different vintages
- different volumes
- different selections
- blank values
- accented producer/selection text

Important:

Accented text must remain accented.

Nothing should normalize it.

Test:

- correct output columns
- correct order
- correct unique combinations
- correct output count
- correct metrics

---

# SUBPHASE 4C — Negative Tests

Test Product Master Action with:

- missing SKU
- misspelled Supplier
- empty file
- unsupported extension

Each must fail clearly.

No case should silently produce partial output.

---

# SUBPHASE 4D — Excel Round Trip

Generate a known XLSX fixture.

Execute each applicable Action.

Download generated XLSX.

Read generated XLSX back using backend test code.

Verify:

- expected columns
- expected row count
- expected values

Do the same logical verification for CSV.

This proves export is not merely creating a file with the correct extension.

---

# SUBPHASE 4E — Action Extensibility Check

Confirm the frontend has not been modified for either Action.

At this point the backend registry should expose both definitions.

GET `/api/actions` should show both.

---

## Phase 4 Exit Criteria

Two real Actions exist.

Automated tests prove exact expected output.

CSV and Excel exports are verified.

No frontend Action-specific logic exists.

Update implementation-status.md.

Stop.

Do not begin Phase 5.

---

# ============================================================

# PHASE 5

# Dynamic Frontend Action Runner

# ============================================================

## Purpose

Build the user-facing workflow from Action selection through execution.

## Difficulty

Moderate.

## Estimated Effort

3–5 hours.

---

## 5.1 API Utility

Create:

    lib/api.js

Centralize frontend communication with FastAPI.

Do not scatter backend URLs across components.

Use:

    NEXT_PUBLIC_API_BASE_URL

---

## 5.2 Load Actions

When the application loads:

request:

    GET /api/actions

Provide:

- loading state
- connection error state
- populated Action list

---

## 5.3 Action Selector

Create:

    components/ActionSelector.js

Populate exclusively from API metadata.

Each option should display Action name.

Selected Action state should contain complete Action metadata.

---

## 5.4 Action Description

Show:

- name
- description
- version where useful

Do not overwhelm the user with internal IDs.

---

## 5.5 Dynamic Input Slots

For each selected Action input:

render one:

    FileUploadSlot

The component receives:

- input ID
- label
- accepted extensions
- required state

No logic such as:

    if action === "product_master_builder"

should be necessary.

---

## 5.6 Drag-and-Drop

Support:

- clicking to browse
- dragging and dropping

Do not add a heavyweight upload dependency unless native browser APIs prove insufficient.

Show:

- filename
- file size
- extension

Allow replacement/removal before Run.

---

## 5.7 Client-Side Preliminary Validation

Before sending:

- confirm required slot filled
- verify extension appears supported
- prevent Run without Action

This is convenience validation only.

Backend validation remains authoritative.

---

## 5.8 Run Submission

Construct FormData.

Add:

    action_id

Then append files using exact Action input IDs.

Example:

    formData.append("action_id", action.id)
    formData.append("sales_file", file)

Do not rename Action input keys in the frontend.

---

## 5.9 Running State

After submission:

- disable Action controls where appropriate
- disable Run button
- display clear processing indicator
- prevent duplicate submissions

Do not fake progress percentages.

If actual percentage is unknown, display:

    Processing...

rather than:

    73%

---

## 5.10 Error Display

Handle structured backend validation errors.

Examples:

    Missing required columns:
    Supplier
    Volume

or:

    Unsupported file type.

Do not display:

    [object Object]

Do not expose raw stack traces.

---

## Phase 5 Exit Criteria

The browser can:

- load Actions dynamically
- select either Action
- render correct input slot
- upload a file
- submit Run
- show success/error

Result preview/export refinement is Phase 6.

Update implementation-status.md.

Stop.

Do not begin Phase 6.

---

# Phase 6 — Filesystem-Independent Runtime, Results, Export, and Testing

## Purpose

Phase 6 changes ForgeXL's runtime architecture so that normal spreadsheet processing does **not depend on the development machine's persistent filesystem**.

This architectural change must be integrated without unnecessarily rebuilding or rewriting work completed in Phases 0/1–5.

The existing ForgeXL architecture remains conceptually:

```text
Next.js frontend
        ↓
FastAPI backend
        ↓
Action Registry
        ↓
Polars-based deterministic Actions
```
````

Phase 6 changes how files and run state move through that system.

The new canonical V1 architecture is:

```text
Browser
    ↓
same-origin /forge-api request
    ↓
Next.js proxy/rewrite
    ↓
FastAPI
    ↓
uploaded bytes in memory
    ↓
CSV/XLSX parser
    ↓
Polars DataFrame(s)
    ↓
existing Action Registry
    ↓
existing deterministic Action
    ↓
result DataFrame(s)
    ↓
preview / metrics / audit data
    ↓
in-memory CSV/XLSX export
    ↓
HTTP download
```

Persistent server-side copies of uploaded spreadsheets, intermediate spreadsheets, and generated exports are **not required for V1**.

---

# Architectural Rules Introduced by Phase 6

These rules override any earlier build-plan instruction that directly conflicts with them.

1. Do not require uploaded spreadsheets to be permanently written to disk.

2. Do not require intermediate Action results to be written to disk.

3. Do not require exported CSV/XLSX files to be written to disk before download.

4. The core Action Engine must operate on parsed tabular data rather than filesystem paths.

5. Existing Action definitions, Action IDs, Action versions, schemas, named input slots, validation rules, and deterministic transformation behavior must be preserved wherever possible.

6. Do not rewrite functioning Phase 0/1–5 functionality solely to conform to a new naming convention.

7. The browser must never need to know the FastAPI address or port.

8. Frontend code must not hard-code:

```text
http://localhost:8000
```

or:

```text
http://127.0.0.1:8000
```

9. Browser requests to ForgeXL's backend must use a same-origin namespace:

```text
/forge-api/*
```

10. Next.js will proxy/rewrite those requests internally to FastAPI.

11. FastAPI should remain bound to the development machine rather than being directly exposed to other LAN devices.

12. The Next.js development server may be exposed to the local network so ForgeXL can be tested from a second laptop using only a web browser.

13. The second laptop must require no development environment.

14. Run state may be stored in memory for V1.

15. Restarting the FastAPI development server may clear V1 run history.

16. No database should be introduced solely to solve this problem.

17. Do not introduce Redis, PostgreSQL, SQLite, Supabase, S3, or other persistent infrastructure during this phase.

18. Architecture should leave room for persistent storage later without requiring the Action Engine to be redesigned.

19. Automated tests must be capable of testing spreadsheet processing without an OS file picker.

20. Existing Phase 6 requirements involving results, preview, metrics, audit information, and export must still be implemented.

---

# Phase 6A — Compatibility Audit and Contract Freeze

## Goal

Determine exactly where the existing Phase 0/1–5 implementation depends on filesystem paths before changing runtime behavior.

This phase exists specifically to prevent the architectural update from damaging already-completed work.

## Required Work

### 1. Audit the existing repository

Search for filesystem-dependent behavior involving:

```text
data/
runs/
uploads/
inputs/
working/
exports/
manifest.json
tmp/
temp/
```

Also search for code using concepts such as:

```text
file_path
filepath
input_path
output_path
run_path
export_path
Path(...)
open(...)
write(...)
```

Do not assume every occurrence is wrong.

Classify each relevant occurrence.

### 2. Identify filesystem dependencies in these categories

Determine whether filesystem paths are currently used by:

- FastAPI upload endpoints
- run creation
- run metadata
- Action execution
- Action validation
- CSV parsing
- XLSX parsing
- preview generation
- result generation
- manifest generation
- export generation
- API responses
- frontend state
- frontend API calls
- automated tests

### 3. Identify existing public contracts

Before modifying implementation details, identify what existing Phase 0/1–5 code expects.

Examples include:

```text
Action IDs
Action Registry APIs
input-slot names
Action configuration schemas
validation response shapes
run IDs
backend endpoint shapes
frontend response objects
Action result structures
```

These contracts should be preserved unless changing one is genuinely necessary.

### 4. Explicitly identify path-coupled Actions

Inspect every currently registered Action.

Classify each Action as either:

```text
DataFrame-compatible
```

or:

```text
filesystem-coupled
```

A filesystem-coupled Action is one that directly opens or saves a file rather than receiving and returning tabular data.

### 5. Protect existing behavior with tests

Before refactoring a functioning subsystem, add or preserve enough automated tests to establish its current expected behavior.

Focus especially on:

- Action registration
- Action input validation
- Action execution
- deterministic output
- error handling

### 6. Do not perform the full architecture migration yet

Phase 6A is primarily defensive.

Small enabling refactors are allowed, but do not implement the complete in-memory upload system during this phase.

## Deliverable

At the end of Phase 6A, the codebase should have:

- documented filesystem dependency points
- identified public contracts
- identified filesystem-coupled Actions
- tests protecting important existing behavior
- a clear list of components requiring migration

## Completion Criteria

Phase 6A is complete only when the code bot can state exactly which existing components need modification and which completed Phase 0/1–5 components can remain untouched.

Stop after Phase 6A.

---

# Phase 6B — Introduce Runtime and Storage Abstractions

## Goal

Separate the concept of a ForgeXL run from the place where its runtime state happens to be stored.

The Action Engine must not care whether runtime state eventually lives:

- in memory
- on disk
- in a database
- in object storage

V1 will use memory.

## Required Work

### 1. Create a logical Run model

Represent a ForgeXL run independently from filesystem directories.

Include applicable metadata such as:

```text
run ID
Action ID
Action version
status
created timestamp
updated timestamp
input metadata
validation results
Action metrics
result metadata
preview metadata
audit information
errors
```

Do not include meaningless filesystem paths.

### 2. Create a Run Store abstraction

Create a narrow runtime-state interface conceptually equivalent to:

```text
create_run()
get_run()
update_run()
delete_run()
list_runs()
```

Exact naming may follow established project conventions.

### 3. Implement InMemoryRunStore

Create:

```text
InMemoryRunStore
```

or the project's equivalent.

Use process memory for V1.

### 4. Do not introduce persistent infrastructure

Do not add:

```text
PostgreSQL
SQLite
Redis
Supabase
S3
database migrations
```

### 5. Make future replacement possible

Business logic should depend on the Run Store interface rather than directly manipulating the in-memory dictionary.

This allows a future implementation such as:

```text
PersistentRunStore
```

without rewriting the Action Engine.

### 6. Add explicit run deletion

Support removing a run and releasing its associated runtime state.

Conceptually:

```text
delete_run(run_id)
```

### 7. Preserve existing run IDs

If ForgeXL already has a functioning run-ID convention, preserve it unless there is a concrete technical reason not to.

## Completion Criteria

Verify:

- runs can be created
- runs can be retrieved
- runs can be updated
- runs can be deleted
- Action Registry still works
- existing Actions still register
- no database was added
- no unrelated Phase 0/1–5 behavior broke

Stop after Phase 6B.

---

# Phase 6C — In-Memory Upload and Spreadsheet Parsing

## Goal

Allow actual CSV/XLSX uploads to reach FastAPI and become DataFrames without creating permanent server-side upload files.

## Required Work

### 1. Preserve named Action input slots

If an Action expects inputs such as:

```text
sales_data
product_master
customer_map
```

uploaded files must remain associated with those exact logical input slots.

### 2. Receive uploads using FastAPI

Accept uploaded files as multipart form data using the existing backend architecture.

### 3. Read upload content into memory

The desired flow is:

```text
browser upload
    ↓
FastAPI UploadFile
    ↓
bytes
    ↓
memory buffer
```

Do not use:

```text
browser upload
    ↓
save file permanently
    ↓
reopen saved file
```

### 4. Validate basic upload properties

Validate:

- required file exists
- supported extension
- file is not empty
- file size does not exceed configured limit
- required number of Action inputs were supplied

### 5. Do not trust MIME type alone

Browser-provided metadata may assist validation, but successful parsing determines whether a spreadsheet is actually usable.

### 6. Parse CSV from memory

Support:

```text
CSV bytes
    ↓
parser
    ↓
Polars DataFrame
```

without requiring a permanent local CSV file.

### 7. Parse XLSX from memory

Support:

```text
XLSX bytes
    ↓
file-like memory buffer
    ↓
XLSX parser
    ↓
tabular representation
    ↓
Polars DataFrame
```

Do not write the workbook to ForgeXL's filesystem simply to reopen it.

### 8. Preserve useful input metadata

Store metadata such as:

```text
original filename
input slot
extension
byte size
worksheet information
row count
column count
column names
parser information
```

### 9. Build understandable errors

Distinguish between errors such as:

```text
Missing required input
Unsupported format
Empty file
Unreadable CSV
Unreadable XLSX
Malformed workbook
Expected worksheet missing
File exceeds upload limit
```

Do not expose raw stack traces to users.

## Completion Criteria

Automated tests must successfully perform:

```text
generated CSV bytes
→ upload
→ parse
→ DataFrame
```

and:

```text
generated XLSX bytes
→ upload
→ parse
→ DataFrame
```

Also test invalid and empty files.

No OS file picker should be required for these tests.

Stop after Phase 6C.

---

# Phase 6D — Convert Action Execution to DataFrame-First Processing

## Goal

Make the deterministic Action Engine consume parsed DataFrames instead of server filesystem locations.

This is the most important separation-of-concerns change in Phase 6.

## Required Work

### 1. Establish the processing boundary

The core Action lifecycle should become:

```text
named uploaded inputs
        ↓
parser
        ↓
named DataFrame(s)
        ↓
Action Registry
        ↓
Action
        ↓
result DataFrame(s)
```

### 2. Refactor filesystem-coupled Actions

Any Action identified in Phase 6A as filesystem-coupled must be converted.

Avoid:

```python
action.run("/some/path/file.xlsx")
```

Prefer conceptually:

```python
action.run(inputs)
```

where `inputs` contains parsed named DataFrames.

### 3. Keep transformation logic inside Actions

Do not move Action-specific transformations into:

- React
- upload handlers
- FastAPI routes
- parser utilities
- export utilities

Those layers orchestrate data movement.

Actions transform data.

### 4. Preserve Action Registry behavior

The architectural migration must not unnecessarily change:

```text
Action IDs
Action versions
Action discovery
Action schemas
Action configuration
Action validation
```

### 5. Support one or more result tables

Design the result contract so an Action may return:

```text
primary result
```

and optionally:

```text
secondary results
```

Do not require multiple results when an Action only needs one.

### 6. Track run lifecycle status

Use clear status states.

For example:

```text
created
validating
ready
running
completed
failed
```

Use existing equivalent status names if already established.

### 7. Keep intermediate processing ephemeral

Temporary transformation state should generally remain as:

```text
DataFrames
Python objects
metadata
```

rather than intermediary spreadsheet files.

### 8. Clean up failed runs correctly

A failed Action must:

- mark the run failed
- retain a usable error description
- avoid leaving a partially valid result
- allow memory associated with abandoned processing to be released

## Completion Criteria

At least one real registered ForgeXL Action must successfully execute through:

```text
upload bytes
→ parse
→ validate
→ Action
→ result DataFrame
```

with no required:

```text
inputs/
working/
exports/
```

directory.

Its output must match the pre-refactor deterministic output.

Stop after Phase 6D.

---

# Phase 6E — Results, Preview, Metrics, and Audit Data

## Goal

Implement the originally planned Phase 6 results functionality using the new DataFrame-first architecture.

## Required Work

### 1. Build result metadata

After successful execution, calculate and store applicable information such as:

```text
input row count
output row count
affected row count
columns added
columns removed
validation warnings
Action-specific metrics
result schema
available result tables
```

### 2. Build preview data directly from results

Preview generation should use the result DataFrame.

Do not generate a temporary spreadsheet merely to read it back for preview.

Conceptually:

```text
Result DataFrame
    ↓
preview rows
    ↓
JSON
    ↓
frontend
```

### 3. Limit preview payload size

Do not send an entire large spreadsheet to the browser merely to display a preview.

Return a reasonable preview subset plus metadata describing the complete result.

### 4. Preserve useful schema information

Return appropriate column metadata so the frontend can accurately render values.

### 5. Build the audit summary

The run should explain what happened.

Where supported by the Action, include information such as:

```text
Action executed
inputs used
rows received
rows returned
rows affected
validation warnings
Action metrics
execution status
```

### 6. Keep audit data separate from transformation data

Audit metadata should not be mixed into the user's result table unless an Action explicitly creates those columns.

### 7. Connect the existing frontend

Use the frontend architecture already built in earlier phases.

Do not rebuild functioning components unnecessarily.

Adapt API integration only where the runtime contract changed.

## Completion Criteria

A completed run must display:

- successful/failed status
- result metrics
- preview
- applicable validation warnings
- audit summary

The UI must not depend on a local server-side result file.

Stop after Phase 6E.

---

# Phase 6F — In-Memory CSV and XLSX Export

## Goal

Allow users to download ForgeXL results without the backend first writing those exports to persistent local storage.

## Required Work

### 1. Generate CSV from the result DataFrame

Conceptually:

```text
Result DataFrame
    ↓
CSV serialization
    ↓
bytes
    ↓
HTTP response
```

### 2. Generate XLSX in memory

Conceptually:

```text
Result DataFrame
    ↓
XLSX writer
    ↓
in-memory binary buffer
    ↓
HTTP response
```

### 3. Maintain Excel compatibility

Generated XLSX files must open normally in Microsoft Excel.

Preserve:

- headers
- column ordering
- sensible cell values
- worksheet structure
- valid workbook format

### 4. Support multiple result tables

If an Action returns multiple tables, XLSX export should support writing them to separate worksheets when appropriate.

### 5. Use sensible worksheet names

Worksheet names must be:

- understandable
- valid for Excel
- collision-safe

### 6. Return correct download information

Send appropriate:

```text
Content-Type
Content-Disposition
filename
```

metadata.

Use a predictable ForgeXL filename convention.

For example:

```text
forgexl-<action>-<timestamp>.xlsx
```

### 7. Do not retain exports unnecessarily

Prefer:

```text
result DataFrame
    ↓
generate requested export
    ↓
send export
    ↓
release export buffer
```

rather than permanently retaining large duplicate export buffers.

### 8. Never expose server filesystem information

API responses must not contain local paths such as:

```text
/Users/...
data/runs/...
/tmp/...
```

## Completion Criteria

Automated tests must:

1. execute an Action
2. request CSV
3. read returned CSV bytes
4. verify expected content
5. request XLSX
6. reopen returned XLSX from memory
7. verify worksheet names
8. verify headers
9. verify representative values

Stop after Phase 6F.

---

# Phase 6G — Same-Origin Next.js Proxy and LAN Testing

## Goal

Allow ForgeXL running on the development machine to be fully tested from another laptop using only a browser.

The second laptop must handle:

- actual file selection
- drag-and-drop
- browser uploads
- browser downloads
- opening exported workbooks in Excel

without becoming a development machine.

## Target Architecture

```text
SECOND LAPTOP

Browser
    │
    │ http://<development-machine>:3000
    │
    ▼

DEVELOPMENT MACHINE

Next.js
:3000
    │
    │ /forge-api/*
    ▼
FastAPI
127.0.0.1:8000
    │
    ▼
ForgeXL Action Engine
```

## Required Work

### 1. Remove browser-side FastAPI URLs

Search frontend code for:

```text
localhost:8000
127.0.0.1:8000
```

Browser-facing requests must not use them.

### 2. Create the same-origin namespace

Frontend requests should use paths such as:

```text
/forge-api/...
```

### 3. Configure a Next.js rewrite/proxy

Configure Next.js so:

```text
/forge-api/<path>
```

is proxied internally to the FastAPI service on the development machine.

Conceptually:

```text
/forge-api/:path*
        ↓
http://127.0.0.1:8000/:path*
```

Use environment configuration where appropriate rather than scattering backend addresses throughout the codebase.

### 4. Avoid unnecessary body transformation

The Next.js layer should act as a transport proxy.

Do not create a second spreadsheet-processing implementation in Next.js.

Do not intentionally parse XLSX/CSV bodies in Next.js before sending them to FastAPI.

### 5. Keep FastAPI private to the development machine

FastAPI should normally remain on:

```text
127.0.0.1:8000
```

The second laptop should not need direct access to port 8000.

### 6. Expose Next.js to the LAN

Configure the development server so another device on the same trusted local network can open ForgeXL.

The development machine remains the machine running:

```text
Node
Next.js
Python
FastAPI
Polars
ForgeXL source code
```

The second laptop only needs:

```text
browser
Excel or equivalent spreadsheet application
test spreadsheets
```

### 7. Test the actual file picker

From the second laptop:

1. open ForgeXL
2. select an Action
3. click the upload control
4. select an actual XLSX file located on the second laptop
5. upload it
6. execute the Action
7. inspect the preview
8. request XLSX export
9. download the resulting workbook
10. open the workbook in Excel
11. verify the result

### 8. Test drag-and-drop

If ForgeXL supports drag-and-drop, test it from the second laptop using a real spreadsheet.

### 9. Test errors from the second laptop

Verify at minimum:

- missing file
- unsupported file
- malformed workbook
- failed Action
- disconnected backend

### 10. Do not introduce public deployment

LAN testing is the default development workflow.

Do not deploy ForgeXL publicly merely to test file upload behavior.

## Completion Criteria

ForgeXL must be usable from the second laptop without installing:

```text
Node
Python
Git
VS Code
ForgeXL dependencies
```

A real XLSX file located on that laptop must successfully travel through:

```text
second-laptop filesystem
→ browser
→ Next.js
→ FastAPI
→ ForgeXL Action
→ XLSX export
→ second-laptop download
```

Stop after Phase 6G.

---

# Phase 6H — Synthetic Spreadsheet Fixtures and End-to-End Regression Tests

## Goal

Make ForgeXL's correctness testable without manually uploading real spreadsheets for every development change.

Manual browser testing should validate UX.

Automated fixtures should validate processing correctness.

## Required Work

### 1. Create a spreadsheet fixture system

Programmatically generate deterministic test workbooks.

Do not depend exclusively on manually created files.

### 2. Create representative fixtures

Include small datasets covering cases such as:

```text
simple table
blank rows
blank cells
duplicate rows
duplicate keys
mixed numeric/text values
dates
malformed dates
Unicode characters
accented characters
unusual column names
multiple worksheets
missing required columns
extra columns
empty workbook
larger dataset
```

Only add fixture scenarios relevant to supported ForgeXL functionality.

### 3. Test known Action outcomes

Each deterministic Action should eventually have fixtures where:

```text
known input
    ↓
known Action configuration
    ↓
known expected output
```

### 4. Test complete backend execution

Tests should be capable of performing:

```text
create fixture in memory
        ↓
submit upload
        ↓
execute Action
        ↓
retrieve preview
        ↓
request export
        ↓
read export
        ↓
assert expected result
```

### 5. Test named input slots

For multi-file Actions, verify that swapping input files or omitting a required input produces correct validation behavior.

### 6. Test XLSX round-trip compatibility

Generate an XLSX export, reopen it programmatically, and verify:

- workbook is readable
- required worksheets exist
- headers are correct
- representative values are correct

### 7. Test failures

Add regression coverage for:

```text
invalid upload
missing required columns
invalid configuration
Action failure
unknown Action ID
unknown run ID
export before completion
```

### 8. Keep test data synthetic

Do not require proprietary company sales spreadsheets in the automated repository test suite.

Real company spreadsheets may still be used manually for final validation.

## Completion Criteria

A developer must be able to verify the majority of ForgeXL's spreadsheet engine by running automated tests without:

- opening a file picker
- manually saving a workbook
- accessing the second laptop
- deploying ForgeXL

Stop after Phase 6H.

---

# Phase 6I — Cleanup, Regression Review, and Architecture Documentation

## Goal

Finish the architectural migration cleanly before beginning the next master-plan phase.

## Required Work

### 1. Remove obsolete runtime filesystem code

After confirming that the new pipeline works, identify code that existed solely for the old model.

Examples may include:

```text
run-directory creation
input-directory creation
working-directory creation
export-directory creation
manifest file writers
path-building helpers
temporary persistent upload helpers
```

Only remove code confirmed to be obsolete.

Do not delete utilities still used elsewhere.

### 2. Search for remaining path coupling

Re-run the Phase 6A audit.

Confirm that Actions do not require server-local input or output paths.

### 3. Search frontend networking code

Confirm there are no browser requests hard-coded to:

```text
localhost:8000
127.0.0.1:8000
```

### 4. Verify cleanup behavior

Create a run, execute it, delete it, and confirm its large in-memory objects are no longer referenced by the Run Store.

### 5. Verify backend restart behavior

Restart FastAPI.

Confirm the application handles missing previous in-memory runs gracefully.

Do not treat lost V1 run history as corruption.

### 6. Update architecture documentation

Document the final architecture:

```text
Browser
    ↓
Next.js
    ↓
same-origin proxy
    ↓
FastAPI
    ↓
parser
    ↓
DataFrames
    ↓
Action Registry
    ↓
Action Engine
    ↓
result DataFrames
    ↓
preview / metrics / audit / export
```

### 7. Document V1 persistence behavior

Explicitly state:

> ForgeXL V1 processes uploaded spreadsheet data ephemerally. Uploaded files and generated exports are not required to persist on the ForgeXL server after processing. Run history stored only in process memory may be lost when the backend restarts.

### 8. Document the extension point for future persistence

Record that future versions may introduce implementations such as:

```text
PersistentRunStore
ObjectStorage
Database-backed run history
```

without changing the DataFrame-first Action contract.

Do not implement them now.

### 9. Run the complete regression suite

Run all automated tests from Phases 0/1–6.

Fix regressions caused by Phase 6 before continuing the master build plan.

Do not use this phase as an opportunity for unrelated cleanup.

## Completion Criteria

Phase 6 is complete when:

- uploads can be processed without persistent server-side files
- Actions operate on DataFrames rather than paths
- results remain in runtime state
- previews work
- metrics work
- audit summaries work
- CSV export works
- XLSX export works
- browser requests use `/forge-api/*`
- FastAPI can remain on `127.0.0.1`
- Next.js can be accessed from the second laptop
- the second laptop can upload real files
- the second laptop can download real XLSX results
- synthetic integration tests pass
- prior Phase 0/1–5 functionality still passes regression tests
- obsolete filesystem assumptions have been removed
- the architecture is documented

Only after all Phase 6 completion criteria pass should implementation proceed to the next phase of the master ForgeXL build plan.

# ============================================================

# PHASE 7

# Reliability, Accuracy, Security, and Performance Hardening

# ============================================================

## Purpose

Attempt to break the POC.

Do not merely demonstrate the happy path.

## Difficulty

High.

## Estimated Effort

3–6 hours.

---

# SUBPHASE 7A — Regression Tests

Run complete backend test suite.

Run:

    frontend lint
    frontend production build

Fix legitimate failures.

Do not suppress warnings merely to make output look clean.

---

# SUBPHASE 7B — Data Edge Cases

Test:

- empty CSV
- headers only
- one-row dataset
- duplicate rows
- all-null column
- Unicode
- accents
- apostrophes
- commas inside quoted CSV cells
- multiline CSV text if parser supports it correctly
- dates
- negative numbers
- zero values
- blank values
- large text cells

Verify logical integrity.

---

# SUBPHASE 7C — Incorrect Schemas

Try:

    SKU

vs:

    Sku

Try:

    Supplier

vs:

    Supplier Name

Confirm Product Master rejects incorrect schema.

Accuracy beats convenience.

---

# SUBPHASE 7D — Filename Security

Test filenames resembling:

    ../../example.csv

    ../data.csv

    strange name.csv

    file (1).csv

    café.csv

Verify no path traversal is possible.

---

# SUBPHASE 7E — Invalid IDs

Test malformed:

    Run IDs
    output IDs
    Action IDs

Ensure controlled 4xx responses.

---

# SUBPHASE 7F — Workbook Cases

Test:

- normal single-sheet XLSX
- empty XLSX
- workbook with multiple plausible data sheets
- workbook without required columns
- workbook containing formulas

Do not execute macros.

The application should read stored workbook values according to the parser's safe behavior.

---

# SUBPHASE 7G — Performance Fixtures

Generate synthetic files locally.

Do not use customer/company data for benchmarking.

Test approximately:

    10,000 rows
    50,000 rows
    100,000 rows

For CSV record:

- upload/save time
- parse time
- validation time
- Action execution time
- export time
- total time

Repeat meaningful test more than once.

Record observed numbers in:

    docs/implementation-status.md

Do not claim performance based on one anecdotal timing if repeated measurements differ substantially.

---

# SUBPHASE 7H — XLSX Performance

Repeat at:

    10,000
    50,000

and if reasonable:

    100,000

rows.

Record XLSX separately.

Do not compare XLSX and CSV as though they are equivalent formats.

---

# SUBPHASE 7I — Preview Performance

Use a large generated output.

Request:

    rows 1–100
    rows 10,001–10,100

Verify backend does not send the entire dataset.

Measure response behavior.

---

# SUBPHASE 7J — Memory/Architecture Review

Inspect code for accidental patterns such as:

- reading upload into memory multiple times
- converting entire dataframe to Python dictionaries unnecessarily
- converting entire result to JSON
- writing unnecessary temporary copies
- processing the same file repeatedly
- proxying through Next.js

Correct straightforward inefficiencies.

Do not perform speculative micro-optimization.

---

# SUBPHASE 7K — Local Exposure

Verify servers bind to:

    127.0.0.1

Verify CORS is not wildcard.

Verify no remote analytics/data calls were introduced.

---

## Phase 7 Exit Criteria

The POC has been deliberately stress-tested.

Known performance is measured.

Known limitations are documented.

All critical tests pass.

Update implementation-status.md.

Stop.

Do not begin Phase 8.

---

# ============================================================

# PHASE 8

# Final POC Validation and Handoff

# ============================================================

## Purpose

Determine whether the POC actually proved the concept.

## Difficulty

Low–Moderate.

## Estimated Effort

1–2 hours.

---

## 8.1 Clean Setup Test

Test installation from a clean state as closely as reasonably possible.

Verify documented setup works.

The user should not need hidden manual setup steps known only to the developer.

---

## 8.2 README

Create/update:

    README.md

Keep it concise.

Include:

- project purpose
- prerequisites
- initial setup
- starting application
- local URLs
- running backend tests
- running frontend lint/build
- where Runs are stored
- current supported formats
- current Actions

Do not duplicate the entire build-plan.md.

---

## 8.3 Startup Workflow

Target final workflow after installation:

    cd <project>

    npm run dev

Then:

    open http://127.0.0.1:3000

If one-command startup is not reliable, document exactly why.

---

## 8.4 Complete Acceptance Test

Perform manually or through browser automation where practical:

### Exact Duplicate Remover

1. Select Action.
2. Upload known fixture.
3. Run.
4. Verify metrics.
5. Preview.
6. Download CSV.
7. Download XLSX.
8. Verify outputs.

### Product Master Builder

1. Select Action.
2. Upload known valid sales fixture.
3. Run.
4. Verify required output fields.
5. Verify duplicates removed.
6. Preview.
7. Download both formats.
8. Verify outputs.

### Validation

1. Select Product Master.
2. Upload file missing a required column.
3. Run.
4. Verify understandable validation error.
5. Verify no successful output is falsely presented.

---

## 8.5 Extensibility Proof

Perform a code review specifically answering:

> If we add another Action tomorrow, what files must change?

Expected answer:

    new Action module
    Action registry
    Action tests

Potentially:

    fixtures

Not expected:

    ActionSelector.js
    page.js
    FileUploadSlot.js
    RunButton.js

If frontend modification is required merely to recognize a new ordinary Action:

the POC architecture has failed an important requirement.

Fix it before declaring success.

---

# 35. Final POC Evaluation

At completion, score each category from 1–10.

## A. Ease of Use

Can a nontechnical user operate it?

## B. Speed

Are ordinary sales datasets processed quickly enough?

## C. Accuracy

Do controlled tests prove correct results?

## D. Error Clarity

Does invalid input produce understandable explanations?

## E. Extensibility

Can new Actions be added cleanly?

## F. Maintainability

Is the implementation understandable rather than clever?

## G. Local Data Privacy

Does ordinary processing remain local?

## H. Export Quality

Are CSV/XLSX outputs immediately usable?

## I. UI Quality

Does the interface feel like a tool rather than a developer demo?

## J. Architectural Potential

Can this foundation reasonably evolve into the larger Data Workbench?

Calculate average only as a convenient summary.

Do not let a strong average conceal a critical failure in accuracy or architecture.

---

# 36. Go / Revise / Stop Decision

At the end, classify the POC.

## GO

Use when:

- workflow is pleasant
- results are accurate
- performance is acceptable
- new Actions are easy to create
- architecture remains understandable
- no fundamental blockers emerged

Then the logical next project is the full:

    Data Workbench

Possible future additions:

- desktop shell
- DuckDB
- Data Library
- saved reference datasets
- reusable normalization components
- Action Builder
- audit browser
- historical Runs
- AI-assisted Action generation

But none should be built during this POC.

---

## REVISE

Use when the concept works but one architectural piece needs improvement.

Examples:

- XLSX handling is too slow
- frontend/backend startup is awkward
- Action interface needs adjustment
- output persistence is inefficient

Make the smallest architecture change needed and retest.

---

## STOP

Use when evidence shows the approach fundamentally fails its objective.

Examples:

- unacceptable performance
- inability to preserve accuracy
- local architecture proves too cumbersome
- Actions cannot be made extensible without major complexity

Do not continue merely because significant time has already been invested.

---

# 37. Definition of Done

The POC is DONE only when all of the following are true:

- Next.js frontend runs locally
- FastAPI backend runs locally
- both bind to loopback
- one command can start development environment
- frontend discovers Actions dynamically
- Action registry exists
- two Actions exist
- CSV input works
- XLSX input works
- invalid data is rejected explicitly
- source uploads are preserved
- Run manifests exist
- internal outputs use Parquet
- browser preview works
- preview is paginated
- CSV export works
- XLSX export works
- automated backend tests pass
- frontend lint passes
- frontend production build passes
- controlled accuracy fixtures pass
- large synthetic data benchmark has been performed
- performance results are documented
- implementation-status.md is current
- README contains working setup instructions
- extensibility test passes
- final POC evaluation is documented

No requirement should be marked complete merely because code exists.

It must be demonstrated to work.

---

# 38. Instructions for Starting Each Future Coding Thread

The user can begin each coding-agent conversation with wording similar to:

    Read docs/build-plan.md completely before doing anything.
    Then read docs/implementation-status.md.
    Execute Phase 1 only.
    Follow the plan exactly.
    Do not begin Phase 2.
    Test your work before stopping.

Replace Phase number as appropriate.

The coding agent must obey the Phase boundary.

---

# 39. Suggested Phase Sequence

Thread 1:

    Phase 0
    Repository Audit and Build Contract

Thread 2:

    Phase 1
    Application Foundation and Local Runtime

Thread 3:

    Phase 2
    Backend Data Engine and Action Contract

Thread 4:

    Phase 3
    Upload, Parsing, Run Execution, Storage, and Export Pipeline

Thread 5:

    Phase 4
    Proof Actions and Accuracy Tests

Thread 6:

    Phase 5
    Dynamic Frontend Action Runner

Thread 7:

    Phase 6
    Results, Preview, Audit Summary, and Export UX

Thread 8:

    Phase 7
    Reliability, Accuracy, Security, and Performance Hardening

Thread 9:

    Phase 8
    Final POC Validation and Handoff

Do not combine Phases merely to finish faster.

The Phase boundaries exist to:

- control complexity
- reduce AI drift
- make testing manageable
- expose architecture problems early
- allow the user to review progress
- make mistakes easier to isolate

---

# 40. Final Engineering Principle

This proof of concept should not attempt to impress anyone through technical complexity.

Its job is to prove one simple system:

    INPUT
      ↓
    VALIDATE
      ↓
    ACTION
      ↓
    RESULT
      ↓
    VERIFY
      ↓
    EXPORT

The software should feel simple because the complexity is controlled underneath it.

Favor:

    explicit over magical

    deterministic over clever

    reusable over duplicated

    measured over assumed

    validated over guessed

    simple over abstract

The POC succeeds if the user can drop in raw data, choose a trusted Action, run it, understand what happened, and confidently use the result.

# ============================================================

# POST-POC PRODUCT EXPANSION

# Persistent Data Library and Automated Monthly Sales Rep Reporting

# ============================================================

## Purpose

This section begins the first deliberate expansion of ForgeXL beyond the original proof of concept.

Do not begin these phases until:

1. Phases 0–8 are complete.
2. The final POC evaluation has been performed.
3. The POC receives a **GO** decision.

The purpose of this expansion is to prove that ForgeXL can evolve from an ephemeral spreadsheet-processing workbench into a persistent local reporting system without discarding the Action architecture already validated by the POC.

The primary initial business workflow is:

```text
Upload latest monthly sales data
        +
Upload latest monthly sample data
        +
Upload current sales-rep/account assignment list
        ↓
Validate all three inputs
        ↓
Persist the new reporting-period data
        ↓
Combine it with previously stored history
        ↓
Execute the Monthly Sales Rep Report Action
        ↓
Generate one finished Excel workbook per sales rep
        ↓
Bundle reports for download
```

The intended recurring user workflow is eventually:

```text
Open ForgeXL
    ↓
Monthly Reports
    ↓
Upload latest files
    ↓
Generate
    ↓
Review validation
    ↓
Download reports
```

The user should not need to rebuild Power Query queries, PivotTables, worksheets, formulas, joins, or report formatting every month.

---

# Post-POC Architectural Authorization

The original POC intentionally prohibited several capabilities that are now required.

This section explicitly authorizes:

- a persistent local Data Library
- saved reference datasets
- versioned historical datasets
- persistent monthly account snapshots
- richer generated Excel artifacts
- multiple generated files from one Run
- ZIP/batch downloads
- a dedicated recurring-workflow UI built on top of the generic ForgeXL backend architecture

This does **not** automatically authorize:

- cloud storage
- remote SaaS databases
- authentication
- multi-user support
- public deployment
- AI-generated business rules
- fuzzy matching
- scheduled jobs
- automatic email distribution
- external analytics or telemetry

ForgeXL remains local-first unless a later build-plan update explicitly changes that requirement.

---

# Architectural Rule: Run State and Business Data Are Different

Do not use `RunStore` as the permanent repository for company sales history.

They solve different problems.

```text
RunStore
    ↓
temporary execution/runtime state

Data Library
    ↓
persistent business datasets used across Runs
```

The existing DataFrame-first Action contract must remain intact.

Actions should continue to receive resolved DataFrames and return deterministic results.

An Action should not directly manipulate arbitrary files in the Data Library.

Persistence, version resolution, dataset loading, and dataset commits belong to dedicated services outside Action transformation logic.

---

# ============================================================

# PHASE 9

# Persistent Data Library Foundation

# ============================================================

## Purpose

Create the persistent local dataset layer required for recurring reporting while preserving ForgeXL's existing Action and Run architecture.

The first required persistent datasets are:

```text
Sales History
Sample History
Account Assignment Snapshots
```

Additional datasets may be added later through the same architecture.

Do not build the Monthly Sales Rep Report yet.

---

## SUBPHASE 9A — Data Library Contract

Create a dedicated Data Library abstraction independent of `RunStore`.

Conceptually support operations equivalent to:

```text
create dataset
get dataset metadata
list datasets
commit dataset version
get dataset version
list dataset versions
load dataset version
```

Exact names should follow existing ForgeXL conventions.

Every persistent dataset must have a stable logical ID.

Initial IDs should conceptually represent:

```text
sales_history
sample_history
account_assignments
```

Do not encode absolute filesystem locations into business logic.

---

## SUBPHASE 9B — Dataset Version Model

Create structured metadata for every committed dataset version.

Record at minimum:

```text
dataset ID
version ID
dataset type
reporting period or effective period
created timestamp
source filename
source byte size
source content hash
row count
column count
column schema
parser information
```

Where applicable also record:

```text
minimum date
maximum date
report month
replacement/supersession information
```

Dataset version IDs must be generated by ForgeXL rather than derived from unsafe user filenames.

---

## SUBPHASE 9C — Local Persistent Storage

Implement a local persistent Data Library.

For the first implementation, prefer:

```text
Parquet
+
small structured metadata files/catalog
```

unless implementation produces concrete evidence that a database is required.

A conceptual physical structure may resemble:

```text
data/
    library/
        sales_history/
            ...
        sample_history/
            ...
        account_assignments/
            ...
```

Do not expose physical paths through the frontend API.

Use atomic writes wherever a partial write could corrupt persistent state.

Do not introduce PostgreSQL, Supabase, Redis, or another server database merely because persistent data now exists.

If repeated cross-file analytical workloads later justify DuckDB or SQLite, that must be a separate approved architecture change supported by measured evidence.

---

## SUBPHASE 9D — Immutable Historical Versions

Historical dataset versions must be treated as immutable after successful commit.

Do not silently overwrite previously stored monthly history.

If the user deliberately replaces incorrect data for an existing month:

1. preserve the old version
2. create a new version
3. mark which version supersedes the previous version
4. retain enough metadata to explain the change

This requirement exists so historical reports can be reproduced.

---

## SUBPHASE 9E — Account Ownership Snapshots

Account assignments require snapshot semantics.

Do **not** maintain only one mutable file representing current ownership.

Store account assignments by effective reporting period.

Example:

```text
account_assignments
    August 2026 snapshot
    September 2026 snapshot
    October 2026 snapshot
```

Reason:

An account may belong to one rep in September and another rep in November.

Regenerating the September report later must use the September ownership snapshot rather than November's current ownership.

---

## SUBPHASE 9F — Persistence Verification

Automated tests must prove:

- a dataset version survives backend restart
- dataset metadata survives backend restart
- multiple versions of one logical dataset can coexist
- an older version can be loaded explicitly
- replacing a period does not silently destroy the previous version
- account snapshots remain independently retrievable
- invalid/corrupt commits do not leave partially valid persistent state

---

## Phase 9 Exit Criteria

Phase 9 is complete when ForgeXL has a persistent, versioned, local Data Library that is clearly separated from ephemeral Run state.

No Monthly Sales Rep Report business logic is required yet.

Stop after Phase 9.

---

# ============================================================

# PHASE 10

# Monthly Dataset Ingestion and Versioning

# ============================================================

## Purpose

Turn the three recurring source files into safe, validated, versioned Data Library updates.

The recurring inputs are:

```text
monthly sales data
monthly sample data
current sales-rep/account assignment data
```

The ingestion layer must protect the Data Library from malformed, duplicated, partial, or period-mismatched uploads.

---

## SUBPHASE 10A — Canonical Source Schemas

Document the exact accepted source schemas for:

```text
Sales
Samples
Account Assignments
```

Use the actual company exports that support the existing manually verified monthly reports.

Do not guess alternative column names.

Do not silently treat semantically similar columns as equivalent.

Any required normalization or aliasing must be explicitly specified, deterministic, and tested.

---

## SUBPHASE 10B — Reporting Period Detection

For recurring monthly uploads, determine the applicable report period from trusted source data.

Do not rely solely on filenames such as:

```text
September Sales.csv
```

Validate applicable dates contained inside the dataset.

The program must detect situations such as:

- wrong month uploaded
- file spanning an unexpected period
- empty reporting period
- future-dated rows
- duplicate monthly upload
- mismatched periods between related files

Where automatic determination is genuinely ambiguous, require explicit user selection rather than guessing.

---

## SUBPHASE 10C — Monthly Sales Commit

Implement controlled monthly updates to Sales History.

The recurring update must:

1. parse the uploaded file using the existing ForgeXL parser
2. validate schema
3. validate reporting period
4. calculate source hash
5. detect an already-imported identical file
6. validate rows before persistence
7. commit the month as a new version/partition
8. update Data Library metadata atomically

An accidental repeat upload of the same source file must not duplicate sales history.

---

## SUBPHASE 10D — Monthly Samples Commit

Implement the equivalent controlled process for Sample History.

Sales and samples must remain logically distinct datasets even if their source schemas overlap.

Do not convert samples into ordinary sales merely to simplify implementation.

---

## SUBPHASE 10E — Account Assignment Commit

Import the current sales-rep/account assignment list as the snapshot for the applicable reporting month.

Validate at minimum:

- required customer identifier
- required sales rep identifier
- duplicate customer assignments where ownership is expected to be unique
- blank customer names
- blank rep names
- rows that cannot be assigned safely

Preserve the full source snapshot required to reproduce the month later.

---

## SUBPHASE 10F — Coordinated Monthly Import

Provide a transaction-like monthly import operation.

The three required monthly inputs must be validated before the reporting cycle is considered ready.

Conceptually:

```text
Sales upload
Samples upload
Account assignment upload
        ↓
validate all
        ↓
show issues
        ↓
commit reporting cycle
```

Do not leave the application in a misleading state where September sales were committed successfully but the September ownership snapshot silently failed.

If one input fails before final commit, the user must receive a clear explanation of what was and was not persisted.

---

## SUBPHASE 10G — Historical Bootstrap

Create a deliberate one-time bootstrap path for loading the historical data needed by the first automated report.

The bootstrap may accept a wider historical period than recurring monthly ingestion.

After bootstrap, the ordinary workflow should require only the newest reporting-period data.

Do not require the user to re-upload the complete historical dataset every month.

---

## Phase 10 Exit Criteria

ForgeXL can safely establish historical data once and subsequently add one reporting period at a time without duplicating or corrupting history.

Stop after Phase 10.

---

# ============================================================

# PHASE 11

# Library-Backed Action Inputs and Reproducible Runs

# ============================================================

## Purpose

Allow ordinary ForgeXL Actions to consume exact versions of persistent Data Library datasets without turning Actions themselves into storage-aware code.

The Action must still receive DataFrames.

---

## SUBPHASE 11A — Extend Input Source Metadata

Extend Action input metadata in a backwards-compatible way so an Action input can originate from either:

```text
uploaded file
```

or:

```text
Data Library dataset version
```

Existing Actions must continue to behave as they do now.

Do not require changes to Exact Duplicate Remover or Product Master Builder merely because library-backed inputs now exist.

---

## SUBPHASE 11B — Dataset Reference Resolution

Before Action execution, the runner or a dedicated input-resolution service must resolve the requested library version into a DataFrame.

Conceptually:

```text
dataset reference
        ↓
Data Library
        ↓
exact immutable dataset version
        ↓
Polars DataFrame
        ↓
Action
```

Actions must not open Data Library files themselves.

---

## SUBPHASE 11C — Explicit Version Provenance

A Run using persistent data must record the exact dataset versions used.

Never record only:

```text
sales_history = current
```

Record the resolved immutable version identity.

This allows the same historical report to be regenerated later against the same source state.

---

## SUBPHASE 11D — Preserve Determinism

The existing rule remains:

> Same Action version + same logical inputs = same logical output.

A moving concept such as `latest` or `current` may be used during input selection, but it must be resolved to immutable version IDs before the Action executes.

The Run manifest must record those resolved IDs.

---

## SUBPHASE 11E — Regression Tests

Verify:

- old upload-backed Actions still work
- library-backed inputs resolve correctly
- specific old versions can be selected
- a Run records exact dataset provenance
- changing the current library version does not change an already recorded Run's input identity
- missing library data fails clearly

---

## Phase 11 Exit Criteria

Persistent datasets can participate in the existing DataFrame-first Action Engine without coupling Action business logic to storage.

Stop after Phase 11.

---

# ============================================================

# PHASE 12

# Rich Artifact Output Framework

# ============================================================

## Purpose

Extend ForgeXL beyond generic result-table CSV/XLSX exports so an Action can also produce purpose-built files such as finished reports.

This capability must be generic enough to support future reporting Actions.

---

## SUBPHASE 12A — Dataset Outputs vs Artifacts

Preserve the existing concept of tabular outputs.

Add a separate concept:

```text
Artifact
```

Examples:

```text
formatted XLSX workbook
ZIP archive
future PDF report
```

Do not pretend a finished workbook containing layout, formatting, multiple report sections, and presentation logic is merely another DataFrame.

---

## SUBPHASE 12B — Extend ActionResult Safely

Extend the Action result contract so it may contain:

```text
tabular outputs
artifacts
metrics
audit information
```

Existing Actions that return only DataFrames must remain valid.

Do not require every Action to generate artifacts.

---

## SUBPHASE 12C — Artifact Metadata

Each artifact should expose structured metadata such as:

```text
artifact ID
label
filename
media type
byte size
artifact type
```

Do not expose local filesystem paths.

Artifacts may remain in runtime memory for a Run unless persistence is explicitly required later.

The persistent Data Library stores source/history data; it does not automatically become a permanent report archive.

---

## SUBPHASE 12D — Rich XLSX Rendering Utilities

Create reusable XLSX helpers for report-quality workbooks.

Support as required:

- multiple worksheets
- formatted headers
- currency formats
- percentage formats
- integer/decimal formats
- sensible date formats
- column widths
- row heights where necessary
- frozen panes
- filters
- tables
- conditional formatting
- worksheet ordering
- readable totals
- consistent styling

Do not implement business calculations in the XLSX formatting layer.

Formatting should render already-calculated report data.

---

## SUBPHASE 12E — Multiple Artifacts Per Run

One Action must be capable of producing multiple files.

Example:

```text
Beth Comeaux - September 2026.xlsx
Kevin Wardell - September 2026.xlsx
Jennifer Jones - September 2026.xlsx
...
```

Artifact IDs and filenames must be collision-safe and deterministic where appropriate.

---

## SUBPHASE 12F — Batch ZIP Export

Support downloading all artifacts from a Run as one ZIP archive.

The ZIP must be generated safely and must not allow artifact filenames to create nested or traversing paths unexpectedly.

---

## SUBPHASE 12G — Artifact API and Frontend Support

Add generic frontend support for:

- listing generated artifacts
- downloading an individual artifact
- downloading all artifacts where a batch download is available

Do not hardcode sales-rep names into the frontend.

---

## Phase 12 Exit Criteria

A test Action can generate multiple polished XLSX artifacts plus a ZIP bundle through generic ForgeXL infrastructure.

Stop after Phase 12.

---

# ============================================================

# PHASE 13

# Monthly Sales Rep Report Specification and Calculation Engine

# ============================================================

## Purpose

Port the existing manually verified monthly sales-rep reporting logic into one deterministic ForgeXL Action.

Do not begin implementation by reverse-engineering vague expectations from memory.

First freeze the existing report's business definitions.

---

## SUBPHASE 13A — Create the Report Specification

Create:

```text
docs/monthly-sales-rep-report-spec.md
```

This document becomes the authoritative specification for this Action.

Derive it from:

- the current verified Excel monthly report
- the existing Power Query logic
- accepted business definitions
- manually verified results from a completed month

Document exactly:

- source datasets
- accepted schemas
- invoice/sample rules
- date-window rules
- sales-rep ownership rules
- account rules
- company-vs-rep comparison rules
- supplier calculations
- percentage calculations
- placement definitions
- sample definitions
- required report sections
- sorting rules
- displayed totals
- treatment of credits/returns where applicable
- treatment of missing ownership
- treatment of zero/null values

Do not invent a formula merely because it appears reasonable.

If the existing report does not establish a rule clearly, document the ambiguity and resolve it before implementation.

---

## SUBPHASE 13B — Define the Monthly Report Action

Create a registered Action conceptually named:

```text
monthly_sales_rep_report
```

Use an appropriate semantic version.

Its logical inputs should be resolved from exact Data Library versions and should include the historical information required by the report specification.

At minimum this will involve:

```text
sales history
sample history
account assignment snapshot for the reporting period
```

Additional persistent reference datasets may be added only if the report specification proves they are required.

---

## SUBPHASE 13C — Reporting Period Resolution

The Run must have one explicit reporting period.

Resolve it before calculations begin.

Do not allow different sections of the same report to independently decide what "current month" means.

All month-over-month, year-over-year, current-period, or prior-period calculations must derive from the same report-period context.

---

## SUBPHASE 13D — Dynamic Rep Roster

Do not hardcode the company's sales-rep list.

Determine applicable reps from the authoritative reporting-period data according to the report specification.

A newly added rep should appear automatically when valid source data assigns activity/accounts to that rep.

A departed rep should not remain merely because their name exists in source code.

---

## SUBPHASE 13E — Shared Prepared Data Model

Perform reusable preparation once.

Conceptually create clean internal frames such as:

```text
prepared sales
prepared samples
prepared account ownership
report-period context
```

Avoid recalculating identical normalization, period, and join logic separately for every rep.

Do not mutate original Data Library versions.

---

## SUBPHASE 13F — Report Calculation Tables

Produce deterministic DataFrames for every table required by the report specification.

The implementation should support the established report categories, including where applicable:

```text
rep summary
account performance
supplier performance
company-vs-rep supplier comparison
supplier share of rep sales
product performance
placements
samples
placement detail
sample detail
supporting validation/detail tables
```

The exact authoritative set is whatever is frozen in `monthly-sales-rep-report-spec.md`.

Do not remove an existing accepted report section merely because it is inconvenient to implement.

---

## SUBPHASE 13G — Company and Rep Calculations

Calculate company-level comparison data once where possible rather than independently rebuilding the same company totals for every rep.

Rep reports may then consume:

```text
shared company metrics
+
rep-specific metrics
```

This is important for both accuracy and performance.

---

## SUBPHASE 13H — Validation Before Report Generation

Detect conditions that could make apparently polished reports factually unreliable.

Examples include:

- unrecognized sales reps
- missing account ownership
- duplicate account ownership
- malformed invoice dates
- unexpected invoice types
- missing required monetary/quantity fields
- reporting-period mismatch
- unexplained source-schema changes
- duplicate monthly source data
- missing historical comparison period

Where a condition makes the report unsafe, fail rather than producing a plausible-looking workbook.

Warnings may be used only when continuing is genuinely safe.

---

## SUBPHASE 13I — Golden-Month Accuracy Tests

Choose at least one previously completed monthly report whose values have been manually spot-checked.

Create synthetic or sanitized test fixtures that reproduce representative business cases from that report.

Verify exact expected results for:

- rep totals
- company totals
- account metrics
- supplier metrics
- percentage calculations
- placements
- sample counts
- representative detail rows

Do not validate only row counts.

Validate values.

---

## Phase 13 Exit Criteria

The Action produces correct report DataFrames for every applicable rep, and automated tests prove the business calculations before any attention is paid to workbook appearance.

Stop after Phase 13.

---

# ============================================================

# PHASE 14

# Batch Sales Rep Workbook Generation

# ============================================================

## Purpose

Turn the verified report calculations into the finished Excel workbooks actually distributed or reviewed by the business.

Formatting must never alter the underlying calculated values.

---

## SUBPHASE 14A — Workbook Structure

Define the exact worksheet structure in the report specification.

Each rep workbook should contain the applicable report sections already accepted by the existing monthly reporting process.

Conceptually this may include:

```text
Summary
Account Performance
Supplier Performance
Product / Placement Performance
Samples
Placement Detail
Sample Detail
```

Use the authoritative report specification if its final worksheet names differ.

---

## SUBPHASE 14B — Consistent Professional Formatting

Create one reusable workbook-rendering system.

Apply consistent:

- title/header structure
- date/report-period labeling
- currency formatting
- percentage formatting
- quantity formatting
- column widths
- alignment
- frozen panes
- filters
- totals
- visual hierarchy

Do not create slightly different formatting logic for every rep.

---

## SUBPHASE 14C — One Workbook Per Rep

For every applicable rep:

```text
rep calculation tables
        ↓
shared workbook renderer
        ↓
finished XLSX artifact
```

Filename convention should include at minimum:

```text
sales rep
reporting period
```

Example:

```text
Beth Comeaux - September 2026.xlsx
```

Filenames must be sanitized safely.

---

## SUBPHASE 14D — Batch Artifact Generation

One successful Monthly Sales Rep Report Run should produce all applicable rep workbooks.

The user must not need to run the Action once per sales rep.

---

## SUBPHASE 14E — ZIP Bundle

Generate a batch archive conceptually named:

```text
September 2026 Sales Rep Reports.zip
```

containing every successfully generated rep workbook.

An individual workbook must still be downloadable separately.

---

## SUBPHASE 14F — Workbook Round-Trip Tests

Programmatically reopen generated XLSX files.

Verify:

- workbook opens
- expected worksheets exist
- headers are correct
- representative values equal the calculation DataFrames
- percentages remain numeric
- currency values remain numeric
- workbook contains no corrupted sheet names
- artifact filenames are correct

Also manually open representative outputs in Microsoft Excel on Mac.

---

## Phase 14 Exit Criteria

One Action Run generates every required rep workbook correctly and packages them for convenient download.

Stop after Phase 14.

---

# ============================================================

# PHASE 15

# One-Step Monthly Reporting Workflow and Production Validation

# ============================================================

## Purpose

Reduce the recurring monthly process to the smallest safe set of user actions.

This phase combines the already-built Data Library, ingestion system, Action Engine, and artifact framework into the final monthly workflow.

Do not move business calculations into the frontend.

---

## SUBPHASE 15A — Monthly Reports Workflow UI

Create a dedicated ForgeXL workflow surface for monthly reporting.

Conceptually:

```text
Monthly Sales Rep Reports

Reporting Period
September 2026

Sales Data
[ Upload ]

Sample Data
[ Upload ]

Account Assignments
[ Upload ]

[ Validate and Generate ]
```

The frontend may orchestrate existing backend capabilities, but the backend remains authoritative for:

- parsing
- validation
- persistence
- reporting-period resolution
- dataset versioning
- report calculations
- workbook generation

---

## SUBPHASE 15B — Pre-Generation Validation Summary

Before final report generation, show a concise validation summary.

Conceptually:

```text
Sales rows                     ✓
Sample rows                    ✓
Reporting period               ✓
Sales reps detected            ✓
Account assignments            ✓
Historical comparison data     ✓

Warnings / Errors
...
```

Do not display a green success state merely because files uploaded successfully.

Validation must reflect whether the resulting reports can be trusted.

---

## SUBPHASE 15C — Atomic Reporting Cycle

The workflow should behave as one logical monthly reporting cycle:

```text
upload
→ validate
→ commit monthly source versions
→ resolve exact versions
→ execute report Action
→ generate artifacts
→ present downloads
```

The workflow must clearly distinguish:

```text
source data committed
```

from:

```text
reports successfully generated
```

If report rendering fails after a valid source commit, do not corrupt or roll back valid historical source data merely to hide the failure.

Instead allow report generation to be rerun from the exact committed dataset versions.

---

## SUBPHASE 15D — Re-Run Without Re-Upload

Once a reporting period has been committed successfully, the user must be able to regenerate its reports without uploading the source files again.

Example:

```text
Reporting Period: September 2026
[ Regenerate Reports ]
```

This is required for reproducibility and for fixing report-rendering code without altering historical company data.

---

## SUBPHASE 15E — Historical Report Reproduction

Select an older reporting period and regenerate it using:

- its historical sales state
- its historical sample state
- its historical account snapshot
- the intended Action version or clearly recorded current-version behavior

The system must not silently substitute today's account ownership for an older month.

---

## SUBPHASE 15F — End-to-End Monthly Acceptance Test

Perform a complete real-data acceptance test against one known monthly reporting cycle.

Steps:

1. Begin with the required historical library already established.
2. Upload one new month's sales file.
3. Upload that month's samples file.
4. Upload that month's account assignment file.
5. Run validation.
6. Review warnings/errors.
7. Commit the reporting cycle.
8. Generate all rep reports.
9. Download the ZIP.
10. Open representative rep workbooks in Excel.
11. Compare representative totals against independently spot-checked source data.
12. Confirm company totals.
13. Confirm supplier totals and percentages.
14. Confirm account ownership.
15. Confirm representative sample/placement metrics.
16. Restart ForgeXL.
17. Regenerate the same month without re-uploading.
18. Confirm the reproduced values match.

---

## SUBPHASE 15G — Duplicate/Correction Test

Test:

- uploading the exact same monthly sales file twice
- uploading corrected sales data for an already committed month
- uploading a different account snapshot for an already committed month
- regenerating reports before and after an explicitly approved correction

The program must make these state changes explicit.

Never silently append corrected data on top of incorrect data and thereby double-count the month.

---

## SUBPHASE 15H — Performance Test

Benchmark the real recurring workflow using synthetic datasets approximating actual company size.

Measure separately:

```text
upload/parsing
validation
persistent commit
historical loading
report calculations
XLSX generation
ZIP generation
total workflow
```

Optimize only after locating measured bottlenecks.

Do not add a database or complex caching architecture merely because it appears theoretically faster.

---

## SUBPHASE 15I — Final Monthly Workflow Acceptance Criteria

The expansion is successful only if the normal monthly process has become approximately:

```text
1. Open ForgeXL.
2. Open Monthly Reports.
3. Upload latest sales data.
4. Upload latest sample data.
5. Upload current account assignments.
6. Review validation.
7. Generate.
8. Download reports.
9. Spot-check.
```

The user must not need to:

- append CSVs manually in Excel
- edit Power Query source paths
- recreate PivotTables
- modify formulas
- manually split reports by rep
- manually copy company totals
- manually calculate supplier percentages
- manually create rep workbooks
- manually rename each workbook

---

## Phase 15 Exit Criteria

The recurring monthly sales-rep reporting process is fully reproducible from persistent ForgeXL data and requires only the latest reporting-period inputs.

After the first historical bootstrap, previously imported history is reused automatically.

A single reporting workflow produces all applicable rep workbooks.

Historical reports can be regenerated accurately.

Automated and manual accuracy checks pass.

Only after these criteria pass should further automation such as scheduled ingestion, automatic distribution, or cloud synchronization be considered.

Stop after Phase 15.

---

# ============================================================

# POST-EXPANSION ARCHITECTURE

# ============================================================

After Phase 15, the intended architecture is:

```text
                         FORGEXL
                            │
          ┌─────────────────┴─────────────────┐
          │                                   │
          ▼                                   ▼
   Ephemeral Run State                 Persistent Data Library
       RunStore                               │
                                              ├── Sales History
                                              ├── Sample History
                                              └── Account Snapshots
                                                     │
                                                     ▼
                                            Version Resolution
                                                     │
                                                     ▼
Browser
   ↓
Next.js
   ↓
/forge-api
   ↓
FastAPI
   ↓
Action Runner
   ↓
Resolved DataFrames
   ↓
Monthly Sales Rep Report Action
   ↓
Verified Result DataFrames
   ↓
Artifact Renderer
   ↓
┌────────────────────────────────────────────────────┐
│ Beth Comeaux - September 2026.xlsx                 │
│ Kevin Wardell - September 2026.xlsx                │
│ Jennifer Jones - September 2026.xlsx               │
│ ...                                                │
│ September 2026 Sales Rep Reports.zip               │
└────────────────────────────────────────────────────┘
```

The important architectural boundaries remain:

```text
Data Library stores durable business data.

RunStore stores execution state.

Parser converts files to DataFrames.

Actions perform deterministic business transformations.

Artifact renderers turn verified results into user-facing files.

The frontend orchestrates the workflow but does not calculate business results.
```

Preserve these boundaries unless later evidence justifies changing them.

```

```
