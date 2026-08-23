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

#: Version of the on-disk manifest format. Bump when the shape changes
#: incompatibly so older Run directories remain identifiable.
MANIFEST_SCHEMA_VERSION = 1


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
    name actually used on disk (build plan sections 16 and 3.2).
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


class OutputMetadata(BaseModel):
    """A dataset a Run produced, described without any of its rows."""

    id: str
    label: str
    row_count: int
    column_count: int
    columns: tuple[str, ...]
    formats: tuple[str, ...]


class RunError(BaseModel):
    """Why a Run failed, in the same shape as an API error (section 22).

    Never contains a traceback: those are logged locally, not returned.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RunManifest(BaseModel):
    """The audit record written to `data/runs/<run-id>/manifest.json`.

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