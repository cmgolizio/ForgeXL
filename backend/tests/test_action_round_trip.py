"""Export round trips for both proof Actions (build plan Phase 4D).

Uploads each Action's controlled fixture as CSV and as XLSX through the real
HTTP API, downloads the generated CSV and XLSX, and reads them back here. This
proves the exports carry the expected columns, row count and values — not
merely that a file with the right extension was produced.

The read-back goes through the application's own parser, so an export that this
application could not itself ingest fails the test.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

import polars as pl
import pytest

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import ProductMasterBuilderAction
from app.services import parser

from tests.fixtures import duplicate_rows, product_rows
from tests.helpers import csv_bytes, upload_file, xlsx_bytes


@dataclass(frozen=True)
class Case:
    """One Action, its fixture and the output that fixture must produce."""

    action_id: str
    slot_id: str
    output_id: str
    header: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    expected_columns: tuple[str, ...]
    expected_rows: tuple[tuple[Any, ...], ...]

    def csv_payload(self) -> bytes:
        return csv_bytes(self.header, self.rows)

    def xlsx_payload(self) -> bytes:
        return xlsx_bytes({"Data": [self.header, *self.rows]})


CASES = (
    Case(
        action_id=ExactDuplicateRemoverAction.id,
        slot_id="source_file",
        output_id="deduplicated_data",
        header=duplicate_rows.HEADER,
        rows=duplicate_rows.ROWS,
        expected_columns=duplicate_rows.HEADER,
        expected_rows=duplicate_rows.EXPECTED_ROWS,
    ),
    Case(
        action_id=ProductMasterBuilderAction.id,
        slot_id="sales_file",
        output_id="product_master",
        header=product_rows.HEADER,
        rows=product_rows.ROWS,
        expected_columns=product_rows.EXPECTED_COLUMNS,
        expected_rows=product_rows.EXPECTED_ROWS,
    ),
)

CASE_IDS = [case.action_id for case in CASES]


def _numbers_as_floats(rows) -> list[tuple[Any, ...]]:
    """Compare values without distinguishing 2019 from 2019.0.

    Excel has a single numeric type, so any value that has passed through an
    XLSX file comes back as a float. That is a property of the format, not a
    change to the data, and it is the only difference this normalisation
    tolerates: text, blanks and the values themselves must still match exactly.
    """
    return [
        tuple(
            float(value) if isinstance(value, (int, float)) else value
            for value in row
        )
        for row in rows
    ]


def _start_run(client, case: Case, filename: str, payload: bytes):
    response = client.post(
        "/api/runs",
        data={"action_id": case.action_id},
        files={case.slot_id: upload_file(filename, payload)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _download(client, run_id: str, case: Case, export_format: str):
    response = client.get(
        f"/api/runs/{run_id}/outputs/{case.output_id}/download/{export_format}"
    )
    assert response.status_code == 200, response.text
    return response


def _read_downloaded_xlsx(content: bytes) -> pl.DataFrame:
    """Read a downloaded workbook back with the application's own parser.

    Straight from the response bytes: since Phase 6C the parser reads memory,
    so the download never has to be written out in order to be checked.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert "xl/workbook.xml" in archive.namelist(), "not a real workbook"

    return parser.parse_tabular_bytes(content, ".xlsx").frame


# ---------------------------------------------------------------------------
# CSV upload -> CSV download.  No Excel anywhere: this comparison is exact,
# down to the dtypes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_downloaded_csv_matches_the_expected_output_exactly(
    client, case: Case
) -> None:
    run = _start_run(client, case, "fixture.csv", case.csv_payload())

    response = _download(client, run["run_id"], case, "csv")
    frame = pl.read_csv(io.BytesIO(response.content))

    assert response.headers["content-type"].startswith("text/csv")
    assert f"{case.output_id}.csv" in response.headers["content-disposition"]
    assert tuple(frame.columns) == case.expected_columns
    assert frame.height == len(case.expected_rows)
    assert frame.rows() == list(case.expected_rows)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_downloaded_csv_preserves_the_dtypes_of_the_run(
    client, case: Case
) -> None:
    run = _start_run(client, case, "fixture.csv", case.csv_payload())
    response = _download(client, run["run_id"], case, "csv")

    downloaded = pl.read_csv(io.BytesIO(response.content))
    source = pl.read_csv(io.BytesIO(case.csv_payload())).select(
        case.expected_columns
    )

    assert downloaded.schema == source.schema


