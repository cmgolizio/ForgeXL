"""Parser service tests (build plan 3.4-3.6 and section 17)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.errors import (
    AmbiguousWorkbookError,
    FileParseError,
    UnsupportedExtensionError,
)
from app.services import parser

from tests.helpers import csv_bytes, xlsx_bytes


def _write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


# ---------------------------------------------------------------------------
# 3.4 Dispatch and supported extensions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", [".xlsm", ".xlsb", ".xls", ".ods", ".json", ".parquet", ".txt", ""])
def test_unsupported_extensions_are_rejected(tmp_path: Path, extension: str) -> None:
    path = _write(tmp_path, f"data{extension or '_none'}", b"a,b\n1,2\n")

    with pytest.raises(UnsupportedExtensionError) as raised:
        parser.parse_tabular_file(path, extension)

    assert raised.value.http_status == 422
    assert raised.value.details["supported_extensions"] == [".csv", ".xlsx"]


def test_supported_extensions_are_exactly_csv_and_xlsx() -> None:
    assert parser.SUPPORTED_EXTENSIONS == (".csv", ".xlsx")


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = _write(tmp_path, "data.csv", csv_bytes(["a"], [[1]]))

    parsed = parser.parse_tabular_file(path, ".CSV")

    assert parsed.row_count == 1


# ---------------------------------------------------------------------------
# 3.5 CSV
# ---------------------------------------------------------------------------


def test_a_csv_file_parses_with_its_shape_recorded(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "data.csv", csv_bytes(["SKU", "Volume"], [["A1", 750], ["A2", 1500]])
    )

    parsed = parser.parse_tabular_file(path, ".csv")

    assert parsed.parser_engine == parser.ENGINE_POLARS_CSV
    assert parsed.worksheet is None
    assert parsed.columns == ("SKU", "Volume")
    assert parsed.row_count == 2
    assert parsed.column_count == 2


def test_a_header_only_csv_is_a_valid_dataset_with_no_rows(tmp_path: Path) -> None:
    path = _write(tmp_path, "data.csv", csv_bytes(["A", "B"], []))

    parsed = parser.parse_tabular_file(path, ".csv")

    assert parsed.row_count == 0
    assert parsed.columns == ("A", "B")


def test_a_completely_empty_csv_is_reported_as_a_parse_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "data.csv", b"")

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_file(path, ".csv")

    assert raised.value.code == "PARSE_ERROR"


def test_quoted_commas_and_accents_survive_parsing(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "data.csv",
        csv_bytes(["Producer", "Note"], [["Château Margaux", "a,b"]]),
    )

    parsed = parser.parse_tabular_file(path, ".csv")

    assert parsed.frame.rows() == [("Château Margaux", "a,b")]


def test_date_shaped_text_is_not_silently_retyped(tmp_path: Path) -> None:
    """Build plan 3.3: no value is silently converted into another type."""
    path = _write(tmp_path, "data.csv", csv_bytes(["d"], [["2024-01-05"]]))

    parsed = parser.parse_tabular_file(path, ".csv")

    assert str(parsed.frame.dtypes[0]) == "String"
    assert parsed.frame.rows() == [("2024-01-05",)]


# ---------------------------------------------------------------------------
# 3.6 XLSX
# ---------------------------------------------------------------------------


def test_a_single_sheet_workbook_parses_and_records_its_worksheet(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "book.xlsx",
        xlsx_bytes({"Sales": [["SKU", "Volume"], ["A1", 750], ["A2", 1500]]}),
    )

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_FASTEXCEL
    assert parsed.worksheet == "Sales"
    assert parsed.columns == ("SKU", "Volume")
    assert parsed.row_count == 2


def test_a_workbook_with_one_data_sheet_and_blank_sheets_is_unambiguous(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "book.xlsx",
        xlsx_bytes({"Blank": [], "Data": [["A"], [1]], "AlsoBlank": []}),
    )

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.worksheet == "Data"


def test_a_workbook_with_several_data_sheets_is_refused(tmp_path: Path) -> None:
    """Build plan section 17: never silently pick one of several data sheets."""
    path = _write(
        tmp_path,
        "book.xlsx",
        xlsx_bytes({"Sales": [["A"], [1]], "Notes": [["B"], [2]]}),
    )

    with pytest.raises(AmbiguousWorkbookError) as raised:
        parser.parse_tabular_file(path, ".xlsx")

    assert raised.value.http_status == 422
    assert set(raised.value.details["worksheets_with_data"]) == {"Sales", "Notes"}
    assert "one data worksheet" in raised.value.message


def test_a_workbook_with_no_data_at_all_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "book.xlsx", xlsx_bytes({"Empty": [], "AlsoEmpty": []}))

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_file(path, ".xlsx")

    assert not isinstance(raised.value, AmbiguousWorkbookError)
    assert raised.value.code == "PARSE_ERROR"


def test_a_header_only_worksheet_is_a_valid_dataset_with_no_rows(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "book.xlsx", xlsx_bytes({"Data": [["A", "B"]]}))

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.row_count == 0
    assert parsed.columns == ("A", "B")


def test_a_file_that_is_not_a_workbook_is_reported_not_guessed(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "book.xlsx", b"this is not a workbook")

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_file(path, ".xlsx")

    # Both engines were tried and both are named in the error.
    assert raised.value.details["primary_engine"] == parser.ENGINE_FASTEXCEL
    assert raised.value.details["fallback_engine"] == parser.ENGINE_OPENPYXL


def test_accented_workbook_values_are_not_normalised(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "book.xlsx",
        xlsx_bytes({"Data": [["Producer"], ["Château Margaux"], ["Bodegas Muñoz"]]}),
    )

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.frame.rows() == [("Château Margaux",), ("Bodegas Muñoz",)]


def test_a_workbook_containing_a_formula_reads_its_stored_value(
    tmp_path: Path,
) -> None:
    """Build plan 7F: read stored values; never evaluate or execute anything."""
    import xlsxwriter

    path = tmp_path / "formula.xlsx"
    workbook = xlsxwriter.Workbook(str(path))
    sheet = workbook.add_worksheet("Data")
    sheet.write_row(0, 0, ["a", "b"])
    sheet.write_number(1, 0, 2)
    sheet.write_formula(1, 1, "=A2*3", None, 6)
    workbook.close()

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.frame.rows() == [(2, 6)]


# ---------------------------------------------------------------------------
# 3.6 Compatibility fallback — the engine used is always recorded
# ---------------------------------------------------------------------------


def test_the_fallback_engine_is_used_and_recorded_when_the_primary_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build plan section 6.2: an engine switch is never silent."""
    path = _write(tmp_path, "book.xlsx", xlsx_bytes({"Data": [["A", "B"], [1, "x"]]}))

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("preferred engine unavailable")

    monkeypatch.setattr(parser.fastexcel, "read_excel", _fail)

    parsed = parser.parse_tabular_file(path, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_OPENPYXL
    assert parsed.worksheet == "Data"
    assert parsed.columns == ("A", "B")
    assert parsed.frame.rows() == [(1, "x")]


def test_worksheet_ambiguity_is_not_retried_with_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A structural refusal must not be turned into a different answer."""
    path = _write(
        tmp_path,
        "book.xlsx",
        xlsx_bytes({"One": [["A"], [1]], "Two": [["B"], [2]]}),
    )
    calls: list[str] = []

    real_load = parser._parse_xlsx_with_openpyxl

    def _record(target: Path) -> object:
        calls.append("fallback")
        return real_load(target)

    monkeypatch.setattr(parser, "_parse_xlsx_with_openpyxl", _record)

    with pytest.raises(AmbiguousWorkbookError):
        parser.parse_tabular_file(path, ".xlsx")

    assert calls == []


def test_a_corrupt_zip_container_is_reported_by_both_engines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "book.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("not-a-workbook.txt", "nothing useful")

    with pytest.raises(FileParseError):
        parser.parse_tabular_file(path, ".xlsx")