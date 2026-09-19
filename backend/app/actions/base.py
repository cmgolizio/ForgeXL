"""The contract every Action implements (build plan Phase 2.3, section 24).

An Action is a reusable, deterministic data-processing recipe. It declares what
it needs and what it produces, and it transforms dataframes. Nothing else.

The runner — not the Action — owns the generic mechanics: reading the upload
into memory, parsing it, checking required columns, recording the Run and its
metrics, and generating CSV/XLSX exports on request. Keeping that split intact
is what makes a new Action a single new module.

(Before Phase 6 the runner also created a Run directory, preserved the upload,
wrote an internal Parquet file and wrote a manifest file. Nothing is written to
disk any more — see docs/architecture.md — but the split itself is unchanged.)

Since Phase 12 an Action may also return *artifacts* — finished files such as
a formatted report workbook (build plan 12A-12B). That is an addition and
nothing more: :attr:`ActionResult.artifacts` defaults to empty, so an Action
that returns only dataframes means exactly what it always meant, and no Action
is required to produce an artifact. Rendering one is still not the Action's own
job to improvise: :mod:`app.services.workbook` owns the spreadsheet engine, and
an Action describes the report it wants rather than driving xlsxwriter itself.

Actions are ordinary imported Python. There is no plugin loader and nothing is
ever executed from disk at runtime.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import polars as pl

from app.models.artifact import Artifact
from app.models.schemas import (
    ActionDefinition,
    ActionInput,
    ActionOutput,
    ValidationIssue,
)

#: Re-exported so one import line carries the whole Action contract:
#: ``from app.actions.base import Action, ActionResult, Artifact``.
__all__ = ["Action", "ActionResult", "Artifact"]


@dataclass(frozen=True)
class ActionResult:
    """What an Action hands back to the runner.

    Plain Python rather than Pydantic: it carries dataframes, which are never
    serialised directly and which Pydantic could not validate anyway.
    """

    #: Output dataframes keyed by output ID. Keys must match the IDs the Action
    #: declares in `outputs`; the runner exports each one.
    outputs: Mapping[str, pl.DataFrame]

    #: Counts worth reporting to the user, e.g. {"duplicates_removed": 238}.
    #: Copied verbatim into the manifest, so the UI never invents a metric.
    metrics: dict[str, Any] = field(default_factory=dict)

    #: How many rows this Action changed, when it can say (build plan 6E.5).
    #: Reported in the Run's audit summary as `rows_affected`.
    #:
    #: Optional on purpose. An Action that removes rows knows exactly how many
    #: it removed; an Action that reshapes data may have no honest single
    #: figure. Leaving this None means "this Action does not state one", and
    #: the audit reports null rather than substituting the difference between
    #: two row counts, which is a different fact (build plan section 3.3).
    rows_affected: int | None = None

    #: Finished files this Action produced, in the order they should be listed
    #: (build plan 12B, 12E). Empty for every Action that returns only tables,
    #: which is what the default means: build plan 12B's "Do not require every
    #: Action to generate artifacts", expressed as a default rather than as a
    #: convention.
    #:
    #: Last in the field order deliberately. Artifacts belong beside `outputs`
    #: by meaning, but inserting a field there would change what a positional
    #: ``ActionResult(frames, metrics)`` constructs, and an addition must not
    #: silently re-point an existing call.
    artifacts: Sequence[Artifact] = ()

    def __post_init__(self) -> None:
        """Freeze the artifact list and refuse a collision inside it.

        Build plan 12E requires artifact IDs and filenames to be
        collision-safe. Two artifacts sharing an ID would leave one of them
        unreachable through the download route; two sharing a filename would
        leave one of them overwriting the other when the ZIP bundle is
        extracted (build plan 12F). Neither is renamed on the Action's behalf —
        this application does not rename things quietly (build plan section
        3.3) — so the collision is reported and the Run fails.

        A :class:`ValueError` rather than a structured error: it is a fault in
        the Action, not in anything the user submitted. The runner converts
        whatever an Action raises into the
        :class:`~app.errors.ActionExecutionError` a client sees, and logs the
        cause locally.
        """
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

        # Filenames are compared case-insensitively because macOS and Windows
        # treat two names differing only in case as one file, so an archive
        # holding both loses one of them on extraction.
        for label, values in (
            ("id", [artifact.id for artifact in self.artifacts]),
            (
                "filename",
                [artifact.filename.casefold() for artifact in self.artifacts],
            ),
        ):
            duplicates = sorted(
                {value for value in values if values.count(value) > 1}
            )
            if duplicates:
                raise ValueError(
                    f"Two artifacts share the same {label}: "
                    f"{', '.join(repr(value) for value in duplicates)}. "
                    "Artifact IDs and filenames must be unique within a Run "
                    "(build plan 12E)."
                )


class Action(abc.ABC):
    """Base class for every Action.

    Subclasses declare their metadata as class attributes and implement
    :meth:`run`::

        class ExampleAction(Action):
            id = "example"
            version = "1.0.0"
            name = "Example"
            description = "..."
            inputs = (ActionInput(id="source_file", ...),)
            outputs = (ActionOutput(id="result", label="Result"),)

            def run(self, inputs):
                return ActionResult(outputs={"result": inputs["source_file"]})

    An Action that also produces finished files returns them beside its
    tables (build plan 12B)::

            def run(self, inputs):
                return ActionResult(
                    outputs={"result": frame},
                    artifacts=(
                        Artifact.workbook(
                            id="summary",
                            label="Summary",
                            filename="Summary.xlsx",
                            payload=render_workbook(sheets),
                        ),
                    ),
                )

    Instances hold no per-Run state: one instance is registered at import time
    and reused for every Run, so :meth:`run` must not mutate ``self``.
    """

    #: Stable identifier, unique across the registry. Also the value the
    #: frontend submits as `action_id`. Never used to build a filesystem path
    #: or a shell command (build plan section 16).
    id: ClassVar[str]

    #: Semantic version of this Action's logic. Recorded in every manifest, so
    #: bump it whenever the transformation changes.
    version: ClassVar[str]

    #: Display name shown in the Action selector.
    name: ClassVar[str]

    #: One or two sentences explaining what the Action does, shown in the UI.
    description: ClassVar[str]

    #: Named input slots, in the order the UI should render them.
    inputs: ClassVar[tuple[ActionInput, ...]]

    #: Datasets this Action produces. An Action may declare more than one.
    outputs: ClassVar[tuple[ActionOutput, ...]]

    def definition(self) -> ActionDefinition:
        """Return this Action's public metadata for `GET /api/actions`."""
        return ActionDefinition(
            id=self.id,
            version=self.version,
            name=self.name,
            description=self.description,
            inputs=self.inputs,
            outputs=self.outputs,
        )

    def validate(self, inputs: Mapping[str, pl.DataFrame]) -> list[ValidationIssue]:
        """Check anything this Action requires beyond the generic rules.

        The runner has already confirmed that required slots are present, that
        extensions are supported, that each file parsed and that the columns in
        `ActionInput.required_columns` exist. Override only for constraints that
        cannot be expressed as required columns.

        Returning a non-empty list fails the Run before :meth:`run` is called.
        """
        return []

    @abc.abstractmethod
    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        """Transform the parsed inputs into this Action's outputs.

        `inputs` is keyed by input slot ID. Implementations must be
        deterministic: the same Action version and the same input data must
        always produce the same logical output (build plan section 3.3).
        """
        raise NotImplementedError