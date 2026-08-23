"""Persisting an Action's output dataframes (build plan 3.10 and section 28).

Every output is written three ways:

    working/<output-id>.parquet   internal representation, read by the preview
    exports/<output-id>.csv       user-facing export
    exports/<output-id>.xlsx      user-facing export

Parquet is the application's own working format because it preserves the
schema and can be sliced cheaply, so paginating a preview never re-runs the
Action and never re-parses a CSV. The CSV and XLSX files exist for the user.

Actions never call this module; the runner does, once per declared output
(build plan section 24).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.services.storage import RunPaths

PARQUET_FORMAT = "parquet"
CSV_FORMAT = "csv"
XLSX_FORMAT = "xlsx"

#: Export formats offered to the user, in the order the UI should show them.
EXPORT_FORMATS: tuple[str, ...] = (CSV_FORMAT, XLSX_FORMAT)


@dataclass(frozen=True)
class WrittenOutput:
    """Where one output's artifacts ended up."""

    output_id: str
    row_count: int
    column_count: int
    columns: tuple[str, ...]

    #: User-facing export formats successfully written.
    formats: tuple[str, ...]


def write_output(paths: RunPaths, output_id: str, frame: pl.DataFrame) -> WrittenOutput:
    """Write `frame` as Parquet, CSV and XLSX for one output.

    The dataframe is written directly by Polars in each format; it is never
    converted to Python objects on the way out.
    """
    write_parquet(paths, output_id, frame)
    write_csv(paths, output_id, frame)
    write_xlsx(paths, output_id, frame)

    return WrittenOutput(
        output_id=output_id,
        row_count=frame.height,
        column_count=frame.width,
        columns=tuple(frame.columns),
        formats=EXPORT_FORMATS,
    )


def write_parquet(paths: RunPaths, output_id: str, frame: pl.DataFrame) -> None:
    """Write the internal working representation under ``working/``."""
    destination = paths.working_artifact(output_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(destination)


def write_csv(paths: RunPaths, output_id: str, frame: pl.DataFrame) -> None:
    """Write the CSV export under ``exports/``."""
    destination = paths.export_artifact(output_id, CSV_FORMAT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.write_csv(destination)


def write_xlsx(paths: RunPaths, output_id: str, frame: pl.DataFrame) -> None:
    """Write the XLSX export under ``exports/``.

    Polars writes the workbook through xlsxwriter. The worksheet is named after
    the output so the file is self-describing when opened in Excel.
    """
    destination = paths.export_artifact(output_id, XLSX_FORMAT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.write_excel(workbook=destination, worksheet=_worksheet_name(output_id))


def _worksheet_name(output_id: str) -> str:
    """Return a worksheet name Excel will accept for `output_id`.

    Excel limits sheet names to 31 characters. Output IDs are short, safe
    tokens already, so truncation is the only adjustment ever needed.
    """
    return output_id[:31]