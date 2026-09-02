"""Phase 6F completion criteria — exports over the real HTTP API.

Build plan 6F lists the battery this module runs, end to end and in order:

1. execute an Action
2. request CSV
3. read the returned CSV bytes
4. verify the expected content
5. request XLSX
6. reopen the returned XLSX from memory
7. verify the worksheet names
8. verify the headers
9. verify representative values

`test_action_round_trip.py` already proves the two real Actions' exports carry
the right values; this module proves the *download contract* around them —
in-memory generation, the whole-Run workbook of 6F.4, the naming rules of 6F.5
and 6F.6, the release rule of 6F.7 and the no-server-paths rule of 6F.8.

Every workbook is reopened from the response bytes with the application's own
Excel engine, so an export ForgeXL could not itself ingest fails here. Nothing
is written to disk at any point, and the ``runs_dir`` fixture is asserted empty
to prove it.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import fastexcel
import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.models.run import new_run_id
from app.models.schemas import ActionInput, ActionOutput
from app.services import export, run_store

from tests.helpers import csv_bytes, upload_file

HEADER = ("SKU", "Vintage", "Producer", "Price")
ROWS = (
    ("A1", 2019, "Château Margaux", 0.000123),
    ("A2", 2020, "Bodegas Muñoz", 1234.56789),
    ("A3", 2021, "Domaine Père et Fils", 42.5),
)


def _sales_csv() -> bytes:
    return csv_bytes(HEADER, ROWS)


class _SplitAction(Action):
    """An Action with two result tables, for the multi-table rules of 6F.4.

    Deliberately not one of the real Actions: this module is about the export
    contract, and neither proof Action declares a second output.
    """

    id = "split_rows"
    version = "1.0.0"
    name = "Split Rows"
    description = "Splits its input into a kept table and a rejected table."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
    )
    outputs = (
        ActionOutput(id="kept_rows", label="Kept Rows"),
        ActionOutput(id="rejected_rows", label="Rejected Rows"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        frame = inputs["source_file"]
        return ActionResult(
            outputs={"kept_rows": frame.head(2), "rejected_rows": frame.tail(1)}
        )


class _CollidingLabelsAction(Action):
    """Two outputs whose labels are the same word in different cases.

    Excel compares worksheet names case-insensitively, so this is the case that
    would produce an unopenable workbook without 6F.5's collision rule.
    """

    id = "colliding_labels"
    version = "1.0.0"
    name = "Colliding Labels"
    description = "Produces two outputs whose labels collide in Excel."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (
        ActionOutput(id="first", label="Results: 2026/2027"),
        ActionOutput(id="second", label="RESULTS: 2026/2027"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        frame = inputs["source_file"]
        return ActionResult(outputs={"first": frame, "second": frame.head(1)})


@pytest.fixture
def split_client(client, registered_actions):
    registered_actions(_SplitAction(), _CollidingLabelsAction())
    return client


def _run(client, action_id: str = "split_rows") -> dict:
    response = client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("sales.csv", _sales_csv())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _filename(response) -> str:
    """The name a download is offered under, read out of the header."""
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="'), disposition
    assert disposition.endswith('"'), disposition
    return disposition[len('attachment; filename="') : -1]


def _sheet_names(payload: bytes) -> list[str]:
    """Worksheet names in workbook order, reopened from the response bytes."""
    return list(fastexcel.read_excel(payload).sheet_names)


def _sheet(payload: bytes, worksheet: str) -> pl.DataFrame:
    """One worksheet, read back with the application's own Excel engine."""
    reader = fastexcel.read_excel(payload)
    return reader.load_sheet(worksheet, header_row=0).to_polars()


# ---------------------------------------------------------------------------
# The completion-criteria battery, single result table
# ---------------------------------------------------------------------------


def test_a_run_downloads_as_csv_with_the_expected_content(
    client, registered_actions
) -> None:
    """Steps 1-4: execute, request CSV, read the bytes, verify the content."""
    registered_actions(_SplitAction())
    run = _run(client)

    response = client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/csv"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    frame = pl.read_csv(io.BytesIO(response.content))
    assert tuple(frame.columns) == HEADER
    assert frame.rows() == [ROWS[0], ROWS[1]]


def test_a_run_downloads_as_a_workbook_with_the_expected_content(
    client, registered_actions
) -> None:
    """Steps 5-9: request XLSX, reopen it, check names, headers and values."""
    registered_actions(_SplitAction())
    run = _run(client)

    response = client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/xlsx"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert _sheet_names(response.content) == ["Kept Rows"]

    sheet = _sheet(response.content, "Kept Rows")
    assert tuple(sheet.columns) == HEADER
    assert sheet["SKU"].to_list() == ["A1", "A2"]
    assert sheet["Producer"].to_list() == ["Château Margaux", "Bodegas Muñoz"]
    assert sheet["Price"].to_list() == [0.000123, 1234.56789]


