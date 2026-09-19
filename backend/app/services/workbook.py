"""Report-quality XLSX rendering (build plan 12D).

:mod:`app.services.export` renders a *result table*: one frame, faithfully,
with nothing added. This module renders a *report*: several worksheets, styled
headers, currency and percentage and date formats, measured column widths,
frozen panes, filters, Excel tables, conditional formatting and a totals row —
the difference between "here is your data back" and "here is a finished file".

It is not a second workbook writer, and that matters. Every rule Phase 6F and
Phase 7 established still applies and is applied from its original home:

* the workbook is opened with :data:`app.services.export.WORKBOOK_OPTIONS`, so
  it is built in memory with no temporary file and no cell of text is ever
  promoted to a formula (build plan 6F.2, section 16);
* every sheet is measured by
  :func:`app.services.export.check_fits_worksheet` before anything is written,
  so a report that would exceed an Excel limit is refused rather than
  truncated (build plan 7B);
* worksheet names are cleaned and de-collided by
  :func:`app.services.export.worksheet_names` (build plan 6F.5);
* the bytes are produced into a buffer and released with the call
  (build plan 6F.7).

Two rules are this module's own.

**It calculates nothing.** Build plan 12D: "Do not implement business
calculations in the XLSX formatting layer. Formatting should render
already-calculated report data." A :class:`Sheet` is handed a frame and, if it
wants one, a totals row whose values the caller has already worked out. This
module chooses fonts, widths and number formats, and never a number.

**It writes no formula.** A totals row is written as literal values, not as
``=SUBTOTAL(...)``. A formula would mean the file *showed* one number and
*stored* another, and a reader that does not evaluate formulas — Polars,
openpyxl, a preview pane — would show something different again from Excel.
That is build plan section 3.3's "valid-looking data" in a new costume, so
ForgeXL does not write one anywhere.

The spreadsheet engine stays behind this module: an Action describes a report
with :class:`Sheet` and :class:`Column` and never imports ``xlsxwriter``,
which the Phase 6A contract freeze forbids it.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, cast

import polars as pl
import xlsxwriter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from polars._typing import ConditionalFormatDict

from app.services.export import (
    GENERAL_NUMBER_FORMAT,
    WORKBOOK_OPTIONS,
    check_fits_worksheet,
    worksheet_names,
)

# ---------------------------------------------------------------------------
# Number formats (build plan 12D)
# ---------------------------------------------------------------------------


class CellFormat(str, Enum):
    """How one column's values should be displayed.

    A closed set, like :class:`~app.models.schemas.ColumnKind`: a report picks
    a presentation, not an arbitrary Excel format string. Keeping the set
    closed is what makes two reports look like the same application produced
    them, which is build plan 12D's "consistent styling".

    :attr:`GENERAL` is the default and is the one Phase 6F already applies to
    every numeric column of a plain export: it shows the stored number exactly
    as it is, with no grouping separators the user's data never had.
    """

    GENERAL = "general"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    CURRENCY_WHOLE = "currency_whole"
    PERCENT = "percent"
    PERCENT_DECIMAL = "percent_decimal"
    DATE = "date"
    MONTH = "month"
    DATETIME = "datetime"


#: The Excel number format each :class:`CellFormat` renders as.
#:
#: The currency formats put negatives in parentheses, which is what an
#: accounting reader expects and what makes a negative figure legible at a
#: glance in a column of positives.
NUMBER_FORMATS: dict[CellFormat, str] = {
    CellFormat.GENERAL: GENERAL_NUMBER_FORMAT,
    CellFormat.TEXT: "@",
    CellFormat.INTEGER: "#,##0",
    CellFormat.DECIMAL: "#,##0.00",
    CellFormat.CURRENCY: "$#,##0.00;($#,##0.00)",
    CellFormat.CURRENCY_WHOLE: "$#,##0;($#,##0)",
    CellFormat.PERCENT: "0%",
    CellFormat.PERCENT_DECIMAL: "0.0%",
    CellFormat.DATE: "yyyy-mm-dd",
    CellFormat.MONTH: "yyyy-mm",
    CellFormat.DATETIME: "yyyy-mm-dd hh:mm",
}

#: Roughly how wide a value in each format renders, in characters. Used to size
#: a column whose values are not text and so cannot be measured directly.
_FORMAT_WIDTHS: dict[CellFormat, int] = {
    CellFormat.GENERAL: 12,
    CellFormat.TEXT: 16,
    CellFormat.INTEGER: 12,
    CellFormat.DECIMAL: 14,
    CellFormat.CURRENCY: 16,
    CellFormat.CURRENCY_WHOLE: 14,
    CellFormat.PERCENT: 10,
    CellFormat.PERCENT_DECIMAL: 10,
    CellFormat.DATE: 12,
    CellFormat.MONTH: 10,
    CellFormat.DATETIME: 18,
}


# ---------------------------------------------------------------------------
# Styling (build plan 12D, "consistent styling")
# ---------------------------------------------------------------------------

#: One palette for every report this application produces. Defined here rather
#: than per report so two reports cannot drift apart.
HEADER_BACKGROUND = "#1F2937"
HEADER_FONT_COLOR = "#FFFFFF"
TITLE_FONT_COLOR = "#111827"
TOTAL_BACKGROUND = "#F3F4F6"
BORDER_COLOR = "#D1D5DB"

#: The font every report uses, and its sizes.
REPORT_FONT = "Calibri"
BODY_FONT_SIZE = 11
TITLE_FONT_SIZE = 14
SUBTITLE_FONT_SIZE = 10

#: Column width bounds. The lower bound keeps a one-character header readable;
#: the upper stops one long free-text value from pushing every other column off
#: the screen.
MIN_COLUMN_WIDTH = 9.0
MAX_COLUMN_WIDTH = 60.0

#: Padding added to a measured width so values do not touch the cell border.
COLUMN_WIDTH_PADDING = 2.0

#: Pixels per character-width unit, at the font size a report uses.
#:
#: Widths are reasoned about here in Excel's own unit — "how many digits fit" —
#: because that is the unit a column width means something in. Polars takes
#: them in pixels, so :func:`_width_pixels` converts at the boundary. The
#: factor is xlsxwriter's own maximum digit width, confirmed against a rendered
#: workbook rather than assumed.
PIXELS_PER_CHARACTER = 7

#: Rows sampled when measuring a text column's width. A report is read at the
#: top, and measuring every row of a large sheet costs more than it is worth.
WIDTH_SAMPLE_ROWS = 2_000

#: Height of the styled header row, in points.
DEFAULT_HEADER_HEIGHT = 22.0

#: Height of the title row, in points.
TITLE_ROW_HEIGHT = 24.0

#: Every sheet's data is written as a real Excel table, which is what gives it
#: filter buttons and banding. Excel names tables workbook-wide and refuses a
#: name containing a space or one that reads as a cell reference, so a name is
#: derived from the worksheet's and prefixed rather than used as written. The
#: prefix is what makes ``Q1`` safe: ``tbl_Q1`` cannot be mistaken for a cell.
TABLE_NAME_PREFIX = "tbl_"

#: Characters Excel accepts inside a table name, after the prefix.
_UNSAFE_TABLE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9_.]+")


class ConditionalRule(str, Enum):
    """The conditional formats a report may apply to a column.

    Closed, for the same reason :class:`CellFormat` is. Each one is a rule a
    reader can interpret without a legend.
    """

    #: Negative values in red.
    NEGATIVE_RED = "negative_red"

    #: Positive values in green.
    POSITIVE_GREEN = "positive_green"

    #: An in-cell bar proportional to the value.
    DATA_BAR = "data_bar"

    #: A three-colour scale across the column's range.
    COLOR_SCALE = "color_scale"


@dataclass(frozen=True)
class ConditionalFormat:
    """One conditional format applied to one column of a sheet."""

    column: str
    rule: ConditionalRule


@dataclass(frozen=True)
class Column:
    """How one column of a report sheet is presented.

    `name` is the column in the frame. `header` is what the reader sees, and
    defaults to `name`, so a report that is happy with its own column names
    says nothing. `width` overrides the measured width when a report knows
    better than the measurement does.
    """

    name: str
    header: str | None = None
    format: CellFormat = CellFormat.GENERAL
    width: float | None = None

    @property
    def heading(self) -> str:
        """What this column is labelled in the rendered sheet."""
        return self.header if self.header is not None else self.name


@dataclass(frozen=True)
class Sheet:
    """One worksheet of a report.

    Everything here is presentation. The numbers arrive already calculated in
    `frame` and, where there is one, in `total_row` (build plan 12D).
    """

    #: What this worksheet is called. Cleaned and de-collided against the other
    #: sheets by :func:`app.services.export.worksheet_names`.
    name: str

    #: The already-calculated data this sheet shows.
    frame: pl.DataFrame

    #: How each column is presented, in the order the columns should appear.
    #: Empty means "every column of the frame, in its own order, unformatted",
    #: which is what a plain table sheet wants.
    columns: tuple[Column, ...] = ()

    #: An optional heading written above the table.
    title: str | None = None

    #: An optional second line under the title — a reporting period, say.
    subtitle: str | None = None

    #: Keep the header row visible while the reader scrolls.
    freeze_header: bool = True

    #: Show Excel's filter buttons on the header row.
    autofilter: bool = True

    #: Excel table style, e.g. "Table Style Medium 2". None renders the sheet
    #: as a plain banded range with this application's own header styling.
    table_style: str | None = None

    #: Already-calculated totals keyed by frame column name, written as
    #: literal values in a styled row beneath the data. Never a formula.
    total_row: Mapping[str, object] | None = None

    #: What the totals row is labelled, in its first column.
    total_label: str = "Total"

    #: Conditional formats to apply to this sheet's columns.
    conditional_formats: tuple[ConditionalFormat, ...] = ()

    #: Height of every data row, in points. None leaves Excel's default.
    row_height: float | None = None

    #: Height of the header row, in points.
    header_height: float = DEFAULT_HEADER_HEIGHT

    #: Hide the worksheet grid, which reads as a finished document rather than
    #: as a spreadsheet.
    hide_gridlines: bool = False

    def resolved_columns(self) -> tuple[Column, ...]:
        """This sheet's columns, filled in from the frame when unstated."""
        if self.columns:
            return self.columns
        return tuple(Column(name=name) for name in self.frame.columns)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_workbook(sheets: Sequence[Sheet]) -> bytes:
    """Render `sheets` as one formatted XLSX workbook, built in memory.

    The worksheets appear in the order given — build plan 12D's "worksheet
    ordering" is simply the order of this sequence, so a report controls it by
    listing its sheets in the order a reader should meet them.

    Every sheet is checked against the XLSX format's capacity before any of
    them is written, so a workbook is never half-built and then abandoned.

    Args:
        sheets: The report, one entry per worksheet.

    Returns:
        The workbook's bytes.

    Raises:
        ValueError: `sheets` is empty, or a sheet names a column its frame
            does not have.
        ExportTooLargeError: a sheet does not fit the XLSX format.
    """
    entries = tuple(sheets)
    if not entries:
        raise ValueError("A workbook must contain at least one worksheet.")

    prepared = [(sheet, _prepare(sheet)) for sheet in entries]

    for sheet, frame in prepared:
        check_fits_worksheet(frame, label=sheet.name)

    names = worksheet_names(sheet.name for sheet in entries)
    table_names = _table_names(names)

    buffer = io.BytesIO()
    try:
        with xlsxwriter.Workbook(buffer, dict(WORKBOOK_OPTIONS)) as workbook:
            styles = _Styles(workbook)
            for name, table_name, (sheet, frame) in zip(
                names, table_names, prepared, strict=True
            ):
                _render_sheet(
                    workbook, styles, name, table_name, sheet, frame
                )
        return buffer.getvalue()
    finally:
        buffer.close()


