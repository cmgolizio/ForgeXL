"""The fixture system itself (build plan 6H.1, 6H.2, 6H.8).

Everything the end-to-end regression suite asserts rests on the fixtures being
what they claim to be, so the generator is tested before it is trusted:

* **Deterministic.** The same fixture renders to the same bytes, every call and
  every process. A fixture that varied would turn every downstream failure
  into a mystery (6H.1).
* **Faithful.** What goes into a fixture comes back out of the parser — values,
  blanks, column names and row order alike. A builder that silently altered
  data could not be used to prove the application does not (6H.1).
* **Complete.** Every scenario build plan 6H.2 lists that is meaningful for
  what ForgeXL supports has a fixture.
* **Synthetic.** No dataset in the repository came from a company spreadsheet,
  and no test needs a file that had to be saved by hand (6H.8).
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest

from app.errors import AmbiguousWorkbookError, FileParseError
from app.services import parser

from tests.fixtures import spreadsheets as fx
from tests.helpers import normalise_rows

CATALOGUE_IDS = [table.name for table in fx.CATALOGUE]
WORKBOOK_IDS = [workbook.name for workbook in fx.WORKBOOKS]


def test_workbook_bytes_do_not_change_when_the_clock_changes(monkeypatch) -> None:
    import xlsxwriter.core

    class Clock(datetime):
        year_now = 2025

        @classmethod
        def now(cls, tz=None):
            return cls(cls.year_now, 1, 1, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(xlsxwriter.core, "datetime", Clock)
    monkeypatch.setattr(fx, "datetime", Clock)
    first = fx.SIMPLE_TABLE.as_xlsx()
    Clock.year_now = 2026
    assert fx.SIMPLE_TABLE.as_xlsx() == first

#: The catalogue minus the one fixture whose values are format-dependent.
#:
#: `MIXED_VALUES` holds numbers and text in one column, and a column has one
#: type: both readers preserve mixed values by making the whole column text.
#: Neither result equals the Python literals the fixture is written
#: from, so the sweeps below would be asserting the wrong thing for it. It is
#: covered explicitly instead, one test per format, further down.
HOMOGENEOUS = tuple(table for table in fx.CATALOGUE if table is not fx.MIXED_VALUES)
HOMOGENEOUS_IDS = [table.name for table in HOMOGENEOUS]


# ---------------------------------------------------------------------------
# 6H.1 Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", fx.CATALOGUE, ids=CATALOGUE_IDS)
@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_fixture_renders_to_the_same_bytes_every_time(
    table: fx.Table, extension: str
) -> None:
    """Byte equality, not merely equal data: the file itself is reproducible."""
    assert table.payload(extension) == table.payload(extension)


@pytest.mark.parametrize("workbook", fx.WORKBOOKS, ids=WORKBOOK_IDS)
def test_a_fixture_workbook_renders_to_the_same_bytes_every_time(
    workbook: fx.Workbook,
) -> None:
    assert workbook.as_xlsx() == workbook.as_xlsx()


@pytest.mark.parametrize("row_count", [0, 1, 500, 5_000])
def test_the_generated_large_table_is_a_pure_function_of_its_size(
    row_count: int,
) -> None:
    """Generated rather than stored, so it must not depend on anything else."""
    assert fx.large_table(row_count).rows == fx.large_table(row_count).rows
    assert fx.large_table(row_count).as_csv() == fx.large_table(row_count).as_csv()


def test_the_generated_large_table_repeats_with_a_known_period() -> None:
    """The property that makes both Actions' expected output closed-form."""
    table = fx.large_table(fx.PRODUCT_POOL * 3 + 7)

    assert table.rows[0] == table.rows[fx.PRODUCT_POOL]
    assert table.rows[0] == table.rows[fx.PRODUCT_POOL * 2]
    assert table.rows[0] != table.rows[1]
    assert len(set(table.rows)) == fx.PRODUCT_POOL


@pytest.mark.parametrize("row_count", [0, 1, 249, 250, 251, 1_000])
def test_the_distinct_row_count_of_a_large_table_is_stated_correctly(
    row_count: int,
) -> None:
    table = fx.large_table(row_count)

    assert len(set(table.rows)) == fx.distinct_rows_in_large_table(row_count)


