"""Run endpoint tests (build plan 3.12-3.16 and section 22).

Covers the HTTP contract: status codes, the structured error shape, the
preview's paging limits and the two download endpoints.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import polars as pl
import pytest

from app import config
from app.models.run import new_run_id
from app.services import export, run_store

from tests.helpers import csv_bytes, make_action, upload_file, xlsx_bytes

SALES_HEADER = ["SKU", "Vintage", "Supplier"]
SALES_ROWS = [["A1", 2019, "Acme"], ["A2", 2020, "Beta"], ["A3", 2021, "Gamma"]]


def _sales_csv() -> bytes:
    return csv_bytes(SALES_HEADER, SALES_ROWS)


@pytest.fixture
def run_client(client, registered_actions):
    """A client whose registry holds a passthrough Action plus a strict one."""
    registered_actions(
        make_action("passthrough"),
        make_action("strict", required_columns=("SKU", "Supplier", "Volume")),
    )
    return client


def _start_run(client, payload: bytes | None = None, action_id: str = "passthrough"):
    return client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("sales.csv", payload or _sales_csv())},
    )


def _disposition_filename(response) -> str:
    """The filename a download response offers, unquoted.

    Read out rather than substring-matched so a test states the whole name the
    convention produced (build plan 6F.6).
    """
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="'), disposition
    assert disposition.endswith('"'), disposition
    return disposition[len('attachment; filename="') : -1]


# ---------------------------------------------------------------------------
# 3.12 POST /api/runs
# ---------------------------------------------------------------------------


def test_a_supported_file_is_accepted(run_client) -> None:
    response = _start_run(run_client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["action"]["id"] == "passthrough"
    assert body["outputs"][0]["row_count"] == 3


def test_an_xlsx_upload_is_accepted(run_client) -> None:
    payload = xlsx_bytes({"Sales": [SALES_HEADER, *SALES_ROWS]})

    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("book.xlsx", payload)},
    )

    assert response.status_code == 200
    assert response.json()["inputs"][0]["worksheet"] == "Sales"


def test_an_unknown_action_returns_404(run_client) -> None:
    response = _start_run(run_client, action_id="does_not_exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_ACTION"


def test_a_missing_action_id_returns_400(run_client) -> None:
    response = run_client.post(
        "/api/runs", files={"source_file": upload_file("s.csv", _sales_csv())}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_blank_action_id_returns_400(run_client) -> None:
    response = run_client.post(
        "/api/runs",
        data={"action_id": "   "},
        files={"source_file": upload_file("s.csv", _sales_csv())},
    )

    assert response.status_code == 400


def test_a_missing_required_input_returns_422(run_client) -> None:
    response = run_client.post("/api/runs", data={"action_id": "passthrough"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MISSING_INPUT"


def test_an_unsupported_extension_returns_422(run_client) -> None:
    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("data.json", b'{"a": 1}')},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_EXTENSION"


def test_missing_required_columns_return_422_with_the_missing_names(
    run_client,
) -> None:
    response = _start_run(run_client, action_id="strict")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == ["Volume"]


def test_an_oversized_upload_returns_413(
    run_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)

    response = _start_run(run_client)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_an_error_response_never_contains_a_traceback(run_client) -> None:
    response = _start_run(run_client, action_id="strict")

    assert "Traceback" not in response.text
    assert "File \"" not in response.text


def test_the_error_body_has_the_documented_shape(run_client) -> None:
    """Build plan section 22: code, message and details."""
    body = _start_run(run_client, action_id="strict").json()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert isinstance(body["error"]["message"], str)


def test_a_failed_run_is_retrievable_afterwards(run_client) -> None:
    _start_run(run_client, action_id="strict")

    (run,) = run_store.list_runs()
    response = run_client.get(f"/api/runs/{run.run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


# ---------------------------------------------------------------------------
# 3.13 GET /api/runs/{run_id}
# ---------------------------------------------------------------------------


def test_a_run_can_be_retrieved_by_id(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_an_unknown_run_id_returns_404(run_client) -> None:
    response = run_client.get(f"/api/runs/{new_run_id()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


@pytest.mark.parametrize(
    "malformed",
    ["not-a-uuid", "12345", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "run..id"],
)
def test_a_malformed_run_id_returns_404(run_client, malformed: str) -> None:
    response = run_client.get(f"/api/runs/{malformed}")

    assert response.status_code == 404


def test_a_traversal_shaped_run_id_cannot_read_another_file(
    run_client, runs_dir: Path
) -> None:
    (runs_dir.parent / "secret.json").write_text('{"secret": true}', encoding="utf-8")

    response = run_client.get("/api/runs/..%2Fsecret")

    assert response.status_code == 404
    assert "secret" not in response.text


# ---------------------------------------------------------------------------
# 3.15 Preview
# ---------------------------------------------------------------------------


def test_the_preview_returns_the_first_page_by_default(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    body = run_client.get(f"/api/runs/{run_id}/outputs/result/preview").json()

    assert body["offset"] == 0
    assert body["limit"] == 100
    assert body["total_rows"] == 3
    assert body["columns"] == SALES_HEADER
    assert body["rows"] == [["A1", 2019, "Acme"], ["A2", 2020, "Beta"], ["A3", 2021, "Gamma"]]


def test_the_preview_returns_only_the_requested_rows(run_client) -> None:
    payload = csv_bytes(["i"], [[n] for n in range(1000)])
    run_id = _start_run(run_client, payload).json()["run_id"]

    body = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?offset=0&limit=100"
    ).json()

    assert len(body["rows"]) == 100
    assert body["total_rows"] == 1000
    assert body["rows"][0] == [0]
    assert body["rows"][-1] == [99]


def test_the_preview_pages_forward(run_client) -> None:
    payload = csv_bytes(["i"], [[n] for n in range(1000)])
    run_id = _start_run(run_client, payload).json()["run_id"]

    body = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?offset=900&limit=100"
    ).json()

    assert body["offset"] == 900
    assert body["rows"][0] == [900]
    assert len(body["rows"]) == 100


def test_a_page_beyond_the_end_is_empty_rather_than_an_error(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    body = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?offset=5000&limit=10"
    ).json()

    assert body["rows"] == []
    assert body["total_rows"] == 3


def test_the_preview_accepts_the_maximum_limit(run_client) -> None:
    payload = csv_bytes(["i"], [[n] for n in range(600)])
    run_id = _start_run(run_client, payload).json()["run_id"]

    response = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?limit=500"
    )

    assert response.status_code == 200
    assert len(response.json()["rows"]) == 500


@pytest.mark.parametrize("limit", [0, -1, 501, 100000])
def test_an_out_of_range_limit_returns_400(run_client, limit: int) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?limit={limit}"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_negative_offset_returns_400(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?offset=-1"
    )

    assert response.status_code == 400


def test_a_non_numeric_paging_parameter_returns_422(run_client) -> None:
    """FastAPI's own parameter validation, not a crash."""
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(
        f"/api/runs/{run_id}/outputs/result/preview?limit=abc"
    )

    assert response.status_code == 422


