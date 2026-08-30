"""Generating a result's user-facing exports (build plan 3.10, 6D.7).

An Action's result is a Polars DataFrame held in memory by its Run. This
module turns one of those frames into the bytes of a downloadable file:

    result DataFrame -> CSV bytes
    result DataFrame -> XLSX bytes

Nothing is written to the filesystem. Until Phase 6D each output was written
three ways — ``working/<id>.parquet`` plus ``exports/<id>.csv`` and
``exports/<id>.xlsx`` — and a download served the file back. Build plan 6D
requires a Run to execute with no ``working/`` or ``exports/`` directory, so
the artifacts are gone and the bytes are produced on request instead. Build
plan section 28's internal Parquet working file goes with them: it existed so
a preview could be paged without re-running the Action, and the retained
DataFrame does that directly (recorded as a deliberate reversal in
`docs/implementation-status.md`).

Actions never call this module; the download endpoint does, once per request
(build plan section 24).
"""

from __future__ import annotations

import io

import polars as pl

CSV_FORMAT = "csv"
XLSX_FORMAT = "xlsx"

#: Export formats offered to the user, in the order the UI should show them.
EXPORT_FORMATS: tuple[str, ...] = (CSV_FORMAT, XLSX_FORMAT)

#: Excel refuses a worksheet name longer than this.
MAX_WORKSHEET_NAME_LENGTH = 31


def to_bytes(frame: pl.DataFrame, export_format: str, *, name: str) -> bytes:
    """Render `frame` in `export_format`, for an output called `name`.

    Raises:
        ValueError: `export_format` is not one this application generates.
    """
    if export_format == CSV_FORMAT:
        return to_csv_bytes(frame)
    if export_format == XLSX_FORMAT:
        return to_xlsx_bytes(frame, worksheet=name)
    raise ValueError(f"Unsupported export format: {export_format!r}")


def to_csv_bytes(frame: pl.DataFrame) -> bytes:
    """Return `frame` as CSV bytes.

    Polars writes straight into the buffer; the dataframe is never converted to
    Python objects on the way out.
    """
    buffer = io.BytesIO()
    frame.write_csv(buffer)
    return buffer.getvalue()


def to_xlsx_bytes(frame: pl.DataFrame, *, worksheet: str) -> bytes:
    """Return `frame` as a real XLSX workbook, built in memory.

    Polars writes the workbook through xlsxwriter into a memory buffer, so the
    result is the same workbook the on-disk writer produced before Phase 6D —
    same headers, same column order, same values, same single worksheet named
    after the output.
    """
    buffer = io.BytesIO()
    frame.write_excel(workbook=buffer, worksheet=worksheet_name(worksheet))
    return buffer.getvalue()


def worksheet_name(output_id: str) -> str:
    """Return a worksheet name Excel will accept for `output_id`.

    Excel limits sheet names to 31 characters. Output IDs are short, safe
    tokens already, so truncation is the only adjustment ever needed.
    """
    return output_id[:MAX_WORKSHEET_NAME_LENGTH]