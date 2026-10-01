"""Browser-facing monthly workflow records, separate from ephemeral Runs."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.library import parse_period, parse_version_id
from app.models.schemas import ActionReference, RunError, RunManifest, ValidationIssue

SOURCE_IDS = ("sales_history", "sample_history", "account_assignments")


class SourceReview(BaseModel):
    dataset_id: str
    label: str
    filename: str
    row_count: int
    period: str | None = None
    version_id: str | None = None
    operation: Literal["import", "reuse", "replace"]
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()


class WorkflowCheck(BaseModel):
    label: str
    status: Literal["passed", "warning", "error"]
    detail: str


class CoverageReview(BaseModel):
    dataset_id: str
    window: str
    missing_months: tuple[str, ...]


class CycleReceipt(BaseModel):
    """Immutable source selection. No outputs, workbook bytes or Run state."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    cycle_id: str
    period: str
    created_at: datetime
    action: ActionReference
    versions: dict[str, tuple[str, ...]]
    origin: Literal["monthly_import", "stored_sources"]
    correction_reason: str | None = None

    @model_validator(mode="after")
    def check_references(self) -> CycleReceipt:
        try:
            parse_version_id(self.cycle_id)
            parse_period(self.period)
            if set(self.versions) != set(SOURCE_IDS):
                raise ValueError("A cycle must name all three source datasets.")
            for dataset_id, ids in self.versions.items():
                if not ids or len(set(ids)) != len(ids):
                    raise ValueError("A source selection must be nonempty and unique.")
                if dataset_id == "account_assignments" and len(ids) != 1:
                    raise ValueError("A cycle needs one assignment snapshot.")
                for version_id in ids:
                    parse_version_id(version_id)
        except Exception as error:
            raise ValueError("The cycle contains invalid source references.") from error
        return self

    def selectors(self) -> dict[str, str]:
        return {
            key: ("version:" if key == "account_assignments" else "versions:") + ",".join(ids)
            for key, ids in self.versions.items()
        }


class WorkflowValidation(BaseModel):
    validation_id: str | None = None
    expires_at: datetime | None = None
    period: str
    ready: bool
    sources: tuple[SourceReview, ...]
    reps: tuple[str, ...] = ()
    checks: tuple[WorkflowCheck, ...] = ()
    coverage: tuple[CoverageReview, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()
    action: ActionReference
    source_selection: str
    timings_ms: dict[str, float] = Field(default_factory=dict)


class WorkflowOutcome(BaseModel):
    period: str
    status: Literal["reports_generated", "generation_failed", "commit_failed"]
    sources_committed: bool
    committed_versions: dict[str, str] = Field(default_factory=dict)
    receipt: CycleReceipt | None = None
    manifest: RunManifest | None = None
    error: RunError | None = None
    timings_ms: dict[str, float] = Field(default_factory=dict)


class SavedValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str
    cycle_id: str | None = None


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    validation_id: str
    acknowledge_warnings: bool = False


class HistoryReview(BaseModel):
    validation_id: str | None = None
    expires_at: datetime | None = None
    ready: bool
    dataset_id: str
    filename: str
    row_count: int
    periods: tuple[str, ...]
    operation: Literal["bootstrap", "monthly"]
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()


class HistoryOutcome(BaseModel):
    status: Literal["saved", "save_failed"]
    dataset_id: str
    committed_versions: tuple[str, ...] = ()
    error: RunError | None = None
