"""Action 2 — Product Master Builder (build plan section 27).

Proves that an Action can enforce a specific schema and build a purpose-built
output from a wider sales extract.

The six required columns are compared exactly by the runner before this Action
runs, so a file with `Sales Person` where `Salesperson` is required is reported
rather than guessed at. Once here, the Action only selects those six columns,
in the order the build plan fixes, and drops rows whose six values repeat a
combination already seen.

Company data is never altered: producer and selection names keep their accents
and their casing, blank Vintage and Volume stay blank, and no SKU is invented.
Normalising or matching those values is a separate future Action, not this one.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput

ACTION_ID = "product_master_builder"
INPUT_SLOT_ID = "sales_file"
OUTPUT_ID = "product_master"

#: The columns the sales file must contain, and the exact order in which the
#: Product Master presents them (build plan section 27).
PRODUCT_COLUMNS: tuple[str, ...] = (
    "SKU",
    "Vintage",
    "Supplier",
    "Producer",
    "Selection",
    "Volume",
)


class ProductMasterBuilderAction(Action):
    """Build a unique product master from a sales extract."""

    id = ACTION_ID
    version = "1.0.0"
    name = "Product Master Builder"
    description = (
        "Build a product master from a sales file by keeping only the SKU, "
        "Vintage, Supplier, Producer, Selection and Volume columns and "
        "removing repeated product combinations. Values are copied exactly as "
        "uploaded: names keep their accents, and blanks stay blank."
    )
    inputs = (
        ActionInput(
            id=INPUT_SLOT_ID,
            label="Sales File",
            description=(
                "A CSV or XLSX sales extract containing the columns "
                f"{', '.join(PRODUCT_COLUMNS)}. Column names must match "
                "exactly. Any other columns are ignored."
            ),
            required=True,
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=PRODUCT_COLUMNS,
        ),
    )
    outputs = (
        ActionOutput(
            id=OUTPUT_ID,
            label="Product Master",
            description=(
                "One row per distinct combination of SKU, Vintage, Supplier, "
                "Producer, Selection and Volume, in the order each "
                "combination first appeared."
            ),
        ),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs[INPUT_SLOT_ID]

        # The runner has already confirmed every required column is present,
        # so this selection cannot silently drop one. It also fixes the output
        # column order regardless of the order they appeared in the upload.
        selected = source.select(PRODUCT_COLUMNS)

        # Exact duplicate combinations across the six columns only; the first
        # occurrence of each is kept, in its original position.
        product_master = selected.unique(keep="first", maintain_order=True)

        input_rows = source.height
        output_rows = product_master.height

        return ActionResult(
            outputs={OUTPUT_ID: product_master},
            metrics={
                "input_rows": input_rows,
                "output_rows": output_rows,
                "duplicate_product_rows_removed": input_rows - output_rows,
            },
        )