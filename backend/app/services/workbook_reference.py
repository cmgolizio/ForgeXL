"""Reference-report presentation using the existing workbook engine.

No source access, grouping, sums or ratios. All cells, subtotals and footers
arrive from the report model. Excel outlines reproduce the grouped display;
these are finished report ranges, not editable PivotTable definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.workbook import NUMBER_FORMATS, Sheet

if TYPE_CHECKING:
    import polars as pl
    import xlsxwriter


def render_reference_sheet(
    workbook: xlsxwriter.Workbook, name: str, table_name: str,
    sheet: Sheet, frame: pl.DataFrame,
) -> None:
    layout = sheet.reference_layout
    assert layout is not None
    ws = workbook.add_worksheet(name)
    ws.set_default_row(16)
    ws.outline_settings(True, False, False, False)
    columns = sheet.resolved_columns()
    count = len(columns)
    kinds = layout.row_roles or ("detail",) * frame.height
    is_table = layout.kind in {"sales", "comparison"}
    comparison = layout.kind == "comparison"
    accent = "#0E5C7A" if comparison else "#196B24"
    stripe = "#C0E6F5" if comparison else "#C1F0C8"
    base = {"font_name": "Aptos Narrow", "font_size": 12, "font_color": "#000000"}
    title = workbook.add_format({**base, "font_size": 20, "bold": True, "align": "center"})
    subtitle = workbook.add_format({**base, "font_size": 16, "align": "center"})
    styles = {}

    def cell_style(index: int, role: str, band: bool = False):
        key = (index, role, band)
        if key not in styles:
            props = {**base, "num_format": NUMBER_FORMATS[columns[index].format]}
            if is_table:
                props.update(font_color=accent)
                if band:
                    props.update(bg_color=stripe)
                if index > 0:
                    props.update(align="center")
                if comparison and index == 1:
                    props.pop("align", None)
                if comparison and index == 5:
                    props.update(font_color="#000000")
            else:
                # Pivot styles in the example give the report range a white
                # background, so worksheet gridlines do not cross its data.
                props.update(bg_color="#FFFFFF")
                if index == 0:
                    props.update(align="left", indent=1 if role == "detail" else 0)
                elif layout.kind in {"samples", "sample_months"}:
                    props.update(align="center")
                if layout.kind in {"samples", "sample_months"} or role in {"group", "total"}:
                    props.update(bold=True)
            if role == "total":
                props.pop("indent", None)
                if comparison:
                    props.update(bold=index in {0, 2, 3, 4}, font_color="#000000")
            if layout.kind == "sales" and count == 3 and index == 2:
                props.update(align="right")
            styles[key] = workbook.add_format(props)
        return styles[key]

    row = 0
    if sheet.title:
        ws.set_row(row, 27)
        ws.merge_range(row, 0, row, count - 1, sheet.title, title)
        row += 1
    for text in (sheet.subtitle or "").splitlines():
        ws.set_row(row, 22)
        ws.merge_range(row, 0, row, count - 1, text, subtitle)
        row += 1
    for values in layout.header_prefix:
        for index, value in enumerate(values):
            if value is not None:
                ws.write(row, index, value, workbook.add_format({**base, "bold": True}))
        row += 1
    header_row = row
    headers = []
    for index, column in enumerate(columns):
        props = {**base, "bold": True}
        if is_table:
            props.update(font_color=accent, top=1, bottom=1, border_color=accent)
        elif layout.kind == "products":
            props.update(bg_color="#C1F0C8")
        else:
            props.update(top=1, bottom=1, border_color="#196B24")
        if index and not (comparison and index == 1):
            props.update(align="center" if layout.kind != "products" else "right")
        header = workbook.add_format(props)
        ws.write(row, index, column.heading, header)
        headers.append({"header": column.heading, "header_format": header,
                        "format": cell_style(index, "detail")})
        # Excel stores padded character widths. XlsxWriter adds five pixels
        # itself; remove that padding to reproduce the example's saved width
        # (within the engine's unavoidable one-pixel rounding).
        width = column.width
        ws.set_column(index, index, None if width is None else max(1, width - 5 / 7))
    if is_table and frame.height:
        # Explicit cell formatting matches the reference's effective built-in
        # Light4/Light2 styling, including in readers without table-style support.
        ws.add_table(header_row, 0, header_row + frame.height, count - 1,
                     {"name": table_name, "columns": headers, "autofilter": True,
                      "style": "Table Style Light 2" if comparison else "Table Style Light 4"})
    row += 1
    for offset, values in enumerate(frame.iter_rows()):
        role = kinds[offset]
        if not is_table and role == "detail":
            ws.set_row(row, None, None, {"level": 1})
        for index, value in enumerate(values):
            fmt = cell_style(index, role, band=is_table and offset % 2 == 0)
            if value is None:
                ws.write_blank(row, index, None, fmt)
            else:
                ws.write(row, index, value, fmt)
        row += 1
    if sheet.total_row is not None:
        for index, column in enumerate(columns):
            value = sheet.total_label if index == 0 else sheet.total_row.get(column.name)
            fmt = cell_style(index, "total")
            if value is None:
                ws.write_blank(row, index, None, fmt)
            else:
                ws.write(row, index, value, fmt)
        row += 1
    if comparison and frame.height:
        for criteria, color, fill in ((">", "#006100", "#C6EFCE"),
                                      ("<", "#9C0006", "#FFC7CE"),
                                      ("==", "#9C5700", "#FFEB9C")):
            ws.conditional_format(header_row + 1, 4, header_row + frame.height, 4,
                                  {"type": "cell", "criteria": criteria, "value": 0,
                                   "format": workbook.add_format({"font_color": color, "bg_color": fill})})
    # Keep the example's compact opening view. Essential qualifications remain
    # in the workbook below the totals and in the unchanged browser audit.
    if sheet.notes:
        row += 1
        note_format = workbook.add_format({**base, "font_size": 11, "text_wrap": True})
        width = sum(column.width or 12 for column in columns)
        for note in sheet.notes:
            ws.set_row(row, max(16, 16 * (1 + int(len(note) / max(1, width)))))
            ws.merge_range(row, 0, row, count - 1, note, note_format)
            row += 1
