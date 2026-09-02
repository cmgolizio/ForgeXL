"""Runner pipeline tests (build plan 3.7-3.11, 6B, 6C and 6D).

These exercise the generic machinery directly, without HTTP: the runner is
what every future Action relies on, so it is tested on its own terms.

Three Phase 6 changes shape this module:

* Run state lives in the Run Store (6B), so an outcome is checked against
  `run_store.get_run(...)` rather than against a `manifest.json` on disk.
* Uploads are read into memory (6C), so a Run writes no input file and the
  assertions that used to read one now assert that nothing was written.
* Results are DataFrames the Run holds (6D), so the assertions that used to
  read `working/<id>.parquet` read the retained frame instead — and the
  pipeline is exercised with no runs directory in existence at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

from app import config
from app.actions.base import Action, ActionResult
from app.errors import (
    ActionExecutionError,
    RunValidationError,
    UploadTooLargeError,
)
from app.models.schemas import (
    ActionInput,
    ActionOutput,
    RunStatus,
    ValidationIssue,
)
from app.models.run import RunResult
from app.models.run import now as _now
from app.services import run_store
from app.services.runner import _finalize_failed, delete_run, execute_run

from tests.helpers import csv_bytes, make_action, upload, xlsx_bytes

SALES_HEADER = ["SKU", "Vintage", "Supplier"]
SALES_ROWS = [["A1", 2019, "Acme"], ["A2", 2020, "Acme"]]


def _sales_csv() -> bytes:
    return csv_bytes(SALES_HEADER, SALES_ROWS)


def _only_run():
    """The single Run this test recorded, read back from the Run Store."""
    (run,) = run_store.list_runs()
    return run


# ---------------------------------------------------------------------------
# Test Actions used by several cases
# ---------------------------------------------------------------------------


class _TwoInputs(Action):
    id = "two_inputs"
    version = "1.0.0"
    name = "Two Inputs"
    description = "Takes two datasets."
    inputs = (
        ActionInput(
            id="current_sales", label="Current Sales", accepted_extensions=(".csv",)
        ),
        ActionInput(
            id="historical_sales",
            label="Historical Sales",
            accepted_extensions=(".csv", ".xlsx"),
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


# ---------------------------------------------------------------------------
# Happy path — the pipeline and what it records (3.8, section 11, 6B)
# ---------------------------------------------------------------------------


def test_a_successful_run_is_recorded_in_the_run_store(runs_dir: Path) -> None:
    action = make_action("passthrough")

    outcome = execute_run(action, {"source_file": upload("sales.csv", _sales_csv())})

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    stored = run_store.get_run(outcome.run.run_id)
    assert stored.status is RunStatus.SUCCEEDED
    assert stored.to_manifest().model_dump(mode="json") == outcome.manifest.model_dump(
        mode="json"
    )


def test_a_run_is_recorded_before_it_finishes(runs_dir: Path) -> None:
    """A Run exists from the moment it starts, so an interruption is visible."""

    class _Watching(Action):
        id = "watching"
        version = "1.0.0"
        name = "Watching"
        description = "Observes its own Run mid-flight."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)
        seen: list[RunStatus] = []

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            self.seen.append(_only_run().status)
            return ActionResult(outputs={"result": inputs["source_file"]})

    action = _Watching()
    execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert action.seen == [RunStatus.RUNNING]


def test_the_manifest_records_the_run_end_to_end(runs_dir: Path) -> None:
    outcome = execute_run(
        make_action("passthrough", version="2.1.0"),
        {"source_file": upload("Q3 sales.csv", _sales_csv())},
    )
    manifest = outcome.manifest

    assert manifest.run_id == outcome.run.run_id
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
    assert recorded_input.file_size_bytes == len(_sales_csv())
    assert recorded_input.row_count == 2
    assert recorded_input.columns == ("SKU", "Vintage", "Supplier")

    (recorded_output,) = manifest.outputs
    assert recorded_output.id == "result"
    assert recorded_output.row_count == 2
    assert recorded_output.formats == ("csv", "xlsx")


def test_the_manifest_never_contains_dataframe_rows(runs_dir: Path) -> None:
    """Build plan section 23: no actual rows belong in the manifest."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    text = outcome.manifest.model_dump_json()
    assert "Acme" not in text