def test_a_negative_large_table_is_refused_rather_than_producing_nothing() -> None:
    with pytest.raises(ValueError):
        fx.large_table(-1)


# ---------------------------------------------------------------------------
# 6H.1 Faithfulness — what goes in comes back out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", fx.CATALOGUE, ids=CATALOGUE_IDS)
@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_fixture_parses_to_the_shape_it_declares(
    table: fx.Table, extension: str
) -> None:
    parsed = parser.parse_tabular_bytes(table.payload(extension), extension)

    assert parsed.columns == table.header
    assert parsed.row_count == table.row_count
    assert parsed.column_count == table.column_count


@pytest.mark.parametrize("table", HOMOGENEOUS, ids=HOMOGENEOUS_IDS)
def test_a_csv_fixture_round_trips_its_values_unchanged(table: fx.Table) -> None:
    """Every value the fixture declares comes back as it went in.

    Text, numbers, blanks, accents and the literal text of a malformed date
    alike. Only the whole-number/float distinction is normalised away, because
    a workbook has one numeric type and the CSV path does not.
    """
    parsed = parser.parse_tabular_bytes(table.as_csv(), fx.CSV_EXTENSION)

    assert normalise_rows(parsed.frame.rows()) == normalise_rows(table.rows)


@pytest.mark.parametrize("table", HOMOGENEOUS, ids=HOMOGENEOUS_IDS)
def test_an_xlsx_fixture_round_trips_its_values_unchanged(table: fx.Table) -> None:
    parsed = parser.parse_tabular_bytes(table.as_xlsx(), fx.XLSX_EXTENSION)

    assert normalise_rows(parsed.frame.rows()) == normalise_rows(table.rows)


# ---------------------------------------------------------------------------
# The one fixture whose two renderings genuinely differ (6H.2, "mixed
# numeric/text values")
# ---------------------------------------------------------------------------


def test_a_mixed_column_uploaded_as_csv_keeps_every_value_as_text() -> None:
    """The CSV path loses nothing: the column becomes text and all of it survives.

    A column holding both numbers and the word "n/a" has one honest reading —
    text — and that is what Polars gives it. `10` reads back as `"10"`, which
    is the same value written as a string, and `"n/a"` reads back as itself.
    """
    parsed = parser.parse_tabular_bytes(fx.MIXED_VALUES.as_csv(), fx.CSV_EXTENSION)

    assert parsed.frame["Value"].to_list() == ["10", "n/a", "2.5", None, "-3", "0"]


def test_a_mixed_column_uploaded_as_xlsx_keeps_every_value() -> None:
    """The XLSX path must not silently blank a cell it cannot type.

    The workbook stores `n/a` as a genuine string cell — verified by reading
    the file back with openpyxl, which returns it intact — so the value is
    present in the user's file and absent from ForgeXL's result.
    """
    parsed = parser.parse_tabular_bytes(fx.MIXED_VALUES.as_xlsx(), fx.XLSX_EXTENSION)

    values = parsed.frame["Value"].to_list()
    assert values[1] is not None, "the text 'n/a' was silently dropped"
    assert values == ["10", "n/a", "2.5", None, "-3", "0"]
    assert parsed.parser_engine == parser.ENGINE_OPENPYXL


def test_the_mixed_column_fixture_really_does_store_text_in_the_workbook() -> None:
    """Evidence that the test above is about the reader, not the fixture.

    If the fixture wrote `n/a` as something other than a string cell, the
    finding would be about the fixture builder. openpyxl reads the same
    workbook bytes and returns the text, so the cell is a string cell and the
    two engines disagree about the same file.
    """
    parsed = parser._parse_xlsx_with_openpyxl(fx.MIXED_VALUES.as_xlsx())

    assert parsed.frame["Value"].to_list() == ["10", "n/a", "2.5", None, "-3", "0"]


