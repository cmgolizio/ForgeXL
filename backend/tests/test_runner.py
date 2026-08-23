"""Runner pipeline tests (build plan 3.7-3.11).

These exercise the generic machinery directly, without HTTP: the runner is
what every future Action relies on, so it is tested on its own terms.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

from app import config
from app.actions.base import Action, ActionResult
from app.errors import RunValidationError, UploadTooLargeError
from app.models.schemas import ActionInput, ActionOutput, RunStatus
from app.services import storage
from app.services.runner import execute_run

from tests.helpers import csv_bytes, make_action, upload, xlsx_bytes

SALES_HEADER = ["SKU", "Vintage", "Supplier"]
SALES_ROWS = [["A1", 2019, "Acme"], ["A2", 2020, "Acme"]]


def _sales_csv() -> bytes:
    return csv_bytes(SALES_HEADER, SALES_ROWS)


# ---------------------------------------------------------------------------
# Happy path — the pipeline and its artifacts (3.8, 3.10, section 11)
# ---------------------------------------------------------------------------


def test_a_successful_run_produces_every_artifact(runs_dir: Path) -> None:
    action = make_action("passthrough")

    outcome = execute_run(action, {"source_file": upload("sales.csv", _sales_csv())})

    paths = outcome.paths
    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert paths.manifest_path.is_file()
    assert (paths.input_directory("source_file") / "source.csv").is_file()
    assert paths.working_artifact("result").is_file()
    assert paths.export_artifact("result", "csv").is_file()
    assert paths.export_artifact("result", "xlsx").is_file()


def test_the_source_upload_is_preserved_byte_for_byte(runs_dir: Path) -> None:
    payload = _sales_csv()

    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("sales.csv", payload)}
    )

    stored = outcome.paths.input_directory("source_file") / "source.csv"
    assert stored.read_bytes() == payload


def test_the_manifest_records_the_run_end_to_end(runs_dir: Path) -> None:
    outcome = execute_run(
        make_action("passthrough", version="2.1.0"),
        {"source_file": upload("Q3 sales.csv", _sales_csv())},
    )
    manifest = outcome.manifest

    assert manifest.run_id == outcome.paths.run_id
    assert manifest.action.id == "passthrough"
    assert manifest.action.version == "2.1.0"
    assert manifest.validation.passed is True
    assert manifest.error is None
    assert manifest.duration_ms is not None and manifest.duration_ms >= 0
    assert manifest.completed_at is not None

    (recorded_input,) = manifest.inputs
    assert recorded_input.original_filename == "Q3 sales.csv"
    assert recorded_input.stored_filename == "source.csv"
    assert recorded_input.parser_engine == "polars-csv"
    assert recorded_input.row_count == 2
    assert recorded_input.columns == ("SKU", "Vintage", "Supplier")

    (recorded_output,) = manifest.outputs
    assert recorded_output.id == "result"
    assert recorded_output.row_count == 2
    assert recorded_output.formats == ("csv", "xlsx")


def test_the_manifest_on_disk_matches_what_was_returned(runs_dir: Path) -> None:
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    reloaded = storage.read_manifest(outcome.paths.run_id)

    assert reloaded.model_dump(mode="json") == outcome.manifest.model_dump(mode="json")


def test_the_manifest_never_contains_dataframe_rows(runs_dir: Path) -> None:
    """Build plan section 23: no actual rows belong in the manifest."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    text = outcome.paths.manifest_path.read_text(encoding="utf-8")
    assert "Acme" not in text