def render_sheet_bytes(sheet: Sheet) -> bytes:
    """Render one :class:`Sheet` as a single-worksheet workbook."""
    return render_workbook((sheet,))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _Styles:
    """The formats one workbook uses, created once and shared by every sheet.

    xlsxwriter creates a new format object per call, and a workbook with a
    format per cell is both large and slow to open. Caching them here is also
    what makes the styling consistent rather than merely similar.
    """

    def __init__(self, workbook: xlsxwriter.Workbook) -> None:
        self._workbook = workbook
        self._cells: dict[tuple[CellFormat, bool], object] = {}

        self.title = workbook.add_format(
            {
                "bold": True,
                "font_name": REPORT_FONT,
                "font_size": TITLE_FONT_SIZE,
                "font_color": TITLE_FONT_COLOR,
            }
        )
        self.subtitle = workbook.add_format(
            {
                "font_name": REPORT_FONT,
                "font_size": SUBTITLE_FONT_SIZE,
                "font_color": TITLE_FONT_COLOR,
                "italic": True,
            }
        )
        self.header = {
            "bold": True,
            "font_name": REPORT_FONT,
            "font_size": BODY_FONT_SIZE,
            "font_color": HEADER_FONT_COLOR,
            "bg_color": HEADER_BACKGROUND,
            "align": "left",
            "valign": "vcenter",
            "text_wrap": True,
            "border": 1,
            "border_color": BORDER_COLOR,
        }

    def cell(self, cell_format: CellFormat, *, total: bool = False) -> object:
        """Return the format for a value, creating it on first use."""
        key = (cell_format, total)
        if key not in self._cells:
            properties: dict[str, object] = {
                "font_name": REPORT_FONT,
                "font_size": BODY_FONT_SIZE,
                "num_format": NUMBER_FORMATS[cell_format],
            }
            if total:
                properties.update(
                    {
                        "bold": True,
                        "bg_color": TOTAL_BACKGROUND,
                        "top": 1,
                        "border_color": BORDER_COLOR,
                    }
                )
            self._cells[key] = self._workbook.add_format(properties)
        return self._cells[key]


