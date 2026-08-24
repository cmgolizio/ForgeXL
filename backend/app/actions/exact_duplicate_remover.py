"""Action 1 — Exact Duplicate Remover (build plan section 26).

Proves that the system can accept a generic dataset and deterministically
transform it without requiring any domain-specific columns.

Only rows that are byte-for-byte identical across *every* column are removed,
and the first occurrence of each is kept. Nothing is trimmed, re-cased,
normalised or compared fuzzily: two rows that merely look similar are two rows.
Two rows that are blank in the same places are exact duplicates, because a null
is a value like any other here.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput

ACTION_ID = "exact_duplicate_remover"
INPUT_SLOT_ID = "source_file"
OUTPUT_ID = "deduplicated_data"


class ExactDuplicateRemoverAction(Action):
    """Remove rows duplicated exactly across every column."""

    id = ACTION_ID
    version = "1.0.0"
    name = "Exact Duplicate Remover"
    description = (
        "Remove rows that are exact duplicates across every column, keeping "
        "the first occurrence of each. Values are compared exactly as they "
        "were uploaded: nothing is trimmed, re-cased or normalised, and rows "
        "that are merely similar are never combined."
    )
    inputs = (
        ActionInput(
            id=INPUT_SLOT_ID,
            label="Source File",
            description=(
                "Any CSV or XLSX file. This Action imposes no schema, so no "
                "particular columns are required."
            ),
            required=True,
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=(),
        ),
    )
    outputs = (
        ActionOutput(
            id=OUTPUT_ID,
            label="Deduplicated Data",
            description=(
                "The uploaded dataset with exact duplicate rows removed. "
                "Column order, and the order of the rows that were kept, are "
                "unchanged."
            ),
        ),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs[INPUT_SLOT_ID]

        # `keep="first"` retains the earliest occurrence of each distinct row
        # and `maintain_order=True` leaves those rows in their original
        # positions — build plan section 26 steps 4-6. `unique()` with no
        # subset compares every column and returns them in their original
        # order, so column order is preserved too (step 5).
        deduplicated = source.unique(keep="first", maintain_order=True)

        input_rows = source.height
        output_rows = deduplicated.height

        return ActionResult(
            outputs={OUTPUT_ID: deduplicated},
            metrics={
                "input_rows": input_rows,
                "output_rows": output_rows,
                "duplicates_removed": input_rows - output_rows,
            },
        )