def test_the_manifest_exposes_no_filesystem_paths(runs_dir: Path) -> None:
    """Build plan section 11: the browser gets logical IDs, never local paths."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    text = outcome.manifest.model_dump_json()
    assert str(runs_dir) not in text
    assert "/tmp/" not in text
    assert "data/runs" not in text


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


def test_an_action_with_several_input_slots_receives_each_one(runs_dir: Path) -> None:
    """The pipeline is generic: multi-input Actions need no special handling."""
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


def test_each_slot_keeps_its_own_data(runs_dir: Path) -> None:
    """Build plan 6C.1: an upload stays bound to the slot it was sent for."""

    class _Recording(Action):
        id = "recording"
        version = "1.0.0"
        name = "Recording"
        description = "Records which frame arrived in which slot."
        inputs = _TwoInputs.inputs
        outputs = (ActionOutput(id="result", label="Result"),)
        seen: dict[str, list[str]] = {}

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            self.seen = {
                slot: frame["SKU"].to_list() for slot, frame in inputs.items()
            }
            return ActionResult(outputs={"result": inputs["current_sales"]})

    action = _Recording()
    execute_run(
        action,
        {
            "current_sales": upload(
                "a.csv", csv_bytes(SALES_HEADER, [["FIRST", 2019, "Acme"]])
            ),
            "historical_sales": upload(
                "b.xlsx",
                xlsx_bytes({"S": [SALES_HEADER, ["SECOND", 2020, "Beta"]]}),
            ),
        },
    )

    assert action.seen == {"current_sales": ["FIRST"], "historical_sales": ["SECOND"]}


# ---------------------------------------------------------------------------
# 6C.3 Uploads are read into memory and never written
# ---------------------------------------------------------------------------


def test_a_successful_run_writes_no_uploaded_file(runs_dir: Path) -> None:
    execute_run(
        make_action("passthrough"), {"source_file": upload("sales.csv", _sales_csv())}
    )

    written = [path for path in runs_dir.rglob("*") if path.is_file()]
    assert not any(path.name.startswith("source.") for path in written)
    assert not any(part == "inputs" for path in written for part in path.parts)


def test_an_unsupported_upload_is_never_read_or_written(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError):
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("data.json", b'{"a": 1}')},
        )

    assert [path for path in runs_dir.rglob("*") if path.is_file()] == []


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


def test_a_zero_byte_upload_fails_as_an_empty_file(runs_dir: Path) -> None:
    """Build plan 6C.4/6C.9: empty is its own case, not a parse failure."""
    with pytest.raises(RunValidationError) as raised:
        execute_run(make_action("passthrough"), {"source_file": upload("s.csv", b"")})

    assert raised.value.code == "EMPTY_FILE"


def test_an_empty_file_and_a_header_only_file_report_different_codes(
    runs_dir: Path,
) -> None:
    with pytest.raises(RunValidationError) as empty:
        execute_run(make_action("passthrough"), {"source_file": upload("s.csv", b"")})
    with pytest.raises(RunValidationError) as header_only:
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("s.csv", csv_bytes(SALES_HEADER, []))},
        )

    assert empty.value.code == "EMPTY_FILE"
    assert header_only.value.code == "EMPTY_DATASET"


def test_an_unparseable_file_fails_the_run(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("s.csv", b"a,b\n1,2,3,4\n")},
        )

    assert raised.value.code == "PARSE_ERROR"


def test_several_validation_failures_are_reported_together(runs_dir: Path) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_TwoInputs(), {})

    assert raised.value.code == "VALIDATION_FAILED"
    assert len(raised.value.issues) == 2
    assert len(raised.value.details["issues"]) == 2


def test_problems_in_different_slots_are_collected_in_one_response(
    runs_dir: Path,
) -> None:
    """Build plan 6C.4: one request reports every slot problem at once."""
    with pytest.raises(RunValidationError) as raised:
        execute_run(
            _TwoInputs(),
            {
                "current_sales": upload("a.json", b"{}"),
                "historical_sales": upload("b.csv", b""),
            },
        )

    codes = {issue.code for issue in raised.value.issues}
    assert codes == {"UNSUPPORTED_EXTENSION", "EMPTY_FILE"}


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


def test_an_unexpected_slot_is_reported_even_on_a_failed_run(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(
            action,
            {
                "source_file": upload("a.csv", _sales_csv()),
                "mystery_file": upload("b.csv", _sales_csv()),
            },
        )

    (warning,) = _only_run().validation.warnings
    assert warning.code == "UNEXPECTED_INPUT"


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
    assert _only_run().status is RunStatus.FAILED


# ---------------------------------------------------------------------------
# 3.9 Failed Runs keep their evidence
# ---------------------------------------------------------------------------


def test_a_failed_run_keeps_its_record(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    run = _only_run()
    assert run.status is RunStatus.FAILED
    assert run.validation.passed is False
    assert run.error is not None
    assert run.error.code == "MISSING_COLUMNS"
    assert run.completed_at is not None


def test_a_failed_run_records_every_validation_error(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume", "Producer"))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    (issue,) = _only_run().validation.errors
    assert issue.code == "MISSING_COLUMNS"
    assert issue.slot_id == "source_file"
    assert issue.details["missing_columns"] == ["Volume", "Producer"]


def test_a_failed_run_still_records_what_was_uploaded(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("Q3.csv", _sales_csv())})

    (recorded_input,) = _only_run().inputs
    assert recorded_input.original_filename == "Q3.csv"
    assert recorded_input.stored_filename == "source.csv"
    assert recorded_input.file_size_bytes == len(_sales_csv())


def test_a_failed_run_produces_no_outputs(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert _only_run().outputs == ()


def test_a_file_that_failed_to_parse_is_still_recorded_as_an_input(
    runs_dir: Path,
) -> None:
    """A failed Run should show what was uploaded, not an empty inputs list."""
    with pytest.raises(RunValidationError):
        execute_run(
            make_action("passthrough"),
            {"source_file": upload("broken.csv", b"a,b\n1,2,3,4\n")},
        )

    (recorded_input,) = _only_run().inputs
    assert recorded_input.original_filename == "broken.csv"
    assert recorded_input.row_count == 0
    assert recorded_input.parser_engine is None


def test_an_action_that_raises_is_reported_without_a_traceback(
    runs_dir: Path,
) -> None:
    with pytest.raises(ActionExecutionError) as raised:
        execute_run(_Exploding(), {"source_file": upload("s.csv", _sales_csv())})

    assert raised.value.http_status == 500
    assert "secret internal detail" not in raised.value.message

    run = _only_run()
    assert run.status is RunStatus.FAILED
    assert run.error is not None
    assert "secret internal detail" not in run.error.message


def test_an_action_that_omits_a_declared_output_fails_the_run(
    runs_dir: Path,
) -> None:
    with pytest.raises(ActionExecutionError):
        execute_run(_Forgetful(), {"source_file": upload("s.csv", _sales_csv())})

    assert _only_run().status is RunStatus.FAILED


# ---------------------------------------------------------------------------
# An Action's own validate() hook
# ---------------------------------------------------------------------------


def test_an_action_validate_hook_can_fail_the_run(runs_dir: Path) -> None:
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


def test_the_validate_hook_receives_the_parsed_frames(runs_dir: Path) -> None:
    class _Inspecting(Action):
        id = "inspecting"
        version = "1.0.0"
        name = "Inspecting"
        description = "Looks at what it was given."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)
        seen: list[tuple[str, ...]] = []

        def validate(
            self, inputs: Mapping[str, pl.DataFrame]
        ) -> list[ValidationIssue]:
            self.seen.append(tuple(inputs["source_file"].columns))
            return []

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"result": inputs["source_file"]})

    action = _Inspecting()
    execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert action.seen == [("SKU", "Vintage", "Supplier")]


# ---------------------------------------------------------------------------
# 6B.6 Run deletion releases the Run's state
# ---------------------------------------------------------------------------


def test_deleting_a_run_forgets_it(runs_dir: Path) -> None:
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    assert delete_run(outcome.run.run_id) is True
    assert run_store.list_runs() == []


def test_deleting_an_unknown_run_reports_false(runs_dir: Path) -> None:
    from app.models.run import new_run_id

    assert delete_run(new_run_id()) is False


def test_deleting_one_run_leaves_the_others(runs_dir: Path) -> None:
    kept = execute_run(
        make_action("passthrough"), {"source_file": upload("a.csv", _sales_csv())}
    )
    removed = execute_run(
        make_action("passthrough"), {"source_file": upload("b.csv", _sales_csv())}
    )

    assert delete_run(removed.run.run_id) is True
    assert [run.run_id for run in run_store.list_runs()] == [kept.run.run_id]


# ---------------------------------------------------------------------------
# 6D DataFrame-first execution
# ---------------------------------------------------------------------------


def test_a_successful_run_keeps_its_result_frame(runs_dir: Path) -> None:
    """Build plan 6D.7: the result stays a DataFrame, not an intermediate file."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    result = outcome.result
    assert isinstance(result, RunResult)
    assert result.primary_output_id == "result"
    assert result.primary.columns == SALES_HEADER
    assert result.primary.rows() == [("A1", 2019, "Acme"), ("A2", 2020, "Acme")]


