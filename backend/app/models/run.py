"""The logical ForgeXL Run (build plan 6B.1).

A Run is *what happened*, not *where it was written*. This module models it as
a value: an ID, the Action that executed, a lifecycle status, timestamps, the
metadata of its inputs and outputs, its validation outcome, its metrics and its
error. Nothing here names a directory, and no field holds a filesystem path.

Two consequences follow, and both are the point of Phase 6B:

* Where a Run's runtime state lives — memory today, something else later — is
  decided by :mod:`app.services.run_store`, not by this model.
* The public :class:`~app.models.schemas.RunManifest` is *derived* from a Run
  through :meth:`Run.to_manifest`, so the API contract frozen in Phase 6A is
  produced from runtime state rather than being the runtime state.

Deliberately a frozen dataclass rather than a Pydantic model, for the same
reason :class:`~app.actions.base.ActionResult` is: this is runtime state, not
API-facing data. Pydantic earns its place at the boundary, which is exactly
where :meth:`Run.to_manifest` hands over. `RunManifest` remains the serialised
shape and is unchanged by this phase.

Since Phase 6D a Run also carries what it produced. :class:`RunResult` holds
the Action's result tables as Polars DataFrames, in memory, for as long as the
Run Store keeps the Run: nothing is written to the filesystem, and forgetting
the Run releases the frames (build plan 6D.5, 6D.7 and 6D.8). This is why the
model is a dataclass and not Pydantic — a DataFrame is runtime state, not
API-facing data, and :meth:`Run.to_manifest` deliberately does not carry it.

Run identity lives here too. :func:`new_run_id` and :func:`parse_run_id` keep
the Phase 3 convention exactly — ``str(uuid.uuid4())``, validated as the
canonical string form of a UUID (build plan 6B.7).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

import polars as pl

from app.errors import UnknownRunError
from app.models.schemas import (
    ActionReference,
    InputMetadata,
    OutputMetadata,
    RunError,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)


def new_run_id() -> str:
    """Generate the UUID identifying one Run (build plan section 9.3)."""
    return str(uuid.uuid4())


def parse_run_id(raw: str) -> str:
    """Validate a client-supplied Run ID, returning its canonical form.

    Accepts only the string form of a UUID. Anything else — a traversal
    attempt, a truncated ID, a name with separators — raises
    :class:`~app.errors.UnknownRunError` rather than reaching a store or a
    filesystem.
    """
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise UnknownRunError(
            "That is not a valid Run ID.", details={"run_id": raw}
        ) from None
    if str(parsed) != raw.lower():
        raise UnknownRunError("That is not a valid Run ID.", details={"run_id": raw})
    return str(parsed)


def now() -> datetime:
    """The timestamp a Run records, always timezone-aware UTC."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RunResult:
    """The tables one Action produced, held in memory (build plan 6D.5).

    An Action declares one or more outputs and returns a frame for each. Most
    Actions produce exactly one, so the common case must stay simple: `primary`
    is the frame for the Action's first declared output, and `secondary` is
    whatever else it produced, in declaration order. Nothing here requires an
    Action to produce more than one table.

    The frames are the Action's own result objects, unwritten and unconverted:
    the run keeps them as DataFrames rather than as intermediary spreadsheet
    files (build plan 6D.7). They are released when the Run is forgotten.

    `tables` is exposed read-only so a caller cannot add or drop a result table
    behind the Run's back — the same rule the frozen Run itself follows.
    """

    #: Result frames keyed by the Action's declared output ID, in declaration
    #: order. Read-only.
    tables: Mapping[str, pl.DataFrame]

    #: The Action's first declared output — the table a caller means when it
    #: does not name one.
    primary_output_id: str

    def __post_init__(self) -> None:
        if self.primary_output_id not in self.tables:
            raise ValueError(
                f"Primary output {self.primary_output_id!r} is not among the "
                f"result tables {sorted(self.tables)}."
            )
        object.__setattr__(self, "tables", MappingProxyType(dict(self.tables)))

    @classmethod
    def of(cls, tables: Mapping[str, pl.DataFrame]) -> RunResult:
        """Build a result whose primary table is the first one given.

        The runner builds `tables` by walking the Action's declared outputs, so
        "first given" is "first declared" — the Action decides which table is
        primary, by declaring it first.
        """
        if not tables:
            raise ValueError("A result must contain at least one table.")
        return cls(tables=tables, primary_output_id=next(iter(tables)))

    @property
    def primary(self) -> pl.DataFrame:
        """The Action's primary result table."""
        return self.tables[self.primary_output_id]

    @property
    def secondary(self) -> Mapping[str, pl.DataFrame]:
        """Any further result tables, keyed by output ID. Usually empty."""
        return MappingProxyType(
            {
                output_id: frame
                for output_id, frame in self.tables.items()
                if output_id != self.primary_output_id
            }
        )

    def table(self, output_id: str) -> pl.DataFrame | None:
        """Return one result table by output ID, or None if it has none."""
        return self.tables.get(output_id)


@dataclass(frozen=True)
class Run:
    """One execution of one Action, as runtime state.

    Frozen: a stage never mutates a Run in place. It derives the next state
    with :meth:`with_changes` and hands that to the Run Store, so a stored Run
    can never be edited behind the store's back.
    """

    run_id: str
    action: ActionReference
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    inputs: tuple[InputMetadata, ...] = ()
    validation: ValidationSummary = field(
        default_factory=lambda: ValidationSummary(passed=True)
    )
    outputs: tuple[OutputMetadata, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    error: RunError | None = None

    #: The tables this Run produced, kept in memory (build plan 6D.5/6D.7).
    #: None until the Action has succeeded, and None on every failed Run, so a
    #: Run never carries a partially valid result (build plan 6D.8). It is
    #: deliberately absent from `to_manifest`: the manifest describes results,
    #: it never carries their rows (build plan section 23).
    result: RunResult | None = None

    @classmethod
    def create(
        cls,
        action: ActionReference,
        *,
        run_id: str | None = None,
        created_at: datetime | None = None,
        warnings: tuple[ValidationIssue, ...] = (),
    ) -> Run:
        """Build the initial ``running`` state for a new Run.

        A Run exists from the moment it starts, so its first recorded state is
        ``running`` rather than a separate pre-created state: an interrupted
        Run is then visibly a Run that never finished (build plan 3.9).
        """
        started = created_at or now()
        return cls(
            run_id=run_id or new_run_id(),
            action=action,
            status=RunStatus.RUNNING,
            created_at=started,
            updated_at=started,
            started_at=started,
            validation=ValidationSummary(passed=True, warnings=warnings),
        )

    def with_changes(self, **changes: Any) -> Run:
        """Return a copy carrying `changes`, with `updated_at` refreshed.

        Pass ``updated_at`` explicitly to control it; otherwise every derived
        state stamps the moment it was derived, which is what makes the
        timestamp meaningful.
        """
        changes.setdefault("updated_at", now())
        return replace(self, **changes)

    def to_manifest(self) -> RunManifest:
        """Render this Run as the public manifest (build plan sections 21, 23).

        The manifest is the API's shape, frozen in Phase 6A. `updated_at` is
        runtime bookkeeping and deliberately does not appear in it: adding a
        field would change a frozen contract for no caller's benefit.
        """
        return RunManifest(
            run_id=self.run_id,
            status=self.status,
            action=self.action,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_ms=self.duration_ms,
            inputs=self.inputs,
            validation=self.validation,
            outputs=self.outputs,
            metrics=dict(self.metrics),
            error=self.error,
        )