def test_the_manifest_exposes_no_filesystem_paths(runs_dir: Path) -> None:
    """Build plan section 11: the browser gets logical IDs, never local paths."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    text = outcome.paths.manifest_path.read_text(encoding="utf-8")
    assert str(runs_dir) not in text
    assert str(outcome.paths.root) not in text


def test_action_metrics_are_copied_into_the_manifest_verbatim(
    runs_dir: Path,
) -> None:
    class _Counting(Action):
        id = "counting"
        version = "1.0.0"
        name = "Counting"
        description = "Reports its own metrics."
        inputs = (
            ActionInput(
                id="source_file",
                label="Source File",
                accepted_extensions=(".csv",),
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            frame = inputs["source_file"]
            return ActionResult(
                outputs={"result": frame},
                metrics={"input_rows": frame.height, "duplicates_removed": 7},
            )

    outcome = execute_run(_Counting(), {"source_file": upload("s.csv", _sales_csv())})

    assert outcome.manifest.metrics == {"input_rows": 2, "duplicates_removed": 7}


def test_an_xlsx_input_records_its_worksheet_and_engine(runs_dir: Path) -> None:
    payload = xlsx_bytes({"Sales": [SALES_HEADER, *SALES_ROWS]})

    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("book.xlsx", payload)}
    )

    (recorded_input,) = outcome.manifest.inputs
    assert recorded_input.extension == ".xlsx"
    assert recorded_input.worksheet == "Sales"
    assert recorded_input.parser_engine == "fastexcel-calamine"


def test_an_action_with_several_input_slots_stores_each_one(runs_dir: Path) -> None:
    """The pipeline is generic: multi-input Actions need no special handling."""

    class _TwoInputs(Action):
        id = "two_inputs"
        version = "1.0.0"
        name = "Two Inputs"
        description = "Takes two datasets."
        inputs = (
            ActionInput(
                id="current_sales",
                label="Current Sales",
                accepted_extensions=(".csv",),
            ),
            ActionInput(
                id="historical_sales",
                label="Historical Sales",
                accepted_extensions=(".csv",),
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(
                outputs={
                    "result": pl.concat(
                        [inputs["current_sales"], inputs["historical_sales"]]
                    )
                }
            )

    outcome = execute_run(
        _TwoInputs(),
        {
            "current_sales": upload("a.csv", _sales_csv()),
            "historical_sales": upload("b.csv", _sales_csv()),
        },
    )

    assert {item.slot_id for item in outcome.manifest.inputs} == {
        "current_sales",
        "historical_sales",
    }
    assert outcome.manifest.outputs[0].row_count == 4


# ---------------------------------------------------------------------------
# 3.7 Generic validation
# ---------------------------------------------------------------------------


def test_a_missing_required_input_fails_the_run(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(make_action("passthrough"), {})

    assert raised.value.code == "MISSING_INPUT"
    assert raised.value.http_status == 422


def test_an_unsupported_extension_fails_the_run(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("data.json", b'{"a": 1}')},
        )

    assert raised.value.code == "UNSUPPORTED_EXTENSION"


def test_an_unsupported_upload_is_never_stored(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError):
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("data.json", b'{"a": 1}')},
        )

    (run_directory,) = list(runs_dir.iterdir())
    assert list((run_directory / "inputs").rglob("*")) == []


def test_missing_required_columns_fail_the_run(runs_dir: Path) -> None:
    action = make_action(
        "schema_action", required_columns=("SKU", "Supplier", "Volume")
    )

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert raised.value.code == "MISSING_COLUMNS"
    assert raised.value.details["missing_columns"] == ["Volume"]


def test_column_comparison_is_exact(runs_dir: Path) -> None:
    """Build plan 3.7: 'Sales Person' is not 'Salesperson'."""
    action = make_action("schema_action", required_columns=("Salesperson",))
    payload = csv_bytes(["Sales Person"], [["Ann"]])

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {"source_file": upload("s.csv", payload)})

    assert raised.value.details["missing_columns"] == ["Salesperson"]
    assert raised.value.details["found_columns"] == ["Sales Person"]


def test_column_comparison_is_case_sensitive(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("SKU",))
    payload = csv_bytes(["Sku"], [["A1"]])

    with pytest.raises(RunValidationError) as raised:
        execute_run(action, {"source_file": upload("s.csv", payload)})

    assert raised.value.details["missing_columns"] == ["SKU"]


def test_a_dataset_with_no_rows_fails_the_run(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("s.csv", csv_bytes(SALES_HEADER, []))},
        )

    assert raised.value.code == "EMPTY_DATASET"


def test_an_unparseable_file_fails_the_run(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(make_action("passthrough"), {"source_file": upload("s.csv", b"")})

    assert raised.value.code == "PARSE_ERROR"


def test_several_validation_failures_are_reported_together(runs_dir: Path) -> None:
    class _TwoRequired(Action):
        id = "two_required"
        version = "1.0.0"
        name = "Two Required"
        description = "Requires two files."
        inputs = (
            ActionInput(id="first", label="First", accepted_extensions=(".csv",)),
            ActionInput(id="second", label="Second", accepted_extensions=(".csv",)),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"result": inputs["first"]})

    with pytest.raises(RunValidationError) as raised:
        execute_run(_TwoRequired(), {})

    assert raised.value.code == "VALIDATION_FAILED"
    assert len(raised.value.issues) == 2
    assert len(raised.value.details["issues"]) == 2


def test_an_optional_slot_may_be_omitted(runs_dir: Path) -> None:
    class _OptionalSecond(Action):
        id = "optional_second"
        version = "1.0.0"
        name = "Optional Second"
        description = "The second file is optional."
        inputs = (
            ActionInput(id="first", label="First", accepted_extensions=(".csv",)),
            ActionInput(
                id="second",
                label="Second",
                accepted_extensions=(".csv",),
                required=False,
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"result": inputs["first"]})

    outcome = execute_run(_OptionalSecond(), {"first": upload("a.csv", _sales_csv())})

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert [item.slot_id for item in outcome.manifest.inputs] == ["first"]


def test_an_unexpected_slot_warns_without_failing_the_run(runs_dir: Path) -> None:
    """Build plan section 6.2: a warning is not an error."""
    outcome = execute_run(
        make_action("passthrough"),
        {
            "source_file": upload("a.csv", _sales_csv()),
            "mystery_file": upload("b.csv", _sales_csv()),
        },
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.manifest.validation.passed is True
    (warning,) = outcome.manifest.validation.warnings
    assert warning.code == "UNEXPECTED_INPUT"
    assert warning.details["unexpected_slot_ids"] == ["mystery_file"]


# ---------------------------------------------------------------------------
# 3.3 Upload limit, through the pipeline
# ---------------------------------------------------------------------------


def test_an_oversized_upload_fails_the_run(
    runs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(UploadTooLargeError) as raised:
        execute_run(
            make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
        )

    assert raised.value.http_status == 413


# ---------------------------------------------------------------------------
# 3.9 Failed Runs keep their evidence
# ---------------------------------------------------------------------------


def test_a_failed_run_keeps_its_directory_and_manifest(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    (run_directory,) = list(runs_dir.iterdir())
    manifest = storage.read_manifest(run_directory.name)

    assert manifest.status is RunStatus.FAILED
    assert manifest.validation.passed is False
    assert manifest.error is not None
    assert manifest.error.code == "MISSING_COLUMNS"
    assert manifest.completed_at is not None


def test_a_failed_run_records_every_validation_error(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume", "Producer"))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    (run_directory,) = list(runs_dir.iterdir())
    manifest = storage.read_manifest(run_directory.name)

    (issue,) = manifest.validation.errors
    assert issue.code == "MISSING_COLUMNS"
    assert issue.slot_id == "source_file"
    assert issue.details["missing_columns"] == ["Volume", "Producer"]


def test_a_failed_run_still_records_what_was_uploaded(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("Q3.csv", _sales_csv())})

    (run_directory,) = list(runs_dir.iterdir())
    manifest = storage.read_manifest(run_directory.name)

    (recorded_input,) = manifest.inputs
    assert recorded_input.original_filename == "Q3.csv"
    assert (run_directory / "inputs" / "source_file" / "source.csv").is_file()


def test_a_failed_run_produces_no_outputs(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    (run_directory,) = list(runs_dir.iterdir())
    manifest = storage.read_manifest(run_directory.name)

    assert manifest.outputs == ()
    assert list((run_directory / "working").iterdir()) == []
    assert list((run_directory / "exports").iterdir()) == []


def test_an_action_that_raises_is_reported_without_a_traceback(
    runs_dir: Path,
) -> None:
    class _Exploding(Action):
        id = "exploding"
        version = "1.0.0"
        name = "Exploding"
        description = "Always fails."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            raise RuntimeError("secret internal detail")

    from app.errors import ActionExecutionError

    with pytest.raises(ActionExecutionError) as raised:
        execute_run(_Exploding(), {"source_file": upload("s.csv", _sales_csv())})

    assert raised.value.http_status == 500
    assert "secret internal detail" not in raised.value.message

    (run_directory,) = list(runs_dir.iterdir())
    manifest = storage.read_manifest(run_directory.name)
    assert manifest.status is RunStatus.FAILED
    assert manifest.error is not None
    assert "secret internal detail" not in manifest.error.message


def test_an_action_that_omits_a_declared_output_fails_the_run(
    runs_dir: Path,
) -> None:
    class _Forgetful(Action):
        id = "forgetful"
        version = "1.0.0"
        name = "Forgetful"
        description = "Declares an output it does not produce."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={})

    from app.errors import ActionExecutionError

    with pytest.raises(ActionExecutionError):
        execute_run(_Forgetful(), {"source_file": upload("s.csv", _sales_csv())})

    (run_directory,) = list(runs_dir.iterdir())
    assert storage.read_manifest(run_directory.name).status is RunStatus.FAILED


# ---------------------------------------------------------------------------
# An Action's own validate() hook
# ---------------------------------------------------------------------------


def test_an_action_validate_hook_can_fail_the_run(runs_dir: Path) -> None:
    from app.models.schemas import ValidationIssue

    class _Picky(Action):
        id = "picky"
        version = "1.0.0"
        name = "Picky"
        description = "Refuses everything."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def validate(
            self, inputs: Mapping[str, pl.DataFrame]
        ) -> list[ValidationIssue]:
            return [ValidationIssue(code="TOO_PICKY", message="No.")]

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            raise AssertionError("run must not be reached")

    with pytest.raises(RunValidationError) as raised:
        execute_run(_Picky(), {"source_file": upload("s.csv", _sales_csv())})

    assert raised.value.code == "TOO_PICKY"