def test_the_retained_frame_is_the_one_the_action_returned(runs_dir: Path) -> None:
    """Nothing is round-tripped through a file, so nothing is re-materialised."""

    class _Identifiable(Action):
        id = "identifiable"
        version = "1.0.0"
        name = "Identifiable"
        description = "Returns a frame the test can identify."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)
        produced: pl.DataFrame | None = None

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            self.produced = inputs["source_file"].head(1)
            return ActionResult(outputs={"result": self.produced})

    action = _Identifiable()
    outcome = execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert outcome.result is not None
    assert outcome.result.primary is action.produced


def test_the_result_travels_with_the_run_in_the_store(runs_dir: Path) -> None:
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    stored = run_store.get_run(outcome.run.run_id)

    assert stored.result is not None
    assert stored.result.primary.rows() == [
        ("A1", 2019, "Acme"),
        ("A2", 2020, "Acme"),
    ]


def test_an_action_with_two_outputs_produces_two_result_tables(
    runs_dir: Path,
) -> None:
    """Build plan 6D.5: an Action may return a primary and secondary results."""

    class _TwoOutputs(Action):
        id = "two_outputs"
        version = "1.0.0"
        name = "Two Outputs"
        description = "Splits its input in two."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (
            ActionOutput(id="kept", label="Kept"),
            ActionOutput(id="rejected", label="Rejected"),
        )

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            frame = inputs["source_file"]
            return ActionResult(
                outputs={"kept": frame.head(1), "rejected": frame.tail(1)}
            )

    outcome = execute_run(_TwoOutputs(), {"source_file": upload("s.csv", _sales_csv())})

    assert [output.id for output in outcome.manifest.outputs] == ["kept", "rejected"]
    result = outcome.result
    assert result is not None
    # The first declared output is the primary one.
    assert result.primary_output_id == "kept"
    assert result.primary.rows() == [("A1", 2019, "Acme")]
    assert list(result.secondary) == ["rejected"]
    assert result.secondary["rejected"].rows() == [("A2", 2020, "Acme")]