def test_the_fixture_writer_does_not_turn_text_into_a_formula() -> None:
    """A builder that converted data could not prove the application does not.

    xlsxwriter writes a string beginning with ``=`` as a formula by default,
    which would read back as its computed value instead of the text. The
    fixture writer turns that off, so the header ``=Not A Formula`` survives
    as itself.
    """
    parsed = parser.parse_tabular_bytes(
        fx.UNUSUAL_COLUMN_NAMES.as_xlsx(), fx.XLSX_EXTENSION
    )

    assert "=Not A Formula" in parsed.columns


def test_unusual_column_names_are_not_tidied_by_the_fixture_or_the_parser() -> None:
    """Column names are identifiers, including their spaces and punctuation."""
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.UNUSUAL_COLUMN_NAMES.payload(extension), extension
        )
        assert parsed.columns[0] == "  Leading Space"
        assert parsed.columns == fx.UNUSUAL_COLUMN_NAMES.header


def test_a_blank_row_survives_as_a_row_of_nulls_in_both_formats() -> None:
    """Build plan section 3.3: rows are never silently dropped."""
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.BLANK_ROWS.payload(extension), extension
        )
        assert parsed.row_count == fx.BLANK_ROWS.row_count
        assert parsed.frame.rows()[1] == (None, None, None)
        assert parsed.frame.rows()[3] == (None, None, None)


def test_a_blank_cell_is_a_null_rather_than_empty_text() -> None:
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.BLANK_CELLS.payload(extension), extension
        )
        assert parsed.frame["Note"].to_list()[0] is None
        assert parsed.frame["Region"].to_list()[1] is None


def test_malformed_dates_arrive_exactly_as_written_in_both_formats() -> None:
    """Nothing repairs, reorders or nulls a date-shaped string."""
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.MALFORMED_DATES.payload(extension), extension
        )
        assert parsed.frame["Received"].to_list() == [
            "2026-02-30",
            "2026-13-01",
            "31/02/26",
            "not a date",
            "2026-01-31",
        ]


def test_accented_and_unaccented_spellings_stay_distinct() -> None:
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.ACCENTED_TEXT.payload(extension), extension
        )
        producers = parsed.frame["Producer"].to_list()
        assert "Château Margaux" in producers
        assert "Chateau Margaux" in producers
        assert len(set(producers)) == len(producers)


def test_unicode_scripts_and_csv_traps_survive() -> None:
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.UNICODE_TEXT.payload(extension), extension
        )
        producers = parsed.frame["Producer"].to_list()
        assert "東京商事" in producers
        assert "Ζορμπάς" in producers
        assert "Пётр Смирнов" in producers
        # A comma and an apostrophe inside one value: the CSV quoting trap.
        assert "O'Brien & Sons, Ltd." in producers


def test_a_csv_and_an_xlsx_rendering_of_one_fixture_carry_the_same_values() -> None:
    """The property that lets one fixture prove both upload paths."""
    for table in fx.CATALOGUE:
        if table is fx.MIXED_VALUES:
            continue
        from_csv = parser.parse_tabular_bytes(table.as_csv(), fx.CSV_EXTENSION)
        from_xlsx = parser.parse_tabular_bytes(table.as_xlsx(), fx.XLSX_EXTENSION)
        assert from_csv.columns == from_xlsx.columns, table.name
        assert normalise_rows(from_csv.frame.rows()) == normalise_rows(
            from_xlsx.frame.rows()
        ), table.name


# ---------------------------------------------------------------------------
# 6H.2 The workbook-structure fixtures
# ---------------------------------------------------------------------------


def test_the_multi_worksheet_fixture_is_refused_rather_than_chosen_from() -> None:
    with pytest.raises(AmbiguousWorkbookError) as raised:
        parser.parse_tabular_bytes(fx.MULTIPLE_WORKSHEETS.as_xlsx(), ".xlsx")

    assert raised.value.details["worksheets_with_data"] == ["January", "February"]


def test_one_data_sheet_among_blank_sheets_is_unambiguous() -> None:
    parsed = parser.parse_tabular_bytes(
        fx.ONE_DATA_SHEET_AMONG_BLANKS.as_xlsx(), ".xlsx"
    )

    assert parsed.worksheet == "Data"
    assert parsed.row_count == 1


