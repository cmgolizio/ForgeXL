"""The report-quality XLSX renderer (build plan 12D).

Build plan 12D lists thirteen things a report workbook must be able to do.
Every one has a test here, and every one is verified by **reopening the
rendered bytes** rather than by trusting the call that wrote them:

| build plan 12D item        | tested by                                       |
| -------------------------- | ----------------------------------------------- |
| multiple worksheets        | `TestWorksheets`                                 |
| worksheet ordering         | `TestWorksheets.test_worksheets_appear_in_order` |
| formatted headers          | `TestHeaders`                                    |
| currency formats           | `TestNumberFormats`                              |
| percentage formats         | `TestNumberFormats`                              |
| integer/decimal formats    | `TestNumberFormats`                              |
| sensible date formats      | `TestNumberFormats`                              |
| column widths              | `TestColumnWidths`                               |
| row heights                | `TestRowHeights`                                 |
| frozen panes               | `TestFrozenPanesAndFilters`                      |
| filters                    | `TestFrozenPanesAndFilters`                      |
| tables                     | `TestTables`                                     |
| conditional formatting     | `TestConditionalFormatting`                      |
| readable totals            | `TestTotals`                                     |
| consistent styling         | `TestConsistentStyling`                          |

And the two rules the module imposes on itself:

* **It calculates nothing** — `TestItCalculatesNothing`.
* **It writes no formula** — `TestNoFormulaIsEverWritten`.

Widths are read from the worksheet XML rather than through
``openpyxl``'s ``column_dimensions``, because that mapping is keyed by the
first column of each stored range: asking it for column ``B`` of a range
written as ``min=1 max=2`` silently invents a default instead of reporting the
width that is actually in the file.
"""

from __future__ import annotations

import io
import re
import warnings
import zipfile
from datetime import date, datetime
from pathlib import Path

import fastexcel
import openpyxl
import polars as pl
import pytest

from app.errors import ExportTooLargeError
from app.services.export import MAX_CELL_CHARACTERS
from app.services.workbook import (
    BODY_FONT_SIZE,
    HEADER_BACKGROUND,
    HEADER_FONT_COLOR,
    MAX_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    NUMBER_FORMATS,
    REPORT_FONT,
    TABLE_NAME_PREFIX,
    CellFormat,
    Column,
    ConditionalFormat,
    ConditionalRule,
    Sheet,
    render_sheet_bytes,
    render_workbook,
)

REPORT = pl.DataFrame(
    {
        "Rep": ["Beth Comeaux", "Kevin Wardell", "Jennifer Jones"],
        "Cases": [120, 88, 205],
        "Revenue": [15340.5, -900.25, 42110.0],
        "Share": [0.234, -0.014, 0.642],
        "Month": [date(2026, 9, 1)] * 3,
        "Updated": [datetime(2026, 9, 30, 17, 5)] * 3,
    }
)

