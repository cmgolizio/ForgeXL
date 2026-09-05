"""Parser service tests (build plan 3.4-3.6, 6C.6-6C.7 and section 17).

Every test here parses **bytes**. Since Phase 6C the parser reads an upload
from memory, so nothing in this module writes a spreadsheet to disk in order
to read it back, and no OS file picker is involved anywhere (build plan 6C
completion criteria).
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from app.errors import (
    AmbiguousWorkbookError,
    FileParseError,
    UnsupportedExtensionError,
)
from app.services import parser

from tests.helpers import csv_bytes, xlsx_bytes


@pytest.mark.parametrize("text", ["n/a", "N/A", "NA", "NULL", "unknown", "001", "  n/a  "])
def test_mixed_xlsx_values_survive_including_null_like_text(text: str) -> None:
    payload = xlsx_bytes({"Data": [["Value", "Number"], [10, 1], [text, 2], [None, 3]]})
    parsed = parser.parse_tabular_bytes(payload, ".xlsx")
    assert parsed.frame["Value"].to_list() == ["10", text, None]
    assert parsed.frame["Number"].to_list() == [1, 2, 3]
    assert parsed.frame["Number"].dtype.is_numeric()


def test_text_beyond_the_old_xlsx_sampling_window_is_preserved() -> None:
    payload = xlsx_bytes({"Data": [["Value"], *[[10]] * 1_100, ["late text"]]})
    parsed = parser.parse_tabular_bytes(payload, ".xlsx")
    assert parsed.frame["Value"].to_list() == ["10"] * 1_100 + ["late text"]
    assert parsed.parser_engine == parser.ENGINE_OPENPYXL


def test_mixed_boolean_and_numeric_xlsx_cells_are_not_collapsed() -> None:
    payload = xlsx_bytes({"Data": [["Value"], [True], [1], [False], [0]]})
    parsed = parser.parse_tabular_bytes(payload, ".xlsx")
    assert parsed.frame["Value"].to_list() == ["True", "1", "False", "0"]


def test_genuine_numeric_blanks_keep_the_preferred_engine() -> None:
    payload = xlsx_bytes({"Data": [["Value", "Row"], [10, 1], [None, 2], [2.5, 3]]})
    parsed = parser.parse_tabular_bytes(payload, ".xlsx")
    assert parsed.frame["Value"].to_list() == [10, None, 2.5]
    assert parsed.frame["Value"].dtype.is_numeric()
    assert parsed.parser_engine == parser.ENGINE_FASTEXCEL


def test_cell_loss_with_an_unavailable_fallback_is_a_parse_error(monkeypatch) -> None:
    payload = xlsx_bytes({"Data": [["Value"], [10], ["n/a"]]})

    def fail(_payload):
        raise RuntimeError("Fallback unavailable")

    monkeypatch.setattr(parser, "_parse_xlsx_with_openpyxl", fail)
    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")
    assert "discard cell values" in raised.value.details["primary_reason"]
    assert "Fallback unavailable" in raised.value.details["fallback_reason"]


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

# ---------------------------------------------------------------------------
# A failed worksheet probe is an engine failure, never an empty sheet
# ---------------------------------------------------------------------------


def _fastexcel_reader_failing_to_probe(name: str):
    """Build a `read_excel` replacement whose probe of `name` raises.

    Probing is the `header_row=None` load `_fastexcel_sheet_has_data` performs
    to decide whether a worksheet holds anything. Everything else is delegated
    to the real reader, so the workbook is genuine and only this one operation
    is made to fail — which is the situation the parser must not silently read
    as "that sheet is empty".
    """
    real_read_excel = parser.fastexcel.read_excel

    class _FailingProbeReader:
        def __init__(self, payload: bytes) -> None:
            self._reader = real_read_excel(payload)

        @property
        def sheet_names(self) -> list[str]:
            return self._reader.sheet_names

        def load_sheet(self, sheet_name: str, **kwargs: Any) -> Any:
            if kwargs.get("header_row", 0) is None and sheet_name == name:
                raise RuntimeError(f"worksheet {sheet_name!r} could not be loaded")
            return self._reader.load_sheet(sheet_name, **kwargs)

    return _FailingProbeReader


def test_a_worksheet_that_fails_to_probe_is_not_reported_as_an_empty_workbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed probe reaches the engine fallback instead of becoming "empty".

    The workbook's only worksheet holds data. If a failed probe were counted as
    an empty sheet, this workbook would have no data sheet at all and would be
    refused as "contains no data" — a statement about the user's file that is
    simply untrue. It must reach the compatibility fallback instead, which can
    read the sheet.
    """
    payload = xlsx_bytes({"Ledger": [["A"], [1]]})
    monkeypatch.setattr(
        parser.fastexcel, "read_excel", _fastexcel_reader_failing_to_probe("Ledger")
    )

    parsed = parser.parse_tabular_bytes(payload, ".xlsx")

    assert parsed.parser_engine == parser.ENGINE_OPENPYXL
    assert parsed.worksheet == "Ledger"
    assert parsed.frame.rows() == [(1,)]


def test_a_failed_probe_never_lets_the_parser_select_a_different_worksheet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build plan section 17: a sheet that could not be read is not "no data".

    Both worksheets hold data, so this workbook is ambiguous and must be
    refused. Treating the unreadable probe as an empty sheet would leave
    exactly one "populated" sheet and the parser would quietly transform the
    other one instead — the silent worksheet selection section 17 forbids.
    """
    payload = xlsx_bytes({"Ledger": [["A"], [1]], "Notes": [["B"], [2]]})
    monkeypatch.setattr(
        parser.fastexcel, "read_excel", _fastexcel_reader_failing_to_probe("Ledger")
    )

    with pytest.raises(AmbiguousWorkbookError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert set(raised.value.details["worksheets_with_data"]) == {"Ledger", "Notes"}


def test_a_failed_probe_that_the_fallback_cannot_rescue_is_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both engines failed, so both are named — the workbook is not "empty"."""
    payload = xlsx_bytes({"Ledger": [["A"], [1]]})
    monkeypatch.setattr(
        parser.fastexcel, "read_excel", _fastexcel_reader_failing_to_probe("Ledger")
    )

    def _fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("fallback engine unavailable")

    monkeypatch.setattr(parser, "_parse_xlsx_with_openpyxl", _fail)

    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(payload, ".xlsx")

    assert raised.value.details["primary_engine"] == parser.ENGINE_FASTEXCEL
    assert raised.value.details["fallback_engine"] == parser.ENGINE_OPENPYXL
    assert "could not be loaded" in raised.value.details["primary_reason"]