def test_a_downloaded_workbook_is_a_valid_workbook_file(
    client, registered_actions
) -> None:
    registered_actions(_SplitAction())
    run = _run(client)

    response = client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/xlsx"
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert "xl/workbook.xml" in names
    assert "[Content_Types].xml" in names


# ---------------------------------------------------------------------------
# 6F.4 Several result tables in one workbook
# ---------------------------------------------------------------------------


def test_the_whole_run_downloads_as_one_workbook_per_result_table(
    split_client,
) -> None:
    run = _run(split_client)

    response = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert response.status_code == 200
    assert _sheet_names(response.content) == ["Kept Rows", "Rejected Rows"]


def test_each_worksheet_of_the_run_workbook_holds_its_own_table(
    split_client,
) -> None:
    run = _run(split_client)

    response = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert _sheet(response.content, "Kept Rows")["SKU"].to_list() == ["A1", "A2"]
    assert _sheet(response.content, "Rejected Rows")["SKU"].to_list() == ["A3"]


def test_the_run_workbook_lists_worksheets_in_declaration_order(
    split_client,
) -> None:
    """The Action's first declared output is its primary result, so it leads."""
    run = _run(split_client)

    response = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert _sheet_names(response.content)[0] == run["outputs"][0]["label"]


def test_the_run_workbook_of_a_single_output_action_has_one_worksheet(
    client, registered_actions
) -> None:
    from tests.helpers import make_action

    registered_actions(make_action("passthrough"))
    run = _run(client, action_id="passthrough")

    response = client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert response.status_code == 200
    assert _sheet_names(response.content) == ["Result"]


def test_the_run_workbook_and_the_per_output_download_agree(
    split_client,
) -> None:
    run = _run(split_client)

    workbook = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")
    single = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/rejected_rows/download/xlsx"
    )

    assert _sheet(workbook.content, "Rejected Rows").equals(
        _sheet(single.content, "Rejected Rows")
    )


def test_an_unknown_run_has_no_workbook(split_client) -> None:
    response = split_client.get(f"/api/runs/{new_run_id()}/download/xlsx")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


def test_a_run_whose_result_has_been_released_has_no_workbook(
    split_client,
) -> None:
    run_id = _run(split_client)["run_id"]
    stored = run_store.get_run(run_id)
    run_store.update_run(stored.with_changes(result=None))

    response = split_client.get(f"/api/runs/{run_id}/download/xlsx")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


def test_a_failed_run_has_no_workbook(split_client) -> None:
    """A failed Run keeps no partial result, so there is nothing to export."""
    failed = split_client.post(
        "/api/runs",
        data={"action_id": "split_rows"},
        files={"source_file": upload_file("empty.csv", b"")},
    )
    assert failed.status_code == 422

    run_id = next(iter(run_store.list_runs())).run_id
    response = split_client.get(f"/api/runs/{run_id}/download/xlsx")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


# ---------------------------------------------------------------------------
# 6F.5 Worksheet names, over HTTP
# ---------------------------------------------------------------------------


def test_a_worksheet_is_named_after_the_output_label(split_client) -> None:
    """"Kept Rows" reads better in Excel than the internal ``kept_rows``."""
    run = _run(split_client)

    response = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/xlsx"
    )

    assert _sheet_names(response.content) == ["Kept Rows"]


def test_labels_excel_would_reject_still_produce_an_openable_workbook(
    split_client,
) -> None:
    """Slashes and colons are invalid in a sheet name; duplicates are fatal."""
    run = _run(split_client, action_id="colliding_labels")

    response = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    names = _sheet_names(response.content)
    assert names == ["Results 2026 2027", "RESULTS 2026 2027 2"]
    assert len({name.casefold() for name in names}) == 2
    assert all(
        not (set(name) & export.INVALID_WORKSHEET_CHARACTERS) for name in names
    )


# ---------------------------------------------------------------------------
# 6F.6 Download information
# ---------------------------------------------------------------------------


def test_a_download_is_named_by_the_forgexl_convention(split_client) -> None:
    run = _run(split_client)

    response = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/csv"
    )

    assert _filename(response) == export.download_filename(
        action_id="split_rows",
        output_id="kept_rows",
        extension="csv",
        timestamp=datetime.fromisoformat(run["completed_at"]),
    )


def test_the_run_workbook_is_named_for_the_run_not_an_output(
    split_client,
) -> None:
    run = _run(split_client)

    response = split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert _filename(response) == export.download_filename(
        action_id="split_rows",
        extension="xlsx",
        timestamp=datetime.fromisoformat(run["completed_at"]),
    )
    assert "kept" not in _filename(response)


