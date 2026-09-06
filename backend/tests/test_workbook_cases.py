"""Excel workbook cases, over the real API (build plan 7F).

Build plan 7F names the list:

    normal single-sheet XLSX
    empty XLSX
    workbook with multiple plausible data sheets
    workbook without required columns
    workbook containing formulas

with two rules attached: **do not execute macros**, and **read stored workbook
values according to the parser's safe behaviour**.

Both rules hold for a reason worth stating, because it is stronger than
"macros are disabled": ForgeXL never opens a file that can carry one. Build
plan section 16 accepts `.csv` and `.xlsx` only and rejects `.xlsm` and
`.xlsb` by extension, before any engine sees the bytes — and `.xlsx` is by
definition the macro-free member of the family. There is no macro setting to
get wrong. The tests below check the refusal at the door and then check that
what *is* read is read as stored: a formula cell yields the value Excel last
saved for it, and the formula text is never evaluated, never executed and
never written back out as a formula.

`tests/test_parser.py` covers these at the parser level. This module is about
what a user gets: the status, the error code, the message and the values, from
`POST /api/runs` through preview and both downloads.
"""

from __future__ import annotations

import io

import openpyxl
import pytest
import xlsxwriter

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import (
    PRODUCT_COLUMNS,
    ProductMasterBuilderAction,
)
from app.services import parser

from tests.fixtures import spreadsheets as fx
from tests.helpers import upload_file

DEDUPE = ExactDuplicateRemoverAction.id
DEDUPE_OUTPUT = "deduplicated_data"
PRODUCT_MASTER = ProductMasterBuilderAction.id


def _run(client, payload: bytes, filename: str = "book.xlsx", action=DEDUPE):
    slot = "source_file" if action == DEDUPE else "sales_file"
    return client.post(
        "/api/runs",
        data={"action_id": action},
        files={slot: upload_file(filename, payload)},
    )


def _rows(client, run_id: str) -> list[tuple[object, ...]]:
    page = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/preview", params={"limit": 500}
    )
    assert page.status_code == 200, page.text
    return [tuple(row) for row in page.json()["rows"]]


def _workbook(build) -> bytes:
    """Build a workbook with xlsxwriter, so a test can write cells directly.

    The fixture system writes values; these cases need control over *how* a
    cell is written — as a formula, with a cached result, with a defined name —
    which is a level below what a fixture expresses.
    """
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    build(workbook)
    workbook.close()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Normal single-sheet workbook
# ---------------------------------------------------------------------------


def test_a_normal_single_sheet_workbook_runs(client) -> None:
    """The control case: one worksheet, ordinary values, nothing unusual."""
    response = _run(client, fx.SIMPLE_TABLE.as_xlsx())

    assert response.status_code == 200, response.text
    manifest = response.json()
    recorded = manifest["inputs"][0]

    assert recorded["extension"] == ".xlsx"
    assert recorded["parser_engine"] == parser.ENGINE_FASTEXCEL
    assert recorded["worksheet"] == fx.DEFAULT_WORKSHEET
    assert tuple(recorded["columns"]) == fx.SIMPLE_TABLE.header
    assert manifest["outputs"][0]["row_count"] == fx.SIMPLE_TABLE.row_count


def test_the_worksheet_name_is_recorded_whatever_it_is_called(client) -> None:
    """Build plan 3.6: which worksheet the rows came from is part of the audit."""
    response = _run(client, fx.SIMPLE_TABLE.as_xlsx(worksheet="Q3 Extract"))

    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["worksheet"] == "Q3 Extract"


# ---------------------------------------------------------------------------
# Empty workbook
# ---------------------------------------------------------------------------


def test_an_empty_workbook_is_refused_with_a_readable_reason(client) -> None:
    """A workbook whose only worksheet holds nothing at all."""
    response = _run(client, fx.EMPTY_WORKBOOK.as_xlsx(), fx.EMPTY_WORKBOOK.filename())

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PARSE_ERROR"
    assert "no data" in error["message"].lower()


def test_a_workbook_with_a_header_and_no_rows_is_an_empty_dataset(client) -> None:
    """Different from the case above, and told apart from it.

    A header row is data — the file says what its columns are — so this is a
    dataset with zero rows rather than an empty workbook, and the user needs a
    different thing from each.
    """
    response = _run(client, fx.HEADER_ONLY.as_xlsx())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_DATASET"


def test_a_zero_byte_file_named_xlsx_is_an_empty_file(client) -> None:
    response = _run(client, b"")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_bytes_that_are_not_a_workbook_are_refused_by_both_engines(
    client,
) -> None:
    """Build plan 3.5 and section 6.2: the failure names both engines tried."""
    response = _run(client, b"Region,Units\nNorth,10\n", "actually-csv.xlsx")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PARSE_ERROR"
    assert error["details"]["primary_engine"] == parser.ENGINE_FASTEXCEL
    assert error["details"]["fallback_engine"] == parser.ENGINE_OPENPYXL


