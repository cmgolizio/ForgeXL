"""Regression coverage for the ways a Run fails (build plan 6H.7).

Build plan 6H.7 names the list this module works through, over the real HTTP
API and against the synthetic fixtures:

    invalid upload
    missing required columns
    invalid configuration
    Action failure
    unknown Action ID
    unknown run ID
    export before completion

The point of gathering them here is that a failure is a *feature* of ForgeXL,
not an accident: build plan section 3.3 requires that bad data be reported
rather than repaired, so each of these has an expected status code, an
expected error code and an expected message. A change that turned any of them
into a silent success, a 500, or a traceback would be a defect, and this
module is what says so.

ForgeXL has no user-supplied Action configuration — an Action declares its own
inputs and outputs — so "invalid configuration" is read here as the
request-shaped equivalents: a missing or blank `action_id`, an output ID that
Action does not declare, a format that output is not offered in, and paging
parameters outside the documented range.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping

import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import ProductMasterBuilderAction
from app.models.schemas import ActionInput, ActionOutput

from tests.fixtures import spreadsheets as fx
from tests.helpers import upload_file

DEDUPE = ExactDuplicateRemoverAction.id
PRODUCT_MASTER = ProductMasterBuilderAction.id

#: A syntactically valid UUID that names no Run.
ABSENT_RUN_ID = "00000000-0000-4000-8000-000000000000"


class _RaisingAction(Action):
    """An Action that fails while transforming input that validated cleanly.

    The distinction matters: everything else in this module is refused
    *before* an Action runs. This is the case where the data was fine and the
    Action itself broke.
    """

    id = "always_raises"
    version = "1.0.0"
    name = "Always Raises"
    description = "Raises while processing, to prove a crash is contained."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        raise RuntimeError("secret internal detail /home/someone/data.csv")


class _MissingOutputAction(Action):
    """Declares two outputs and produces one.

    A half-finished result must not be served as a whole one.
    """

    id = "forgets_an_output"
    version = "1.0.0"
    name = "Forgets An Output"
    description = "Declares two result tables and returns only the first."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
    )
    outputs = (
        ActionOutput(id="first", label="First"),
        ActionOutput(id="second", label="Second"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"first": inputs["source_file"]})


@pytest.fixture
def failing_client(client, registered_actions):
    registered_actions(
        _RaisingAction(),
        _MissingOutputAction(),
        ExactDuplicateRemoverAction(),
        ProductMasterBuilderAction(),
    )
    return client


def _submit(client, action_id: str, files: dict | None = None, **data):
    return client.post(
        "/api/runs",
        data={"action_id": action_id, **data},
        files=files if files is not None else {},
    )


def _run_ok(client, action_id: str, table: fx.Table, slot: str = "source_file"):
    response = _submit(
        client,
        action_id,
        {slot: upload_file(table.filename(".csv"), table.as_csv())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _error(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) >= {"code", "message"}, body
    return body["error"]


# ---------------------------------------------------------------------------
# Invalid upload
# ---------------------------------------------------------------------------


def test_a_file_that_is_not_a_spreadsheet_is_refused_by_extension(
    failing_client,
) -> None:
    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("notes.txt", b"hello")}
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "UNSUPPORTED_EXTENSION"
    assert ".csv" in error["message"] and ".xlsx" in error["message"]


def test_a_file_with_no_extension_is_refused(failing_client) -> None:
    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("data", b"a,b\n1,2\n")}
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "UNSUPPORTED_EXTENSION"


def test_a_zero_byte_upload_is_refused_as_an_empty_file(failing_client) -> None:
    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("empty.csv", b"")}
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "EMPTY_FILE"


def test_a_completely_empty_csv_is_a_parse_error_not_a_crash(
    failing_client,
) -> None:
    """Distinct from a zero-byte upload: bytes arrived, none of them tabular."""
    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("blank.csv", b"\n")}
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "PARSE_ERROR"


def test_csv_bytes_named_xlsx_are_refused_rather_than_trusted(
    failing_client,
) -> None:
    """Build plan 6C.5: the extension chooses the reader, not the outcome."""
    response = _submit(
        failing_client,
        DEDUPE,
        {"source_file": upload_file("actually.xlsx", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "PARSE_ERROR"
    assert error["details"]["primary_engine"] == "fastexcel-calamine"
    assert error["details"]["fallback_engine"] == "openpyxl"


def test_workbook_bytes_named_csv_are_refused_rather_than_trusted(
    failing_client,
) -> None:
    response = _submit(
        failing_client,
        DEDUPE,
        {"source_file": upload_file("actually.csv", fx.SIMPLE_TABLE.as_xlsx())},
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "PARSE_ERROR"


def test_a_corrupt_workbook_is_reported_by_both_engines(failing_client) -> None:
    corrupt = fx.SIMPLE_TABLE.as_xlsx()[: len(fx.SIMPLE_TABLE.as_xlsx()) // 2]
    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("broken.xlsx", corrupt)}
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "PARSE_ERROR"
    assert "primary_reason" in error["details"]
    assert "fallback_reason" in error["details"]


def test_a_workbook_with_several_data_sheets_is_refused_not_guessed(
    failing_client,
) -> None:
    """Build plan section 17: never silently choose a worksheet."""
    response = _submit(
        failing_client,
        DEDUPE,
        {
            "source_file": upload_file(
                fx.MULTIPLE_WORKSHEETS.filename(), fx.MULTIPLE_WORKSHEETS.as_xlsx()
            )
        },
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "AMBIGUOUS_WORKBOOK"
    assert error["details"]["worksheets_with_data"] == ["January", "February"]


def test_an_empty_workbook_is_refused(failing_client) -> None:
    response = _submit(
        failing_client,
        DEDUPE,
        {
            "source_file": upload_file(
                fx.EMPTY_WORKBOOK.filename(), fx.EMPTY_WORKBOOK.as_xlsx()
            )
        },
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "PARSE_ERROR"


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_dataset_with_no_rows_is_refused(failing_client, extension: str) -> None:
    """Header-only parses cleanly, then fails the dataset check with its own code."""
    response = _submit(
        failing_client,
        DEDUPE,
        {
            "source_file": upload_file(
                fx.HEADER_ONLY.filename(extension), fx.HEADER_ONLY.payload(extension)
            )
        },
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "EMPTY_DATASET"


def test_an_upload_over_the_limit_is_refused_with_the_limit_named(
    failing_client, monkeypatch
) -> None:
    from app import config

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 512)
    payload = fx.large_table(200).as_csv()
    assert len(payload) > 512

    response = _submit(
        failing_client, DEDUPE, {"source_file": upload_file("big.csv", payload)}
    )

    assert response.status_code == 413
    error = _error(response)
    assert error["code"] == "FILE_TOO_LARGE"
    assert error["details"]["limit_bytes"] == 512
    assert error["details"]["slot_id"] == "source_file"
    assert "upload limit" in error["message"]


# ---------------------------------------------------------------------------
# Missing required columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_missing_required_columns_are_refused_and_listed(
    failing_client, extension: str
) -> None:
    """The fixture misspells `SKU` as `Sku` and omits four more columns."""
    response = _submit(
        failing_client,
        PRODUCT_MASTER,
        {
            "sales_file": upload_file(
                fx.MISSING_REQUIRED_COLUMNS.filename(extension),
                fx.MISSING_REQUIRED_COLUMNS.payload(extension),
            )
        },
    )

    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == [
        "SKU",
        "Vintage",
        "Producer",
        "Selection",
        "Volume",
    ]
    assert error["details"]["found_columns"] == list(
        fx.MISSING_REQUIRED_COLUMNS.header
    )


def test_a_misspelled_column_is_reported_rather_than_matched(
    failing_client,
) -> None:
    """Build plan section 3.3: never guess what a column represents.

    `Sku` is present and `SKU` is required. Nothing case-folds them together.
    """
    response = _submit(
        failing_client,
        PRODUCT_MASTER,
        {
            "sales_file": upload_file(
                "sales.csv", fx.MISSING_REQUIRED_COLUMNS.as_csv()
            )
        },
    )

    error = _error(response)
    assert "SKU" in error["details"]["missing_columns"]
    assert "Sku" in error["details"]["found_columns"]


def test_a_file_valid_for_one_action_can_be_invalid_for_another(
    failing_client,
) -> None:
    """The same upload: accepted by the schema-free Action, refused by the other."""
    payload = upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())
    accepted = _submit(failing_client, DEDUPE, {"source_file": payload})
    assert accepted.status_code == 200

    refused = _submit(
        failing_client,
        PRODUCT_MASTER,
        {"sales_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )
    assert refused.status_code == 422
    assert _error(refused)["code"] == "MISSING_COLUMNS"


# ---------------------------------------------------------------------------
# Invalid configuration (the request-shaped equivalents)
# ---------------------------------------------------------------------------


def test_a_request_with_no_action_id_is_refused(failing_client) -> None:
    response = failing_client.post(
        "/api/runs",
        files={"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("action_id", ["", "   "])
def test_a_blank_action_id_is_refused(failing_client, action_id: str) -> None:
    response = _submit(
        failing_client,
        action_id,
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_REQUEST"


def test_an_output_the_action_does_not_declare_is_a_404(failing_client) -> None:
    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/not_an_output/preview"
    )

    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "UNKNOWN_OUTPUT"
    assert error["details"]["available_output_ids"] == ["deduplicated_data"]


@pytest.mark.parametrize("limit", [0, -1, 501, 10_000])
def test_a_preview_limit_outside_the_documented_range_is_refused(
    failing_client, limit: int
) -> None:
    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/preview",
        params={"limit": limit},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_REQUEST"


def test_a_negative_preview_offset_is_refused(failing_client) -> None:
    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/preview",
        params={"offset": -1},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_REQUEST"


def test_an_unknown_download_format_is_not_a_route(failing_client) -> None:
    """Only the two declared formats have endpoints; nothing else is invented."""
    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/json"
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Action failure
# ---------------------------------------------------------------------------


def test_an_action_that_raises_returns_a_structured_500(failing_client) -> None:
    response = _submit(
        failing_client,
        "always_raises",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "ACTION_FAILED"
    assert error["details"]["action_id"] == "always_raises"


def test_an_action_crash_never_leaks_its_message_or_a_traceback(
    failing_client,
) -> None:
    """Build plan section 22: the local log gets the detail, the client does not."""
    response = _submit(
        failing_client,
        "always_raises",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    body = response.text
    assert "secret internal detail" not in body
    assert "/home/someone" not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body


def test_an_action_that_omits_a_declared_output_fails_the_run(
    failing_client,
) -> None:
    """A partial result is never served as a complete one."""
    response = _submit(
        failing_client,
        "forgets_an_output",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "ACTION_FAILED"
    assert error["details"]["output_id"] == "second"


def test_a_failed_run_keeps_its_record(failing_client, run_store) -> None:
    """Build plan 3.9: evidence of a failure is never destroyed."""
    _submit(
        failing_client,
        "always_raises",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    runs = run_store.list_runs()
    assert len(runs) == 1

    manifest = failing_client.get(f"/api/runs/{runs[0].run_id}").json()
    assert manifest["status"] == "failed"
    assert manifest["error"]["code"] == "ACTION_FAILED"
    assert manifest["outputs"] == []
    assert manifest["inputs"][0]["row_count"] == fx.SIMPLE_TABLE.row_count


# ---------------------------------------------------------------------------
# Unknown Action ID
# ---------------------------------------------------------------------------


def test_an_unknown_action_id_is_a_404(failing_client) -> None:
    response = _submit(
        failing_client,
        "no_such_action",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "UNKNOWN_ACTION"
    assert error["details"]["action_id"] == "no_such_action"


def test_an_unknown_action_id_never_falls_back_to_a_registered_one(
    failing_client,
) -> None:
    """Build plan 2.4: no default, no near match."""
    for candidate in ("Exact_Duplicate_Remover", "exact-duplicate-remover", "dedupe"):
        response = _submit(
            failing_client,
            candidate,
            {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
        )
        assert response.status_code == 404, candidate


def test_an_unknown_action_records_no_run(failing_client, run_store) -> None:
    """A request that never named a real Action never became a Run."""
    _submit(
        failing_client,
        "no_such_action",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert run_store.list_runs() == []


# ---------------------------------------------------------------------------
# Unknown run ID
# ---------------------------------------------------------------------------


def test_an_unknown_run_id_is_a_404(failing_client) -> None:
    response = failing_client.get(f"/api/runs/{ABSENT_RUN_ID}")

    assert response.status_code == 404
    assert _error(response)["code"] == "UNKNOWN_RUN"


@pytest.mark.parametrize(
    "run_id", ["not-a-uuid", "../../etc/passwd", "1", "%2e%2e%2f", "  "]
)
def test_a_malformed_run_id_is_a_404_rather_than_a_crash(
    failing_client, run_id: str
) -> None:
    response = failing_client.get(f"/api/runs/{run_id}")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/outputs/deduplicated_data/preview",
        "/api/runs/{run_id}/outputs/deduplicated_data/download/csv",
        "/api/runs/{run_id}/outputs/deduplicated_data/download/xlsx",
        "/api/runs/{run_id}/download/xlsx",
    ],
)
def test_every_run_endpoint_answers_an_unknown_run_with_404(
    failing_client, path: str
) -> None:
    response = failing_client.get(path.format(run_id=ABSENT_RUN_ID))

    assert response.status_code == 404
    assert _error(response)["code"] == "UNKNOWN_RUN"


# ---------------------------------------------------------------------------
# Export before completion
# ---------------------------------------------------------------------------


def _failed_run_id(client, run_store) -> str:
    response = _submit(
        client,
        "always_raises",
        {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
    )
    assert response.status_code == 500
    runs = run_store.list_runs()
    assert len(runs) == 1
    return runs[0].run_id


def test_a_failed_run_has_no_output_to_download(failing_client, run_store) -> None:
    """There is nothing to export, and the refusal says so rather than 500."""
    run_id = _failed_run_id(failing_client, run_store)

    response = failing_client.get(
        f"/api/runs/{run_id}/outputs/result/download/csv"
    )

    assert response.status_code == 404
    assert _error(response)["code"] == "UNKNOWN_OUTPUT"


def test_a_failed_run_has_no_workbook_to_download(failing_client, run_store) -> None:
    run_id = _failed_run_id(failing_client, run_store)

    response = failing_client.get(f"/api/runs/{run_id}/download/xlsx")

    assert response.status_code == 404
    assert _error(response)["code"] == "MISSING_ARTIFACT"


def test_a_failed_run_has_nothing_to_preview(failing_client, run_store) -> None:
    run_id = _failed_run_id(failing_client, run_store)

    response = failing_client.get(f"/api/runs/{run_id}/outputs/result/preview")

    assert response.status_code == 404


def test_a_run_whose_result_was_released_can_no_longer_be_exported(
    failing_client, run_store
) -> None:
    """The Run is still known; its data is not (build plan 6D.8).

    A missing artifact is reported as one — never as an unknown Run, and never
    as an empty file that a user might mistake for a real result.
    """
    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)
    stored = run_store.get_run(run["run_id"])
    run_store.update_run(stored.with_changes(result=None))

    for path in (
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/csv",
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/xlsx",
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/preview",
        f"/api/runs/{run['run_id']}/download/xlsx",
    ):
        response = failing_client.get(path)
        assert response.status_code == 404, path
        assert _error(response)["code"] == "MISSING_ARTIFACT", path

    # The Run itself is still retrievable: only its data is gone.
    assert failing_client.get(f"/api/runs/{run['run_id']}").status_code == 200


def test_a_deleted_run_is_gone_from_every_endpoint(
    failing_client, run_store
) -> None:
    from app.services import runner

    run = _run_ok(failing_client, DEDUPE, fx.SIMPLE_TABLE)
    assert runner.delete_run(run["run_id"]) is True

    for path in (
        f"/api/runs/{run['run_id']}",
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/preview",
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/csv",
        f"/api/runs/{run['run_id']}/download/xlsx",
    ):
        assert failing_client.get(path).status_code == 404, path


# ---------------------------------------------------------------------------
# Every failure is a structured error, and none of them leaks anything
# ---------------------------------------------------------------------------


def test_no_failure_response_exposes_a_server_path(failing_client) -> None:
    """Build plan 6F.8, across the failure surface rather than the happy one."""
    responses = [
        _submit(failing_client, "no_such_action", {}),
        _submit(
            failing_client,
            DEDUPE,
            {"source_file": upload_file("notes.txt", b"nope")},
        ),
        _submit(
            failing_client,
            "always_raises",
            {"source_file": upload_file("simple.csv", fx.SIMPLE_TABLE.as_csv())},
        ),
        failing_client.get(f"/api/runs/{ABSENT_RUN_ID}"),
    ]

    for response in responses:
        body = response.text
        for fragment in ("/Users/", "/home/", "/tmp", "data/runs", "\\Users\\"):
            assert fragment not in body, (response.url, fragment)


def test_a_hostile_upload_filename_is_never_echoed_into_a_download_name(
    failing_client,
) -> None:
    """Build plan section 16: the client's filename is metadata, never a name."""
    run = _submit(
        failing_client,
        DEDUPE,
        {
            "source_file": upload_file(
                "../../../etc/passwd.csv", fx.SIMPLE_TABLE.as_csv()
            )
        },
    ).json()

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/csv"
    )

    disposition = response.headers["content-disposition"]
    assert ".." not in disposition
    assert "passwd" not in disposition
    assert "/" not in disposition.split('filename="')[1]


def test_a_successful_download_is_still_a_real_file_after_all_of_this(
    failing_client,
) -> None:
    """A guard on the battery itself: the happy path still works.

    A suite of failure tests that passed because everything failed would be
    worthless, so one success is asserted alongside them.
    """
    run = _run_ok(failing_client, DEDUPE, fx.DUPLICATE_ROWS)

    response = failing_client.get(
        f"/api/runs/{run['run_id']}/outputs/deduplicated_data/download/xlsx"
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
