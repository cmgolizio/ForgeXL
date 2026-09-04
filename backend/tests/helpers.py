"""Shared test helpers for the backend suite."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence

import polars as pl
import xlsxwriter

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput
from app.services.runner import PendingUpload


def make_action(
    action_id: str,
    *,
    version: str = "1.0.0",
    name: str | None = None,
    required_columns: tuple[str, ...] = (),
) -> Action:
    """Build a throwaway Action for registry and API tests.

    Deliberately not one of the application's real Actions: these tests are
    about the contract and the registry, not about any transformation.
    """
    # Bound to locals because a class body cannot read the enclosing
    # function's names for attributes of the same name.
    declared_version = version
    declared_name = name or action_id.replace("_", " ").title()
    declared_inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=required_columns,
        ),
    )

    class _TestAction(Action):
        id = action_id
        version = declared_version
        name = declared_name
        description = f"Test Action {action_id}."
        inputs = declared_inputs
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"result": inputs["source_file"]})

    return _TestAction()


# ---------------------------------------------------------------------------
# Fixture data builders (Phase 3)
# ---------------------------------------------------------------------------


def csv_bytes(header: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    """Build a small CSV payload from a header and rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def xlsx_bytes(sheets: Mapping[str, Sequence[Sequence[object]]]) -> bytes:
    """Build a workbook from ``{sheet name: rows}``, first row as the header.

    A sheet mapped to an empty row list is created but left blank, which is how
    the worksheet-ambiguity tests express "this sheet holds no data".

    ``strings_to_formulas`` is off. xlsxwriter otherwise writes any cell whose
    text begins with ``=`` as a *formula*, so a fixture containing the literal
    text ``=Not A Formula`` would silently become a formula and read back as
    its computed value. A fixture builder that converts data cannot be used to
    prove the application never does (build plan section 16) — the same reason
    `app.services.export` sets it for real exports. A test that wants a genuine
    formula writes one explicitly with `write_formula`, as `test_parser.py`
    does.
    """
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer, {"in_memory": True, "strings_to_formulas": False}
    )
    for name, rows in sheets.items():
        worksheet = workbook.add_worksheet(name)
        for row_index, row in enumerate(rows):
            worksheet.write_row(row_index, 0, row)
    workbook.close()
    return buffer.getvalue()


def upload(filename: str, payload: bytes) -> PendingUpload:
    """Build a :class:`PendingUpload` over in-memory bytes."""
    return PendingUpload(filename=filename, stream=io.BytesIO(payload))


def upload_file(filename: str, payload: bytes) -> tuple[str, io.BytesIO]:
    """Build the ``(filename, stream)`` pair httpx expects for a file field."""
    return (filename, io.BytesIO(payload))


# ---------------------------------------------------------------------------
# Comparing values across the two upload formats (Phase 6H)
# ---------------------------------------------------------------------------


def normalise_value(value: object) -> object:
    """Render `value` in a form the CSV and XLSX paths can be compared in.

    A whole number written to a spreadsheet comes back as a float, because
    that is the only numeric type a workbook has: `10` uploaded as CSV parses
    to `Int64` and the same `10` uploaded as XLSX parses to `Float64`. The
    *value* is the same and neither path lost anything, so a test asserting an
    expected row must not fail over the difference.

    Only that difference is normalised. Text stays text, `None` stays `None`,
    and a bool is left alone rather than being turned into a number, so a
    genuine change of value still fails the comparison.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def normalise_rows(
    rows: Iterable[Sequence[object]],
) -> list[tuple[object, ...]]:
    """Apply :func:`normalise_value` across a table of rows."""
    return [tuple(normalise_value(value) for value in row) for row in rows]