def _prepare(sheet: Sheet) -> pl.DataFrame:
    """Select and rename `sheet`'s columns into the frame it renders.

    Selecting here rather than at write time is what makes `columns` mean
    "these columns, in this order": a report that lists three of a frame's ten
    columns gets a three-column sheet.
    """
    columns = sheet.resolved_columns()
    available = set(sheet.frame.columns)

    missing = [column.name for column in columns if column.name not in available]
    if missing:
        raise ValueError(
            f"Sheet {sheet.name!r} names columns its data does not have: "
            f"{', '.join(missing)}."
        )

    headings = [column.heading for column in columns]
    if len(set(headings)) != len(headings):
        duplicates = sorted(
            {heading for heading in headings if headings.count(heading) > 1}
        )
        raise ValueError(
            f"Sheet {sheet.name!r} gives two columns the same heading: "
            f"{', '.join(repr(name) for name in duplicates)}."
        )

    return sheet.frame.select(
        pl.col(column.name).alias(column.heading) for column in columns
    )


def _render_sheet(
    workbook: xlsxwriter.Workbook,
    styles: _Styles,
    name: str,
    table_name: str,
    sheet: Sheet,
    frame: pl.DataFrame,
) -> None:
    """Write one worksheet: title, table, totals row, conditional formats."""
    worksheet = workbook.add_worksheet(name)
    columns = sheet.resolved_columns()

    if sheet.hide_gridlines:
        worksheet.hide_gridlines(2)

    header_row = _write_heading(worksheet, styles, sheet)
    first_data_row = header_row + 1

    worksheet.set_row(header_row, sheet.header_height)
    if sheet.row_height is not None:
        # One default rather than one call per row: a report with 50,000 rows
        # would otherwise carry 50,000 row records.
        worksheet.set_default_row(sheet.row_height)

    frame.write_excel(
        workbook=workbook,
        worksheet=worksheet,
        position=(header_row, 0),
        table_style=sheet.table_style,
        table_name=table_name,
        include_header=True,
        autofilter=sheet.autofilter,
        header_format=styles.header,
        column_formats={
            column.heading: NUMBER_FORMATS[column.format] for column in columns
        },
        column_widths={
            column.heading: _width_pixels(_column_width(column, frame))
            for column in columns
        },
        # Polars types this parameter with a key of
        # ``ColumnNameOrSelector | Collection[str]``, and a Mapping's key type
        # is invariant, so a plain ``dict[str, ...]`` does not satisfy it even
        # though every key this passes is a column name. The cast states that
        # rather than widening the helper's own return type, which is exact
        # and useful.
        conditional_formats=cast(
            "ConditionalFormatDict", _conditional_formats(sheet, columns)
        ),
        freeze_panes=(first_data_row, 0) if sheet.freeze_header else None,
    )

    if sheet.total_row is not None:
        _write_total_row(
            worksheet,
            styles,
            sheet,
            columns,
            row=first_data_row + frame.height,
        )