# ---------------------------------------------------------------------------
# Several plausible data sheets (build plan section 17)
# ---------------------------------------------------------------------------


def test_a_workbook_with_two_data_sheets_is_refused_not_guessed(client) -> None:
    """Build plan section 17: ForgeXL never picks a worksheet for the user.

    The message is the one the build plan writes, because a refusal the user
    cannot act on is only half of the requirement.
    """
    response = _run(
        client, fx.MULTIPLE_WORKSHEETS.as_xlsx(), fx.MULTIPLE_WORKSHEETS.filename()
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "AMBIGUOUS_WORKBOOK"
    assert error["details"]["worksheets_with_data"] == ["January", "February"]
    assert "one data worksheet" in error["message"]


def test_one_data_sheet_beside_blank_ones_is_unambiguous(client) -> None:
    """An empty sheet is not a plausible data sheet, so this is not ambiguous."""
    response = _run(
        client,
        fx.ONE_DATA_SHEET_AMONG_BLANKS.as_xlsx(),
        fx.ONE_DATA_SHEET_AMONG_BLANKS.filename(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["worksheet"] == "Data"


def test_a_second_sheet_holding_one_cell_is_refused(client) -> None:
    """The conservative reading, stated as a test rather than left implicit.

    A one-cell "Notes" tab makes the workbook ambiguous. That is a real cost
    to a real user, and it is the cost build plan section 17 chooses: the
    alternative is picking a sheet and being wrong silently. Recorded as Known
    Issue 12; pinned here so a change to it is a decision.
    """
    payload = fx.Workbook(
        name="data-plus-a-note",
        description="A data sheet and a one-cell notes tab.",
        sheets=(
            ("Data", (("Region", "Units"), ("North", 10))),
            ("Notes", (("checked by Ana",),)),
        ),
    ).as_xlsx()

    response = _run(client, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AMBIGUOUS_WORKBOOK"


# ---------------------------------------------------------------------------
# Workbook without required columns
# ---------------------------------------------------------------------------


def test_a_workbook_without_the_required_columns_is_refused(client) -> None:
    """The 7C rule, reached through a workbook rather than through a CSV.

    Worth its own test: the file parses perfectly, so the refusal comes from
    the runner's column check rather than from any workbook problem.
    """
    response = _run(
        client,
        fx.MISSING_REQUIRED_COLUMNS.as_xlsx(),
        fx.MISSING_REQUIRED_COLUMNS.filename(".xlsx"),
        action=PRODUCT_MASTER,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == [
        "SKU",
        "Vintage",
        "Producer",
        "Selection",
        "Volume",
    ]
    # The workbook was read successfully; the refusal is about its schema.
    assert error["details"]["found_columns"] == list(
        fx.MISSING_REQUIRED_COLUMNS.header
    )


def test_a_workbook_with_the_required_columns_builds_a_product_master(
    client,
) -> None:
    """The control for the test above."""
    response = _run(
        client,
        fx.EXTRA_COLUMNS.as_xlsx(),
        fx.EXTRA_COLUMNS.filename(".xlsx"),
        action=PRODUCT_MASTER,
    )

    assert response.status_code == 200, response.text
    assert tuple(response.json()["outputs"][0]["columns"]) == PRODUCT_COLUMNS


# ---------------------------------------------------------------------------
# Formulas (build plan 7F: read stored values, execute nothing)
# ---------------------------------------------------------------------------


def test_a_formula_cell_reads_the_value_excel_stored_for_it(client) -> None:
    """Build plan 7F: stored workbook values, per the parser's safe behaviour.

    The cached results here are deliberately *wrong* arithmetic — `2+3` is
    cached as `99`. If the reader evaluated the formula it would return 5, and
    the test would fail. It returns 99, which proves the value came out of the
    file rather than out of a calculation.
    """

    def build(workbook):
        sheet = workbook.add_worksheet("Data")
        sheet.write_row(0, 0, ["a", "b", "total"])
        sheet.write_row(1, 0, [2, 3])
        sheet.write_formula(1, 2, "=A2+B2", None, 99)
        sheet.write_row(2, 0, [4, 5])
        sheet.write_formula(2, 2, "=A3+B3", None, 77)

    response = _run(client, _workbook(build))

    assert response.status_code == 200, response.text
    rows = _rows(client, response.json()["run_id"])

    assert rows == [(2.0, 3.0, 99.0), (4.0, 5.0, 77.0)]


def test_a_formula_is_never_returned_as_formula_text(client) -> None:
    """The value is what the user sees; the formula text is not data."""

    def build(workbook):
        sheet = workbook.add_worksheet("Data")
        sheet.write_row(0, 0, ["label", "value"])
        sheet.write_string(1, 0, "computed")
        sheet.write_formula(1, 1, "=1+1", None, 2)

    response = _run(client, _workbook(build))

    assert response.status_code == 200, response.text
    assert "=1+1" not in response.text
    assert _rows(client, response.json()["run_id"]) == [("computed", 2.0)]


def test_text_that_looks_like_a_formula_stays_text_through_both_exports(
    client,
) -> None:
    """Build plan section 16, in both directions at once.

    A cell whose text begins with `=` — including the DDE form that some
    spreadsheet applications will offer to execute — is data. It is read as
    text, and written back as text, so downloading the result and opening it
    cannot produce a formula that was never in the user's file.
    """
    dangerous = (
        "=SUM(A1:A9)",
        "=cmd|' /C calc'!A0",
        "+1+1",
        "-1-1",
        "@SUM(1)",
    )

    def build(workbook):
        sheet = workbook.add_worksheet("Data")
        sheet.write_row(0, 0, ["payload"])
        for index, value in enumerate(dangerous, start=1):
            # Written as a string explicitly: xlsxwriter's default would turn
            # each of these into a real formula, which is the thing being
            # tested against.
            sheet.write_string(index, 0, value)

    response = _run(client, _workbook(build))
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]

    assert _rows(client, run_id) == [(value,) for value in dangerous]

    exported = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/xlsx"
    )
    assert exported.status_code == 200

    # Reopened with openpyxl in formula mode: a cell that had become a formula
    # would show its formula text here, and none does.
    workbook = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=False)
    sheet = workbook[workbook.sheetnames[0]]
    written = [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)]
    workbook.close()

    assert written == list(dangerous)
    assert all(cell.data_type != "f" for row in sheet.iter_rows() for cell in row)


def test_the_exported_workbook_declares_no_macro_content(client) -> None:
    """An `.xlsx` cannot carry a macro, and what ForgeXL writes is an `.xlsx`.

    Checked structurally rather than trusted: a macro-enabled workbook is a
    different content type and carries a `vbaProject.bin` part, and neither
    appears in anything this application produces.
    """
    import zipfile

    response = _run(client, fx.SIMPLE_TABLE.as_xlsx())
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]

    exported = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/xlsx"
    )
    assert exported.status_code == 200

    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        names = archive.namelist()
        content_types = archive.read("[Content_Types].xml").decode()

    assert not any("vbaProject" in name for name in names)
    assert "macroEnabled" not in content_types