def test_an_unknown_output_returns_404(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/nope/preview")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_OUTPUT"


@pytest.mark.parametrize("output_id", ["..", "../../manifest", "result.csv"])
def test_a_traversal_shaped_output_id_returns_404(run_client, output_id: str) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/{output_id}/preview")

    assert response.status_code == 404


def test_the_preview_of_an_unknown_run_returns_404(run_client) -> None:
    response = run_client.get(
        f"/api/runs/{new_run_id()}/outputs/result/preview"
    )

    assert response.status_code == 404


def test_null_values_survive_the_preview_as_null(run_client) -> None:
    payload = b"a,b\n1,\n2,x\n"
    run_id = _start_run(run_client, payload).json()["run_id"]

    body = run_client.get(f"/api/runs/{run_id}/outputs/result/preview").json()

    assert body["rows"] == [[1, None], [2, "x"]]


def test_the_literal_text_null_is_not_turned_into_a_null(run_client) -> None:
    """Build plan 6.5: actual text 'null' must not become blank."""
    payload = b"a\nnull\n"
    run_id = _start_run(run_client, payload).json()["run_id"]

    body = run_client.get(f"/api/runs/{run_id}/outputs/result/preview").json()

    assert body["rows"] == [["null"]]


# ---------------------------------------------------------------------------
# 3.16 Downloads
# ---------------------------------------------------------------------------


def test_the_csv_download_returns_the_generated_file(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert _disposition_filename(response).startswith(
        f"{export.FILENAME_PREFIX}-passthrough-result-"
    )
    assert _disposition_filename(response).endswith(".csv")
    assert response.text.splitlines()[0] == "SKU,Vintage,Supplier"


def test_the_xlsx_download_returns_a_real_workbook(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/xlsx")

    assert response.status_code == 200
    assert _disposition_filename(response).startswith(
        f"{export.FILENAME_PREFIX}-passthrough-result-"
    )
    assert _disposition_filename(response).endswith(".xlsx")
    # A genuine XLSX is a zip container holding the workbook part.
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "xl/workbook.xml" in archive.namelist()


def test_a_downloaded_csv_reads_back_with_the_expected_data(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")
    frame = pl.read_csv(io.BytesIO(response.content))

    assert frame.columns == SALES_HEADER
    assert frame.height == 3
    assert frame.rows() == [("A1", 2019, "Acme"), ("A2", 2020, "Beta"), ("A3", 2021, "Gamma")]


def test_the_download_filename_comes_from_the_output_not_the_upload(
    run_client,
) -> None:
    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("../../evil.csv", _sales_csv())},
    )
    run_id = response.json()["run_id"]

    download = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    filename = _disposition_filename(download)
    assert filename.startswith(f"{export.FILENAME_PREFIX}-passthrough-result-")
    assert filename.endswith(".csv")
    assert "evil" not in filename


def test_each_output_downloads_and_previews_its_own_table(
    client, registered_actions
) -> None:
    """Build plan 6D.5, over HTTP: a secondary result is its own dataset."""
    from collections.abc import Mapping

    from app.actions.base import Action, ActionResult
    from app.models.schemas import ActionInput, ActionOutput

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

    registered_actions(_TwoOutputs())
    run_id = _start_run(client, action_id="two_outputs").json()["run_id"]

    kept = client.get(f"/api/runs/{run_id}/outputs/kept/preview").json()
    rejected = client.get(f"/api/runs/{run_id}/outputs/rejected/preview").json()
    assert kept["rows"] == [["A1", 2019, "Acme"]]
    assert rejected["rows"] == [["A3", 2021, "Gamma"]]

    kept_csv = client.get(f"/api/runs/{run_id}/outputs/kept/download/csv")
    rejected_csv = client.get(f"/api/runs/{run_id}/outputs/rejected/download/csv")
    assert kept_csv.text.splitlines()[1] == "A1,2019,Acme"
    assert rejected_csv.text.splitlines()[1] == "A3,2021,Gamma"
    assert _disposition_filename(kept_csv).startswith(
        f"{export.FILENAME_PREFIX}-two-outputs-kept-"
    )
    assert _disposition_filename(rejected_csv).startswith(
        f"{export.FILENAME_PREFIX}-two-outputs-rejected-"
    )


def test_each_output_downloads_its_own_worksheet(client, registered_actions) -> None:
    """The XLSX of a secondary output names that output, not the primary one."""
    from collections.abc import Mapping

    from app.actions.base import Action, ActionResult
    from app.models.schemas import ActionInput, ActionOutput
    from app.services import parser

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

    registered_actions(_TwoOutputs())
    run_id = _start_run(client, action_id="two_outputs").json()["run_id"]

    response = client.get(f"/api/runs/{run_id}/outputs/rejected/download/xlsx")
    parsed = parser.parse_tabular_bytes(response.content, ".xlsx")

    assert parsed.worksheet == "Rejected"
    assert parsed.frame.rows() == [("A3", 2021.0, "Gamma")]


def test_downloading_an_unknown_output_returns_404(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    assert (
        run_client.get(f"/api/runs/{run_id}/outputs/nope/download/csv").status_code
        == 404
    )
    assert (
        run_client.get(f"/api/runs/{run_id}/outputs/nope/download/xlsx").status_code
        == 404
    )


def test_downloading_from_an_unknown_run_returns_404(run_client) -> None:
    run_id = new_run_id()

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    assert response.status_code == 404


def test_a_download_whose_result_has_been_released_returns_404(
    run_client,
) -> None:
    """A Run whose result is gone reports a missing artifact, not a crash.

    Before Phase 6D this was an export file that had been removed from disk.
    A result lives in the Run now, so the equivalent state is a Run whose
    result has been released while its metadata is still recorded.
    """
    run_id = _start_run(run_client).json()["run_id"]
    run = run_store.get_run(run_id)
    run_store.update_run(run.with_changes(result=None))

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


def test_a_preview_whose_result_has_been_released_returns_404(
    run_client,
) -> None:
    run_id = _start_run(run_client).json()["run_id"]
    run = run_store.get_run(run_id)
    run_store.update_run(run.with_changes(result=None))

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/preview")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


def test_a_download_is_generated_from_the_result_not_a_file(
    run_client, runs_dir: Path
) -> None:
    """Build plan 6D: no ``exports/`` directory is required to download."""
    run_id = _start_run(run_client).json()["run_id"]

    csv_response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")
    xlsx_response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/xlsx")

    assert csv_response.status_code == 200
    assert xlsx_response.status_code == 200
    assert list(runs_dir.rglob("*")) == []


# ---------------------------------------------------------------------------
# Uploads reach the backend directly and are read into memory
# (sections 5 and 16, build plan 6C.3)
# ---------------------------------------------------------------------------


def test_a_run_writes_nothing_to_the_filesystem(
    run_client, runs_dir: Path
) -> None:
    """Build plan 6C.3 and 6D: a Run needs no directory of any kind.

    Until Phase 6D this asserted the three generated artifacts and no more.
    Results are held in memory now, so the whole runs directory stays empty:
    no ``inputs/``, no ``working/``, no ``exports/``, no run directory at all.
    """
    run_id = _start_run(run_client).json()["run_id"]

    assert list(runs_dir.rglob("*")) == []
    assert not (runs_dir / run_id).exists()


def test_the_uploaded_source_is_recorded_under_a_generated_name(
    run_client,
) -> None:
    """The client's filename stays metadata; the generated name is what is used."""
    manifest = _start_run(run_client).json()

    (recorded,) = manifest["inputs"]
    assert recorded["original_filename"] == "sales.csv"
    assert recorded["stored_filename"] == "source.csv"
    assert recorded["file_size_bytes"] == len(_sales_csv())
    assert recorded["extension"] == ".csv"


def test_a_hostile_upload_filename_writes_nothing_anywhere(
    run_client, runs_dir: Path
) -> None:
    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("../../../escaped.csv", _sales_csv())},
    )
    run_id = response.json()["run_id"]

    assert response.status_code == 200
    assert not (runs_dir.parent / "escaped.csv").exists()
    # There is no longer any file written from the upload at all.
    assert list(runs_dir.rglob("*")) == []
    # The generated name is what the application used...
    assert response.json()["inputs"][0]["stored_filename"] == "source.csv"
    # ...and the name is still recorded as metadata.
    assert response.json()["inputs"][0]["original_filename"] == "../../../escaped.csv"


def test_an_empty_upload_is_refused_over_http(run_client) -> None:
    """Build plan 6C.4 and 6C.9, through the HTTP boundary."""
    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("empty.csv", b"")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_an_xlsx_upload_becomes_a_dataframe_over_http(run_client) -> None:
    """Build plan 6C completion criteria, end to end and with no file picker."""
    payload = xlsx_bytes({"Sales": [SALES_HEADER, *SALES_ROWS]})

    response = run_client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("book.xlsx", payload)},
    )

    assert response.status_code == 200, response.text
    (recorded,) = response.json()["inputs"]
    assert recorded["extension"] == ".xlsx"
    assert recorded["worksheet"] == "Sales"
    assert recorded["parser_engine"] == "fastexcel-calamine"
    assert recorded["row_count"] == len(SALES_ROWS)
    assert response.json()["outputs"][0]["row_count"] == len(SALES_ROWS)