def _write_heading(
    worksheet, styles: _Styles, sheet: Sheet
) -> int:
    """Write the title and subtitle, returning the row the header goes on.

    A sheet with neither starts its table at row 0, so an unadorned sheet
    renders exactly as :mod:`app.services.export` would have rendered it.
    """
    row = 0
    if sheet.title:
        worksheet.set_row(row, TITLE_ROW_HEIGHT)
        worksheet.write(row, 0, sheet.title, styles.title)
        row += 1
    if sheet.subtitle:
        worksheet.write(row, 0, sheet.subtitle, styles.subtitle)
        row += 1
    # One blank row between the heading and the table, when there is a heading.
    return row + 1 if row else 0


def _write_total_row(
    worksheet,
    styles: _Styles,
    sheet: Sheet,
    columns: Sequence[Column],
    *,
    row: int,
) -> None:
    """Write the already-calculated totals beneath the table.

    Literal values only. See this module's docstring: ForgeXL never writes a
    formula, so what the file shows and what it stores are the same number.

    A column with no total gets an empty styled cell, so the row reads as one
    band across the table rather than as a scatter of figures.
    """
    totals = dict(sheet.total_row or {})
    unknown = sorted(
        key for key in totals if key not in {column.name for column in columns}
    )
    if unknown:
        raise ValueError(
            f"Sheet {sheet.name!r} has totals for columns it does not show: "
            f"{', '.join(unknown)}."
        )

    for index, column in enumerate(columns):
        if index == 0 and column.name not in totals:
            worksheet.write(
                row, index, sheet.total_label, styles.cell(CellFormat.TEXT, total=True)
            )
            continue
        value = totals.get(column.name)
        style = styles.cell(column.format, total=True)
        if value is None:
            worksheet.write_blank(row, index, None, style)
        else:
            worksheet.write(row, index, value, style)


