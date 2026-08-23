"""Temporary placeholder Action (build plan Phase 2.5).

Phase 2 builds the Action contract, the registry and `GET /api/actions`, but
the two real Actions belong to Phase 4. Without a registered Action the
endpoint could only be verified against an empty list, so this module provides
the smallest Action that genuinely exercises the contract end to end.

It is deliberately a real, working, deterministic Action rather than a stub
with a broken `run`: it returns its input unchanged. It is NOT one of the two
proof Actions and carries a 0.x version to say so.

Remove this module in Phase 4, once `exact_duplicate_remover` and
`product_master_builder` are registered.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput

INPUT_SLOT_ID = "source_file"
OUTPUT_ID = "passthrough_data"


class ExamplePassthroughAction(Action):
    """Return the uploaded dataset unchanged.

    Proves the whole path — definition, registry, API, and later the runner —
    without asserting any business logic of its own.
    """

    id = "example_passthrough"
    version = "0.1.0"
    name = "Example Passthrough (Placeholder)"
    description = (
        "Placeholder Action used while the pipeline is built. Returns the "
        "uploaded dataset unchanged, without altering any value."
    )
    inputs = (
        ActionInput(
            id=INPUT_SLOT_ID,
            label="Source File",
            description="Any CSV or XLSX file. No particular columns are required.",
            required=True,
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=(),
        ),
    )
    outputs = (
        ActionOutput(
            id=OUTPUT_ID,
            label="Passthrough Data",
            description="The uploaded dataset, unchanged.",
        ),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs[INPUT_SLOT_ID]
        return ActionResult(
            outputs={OUTPUT_ID: source},
            metrics={"input_rows": source.height, "output_rows": source.height},
        )