# ---------------------------------------------------------------------------
# 6E — result metadata, preview schema and audit over HTTP
# ---------------------------------------------------------------------------


def test_the_manifest_carries_result_metadata(run_client) -> None:
    """Build plan 6E.1, through the HTTP boundary."""
    (output,) = _start_run(run_client).json()["outputs"]

    assert output["input_row_count"] == 3
    assert output["row_count"] == 3
    assert output["columns_added"] == []
    assert output["columns_removed"] == []
    assert [c["name"] for c in output["column_schema"]] == SALES_HEADER
    assert [c["kind"] for c in output["column_schema"]] == [
        "text",
        "number",
        "text",
    ]


def test_the_preview_response_carries_the_column_schema(run_client) -> None:
    """Build plan 6E.4: enough type information to render values correctly."""
    run_id = _start_run(run_client).json()["run_id"]

    body = run_client.get(f"/api/runs/{run_id}/outputs/result/preview").json()

    assert [c["name"] for c in body["column_schema"]] == body["columns"]
    assert [c["kind"] for c in body["column_schema"]] == ["text", "number", "text"]
    assert [c["dtype"] for c in body["column_schema"]] == [
        "String",
        "Int64",
        "String",
    ]


def test_the_manifest_carries_the_audit_summary(run_client) -> None:
    """Build plan 6E.5: a completed Run explains what happened."""
    audit = _start_run(run_client).json()["audit"]

    assert audit["status"] == "succeeded"
    assert audit["action"]["id"] == "passthrough"
    assert audit["rows_received"] == 3
    assert audit["rows_returned"] == 3
    assert audit["primary_result_id"] == "result"
    assert [r["output_id"] for r in audit["results"]] == ["result"]
    assert audit["inputs"][0]["slot_id"] == "source_file"
    assert audit["inputs"][0]["original_filename"] == "sales.csv"
    assert audit["duration_ms"] is not None


