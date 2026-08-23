"""The contract every Action implements (build plan Phase 2.3, section 24).

An Action is a reusable, deterministic data-processing recipe. It declares what
it needs and what it produces, and it transforms dataframes. Nothing else.

The runner — not the Action — owns the generic mechanics: creating the Run
directory, preserving the upload, parsing files, checking required columns,
writing Parquet, generating CSV/XLSX exports and writing the manifest. Keeping
that split intact is what makes a new Action a single new module.

Actions are ordinary imported Python. There is no plugin loader and nothing is
ever executed from disk at runtime.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

import polars as pl

from app.models.schemas import (
    ActionDefinition,
    ActionInput,
    ActionOutput,
    ValidationIssue,
)


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