FORMATTED_COLUMNS = (
    Column("Rep", header="Sales Rep", format=CellFormat.TEXT),
    Column("Cases", format=CellFormat.INTEGER),
    Column("Revenue", format=CellFormat.CURRENCY),
    Column("Share", format=CellFormat.PERCENT_DECIMAL),
    Column("Month", format=CellFormat.MONTH),
    Column("Updated", format=CellFormat.DATETIME),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reopen(payload: bytes) -> openpyxl.Workbook:
    """Reopen rendered bytes, so every assertion is about the real file.

    openpyxl warns about the xlsxwriter extension records that carry a solid
    data bar, which it can read past but cannot model. The warning is about
    openpyxl's own coverage, not about the file, so it is suppressed here
    rather than left to clutter every run.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*extension is not supported.*"
        )
        return openpyxl.load_workbook(io.BytesIO(payload))


def _sheet_xml(payload: bytes, index: int = 1) -> str:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    return archive.read(f"xl/worksheets/sheet{index}.xml").decode("utf-8")


def _column_widths(payload: bytes, index: int = 1) -> dict[int, float]:
    """Read every column's width straight out of the worksheet XML."""
    widths: dict[int, float] = {}
    for entry in re.findall(r"<col [^>]*>", _sheet_xml(payload, index)):
        first = _matched(r'min="(\d+)"', entry)
        last = _matched(r'max="(\d+)"', entry)
        width = _matched(r'width="([\d.]+)"', entry)
        for number in range(int(first), int(last) + 1):
            widths[number] = float(width)
    return widths


def _matched(pattern: str, text: str) -> str:
    """Return the first capture of `pattern` in `text`, or fail the test.

    A missing match means the rendered XML is not the shape the assertion is
    about, which is a failure to report rather than an exception to raise from
    a helper.
    """
    found = re.search(pattern, text)
    assert found is not None, f"{pattern!r} is not in {text!r}"
    return found.group(1)


def _table_xml(payload: bytes) -> list[str]:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    return [
        archive.read(name).decode("utf-8")
        for name in sorted(archive.namelist())
        if name.startswith("xl/tables/")
    ]


def _formatted_sheet(**overrides) -> Sheet:
    options = {
        "name": "Summary",
        "frame": REPORT,
        "columns": FORMATTED_COLUMNS,
    }
    options.update(overrides)
    return Sheet(**options)


# ---------------------------------------------------------------------------
# Worksheets and ordering
# ---------------------------------------------------------------------------


class TestWorksheets:
    def test_a_workbook_may_hold_several_worksheets(self) -> None:
        payload = render_workbook(
            [
                Sheet(name="Summary", frame=REPORT),
                Sheet(name="Detail", frame=REPORT),
                Sheet(name="Notes", frame=REPORT.head(1)),
            ]
        )

        assert _reopen(payload).sheetnames == ["Summary", "Detail", "Notes"]

    def test_worksheets_appear_in_order(self) -> None:
        """Build plan 12D's "worksheet ordering": the order given, exactly."""
        names = ["Zebra", "Apple", "Middle"]
        payload = render_workbook(
            [Sheet(name=name, frame=REPORT.head(1)) for name in names]
        )

        assert _reopen(payload).sheetnames == names

    def test_a_workbook_with_no_worksheet_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one worksheet"):
            render_workbook([])

    def test_worksheet_names_are_cleaned_and_de_collided(self) -> None:
        """Delegated to the Phase 6F rules rather than reimplemented."""
        payload = render_workbook(
            [
                Sheet(name="Q1/Q2", frame=REPORT.head(1)),
                Sheet(name="Q1/Q2", frame=REPORT.head(1)),
            ]
        )

        assert _reopen(payload).sheetnames == ["Q1 Q2", "Q1 Q2 2"]

    def test_one_sheet_renders_as_a_one_worksheet_workbook(self) -> None:
        payload = render_sheet_bytes(Sheet(name="Only", frame=REPORT))

        assert _reopen(payload).sheetnames == ["Only"]

    def test_nothing_is_written_to_disk(self, quarantine: Path) -> None:
        render_workbook([_formatted_sheet()])

        assert list(quarantine.iterdir()) == []


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_the_header_row_carries_the_declared_headings(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        assert [cell.value for cell in worksheet[1]] == [
            "Sales Rep",
            "Cases",
            "Revenue",
            "Share",
            "Month",
            "Updated",
        ]

    def test_an_unstated_header_is_the_column_name(self) -> None:
        payload = render_workbook([Sheet(name="Plain", frame=REPORT)])
        worksheet = _reopen(payload)["Plain"]

        assert [cell.value for cell in worksheet[1]] == REPORT.columns

    def test_the_header_row_is_styled(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        header = _reopen(payload)["Summary"]["A1"]

        assert header.font.bold is True
        assert header.font.color.rgb.endswith(HEADER_FONT_COLOR.lstrip("#"))
        assert header.fill.fgColor.rgb.endswith(HEADER_BACKGROUND.lstrip("#"))

    def test_a_title_and_subtitle_sit_above_the_table(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    title="Monthly Sales Summary", subtitle="September 2026"
                )
            ]
        )
        worksheet = _reopen(payload)["Summary"]

        assert worksheet["A1"].value == "Monthly Sales Summary"
        assert worksheet["A2"].value == "September 2026"
        assert worksheet["A3"].value is None
        assert worksheet["A4"].value == "Sales Rep"
        assert worksheet["A1"].font.bold is True

    def test_a_sheet_with_no_heading_starts_at_the_first_row(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert _reopen(payload)["Summary"]["A1"].value == "Sales Rep"

    def test_only_the_named_columns_are_written_in_the_order_named(
        self,
    ) -> None:
        payload = render_workbook(
            [
                Sheet(
                    name="Two",
                    frame=REPORT,
                    columns=(Column("Cases"), Column("Rep")),
                )
            ]
        )
        worksheet = _reopen(payload)["Two"]

        assert [cell.value for cell in worksheet[1]] == ["Cases", "Rep"]
        assert worksheet["A2"].value == 120

    def test_a_column_the_data_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not have"):
            render_workbook(
                [Sheet(name="S", frame=REPORT, columns=(Column("Missing"),))]
            )

    def test_two_columns_may_not_share_a_heading(self) -> None:
        with pytest.raises(ValueError, match="same heading"):
            render_workbook(
                [
                    Sheet(
                        name="S",
                        frame=REPORT,
                        columns=(
                            Column("Cases", header="Total"),
                            Column("Revenue", header="Total"),
                        ),
                    )
                ]
            )


# ---------------------------------------------------------------------------
# Number formats
# ---------------------------------------------------------------------------


class TestNumberFormats:
    @pytest.mark.parametrize(
        "column_letter,cell_format",
        [
            ("A", CellFormat.TEXT),
            ("B", CellFormat.INTEGER),
            ("C", CellFormat.CURRENCY),
            ("D", CellFormat.PERCENT_DECIMAL),
            ("E", CellFormat.MONTH),
            ("F", CellFormat.DATETIME),
        ],
    )
    def test_each_column_carries_its_declared_format(
        self, column_letter: str, cell_format: CellFormat
    ) -> None:
        payload = render_workbook([_formatted_sheet()])
        cell = _reopen(payload)["Summary"][f"{column_letter}2"]

        assert cell.number_format == NUMBER_FORMATS[cell_format]

    @pytest.mark.parametrize("cell_format", list(CellFormat))
    def test_every_declared_format_has_an_excel_format_string(
        self, cell_format: CellFormat
    ) -> None:
        assert NUMBER_FORMATS[cell_format]

    def test_currency_shows_negatives_in_parentheses(self) -> None:
        """What an accounting reader expects, and legible at a glance."""
        assert NUMBER_FORMATS[CellFormat.CURRENCY] == "$#,##0.00;($#,##0.00)"
        assert NUMBER_FORMATS[CellFormat.CURRENCY_WHOLE] == "$#,##0;($#,##0)"

    def test_percentages_come_in_two_precisions(self) -> None:
        assert NUMBER_FORMATS[CellFormat.PERCENT] == "0%"
        assert NUMBER_FORMATS[CellFormat.PERCENT_DECIMAL] == "0.0%"

    def test_dates_are_rendered_unambiguously(self) -> None:
        """ISO order, so 2026-09-01 cannot be read as the ninth of January."""
        assert NUMBER_FORMATS[CellFormat.DATE] == "yyyy-mm-dd"
        assert NUMBER_FORMATS[CellFormat.MONTH] == "yyyy-mm"
        assert NUMBER_FORMATS[CellFormat.DATETIME] == "yyyy-mm-dd hh:mm"

    def test_the_stored_values_are_the_values_given(self) -> None:
        """A format changes what is shown, never what is stored."""
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet["B2"].value == 120
        assert worksheet["C3"].value == -900.25
        assert worksheet["D4"].value == 0.642
        assert worksheet["E2"].value == datetime(2026, 9, 1)

    def test_the_default_format_shows_a_number_verbatim(self) -> None:
        """Unchanged from the plain export: no invented grouping (6F.3)."""
        payload = render_workbook([Sheet(name="Plain", frame=REPORT)])
        cell = _reopen(payload)["Plain"]["C2"]

        assert cell.number_format == NUMBER_FORMATS[CellFormat.GENERAL]


# ---------------------------------------------------------------------------
# Widths, heights, panes, filters
# ---------------------------------------------------------------------------


class TestColumnWidths:
    def test_a_declared_width_is_used(self) -> None:
        payload = render_workbook(
            [
                Sheet(
                    name="S",
                    frame=REPORT,
                    columns=(Column("Rep", width=33.0), Column("Cases")),
                )
            ]
        )

        assert _column_widths(payload)[1] == pytest.approx(33.0, abs=0.2)

    def test_a_text_column_is_measured_against_its_values(self) -> None:
        payload = render_workbook(
            [Sheet(name="S", frame=REPORT, columns=(Column("Rep"),))]
        )

        # "Jennifer Jones" is 14 characters, plus padding.
        assert _column_widths(payload)[1] == pytest.approx(16.0, abs=0.5)

    def test_a_short_column_with_a_long_heading_fits_the_heading(self) -> None:
        payload = render_workbook(
            [
                Sheet(
                    name="S",
                    frame=pl.DataFrame({"n": ["a", "b"]}),
                    columns=(
                        Column("n", header="A Very Long Column Heading"),
                    ),
                )
            ]
        )

        assert _column_widths(payload)[1] >= len("A Very Long Column Heading")

    def test_one_enormous_value_cannot_push_the_sheet_off_screen(self) -> None:
        payload = render_workbook(
            [Sheet(name="S", frame=pl.DataFrame({"n": ["x" * 5000]}))]
        )

        assert _column_widths(payload)[1] == pytest.approx(
            MAX_COLUMN_WIDTH, abs=0.5
        )

    def test_a_one_character_column_stays_readable(self) -> None:
        payload = render_workbook(
            [Sheet(name="S", frame=pl.DataFrame({"n": ["x"]}))]
        )

        assert _column_widths(payload)[1] >= MIN_COLUMN_WIDTH - 0.5

    def test_a_numeric_column_is_sized_from_its_format(self) -> None:
        """"$1,234.56" is a known width whatever the number happens to be."""
        payload = render_workbook(
            [
                Sheet(
                    name="S",
                    frame=REPORT,
                    columns=(Column("Revenue", format=CellFormat.CURRENCY),),
                )
            ]
        )

        assert _column_widths(payload)[1] >= 16.0

    def test_an_empty_frame_still_produces_widths(self) -> None:
        payload = render_workbook(
            [Sheet(name="S", frame=REPORT.head(0), columns=FORMATTED_COLUMNS)]
        )

        assert len(_column_widths(payload)) == len(FORMATTED_COLUMNS)


class TestRowHeights:
    def test_a_declared_row_height_is_applied(self) -> None:
        payload = render_workbook([_formatted_sheet(row_height=21.0)])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet.sheet_format.defaultRowHeight == pytest.approx(21.0)

    def test_the_header_row_has_its_own_height(self) -> None:
        payload = render_workbook([_formatted_sheet(header_height=30.0)])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet.row_dimensions[1].height == pytest.approx(30.0)

    def test_a_sheet_without_a_declared_height_leaves_the_default(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet.sheet_format.defaultRowHeight == pytest.approx(15.0)


class TestFrozenPanesAndFilters:
    def test_the_header_row_is_frozen_by_default(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert _reopen(payload)["Summary"].freeze_panes == "A2"

    def test_a_frozen_pane_accounts_for_the_title_rows(self) -> None:
        payload = render_workbook(
            [_formatted_sheet(title="Report", subtitle="September 2026")]
        )

        assert _reopen(payload)["Summary"].freeze_panes == "A5"

    def test_freezing_can_be_turned_off(self) -> None:
        payload = render_workbook([_formatted_sheet(freeze_header=False)])

        assert _reopen(payload)["Summary"].freeze_panes is None

    def test_the_header_row_carries_filter_buttons(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert "<autoFilter" in _table_xml(payload)[0]

    def test_filters_can_be_turned_off(self) -> None:
        payload = render_workbook([_formatted_sheet(autofilter=False)])

        assert "<autoFilter" not in _table_xml(payload)[0]


class TestTables:
    def test_each_sheet_is_a_real_excel_table(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert len(_table_xml(payload)) == 1
        assert "<table " in _table_xml(payload)[0]

    def test_a_table_is_named_after_its_worksheet(self) -> None:
        payload = render_workbook([Sheet(name="Summary", frame=REPORT)])

        assert f'displayName="{TABLE_NAME_PREFIX}Summary"' in _table_xml(payload)[0]

    def test_table_names_are_unique_across_the_workbook(self) -> None:
        """Excel scopes them to the workbook, not to the worksheet."""
        payload = render_workbook(
            [
                Sheet(name="Summary", frame=REPORT.head(1)),
                Sheet(name="Summary", frame=REPORT.head(1)),
            ]
        )

        names = [
            _matched(r'displayName="([^"]+)"', xml)
            for xml in _table_xml(payload)
        ]
        assert names == [
            f"{TABLE_NAME_PREFIX}Summary",
            f"{TABLE_NAME_PREFIX}Summary_2",
        ]

    def test_a_table_name_never_reads_as_a_cell_reference(self) -> None:
        payload = render_workbook([Sheet(name="A1", frame=REPORT.head(1))])
        name = _matched(r'displayName="([^"]+)"', _table_xml(payload)[0])

        assert name == f"{TABLE_NAME_PREFIX}A1"
        assert not re.fullmatch(r"[A-Z]+\d+", name)

    def test_a_table_style_is_applied_when_asked_for(self) -> None:
        payload = render_workbook(
            [_formatted_sheet(table_style="Table Style Medium 2")]
        )

        # xlsxwriter stores the style under Excel's own spelling of it.
        assert 'name="TableStyleMedium2"' in _table_xml(payload)[0]


# ---------------------------------------------------------------------------
# Conditional formatting
# ---------------------------------------------------------------------------


class TestConditionalFormatting:
    @pytest.mark.parametrize("rule", list(ConditionalRule))
    def test_every_rule_reaches_the_file(
        self, rule: ConditionalRule
    ) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    conditional_formats=(ConditionalFormat("Revenue", rule),)
                )
            ]
        )

        assert "<conditionalFormatting" in _sheet_xml(payload)

    def test_a_rule_applies_to_the_data_rows_of_its_own_column(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    conditional_formats=(
                        ConditionalFormat(
                            "Revenue", ConditionalRule.NEGATIVE_RED
                        ),
                    )
                )
            ]
        )
        worksheet = _reopen(payload)["Summary"]

        ranges = [str(entry) for entry in worksheet.conditional_formatting]
        assert any("C2:C4" in entry for entry in ranges)

    def test_several_columns_may_be_formatted_at_once(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    conditional_formats=(
                        ConditionalFormat(
                            "Revenue", ConditionalRule.NEGATIVE_RED
                        ),
                        ConditionalFormat("Cases", ConditionalRule.DATA_BAR),
                    )
                )
            ]
        )

        assert _sheet_xml(payload).count("<conditionalFormatting") == 2

    def test_a_rule_naming_a_hidden_column_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not show"):
            render_workbook(
                [
                    Sheet(
                        name="S",
                        frame=REPORT,
                        columns=(Column("Rep"),),
                        conditional_formats=(
                            ConditionalFormat(
                                "Revenue", ConditionalRule.DATA_BAR
                            ),
                        ),
                    )
                ]
            )

    def test_a_sheet_with_no_rules_has_no_conditional_formatting(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert "<conditionalFormatting" not in _sheet_xml(payload)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


class TestTotals:
    def test_a_totals_row_is_written_beneath_the_data(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    total_row={"Cases": 413, "Revenue": 56550.25}
                )
            ]
        )
        worksheet = _reopen(payload)["Summary"]

        assert worksheet["A5"].value == "Total"
        assert worksheet["B5"].value == 413
        assert worksheet["C5"].value == 56550.25

    def test_a_totals_row_is_readable(self) -> None:
        """Build plan 12D's "readable totals": bold, shaded, ruled off."""
        payload = render_workbook([_formatted_sheet(total_row={"Cases": 413})])
        cell = _reopen(payload)["Summary"]["B5"]

        assert cell.font.bold is True
        assert cell.border.top.style is not None

    def test_a_total_keeps_its_column_format(self) -> None:
        payload = render_workbook(
            [_formatted_sheet(total_row={"Revenue": 56550.25})]
        )
        cell = _reopen(payload)["Summary"]["C5"]

        assert cell.number_format == NUMBER_FORMATS[CellFormat.CURRENCY]

    def test_a_column_with_no_total_is_left_blank(self) -> None:
        payload = render_workbook([_formatted_sheet(total_row={"Cases": 413})])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet["C5"].value is None
        assert worksheet["C5"].font.bold is True

    def test_the_total_label_can_be_set(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(
                    total_row={"Cases": 413}, total_label="All reps"
                )
            ]
        )

        assert _reopen(payload)["Summary"]["A5"].value == "All reps"

    def test_a_total_for_the_first_column_replaces_the_label(self) -> None:
        payload = render_workbook(
            [
                Sheet(
                    name="S",
                    frame=REPORT,
                    columns=(Column("Cases", format=CellFormat.INTEGER),),
                    total_row={"Cases": 413},
                )
            ]
        )

        assert _reopen(payload)["S"]["A5"].value == 413

    def test_a_total_for_a_column_the_sheet_does_not_show_is_refused(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="does not show"):
            render_workbook(
                [
                    Sheet(
                        name="S",
                        frame=REPORT,
                        columns=(Column("Rep"),),
                        total_row={"Cases": 413},
                    )
                ]
            )

    def test_a_sheet_without_a_totals_row_has_none(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet.max_row == 1 + REPORT.height


class TestItCalculatesNothing:
    """Build plan 12D: no business calculation in the formatting layer."""

    def test_a_total_is_whatever_the_caller_supplied(self) -> None:
        """Even a total that is not the sum, because it is not this layer's
        business to decide what a column's total means. A weighted average, a
        prior-year figure and a sum are all legitimate totals, and only the
        caller knows which it calculated.
        """
        payload = render_workbook(
            [_formatted_sheet(total_row={"Cases": 999_999})]
        )

        assert _reopen(payload)["Summary"]["B5"].value == 999_999

    def test_no_total_appears_unless_one_is_supplied(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        assert worksheet[f"B{REPORT.height + 2}"].value is None

    def test_no_value_in_the_table_is_altered(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        worksheet = _reopen(payload)["Summary"]

        rendered = [
            [worksheet.cell(row=row, column=col).value for col in range(1, 5)]
            for row in range(2, 2 + REPORT.height)
        ]
        assert rendered == [
            list(row)[:4] for row in REPORT.select(
                "Rep", "Cases", "Revenue", "Share"
            ).iter_rows()
        ]


class TestNoFormulaIsEverWritten:
    """A file that shows one number and stores another is not acceptable."""

    def test_the_totals_row_holds_values_not_formulas(self) -> None:
        payload = render_workbook(
            [_formatted_sheet(total_row={"Cases": 413, "Revenue": 56550.25})]
        )

        assert "<f>" not in _sheet_xml(payload)

    def test_no_worksheet_in_a_rendered_report_holds_a_formula(self) -> None:
        payload = render_workbook(
            [
                _formatted_sheet(total_row={"Cases": 413}),
                Sheet(name="Detail", frame=REPORT),
            ]
        )
        archive = zipfile.ZipFile(io.BytesIO(payload))

        for name in archive.namelist():
            if name.startswith("xl/worksheets/"):
                assert "<f>" not in archive.read(name).decode("utf-8")

    def test_text_beginning_with_an_equals_sign_stays_text(self) -> None:
        """Build plan section 16, inherited from the export options."""
        frame = pl.DataFrame({"Note": ["=SUM(A1:A9)", "=1+1"]})
        payload = render_workbook([Sheet(name="S", frame=frame)])
        worksheet = _reopen(payload)["S"]

        assert worksheet["A2"].value == "=SUM(A1:A9)"
        assert worksheet["A2"].data_type == "s"


# ---------------------------------------------------------------------------
# Styling and the inherited guards
# ---------------------------------------------------------------------------


class TestConsistentStyling:
    def test_two_sheets_of_one_report_look_the_same(self) -> None:
        payload = render_workbook(
            [_formatted_sheet(), _formatted_sheet(name="Detail")]
        )
        workbook = _reopen(payload)

        first = workbook["Summary"]["A1"]
        second = workbook["Detail"]["A1"]
        assert first.font.bold == second.font.bold
        assert first.fill.fgColor.rgb == second.fill.fgColor.rgb

    def test_the_body_font_is_the_report_font(self) -> None:
        payload = render_workbook([_formatted_sheet()])
        cell = _reopen(payload)["Summary"]["A2"]

        assert cell.font.name == REPORT_FONT
        assert cell.font.size == BODY_FONT_SIZE

    def test_gridlines_can_be_hidden(self) -> None:
        payload = render_workbook([_formatted_sheet(hide_gridlines=True)])

        assert _reopen(payload)["Summary"].sheet_view.showGridLines is False

    def test_gridlines_are_shown_by_default(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert _reopen(payload)["Summary"].sheet_view.showGridLines is not False


class TestTheExportGuardsStillApply:
    """The renderer inherits Phase 6F/7's rules rather than restating them."""

    def test_a_result_the_format_cannot_hold_is_refused(self) -> None:
        frame = pl.DataFrame({"Note": ["x" * (MAX_CELL_CHARACTERS + 1)]})

        with pytest.raises(ExportTooLargeError) as raised:
            render_workbook([Sheet(name="S", frame=frame)])

        assert raised.value.code == "EXPORT_TOO_LARGE"

    def test_every_sheet_is_checked_before_any_is_written(self) -> None:
        """A workbook is never half-built and then abandoned."""
        good = Sheet(name="Good", frame=REPORT)
        bad = Sheet(
            name="Bad",
            frame=pl.DataFrame({"n": ["x" * (MAX_CELL_CHARACTERS + 1)]}),
        )

        with pytest.raises(ExportTooLargeError) as raised:
            render_workbook([good, bad])

        assert raised.value.details["output_label"] == "Bad"

    def test_the_failure_names_the_sheet_that_did_not_fit(self) -> None:
        with pytest.raises(ExportTooLargeError) as raised:
            render_workbook(
                [
                    Sheet(
                        name="Notes",
                        frame=pl.DataFrame(
                            {"n": ["x" * (MAX_CELL_CHARACTERS + 1)]}
                        ),
                    )
                ]
            )

        assert "Notes" in raised.value.message


class TestTheRenderedFileIsRealXlsx:
    def test_the_bytes_are_a_zip_container(self) -> None:
        payload = render_workbook([_formatted_sheet()])

        assert payload[:2] == b"PK"
        assert zipfile.ZipFile(io.BytesIO(payload)).testzip() is None

    def test_the_application_can_read_back_what_it_wrote(self) -> None:
        """Rendered with xlsxwriter, read with the engine that ingests uploads."""
        payload = render_workbook([Sheet(name="Detail", frame=REPORT)])
        reader = fastexcel.read_excel(payload)
        frame = reader.load_sheet_by_name("Detail").to_polars()

        assert frame.columns == REPORT.columns
        assert frame.height == REPORT.height

    def test_rendering_the_same_report_twice_produces_the_same_table(
        self,
    ) -> None:
        first = _reopen(render_workbook([_formatted_sheet()]))["Summary"]
        second = _reopen(render_workbook([_formatted_sheet()]))["Summary"]

        assert [[c.value for c in row] for row in first.iter_rows()] == [
            [c.value for c in row] for row in second.iter_rows()
        ]