def test_a_run_fetched_later_carries_the_same_audit(run_client) -> None:
    created = _start_run(run_client).json()

    fetched = run_client.get(f"/api/runs/{created['run_id']}").json()

    assert fetched["audit"] == created["audit"]
    assert fetched["outputs"] == created["outputs"]


def test_a_failed_run_reports_a_failed_audit(run_client) -> None:
    response = run_client.post(
        "/api/runs",
        data={"action_id": "strict"},
        files={"source_file": upload_file("sales.csv", _sales_csv())},
    )
    assert response.status_code == 422

    (run,) = run_store.list_runs()
    audit = run_client.get(f"/api/runs/{run.run_id}").json()["audit"]

    assert audit["status"] == "failed"
    assert audit["rows_returned"] is None
    assert audit["rows_affected"] is None
    assert audit["results"] == []
    assert audit["errors"][0]["code"] == "MISSING_COLUMNS"


def test_no_response_exposes_a_server_path(run_client) -> None:
    """Build plan section 11: the browser sees logical IDs, never paths."""
    created = _start_run(run_client)
    run_id = created.json()["run_id"]
    preview = run_client.get(f"/api/runs/{run_id}/outputs/result/preview")

    for body in (created.text, preview.text):
        assert "/home/" not in body
        assert "/Users/" not in body
        assert "data/runs" not in body
        assert "/tmp/" not in body