# ---------------------------------------------------------------------------
# CSV upload -> XLSX download
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_downloaded_xlsx_matches_the_expected_output(
    client, case: Case
) -> None:
    run = _start_run(client, case, "fixture.csv", case.csv_payload())

    response = _download(client, run["run_id"], case, "xlsx")
    frame = _read_downloaded_xlsx(response.content)

    assert f"{case.output_id}.xlsx" in response.headers["content-disposition"]
    assert tuple(frame.columns) == case.expected_columns
    assert frame.height == len(case.expected_rows)
    assert _numbers_as_floats(frame.rows()) == _numbers_as_floats(
        case.expected_rows
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_text_in_the_downloaded_xlsx_is_byte_identical(
    client, case: Case
) -> None:
    """Accents, casing and blanks must survive the workbook unchanged."""
    run = _start_run(client, case, "fixture.csv", case.csv_payload())
    response = _download(client, run["run_id"], case, "xlsx")

    frame = _read_downloaded_xlsx(response.content)
    expected = pl.DataFrame(
        list(case.expected_rows),
        schema=list(case.expected_columns),
        orient="row",
    )

    text_columns = [
        name for name, dtype in expected.schema.items() if dtype == pl.String
    ]
    assert text_columns, "the fixture must contain text to check"
    for name in text_columns:
        assert frame[name].to_list() == expected[name].to_list(), name


def test_an_accented_producer_survives_the_excel_download(client) -> None:
    case = CASES[1]
    run = _start_run(client, case, "fixture.csv", case.csv_payload())

    frame = _read_downloaded_xlsx(
        _download(client, run["run_id"], case, "xlsx").content
    )

    assert frame["Producer"].to_list() == [
        "Château Margaux",
        "Château Margaux",
        "Château Margaux",
        "Château Margaux",
        "Bodega Catena Zapata",
        "Bodega Catena Zapata",
        "Domaine Père et Fils",
        "Domaine Pere et Fils",
    ]


# ---------------------------------------------------------------------------
# XLSX upload -> both downloads.  A known workbook goes in; the same output
# must come out as it does from the equivalent CSV.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_an_xlsx_upload_produces_the_expected_csv_download(
    client, case: Case
) -> None:
    run = _start_run(client, case, "fixture.xlsx", case.xlsx_payload())

    assert run["inputs"][0]["parser_engine"] == "fastexcel-calamine"
    assert run["inputs"][0]["worksheet"] == "Data"

    response = _download(client, run["run_id"], case, "csv")
    frame = pl.read_csv(io.BytesIO(response.content))

    assert tuple(frame.columns) == case.expected_columns
    assert frame.height == len(case.expected_rows)
    assert _numbers_as_floats(frame.rows()) == _numbers_as_floats(
        case.expected_rows
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_an_xlsx_upload_round_trips_back_through_xlsx(
    client, case: Case
) -> None:
    run = _start_run(client, case, "fixture.xlsx", case.xlsx_payload())

    frame = _read_downloaded_xlsx(
        _download(client, run["run_id"], case, "xlsx").content
    )

    assert tuple(frame.columns) == case.expected_columns
    assert frame.height == len(case.expected_rows)
    assert _numbers_as_floats(frame.rows()) == _numbers_as_floats(
        case.expected_rows
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_a_csv_and_an_xlsx_upload_of_the_same_data_agree(
    client, case: Case
) -> None:
    from_csv = _start_run(client, case, "fixture.csv", case.csv_payload())
    from_xlsx = _start_run(client, case, "fixture.xlsx", case.xlsx_payload())

    assert from_csv["metrics"] == from_xlsx["metrics"]
    assert from_csv["outputs"][0]["row_count"] == from_xlsx["outputs"][0]["row_count"]
    assert from_csv["outputs"][0]["columns"] == from_xlsx["outputs"][0]["columns"]


# ---------------------------------------------------------------------------
# The preview serves the same values the exports do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_preview_returns_the_expected_output(client, case: Case) -> None:
    run = _start_run(client, case, "fixture.csv", case.csv_payload())

    response = client.get(
        f"/api/runs/{run['run_id']}/outputs/{case.output_id}/preview"
    )

    assert response.status_code == 200
    payload = response.json()
    assert tuple(payload["columns"]) == case.expected_columns
    assert payload["total_rows"] == len(case.expected_rows)
    assert [tuple(row) for row in payload["rows"]] == list(case.expected_rows)