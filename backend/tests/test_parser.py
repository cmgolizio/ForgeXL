"""Parser service tests (build plan 3.4-3.6, 6C.6-6C.7 and section 17).

Every test here parses **bytes**. Since Phase 6C the parser reads an upload
from memory, so nothing in this module writes a spreadsheet to disk in order
to read it back, and no OS file picker is involved anywhere (build plan 6C
completion criteria).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.errors import (
    AmbiguousWorkbookError,
    FileParseError,
    UnsupportedExtensionError,
)
from app.services import parser

from tests.helpers import csv_bytes, xlsx_bytes


# ---------------------------------------------------------------------------
# 3.4 Dispatch and supported extensions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extension",
    [".xlsm", ".xlsb", ".xls", ".ods", ".json", ".parquet", ".txt", ""],
)
def test_unsupported_extensions_are_rejected(extension: str) -> None:
    with pytest.raises(UnsupportedExtensionError) as raised:
        parser.parse_tabular_bytes(b"a,b\n1,2\n", extension)

    assert raised.value.http_status == 422
    assert raised.value.details["supported_extensions"] == [".csv", ".xlsx"]


def test_supported_extensions_are_exactly_csv_and_xlsx() -> None:
    assert parser.SUPPORTED_EXTENSIONS == (".csv", ".xlsx")


def test_extension_matching_is_case_insensitive() -> None:
    parsed = parser.parse_tabular_bytes(csv_bytes(["a"], [[1]]), ".CSV")

    assert parsed.row_count == 1


# ---------------------------------------------------------------------------
# 6C.6 CSV from memory
# ---------------------------------------------------------------------------


def test_csv_bytes_become_a_dataframe_with_their_shape_recorded() -> None:
    """Build plan 6C.6: CSV bytes -> parser -> Polars DataFrame."""
    payload = csv_bytes(["SKU", "Volume"], [["A1", 750], ["A2", 1500]])

    parsed = parser.parse_tabular_bytes(payload, ".csv")

    assert parsed.parser_engine == parser.ENGINE_POLARS_CSV
    assert parsed.worksheet is None
    assert parsed.columns == ("SKU", "Volume")
    assert parsed.row_count == 2
    assert parsed.column_count == 2
    assert parsed.frame.rows() == [("A1", 750), ("A2", 1500)]


def test_a_header_only_csv_is_a_valid_dataset_with_no_rows() -> None:
    parsed = parser.parse_tabular_bytes(csv_bytes(["A", "B"], []), ".csv")

    assert parsed.row_count == 0
    assert parsed.columns == ("A", "B")


def test_a_completely_empty_csv_is_reported_as_a_parse_error() -> None:
    """Zero bytes reaching the parser directly is still a refusal.

    The runner rejects an empty upload earlier, with `EMPTY_FILE`; this is the
    parser's own guard for a caller that did not.
    """
    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(b"", ".csv")

    assert raised.value.code == "PARSE_ERROR"


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("ragged rows", b"a,b\n1,2,3,4\n"),
        ("unterminated quote", b'a,b\n"unclosed,2\n'),
        ("invalid utf-8", b"a,b\n\xff\xfe,2\n"),
        ("blank lines only", b"\n\n\n"),
    ],
)
def test_an_unreadable_csv_is_reported_not_repaired(
    label: str, payload: bytes
) -> None:
    """Build plan 3.5: a parse failure is reported, never quietly worked around."""
    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".csv")

    assert raised.value.code == "PARSE_ERROR"
    assert "CSV" in raised.value.message


def test_quoted_commas_and_accents_survive_parsing() -> None:
    payload = csv_bytes(["Producer", "Note"], [["Château Margaux", "a,b"]])

    parsed = parser.parse_tabular_bytes(payload, ".csv")

    assert parsed.frame.rows() == [("Château Margaux", "a,b")]


def test_date_shaped_text_is_not_silently_retyped() -> None:
    """Build plan 3.3: no value is silently converted into another type."""
    parsed = parser.parse_tabular_bytes(csv_bytes(["d"], [["2024-01-05"]]), ".csv")

    assert str(parsed.frame.dtypes[0]) == "String"
    assert parsed.frame.rows() == [("2024-01-05",)]


# ---------------------------------------------------------------------------
# 6C.7 XLSX from memory
# ---------------------------------------------------------------------------


def test_xlsx_bytes_become_a_dataframe_with_their_worksheet_recorded() -> None:
    """Build plan 6C.7: XLSX bytes -> memory buffer -> Polars DataFrame."""
    payload = xlsx_bytes({"Sales": [["SKU", "Volume"], ["A1", 750], ["A2", 1500]]})

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_FASTEXCEL
    assert parsed.worksheet == "Sales"
    assert parsed.columns == ("SKU", "Volume")
    assert parsed.row_count == 2
    assert parsed.frame.rows() == [("A1", 750), ("A2", 1500)]


def test_a_workbook_with_one_data_sheet_and_blank_sheets_is_unambiguous() -> None:
    payload = xlsx_bytes({"Blank": [], "Data": [["A"], [1]], "AlsoBlank": []})

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.worksheet == "Data"


def test_a_workbook_with_several_data_sheets_is_refused() -> None:
    """Build plan section 17: never silently pick one of several data sheets."""
    payload = xlsx_bytes({"Sales": [["A"], [1]], "Notes": [["B"], [2]]})

    with pytest.raises(AmbiguousWorkbookError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert raised.value.http_status == 422
    assert set(raised.value.details["worksheets_with_data"]) == {"Sales", "Notes"}
    assert "one data worksheet" in raised.value.message


def test_the_section_17_refusal_wording_is_unchanged() -> None:
    """The message build plan section 17 dictates is public contract."""
    payload = xlsx_bytes({"One": [["A"], [1]], "Two": [["B"], [2]]})

    with pytest.raises(AmbiguousWorkbookError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert raised.value.message == (
        "This workbook contains multiple worksheets. The POC currently "
        "requires a workbook containing one data worksheet. Save the required "
        "worksheet as its own workbook or CSV and try again."
    )


def test_a_workbook_with_no_data_at_all_is_reported() -> None:
    payload = xlsx_bytes({"Empty": [], "AlsoEmpty": []})

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert not isinstance(raised.value, AmbiguousWorkbookError)
    assert raised.value.code == "PARSE_ERROR"


def test_a_header_only_worksheet_is_a_valid_dataset_with_no_rows() -> None:
    parsed = parser.parse_tabular_bytes(xlsx_bytes({"Data": [["A", "B"]]}), ".xlsx")

    assert parsed.row_count == 0
    assert parsed.columns == ("A", "B")


def test_a_file_that_is_not_a_workbook_is_reported_not_guessed() -> None:
    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(b"this is not a workbook", ".xlsx")

    # Both engines were tried and both are named in the error.
    assert raised.value.details["primary_engine"] == parser.ENGINE_FASTEXCEL
    assert raised.value.details["fallback_engine"] == parser.ENGINE_OPENPYXL
    assert "Excel workbook" in raised.value.message


def test_accented_workbook_values_are_not_normalised() -> None:
    payload = xlsx_bytes(
        {"Data": [["Producer"], ["Château Margaux"], ["Bodegas Muñoz"]]}
    )

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.frame.rows() == [("Château Margaux",), ("Bodegas Muñoz",)]


def test_a_workbook_containing_a_formula_reads_its_stored_value() -> None:
    """Build plan 7F: read stored values; never evaluate or execute anything."""
    import xlsxwriter

    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    sheet = workbook.add_worksheet("Data")
    sheet.write_row(0, 0, ["a", "b"])
    sheet.write_number(1, 0, 2)
    sheet.write_formula(1, 1, "=A2*3", None, 6)
    workbook.close()

    parsed = parser.parse_tabular_bytes(buffer.getvalue(), ".xlsx")

    assert parsed.frame.rows() == [(2, 6)]


def test_a_corrupt_zip_container_is_reported_by_both_engines() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("not-a-workbook.txt", "nothing useful")

    with pytest.raises(FileParseError):
        parser.parse_tabular_bytes(buffer.getvalue(), ".xlsx")


# ---------------------------------------------------------------------------
# 6C.5 The extension chooses the reader; parsing decides the outcome
# ---------------------------------------------------------------------------


def test_workbook_bytes_named_csv_are_refused_not_accepted_on_the_name() -> None:
    """Build plan 6C.5: client-supplied metadata does not make a file usable."""
    payload = xlsx_bytes({"Data": [["A"], [1]]})

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".csv")

    assert raised.value.code == "PARSE_ERROR"


def test_csv_bytes_named_xlsx_are_refused_not_accepted_on_the_name() -> None:
    payload = csv_bytes(["A", "B"], [[1, 2]])

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert raised.value.code == "PARSE_ERROR"


# ---------------------------------------------------------------------------
# 3.6 Compatibility fallback — the engine used is always recorded
# ---------------------------------------------------------------------------


def test_the_fallback_engine_is_used_and_recorded_when_the_primary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build plan section 6.2: an engine switch is never silent."""
    payload = xlsx_bytes({"Data": [["A", "B"], [1, "x"]]})

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("preferred engine unavailable")

    monkeypatch.setattr(parser.fastexcel, "read_excel", _fail)

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_OPENPYXL
    assert parsed.worksheet == "Data"
    assert parsed.columns == ("A", "B")
    assert parsed.frame.rows() == [(1, "x")]


