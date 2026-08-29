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
from app.services import storage

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


def test_a_failed_run_is_retrievable_afterwards(run_client, runs_dir: Path) -> None:
    _start_run(run_client, action_id="strict")

    (run_directory,) = list(runs_dir.iterdir())
    response = run_client.get(f"/api/runs/{run_directory.name}")

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
    response = run_client.get(f"/api/runs/{storage.new_run_id()}")

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
        f"/api/runs/{storage.new_run_id()}/outputs/result/preview"
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
    assert "result.csv" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == "SKU,Vintage,Supplier"


def test_the_xlsx_download_returns_a_real_workbook(run_client) -> None:
    run_id = _start_run(run_client).json()["run_id"]

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/xlsx")

    assert response.status_code == 200
    assert "result.xlsx" in response.headers["content-disposition"]
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

    disposition = download.headers["content-disposition"]
    assert "result.csv" in disposition
    assert "evil" not in disposition


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
    run_id = storage.new_run_id()

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    assert response.status_code == 404


def test_a_download_whose_file_is_missing_returns_404(
    run_client, runs_dir: Path
) -> None:
    run_id = _start_run(run_client).json()["run_id"]
    storage.run_paths(run_id).export_artifact("result", "csv").unlink()

    response = run_client.get(f"/api/runs/{run_id}/outputs/result/download/csv")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


# ---------------------------------------------------------------------------
# Uploads reach the backend directly and are read into memory
# (sections 5 and 16, build plan 6C.3)
# ---------------------------------------------------------------------------


def test_the_uploaded_source_is_never_written_to_the_run_directory(
    run_client, runs_dir: Path
) -> None:
    """Build plan 6C.3: no permanent server-side upload file is created."""
    run_id = _start_run(run_client).json()["run_id"]

    run_directory = runs_dir / run_id
    written = sorted(
        path.relative_to(run_directory).as_posix()
        for path in run_directory.rglob("*")
        if path.is_file()
    )
    assert written == [
        "exports/result.csv",
        "exports/result.xlsx",
        "working/result.parquet",
    ]
    assert not (run_directory / "inputs").exists()


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
    assert not (runs_dir / run_id / "inputs").exists()
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