@pytest.mark.parametrize("extension", [".xlsm", ".xlsb", ".xls", ".ods"])
def test_a_macro_capable_workbook_format_is_refused_by_extension(
    client, extension: str
) -> None:
    """Build plan section 16: refused before any engine opens the bytes.

    This is why "do not execute macros" needs no macro setting anywhere. The
    payload here is a perfectly valid workbook; the extension alone refuses it.
    """
    response = _run(client, fx.SIMPLE_TABLE.as_xlsx(), f"macros{extension}")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED_EXTENSION"
    assert error["details"]["accepted_extensions"] == [".csv", ".xlsx"]


# ---------------------------------------------------------------------------
# Mixed cell types (the pre-6I repair, through the API)
# ---------------------------------------------------------------------------


def test_a_mixed_type_column_is_preserved_as_text_with_a_visible_warning(
    client,
) -> None:
    """Known Issue 65's repair, asserted end to end.

    Every one of the six values survives, the engine that read them is
    recorded, and the user is told the column was preserved as text rather
    than left to discover it.
    """
    response = _run(client, fx.MIXED_VALUES.as_xlsx())

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert manifest["inputs"][0]["parser_engine"] == parser.ENGINE_OPENPYXL
    warnings = {warning["code"] for warning in manifest["validation"]["warnings"]}
    assert "MIXED_COLUMN_TYPES" in warnings

    values = [row[1] for row in _rows(client, manifest["run_id"])]
    assert values == ["10", "n/a", "2.5", None, "-3", "0"]


def test_a_numeric_only_column_keeps_its_type(client) -> None:
    """The control: the fallback is not applied to a column that does not need it."""
    response = _run(client, fx.SIMPLE_TABLE.as_xlsx())

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert manifest["inputs"][0]["parser_engine"] == parser.ENGINE_FASTEXCEL
    assert manifest["validation"]["warnings"] == []
    kinds = {
        entry["name"]: entry["kind"]
        for entry in client.get(
            f"/api/runs/{manifest['run_id']}/outputs/{DEDUPE_OUTPUT}/preview"
        ).json()["column_schema"]
    }
    assert kinds["Units"] == "number"