def _conditional_formats(
    sheet: Sheet, columns: Sequence[Column]
) -> dict[str, dict[str, object]]:
    """Translate this sheet's rules into what Polars hands xlsxwriter."""
    if not sheet.conditional_formats:
        return {}

    headings = {column.name: column.heading for column in columns}
    rendered: dict[str, dict[str, object]] = {}

    for entry in sheet.conditional_formats:
        heading = headings.get(entry.column)
        if heading is None:
            raise ValueError(
                f"Sheet {sheet.name!r} conditionally formats {entry.column!r}, "
                "which it does not show."
            )
        rendered[heading] = dict(_CONDITIONAL_RULES[entry.rule])

    return rendered


#: What each :class:`ConditionalRule` becomes in xlsxwriter's vocabulary.
#:
#: The two "cell" rules name their colours here rather than taking a workbook
#: format, because Polars builds the format object itself from this dictionary.
_CONDITIONAL_RULES: dict[ConditionalRule, dict[str, object]] = {
    ConditionalRule.NEGATIVE_RED: {
        "type": "cell",
        "criteria": "<",
        "value": 0,
        "format": {"font_color": "#991B1B", "bold": True},
    },
    ConditionalRule.POSITIVE_GREEN: {
        "type": "cell",
        "criteria": ">",
        "value": 0,
        "format": {"font_color": "#166534"},
    },
    ConditionalRule.DATA_BAR: {"type": "data_bar", "bar_solid": True},
    ConditionalRule.COLOR_SCALE: {"type": "3_color_scale"},
}


def _column_width(column: Column, frame: pl.DataFrame) -> float:
    """Return how wide `column` should be, measured or declared.

    A declared width wins. Otherwise a text column is measured against its own
    values — the only honest way to size one — and every other column is sized
    from its format, because "$1,234.56" is a known width whatever the number
    happens to be. The header is always accounted for, so a short column with
    a long name is still readable.
    """
    if column.width is not None:
        return float(column.width)

    heading = column.heading
    widest = len(heading)

    if frame.schema[heading] == pl.String and frame.height:
        sample = frame.get_column(heading).head(WIDTH_SAMPLE_ROWS)
        longest = sample.str.len_chars().max()
        # `max()` is typed as any Python literal because a Series may hold one;
        # on a character count it is an int or, for an all-null column, None.
        if isinstance(longest, int):
            widest = max(widest, longest)
    else:
        widest = max(widest, _FORMAT_WIDTHS[column.format])

    return min(
        MAX_COLUMN_WIDTH, max(MIN_COLUMN_WIDTH, widest + COLUMN_WIDTH_PADDING)
    )


def _width_pixels(width: float) -> int:
    """Convert a character-unit column width into the pixels Polars takes."""
    return max(1, round(width * PIXELS_PER_CHARACTER))


def _table_names(worksheet_names_: Sequence[str]) -> tuple[str, ...]:
    """Return one legal, workbook-unique table name per worksheet, in order.

    Derived from the worksheet names rather than generated, so a reader who
    opens Excel's name manager sees ``tbl_Summary`` rather than ``Frame0``.
    Uniqueness is enforced here because Excel scopes table names to the whole
    workbook, not to a worksheet.
    """
    taken: set[str] = set()
    names: list[str] = []
    for worksheet in worksheet_names_:
        base = _UNSAFE_TABLE_NAME_CHARACTERS.sub("_", worksheet).strip("_")
        candidate = f"{TABLE_NAME_PREFIX}{base or 'sheet'}"
        counter = 1
        while candidate.casefold() in taken:
            counter += 1
            candidate = f"{TABLE_NAME_PREFIX}{base or 'sheet'}_{counter}"
        taken.add(candidate.casefold())
        names.append(candidate)
    return tuple(names)