def test_each_declared_output_is_described_from_its_own_frame(
    runs_dir: Path,
) -> None:
    class _Uneven(Action):
        id = "uneven"
        version = "1.0.0"
        name = "Uneven"
        description = "Produces two differently shaped outputs."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (
            ActionOutput(id="wide", label="Wide"),
            ActionOutput(id="narrow", label="Narrow"),
        )

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            frame = inputs["source_file"]
            return ActionResult(
                outputs={"wide": frame, "narrow": frame.select("SKU").head(1)}
            )

    outcome = execute_run(_Uneven(), {"source_file": upload("s.csv", _sales_csv())})

    wide, narrow = outcome.manifest.outputs
    assert (wide.row_count, wide.column_count, wide.columns) == (
        2,
        3,
        ("SKU", "Vintage", "Supplier"),
    )
    assert (narrow.row_count, narrow.column_count, narrow.columns) == (1, 1, ("SKU",))


def test_the_pipeline_needs_no_run_directory_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build plan 6D completion criteria, as an assertion.

    The data directory is pointed somewhere that does not exist and the process
    working directory at an empty one: a pipeline that still required
    ``inputs/``, ``working/`` or ``exports/`` could not complete here.
    """
    missing = tmp_path / "definitely-not-created"
    empty = tmp_path / "cwd"
    empty.mkdir()
    monkeypatch.setattr(config, "DATA_DIRECTORY", missing)
    monkeypatch.setattr(config, "RUNS_DIRECTORY", missing / "runs")
    monkeypatch.chdir(empty)

    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result.primary.height == 2
    assert not missing.exists()
    assert list(empty.iterdir()) == []


def test_a_successful_run_writes_nothing_anywhere(runs_dir: Path) -> None:
    execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    assert list(runs_dir.rglob("*")) == []


def test_the_run_outcome_no_longer_carries_a_directory(runs_dir: Path) -> None:
    """A Run has no filesystem location to hand back."""
    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )

    assert not hasattr(outcome, "paths")


# ---------------------------------------------------------------------------
# 6D.8 A failed Run leaves no partially valid result
# ---------------------------------------------------------------------------


def test_a_failed_validation_leaves_no_result(runs_dir: Path) -> None:
    action = make_action("schema_action", required_columns=("Volume",))

    with pytest.raises(RunValidationError):
        execute_run(action, {"source_file": upload("s.csv", _sales_csv())})

    assert _only_run().result is None


def test_an_action_that_raises_leaves_no_result(runs_dir: Path) -> None:
    with pytest.raises(ActionExecutionError):
        execute_run(_Exploding(), {"source_file": upload("s.csv", _sales_csv())})

    assert _only_run().result is None


def test_an_action_that_produces_only_some_of_its_outputs_leaves_no_result(
    runs_dir: Path,
) -> None:
    """The half-finished tables must not survive as a usable result."""

    class _HalfDone(Action):
        id = "half_done"
        version = "1.0.0"
        name = "Half Done"
        description = "Produces one of its two declared outputs."
        inputs = (
            ActionInput(
                id="source_file", label="Source File", accepted_extensions=(".csv",)
            ),
        )
        outputs = (
            ActionOutput(id="first", label="First"),
            ActionOutput(id="second", label="Second"),
        )

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"first": inputs["source_file"]})

    with pytest.raises(ActionExecutionError):
        execute_run(_HalfDone(), {"source_file": upload("s.csv", _sales_csv())})

    failed = _only_run()
    assert failed.status is RunStatus.FAILED
    assert failed.result is None
    assert failed.outputs == ()


def test_finalizing_a_failure_clears_any_result_already_recorded(
    runs_dir: Path,
) -> None:
    """Build plan 6D.8, at the one place the guarantee is made.

    Today the pipeline cannot reach a failure after a result has been recorded,
    so the pipeline-level tests above pass whether or not the failure finalizer
    clears it. This drives the finalizer with a Run that *does* carry a result,
    which is the state a future reordering of the stages would produce, and
    asserts the finalizer refuses to leave it behind.
    """
    from app.errors import ActionExecutionError as _Failure
    from app.models.run import Run
    from app.models.schemas import (
        ActionReference,
        ColumnKind,
        ColumnSchema,
        OutputMetadata,
    )

    reference = ActionReference(id="passthrough", version="1.0.0", name="Passthrough")
    started = _now()
    recorded = run_store.create_run(
        Run.create(reference, created_at=started)
    ).with_changes(
        outputs=(
            OutputMetadata(
                id="result",
                label="Result",
                row_count=2,
                column_count=1,
                columns=("SKU",),
                formats=("csv", "xlsx"),
                column_schema=(
                    ColumnSchema(name="SKU", dtype="String", kind=ColumnKind.TEXT),
                ),
                input_row_count=2,
                columns_added=(),
                columns_removed=(),
            ),
        ),
        result=RunResult.of({"result": pl.DataFrame({"SKU": ["A1", "A2"]})}),
        rows_affected=0,
    )
    run_store.update_run(recorded)

    finalized = _finalize_failed(recorded, started, _Failure("It broke."))

    assert finalized.status is RunStatus.FAILED
    assert finalized.result is None
    assert finalized.outputs == ()
    # Build plan 6D.8: nothing from the abandoned result survives, including
    # the effect it would have reported.
    assert finalized.rows_affected is None
    assert run_store.get_run(recorded.run_id).result is None


def test_deleting_a_run_releases_its_result_frames(runs_dir: Path) -> None:
    """Build plan 6D.8: abandoned processing must be able to release memory."""
    import gc
    import weakref

    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("s.csv", _sales_csv())}
    )
    assert outcome.result is not None
    watcher = weakref.ref(outcome.result.primary)
    run_id = outcome.run.run_id
    del outcome

    assert watcher() is not None, "the store should still be holding the frame"

    assert delete_run(run_id) is True
    gc.collect()

    assert watcher() is None, "the result frame is still reachable after deletion"