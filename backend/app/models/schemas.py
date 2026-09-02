"""Structured schemas for everything the backend exposes or persists.

These Pydantic models are the contract shared by the Action registry, the HTTP
API and the on-disk Run manifest (build plan Phase 2.2, sections 21 and 23).

Action *execution* is deliberately not modelled here. Dataframes and the
result of a transformation stay in plain Python (`app.actions.base`), because
Pydantic adds nothing to values that are never serialised directly.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Version of the manifest format. Bump when the shape changes incompatibly,
#: so a manifest produced by an older build remains identifiable.
#:
#: Version 2 (Phase 6E): a manifest now carries the result metadata build plan
#: 6E.1 requires and the assembled audit summary of 6E.5. `OutputMetadata`
#: gained `column_schema`, `input_row_count`, `columns_added` and
#: `columns_removed`, and `RunManifest` gained `audit`; all four are required,
#: so a version 1 manifest does not validate against this model. The change is
#: deliberate and is recorded in `docs/implementation-status.md`.
MANIFEST_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Action metadata
#
# An Action declares these once, as module-level constants, so they are frozen.
# ---------------------------------------------------------------------------


class ActionInput(BaseModel):
    """One named input slot an Action requires (build plan section 9.2).

    Actions request named datasets rather than "some files", so the frontend
    can build one upload control per slot without Action-specific code.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Slot ID; also the multipart form field name.")
    label: str = Field(description="Human-readable slot name shown in the UI.")
    description: str | None = Field(
        default=None, description="Optional guidance about the expected data."
    )
    required: bool = Field(
        default=True, description="Whether a Run may proceed without this slot."
    )
    accepted_extensions: tuple[str, ...] = Field(
        description="Lowercase extensions including the leading dot, e.g. '.csv'."
    )
    required_columns: tuple[str, ...] = Field(
        default=(),
        description=(
            "Columns that must be present, compared exactly. An empty tuple "
            "means the Action imposes no schema."
        ),
    )


class ActionOutput(BaseModel):
    """One dataset an Action produces (build plan section 9.4)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable output ID, unique within the Action.")
    label: str = Field(description="Human-readable output name shown in the UI.")
    description: str | None = Field(
        default=None, description="Optional explanation of what the output contains."
    )
    formats: tuple[str, ...] = Field(
        default=("csv", "xlsx"),
        description="Export formats the runner generates for this output.",
    )


class ActionDefinition(BaseModel):
    """The complete public description of an Action.

    This is what `GET /api/actions` returns and the only thing the frontend
    needs in order to render an Action (build plan sections 21 and 32).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    version: str
    name: str
    description: str
    inputs: tuple[ActionInput, ...]
    outputs: tuple[ActionOutput, ...]


class ActionListResponse(BaseModel):
    """Response body of `GET /api/actions`."""

    actions: tuple[ActionDefinition, ...]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    """One structured validation problem (build plan section 22).

    Carries a machine-readable `code` alongside the human message so the UI can
    present the failure without parsing prose.
    """

    code: str = Field(description="Stable identifier, e.g. 'MISSING_COLUMNS'.")
    message: str = Field(description="Plain-language explanation for the user.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured specifics, e.g. {'missing_columns': [...]}.",
    )
    slot_id: str | None = Field(
        default=None, description="Input slot the issue applies to, when relevant."
    )


class ValidationSummary(BaseModel):
    """Outcome of validating a Run's inputs.

    `passed` is false only when `errors` is non-empty; warnings never fail a
    Run, so the absence of an error is never rendered as a warning
    (build plan section 6.2).
    """

    passed: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    """Lifecycle of a Run (build plan section 3.11)."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ColumnKind(str, Enum):
    """How a column's values should be presented (build plan 6E.4).

    Deliberately coarse and closed: a client needs to know whether to
    right-align a value, not which width of integer it is. `OTHER` covers
    everything a simple table renders as-is, so a new Polars type can never
    make this set incomplete.
    """

    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"
    TEMPORAL = "temporal"
    OTHER = "other"


class ActionReference(BaseModel):
    """Identity of the Action a Run executed, captured at execution time.

    Recorded separately from the registry so a manifest stays meaningful after
    the Action is revised.
    """

    id: str
    version: str
    name: str


class InputMetadata(BaseModel):
    """What was uploaded into one input slot, and how it parsed.

    The original filename is metadata only; `stored_filename` is the generated
    name the input is known by, derived from its extension alone (build plan
    sections 16 and 3.2). Since Phase 6C uploads are held in memory and no file
    is written under that name — it records that the client's filename never
    became a name the application used, which is the rule section 16 states.
    """

    slot_id: str
    original_filename: str
    stored_filename: str
    file_size_bytes: int
    extension: str
    parser_engine: str | None = Field(
        default=None,
        description=(
            "Engine that read the file. Recorded explicitly so a compatibility "
            "fallback is never silent (build plan section 6.2)."
        ),
    )
    worksheet: str | None = Field(
        default=None,
        description="Worksheet read from an XLSX input; null for CSV.",
    )
    row_count: int
    column_count: int
    columns: tuple[str, ...]


class ColumnSchema(BaseModel):
    """One column of a result table, described so a client can render it.

    `dtype` is the Polars type name exactly as the engine reports it, e.g.
    ``Int64`` or ``String``; it is evidence of what the data actually is and is
    never adjusted to look tidier. `kind` is the coarse category a table needs
    in order to present a value correctly — numbers right-aligned, text left —
    so the UI branches on a small closed set rather than on every Polars type
    (build plan 6E.4).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    dtype: str = Field(description="Polars type name, e.g. 'Int64'.")
    kind: ColumnKind = Field(
        description="Coarse category for rendering; see ColumnKind."
    )


