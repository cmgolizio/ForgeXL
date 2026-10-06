"""CSV actions transform already prepared string frames and validated options."""
from collections.abc import Mapping
from typing import Any
import polars as pl

from app.actions.base import Action, ActionResult
from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.models.csv_tools import CSVOptions
from app.models.schemas import ActionInput, ActionOutput
from app.services.csv_filters import apply_filters


class FilterCSVAction(Action):
    id = "filter_csv"
    version = "1.0.0"
    name = "Filter a CSV"
    description = "Keep rows matching your filters. Repeated rows stay unless you choose duplicate removal."
    workflow_path = "/csv-tools?action=filter_csv"
    accepts_options = True
    inputs = (ActionInput(id="filter_source", label="Source CSV", accepted_extensions=(".csv",)),)
    outputs = (ActionOutput(id="csv_result", label="CSV result", formats=("csv",)),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        raise ValueError("Use the CSV workflow with inspected files and validated options.")

    def run_configured(self, inputs: Mapping[str, pl.DataFrame], options: Mapping[str, Any]) -> ActionResult:
        config_options = CSVOptions.model_validate(dict(options))
        received = inputs["csv_data"]
        frame = received
        removed = 0
        if config_options.remove_duplicates:
            # Reuse the existing deterministic all-column, keep-first behavior.
            cleaned = ExactDuplicateRemoverAction().run({"source_file": frame})
            frame = cleaned.outputs["deduplicated_data"]
            removed = received.height - frame.height
        filtered = apply_filters(frame, config_options)
        excluded = frame.height - filtered.height
        return ActionResult(outputs={"csv_result": filtered}, metrics={
            "input_rows": received.height, "duplicates_removed": removed,
            "rows_excluded": excluded, "output_rows": filtered.height,
        }, rows_affected=removed + excluded)


class CombineCSVAction(FilterCSVAction):
    id = "combine_csv"
    name = "Combine CSV files"
    description = "Append CSVs in your chosen order, remove exact duplicate rows, then optionally filter."
    workflow_path = "/csv-tools?action=combine_csv"
    inputs = (
        ActionInput(id="combine_source", label="Source CSV", accepted_extensions=(".csv",)),
        ActionInput(id="combine_additional", label="Additional CSVs", accepted_extensions=(".csv",), max_files=19),
    )