def test_the_fallback_reads_the_same_bytes_without_a_temporary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Build plan 6C.7: the workbook is not written out to be reopened."""
    payload = xlsx_bytes({"Data": [["A"], [1]]})

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("preferred engine unavailable")

    monkeypatch.setattr(parser.fastexcel, "read_excel", _fail)
    monkeypatch.chdir(tmp_path)

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_OPENPYXL
    assert list(tmp_path.iterdir()) == []


def test_worksheet_ambiguity_is_not_retried_with_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structural refusal must not be turned into a different answer."""
    payload = xlsx_bytes({"One": [["A"], [1]], "Two": [["B"], [2]]})
    calls: list[str] = []

    real_load = parser._parse_xlsx_with_openpyxl

    def _record(target: bytes) -> object:
        calls.append("fallback")
        return real_load(target)

    monkeypatch.setattr(parser, "_parse_xlsx_with_openpyxl", _record)

    with pytest.raises(AmbiguousWorkbookError):
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert calls == []


# ---------------------------------------------------------------------------
# 6C.3 Nothing the parser reads ever reaches the filesystem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extension", "builder"),
    [
        (".csv", lambda: csv_bytes(["A", "B"], [[1, 2]])),
        (".xlsx", lambda: xlsx_bytes({"Data": [["A", "B"], [1, 2]]})),
    ],
    ids=["csv", "xlsx"],
)
def test_parsing_writes_nothing_to_the_working_directory(
    extension: str, builder, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)

    parsed = parser.parse_tabular_bytes(builder(), extension)

    assert parsed.row_count == 1
    assert list(tmp_path.iterdir()) == []