def test_two_outputs_of_one_run_download_under_different_names(
    split_client,
) -> None:
    run = _run(split_client)

    kept = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/kept_rows/download/csv"
    )
    rejected = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/rejected_rows/download/csv"
    )

    assert _filename(kept) != _filename(rejected)


def test_re_downloading_an_output_offers_the_same_filename(
    split_client,
) -> None:
    """The name is derived from the Run, so it does not drift between requests."""
    run = _run(split_client)
    url = f"/api/runs/{run['run_id']}/outputs/kept_rows/download/csv"

    assert _filename(split_client.get(url)) == _filename(split_client.get(url))


@pytest.mark.parametrize(
    "path_suffix", ["outputs/kept_rows/download/csv", "download/xlsx"]
)
def test_every_download_filename_is_safe_to_send(
    split_client, path_suffix: str
) -> None:
    run = _run(split_client)

    response = split_client.get(f"/api/runs/{run['run_id']}/{path_suffix}")

    filename = _filename(response)
    assert re.fullmatch(r"[a-z0-9-]+\.(csv|xlsx)", filename), filename


def test_a_hostile_upload_filename_never_reaches_the_download_name(
    split_client,
) -> None:
    """Build plan section 16: a client filename stays metadata, always."""
    response = split_client.post(
        "/api/runs",
        data={"action_id": "split_rows"},
        files={"source_file": upload_file('../../"evil".csv', _sales_csv())},
    )
    run_id = response.json()["run_id"]

    download = split_client.get(
        f"/api/runs/{run_id}/outputs/kept_rows/download/csv"
    )

    filename = _filename(download)
    assert "evil" not in filename
    assert ".." not in filename
    assert filename.startswith(f"{export.FILENAME_PREFIX}-split-rows-kept-rows-")


# ---------------------------------------------------------------------------
# 6F.7 Nothing is retained, 6F.8 nothing names a server location
# ---------------------------------------------------------------------------


def test_downloading_writes_nothing_to_the_filesystem(
    split_client, runs_dir: Path
) -> None:
    run = _run(split_client)

    split_client.get(f"/api/runs/{run['run_id']}/outputs/kept_rows/download/csv")
    split_client.get(f"/api/runs/{run['run_id']}/outputs/kept_rows/download/xlsx")
    split_client.get(f"/api/runs/{run['run_id']}/download/xlsx")

    assert list(runs_dir.rglob("*")) == []


def test_a_run_holds_no_rendered_export_after_a_download(split_client) -> None:
    """Build plan 6F.7: the bytes go out with the response and are not kept.

    A Run carries its result frames and nothing else; if a rendered export were
    being cached anywhere, downloading would leave it on the Run.
    """
    run_id = _run(split_client)["run_id"]
    before = run_store.get_run(run_id)

    split_client.get(f"/api/runs/{run_id}/outputs/kept_rows/download/xlsx")
    split_client.get(f"/api/runs/{run_id}/download/xlsx")

    after = run_store.get_run(run_id)
    assert after == before
    assert set(vars(after)) == set(vars(before))


def test_repeated_downloads_return_the_same_data(split_client) -> None:
    """Generating on demand is as stable as serving a stored file would be."""
    run_id = _run(split_client)["run_id"]
    url = f"/api/runs/{run_id}/outputs/kept_rows/download/csv"

    assert split_client.get(url).content == split_client.get(url).content


@pytest.mark.parametrize(
    "path_suffix",
    [
        "",
        "outputs/kept_rows/preview",
        "outputs/kept_rows/download/csv",
        "outputs/kept_rows/download/xlsx",
        "download/xlsx",
    ],
)
def test_no_response_names_a_server_location(
    split_client, path_suffix: str
) -> None:
    """Build plan 6F.8: no local path appears in any header or body."""
    run = _run(split_client)
    path = f"/api/runs/{run['run_id']}"
    response = split_client.get(f"{path}/{path_suffix}" if path_suffix else path)

    assert response.status_code == 200
    text = " ".join(
        [*(f"{key}: {value}" for key, value in response.headers.items())]
    )
    if response.headers["content-type"].startswith(("application/json", "text/csv")):
        text = f"{text} {response.text}"

    for marker in ("/Users/", "/home/", "/tmp", "data/runs", "\\Users\\"):
        assert marker not in text, f"{marker!r} leaked in {path_suffix or 'manifest'}"


def test_a_download_error_names_no_server_location(split_client) -> None:
    run = _run(split_client)

    response = split_client.get(
        f"/api/runs/{run['run_id']}/outputs/nope/download/csv"
    )

    assert response.status_code == 404
    for marker in ("/Users/", "/home/", "/tmp", "data/runs"):
        assert marker not in response.text