def test_the_empty_workbook_fixture_is_reported_as_holding_no_data() -> None:
    with pytest.raises(FileParseError) as raised:
        parser.parse_tabular_bytes(fx.EMPTY_WORKBOOK.as_xlsx(), ".xlsx")

    assert "no data" in str(raised.value)


def test_a_header_only_fixture_is_a_dataset_with_no_rows() -> None:
    """Distinct from an empty file: this one parses, and holds zero rows."""
    for extension in fx.UPLOAD_EXTENSIONS:
        parsed = parser.parse_tabular_bytes(
            fx.HEADER_ONLY.payload(extension), extension
        )
        assert parsed.row_count == 0
        assert parsed.columns == fx.HEADER_ONLY.header


# ---------------------------------------------------------------------------
# 6H.2 Coverage of the scenarios the build plan lists
# ---------------------------------------------------------------------------


def test_every_scenario_the_build_plan_lists_has_a_fixture() -> None:
    """Build plan 6H.2's list, restricted to what ForgeXL supports.

    Named here so that removing a fixture is a decision rather than an
    omission. The build plan's list is the source; each entry maps to the
    fixture that covers it.
    """
    coverage = {
        "simple table": fx.SIMPLE_TABLE,
        "blank rows": fx.BLANK_ROWS,
        "blank cells": fx.BLANK_CELLS,
        "duplicate rows": fx.DUPLICATE_ROWS,
        "duplicate keys": fx.DUPLICATE_KEYS,
        "mixed numeric/text values": fx.MIXED_VALUES,
        "dates": fx.DATES,
        "malformed dates": fx.MALFORMED_DATES,
        "unicode characters": fx.UNICODE_TEXT,
        "accented characters": fx.ACCENTED_TEXT,
        "unusual column names": fx.UNUSUAL_COLUMN_NAMES,
        "missing required columns": fx.MISSING_REQUIRED_COLUMNS,
        "extra columns": fx.EXTRA_COLUMNS,
    }

    for scenario, table in coverage.items():
        assert table in fx.CATALOGUE, scenario
        assert table.description, scenario

    # The three the build plan lists that are not single tables.
    assert fx.MULTIPLE_WORKSHEETS in fx.WORKBOOKS  # multiple worksheets
    assert fx.EMPTY_WORKBOOK in fx.WORKBOOKS  # empty workbook
    assert fx.large_table(1_000).row_count == 1_000  # larger dataset


def test_every_catalogue_fixture_is_named_and_described_uniquely() -> None:
    names = [table.name for table in fx.CATALOGUE]

    assert len(set(names)) == len(names)
    assert all(table.description for table in fx.CATALOGUE)


# ---------------------------------------------------------------------------
# 6H.8 The test data is synthetic
# ---------------------------------------------------------------------------


def test_the_repository_contains_no_spreadsheet_files() -> None:
    """Build plan 6H.8: no company data, and nothing saved by hand.

    Every dataset the suite processes is built in memory from Python literals,
    so a spreadsheet appearing in the repository would mean a fixture had
    started depending on a stored artifact instead.
    """
    repository_root = Path(__file__).resolve().parents[2]
    ignored = {".git", "node_modules", ".venv", ".next", "__pycache__"}

    found = [
        path
        for path in repository_root.rglob("*")
        if path.suffix.lower() in {".csv", ".xlsx", ".xls", ".xlsm", ".parquet"}
        and not ignored & set(path.parts)
    ]

    assert found == [], f"Unexpected spreadsheet files in the repository: {found}"


def test_fixture_construction_writes_nothing_to_disk(tmp_path: Path) -> None:
    """The workbook writer is in-memory, so no fixture spools through /tmp."""
    import tempfile

    before = set(Path(tempfile.gettempdir()).iterdir())

    for table in fx.CATALOGUE:
        table.as_csv()
        table.as_xlsx()
    for workbook in fx.WORKBOOKS:
        workbook.as_xlsx()

    after = set(Path(tempfile.gettempdir()).iterdir())
    assert after - before <= {tmp_path, tmp_path.parent}