class OutputMetadata(BaseModel):
    """A dataset a Run produced, described without any of its rows.

    Phase 6E adds the result metadata build plan 6E.1 asks for. The counts are
    measured, never inferred: `input_row_count` is what the Run actually
    received, `row_count` is what this table actually holds, and the two column
    lists are set differences over the real column names rather than a guess at
    what the Action meant to do.
    """

    id: str
    label: str
    row_count: int
    column_count: int
    columns: tuple[str, ...]
    formats: tuple[str, ...]
    column_schema: tuple[ColumnSchema, ...] = Field(
        description=(
            "This table's columns with their types, in column order. Carries "
            "the same names as `columns`, which stays a plain list because "
            "every existing client reads it (build plan 6E.1, 6E.4)."
        )
    )
    input_row_count: int = Field(
        description=(
            "Rows the Run received across all of its inputs, so a reader can "
            "see this result against what went in."
        )
    )
    columns_added: tuple[str, ...] = Field(
        description=(
            "Columns in this result that appeared in no input — the columns "
            "the Action created."
        )
    )
    columns_removed: tuple[str, ...] = Field(
        description=(
            "Columns that appeared in an input and are not in this result."
        )
    )


class RunError(BaseModel):
    """Why a Run failed, in the same shape as an API error (section 22).

    Never contains a traceback: those are logged locally, not returned.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit summary (build plan 6E.5)
#
# The manifest already records every fact about a Run. The audit *assembles*
# those facts into the short explanation of what happened that a user reads
# after a Run, in one place and in audit vocabulary.
#
# It is metadata about the Run and never touches the Run's data: no audit value
# is ever written into a result table (build plan 6E.6).
# ---------------------------------------------------------------------------


class AuditInput(BaseModel):
    """One input the Run used, as the audit reports it."""

    slot_id: str
    original_filename: str
    row_count: int = Field(description="Rows this input contributed.")
    column_count: int


class AuditResult(BaseModel):
    """One result table the Run produced, as the audit reports it."""

    output_id: str
    label: str
    row_count: int
    column_count: int


class RunAudit(BaseModel):
    """What happened during one Run (build plan 6E.5).

    Every field is measured or reported, never inferred. In particular
    `rows_affected` is the Action's own count of the rows it changed and is
    ``null`` unless the Action states one: a difference between two row counts
    is a difference between two row counts, and calling it "rows affected"
    would be a guess about what the Action did (build plan section 3.3).
    """

    action: ActionReference
    status: RunStatus
    inputs: tuple[AuditInput, ...] = ()
    rows_received: int = Field(
        default=0, description="Rows received across every input."
    )
    rows_returned: int | None = Field(
        default=None,
        description="Rows in the primary result, or null if there is none.",
    )
    rows_affected: int | None = Field(
        default=None,
        description=(
            "Rows the Action reports it changed. Null when the Action does "
            "not report one."
        ),
    )
    results: tuple[AuditResult, ...] = Field(
        default=(), description="The result tables this Run makes available."
    )
    primary_result_id: str | None = Field(
        default=None, description="Output ID of the primary result table."
    )
    warnings: tuple[ValidationIssue, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="The Action's own reported counts."
    )
    duration_ms: int | None = None


class RunManifest(BaseModel):
    """The audit record for one Run, rendered from the Run Store (6B).

    Must be sufficient on its own to determine what ran, against what input, at
    which Action version, whether validation passed, what came out and how long
    it took (build plan sections 9.5 and 23). It never contains dataframe rows.
    """

    schema_version: int = MANIFEST_SCHEMA_VERSION
    run_id: str
    status: RunStatus
    action: ActionReference
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    inputs: tuple[InputMetadata, ...] = ()
    validation: ValidationSummary
    outputs: tuple[OutputMetadata, ...] = ()
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Action-reported counts, e.g. {'duplicates_removed': 238}.",
    )
    error: RunError | None = None
    audit: RunAudit = Field(
        description=(
            "The assembled explanation of what happened (build plan 6E.5). "
            "Derived from the fields above rather than recorded separately, so "
            "it can never disagree with them."
        )
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


class PreviewResponse(BaseModel):
    """One page of an output dataset (build plan sections 21 and 31).

    Rows are positional lists aligned to `columns` rather than per-row objects:
    the whole dataset is never serialised, and repeating every column name on
    every row would be pure overhead.
    """

    run_id: str
    output_id: str
    columns: tuple[str, ...]
    rows: list[list[Any]]
    offset: int
    limit: int
    total_rows: int
    column_schema: tuple[ColumnSchema, ...] = Field(
        description=(
            "The columns with their types, in column order, so the client can "
            "render each value correctly (build plan 6E.4)."
        )
    )