"""Generating a result's user-facing exports (build plan 3.10, 6D.7, 6F).

An Action's result is a Polars DataFrame held in memory by its Run. This
module turns one or more of those frames into the bytes of a downloadable
file, and names that file:

    result DataFrame  -> CSV bytes
    result DataFrame  -> XLSX bytes (one worksheet)
    result DataFrames -> XLSX bytes (one worksheet each)

Nothing is written to the filesystem. Until Phase 6D each output was written
three ways — ``working/<id>.parquet`` plus ``exports/<id>.csv`` and
``exports/<id>.xlsx`` — and a download served the file back. Build plan 6D
requires a Run to execute with no ``working/`` or ``exports/`` directory, so
the artifacts are gone and the bytes are produced on request instead. Build
plan section 28's internal Parquet working file goes with them: it existed so
a preview could be paged without re-running the Action, and the retained
DataFrame does that directly (recorded as a deliberate reversal in
`docs/implementation-status.md`).

Phase 6F finishes the job:

* **The workbook is genuinely built in memory** (6F.2). xlsxwriter spools its
  parts through the OS temporary directory unless told otherwise, so the
  workbook is opened with ``in_memory`` and never touches a disk at all — not
  even ``/tmp``.
* **A workbook may hold several result tables** (6F.4), one worksheet each, so
  an Action with more than one output can be exported as the single file a
  user would expect.
* **Worksheet names are understandable, valid and collision-safe** (6F.5).
* **Downloads are named by a predictable ForgeXL convention** (6F.6):
  ``forgexl-<action>-<output>-<timestamp>.<ext>``.
* **Nothing is retained** (6F.7). Every call builds its bytes, hands them back
  and releases its buffer; this module holds no cache, and a Run stores no
  rendered export.

Actions never call this module; the download endpoints do, once per request
(build plan section 24).
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

import polars as pl
import xlsxwriter

CSV_FORMAT = "csv"
XLSX_FORMAT = "xlsx"

#: Export formats offered to the user, in the order the UI should show them.
EXPORT_FORMATS: tuple[str, ...] = (CSV_FORMAT, XLSX_FORMAT)

# ---------------------------------------------------------------------------
# Worksheet naming (build plan 6F.5)
# ---------------------------------------------------------------------------

#: Excel refuses a worksheet name longer than this.
MAX_WORKSHEET_NAME_LENGTH = 31

#: Characters Excel refuses inside a worksheet name.
INVALID_WORKSHEET_CHARACTERS = frozenset(":\\/?*[]")

#: Names Excel reserves for itself, compared case-insensitively.
RESERVED_WORKSHEET_NAMES = frozenset({"history"})

#: Used when a label cleans down to nothing at all.
FALLBACK_WORKSHEET_NAME = "Sheet"

# ---------------------------------------------------------------------------
# Download filenames (build plan 6F.6)
# ---------------------------------------------------------------------------

#: Every generated download starts with this, so a ForgeXL export is
#: recognisable in a downloads folder full of other spreadsheets.
FILENAME_PREFIX = "forgexl"

#: UTC, so the name is stable wherever the machine happens to be, and ordered
#: so a downloads folder sorts chronologically.
FILENAME_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

#: Cap on one slug within a filename. Action and output IDs are short tokens;
#: this only stops a pathological ID from producing an unusable filename.
MAX_FILENAME_TOKEN_LENGTH = 60

# ---------------------------------------------------------------------------
# Workbook construction
# ---------------------------------------------------------------------------

#: Options the workbook is opened with.
#:
#: The first is this phase's: ``in_memory`` keeps xlsxwriter from spooling the
#: workbook's parts through the OS temporary directory, which is what it does
#: by default and what build plan 6F.2 asks it not to do.
#:
#: The rest reproduce the defaults Polars applies when it opens the workbook
#: itself, so building the workbook here changes nothing else about the file.
#: ``strings_to_formulas`` is a data-safety rule as well as a default: a cell
#: whose text begins with ``=`` is written as text, never as a formula
#: (build plan section 16).
WORKBOOK_OPTIONS: dict[str, object] = {
    "in_memory": True,
    "strings_to_formulas": False,
    "nan_inf_to_errors": True,
    "default_date_format": "yyyy-mm-dd;@",
    "use_zip64": False,
}

#: Excel's own "show the number as it is" format.
#:
#: Polars otherwise applies ``#,##0.000`` to every numeric column, which adds
#: grouping separators the user's data never had and displays 0.000123 as
#: "0.000". The stored value stays exact either way, but what Excel *shows* is
#: what a reader will trust, so the user's numbers are presented verbatim —
#: the same rule the preview follows (build plan sections 3.3 and 6F.3).
#: Dates keep their format: an Excel date with no format shows as a serial
#: number, which would be strictly less faithful.
GENERAL_NUMBER_FORMAT = "General"


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
    """Return `frame` as CSV bytes (build plan 6F.1).

    Polars writes straight into the buffer; the dataframe is never converted to
    Python objects on the way out. The buffer is released before returning, so
    only the bytes handed to the caller survive the call (build plan 6F.7).
    """
    buffer = io.BytesIO()
    try:
        frame.write_csv(buffer)
        return buffer.getvalue()
    finally:
        buffer.close()


def to_xlsx_bytes(frame: pl.DataFrame, *, worksheet: str) -> bytes:
    """Return `frame` as a real XLSX workbook of one worksheet.

    The single-table case of :func:`to_workbook_bytes`, which is what every
    per-output download uses.
    """
    return to_workbook_bytes(((worksheet, frame),))


def to_workbook_bytes(sheets: Iterable[tuple[str, pl.DataFrame]]) -> bytes:
    """Return one XLSX workbook holding `sheets`, built in memory.

    Args:
        sheets: ``(label, frame)`` pairs, in the order the worksheets should
            appear. The labels are what the Action calls its outputs; they are
            cleaned into valid, unique worksheet names by
            :func:`worksheet_names`.

    Build plan 6F.4: an Action that returns several tables is exported as the
    one workbook a user expects rather than as several files they have to
    reassemble. Build plan 6F.2 and 6F.7: the workbook is assembled in a memory
    buffer, which is released as soon as its bytes have been taken.

    Raises:
        ValueError: `sheets` is empty — a workbook with no worksheet is not a
            file Excel will open.
    """
    entries: Sequence[tuple[str, pl.DataFrame]] = [
        (str(label), frame) for label, frame in sheets
    ]
    if not entries:
        raise ValueError("A workbook must contain at least one worksheet.")

    names = worksheet_names(label for label, _ in entries)

    buffer = io.BytesIO()
    try:
        with xlsxwriter.Workbook(buffer, dict(WORKBOOK_OPTIONS)) as workbook:
            for name, (_, frame) in zip(names, entries, strict=True):
                frame.write_excel(
                    workbook=workbook,
                    worksheet=name,
                    # Every numeric column, asked for verbatim. Derived from
                    # the frame's own schema rather than from a list of Polars
                    # types, so a numeric type this application has never seen
                    # is covered too.
                    column_formats={
                        column: GENERAL_NUMBER_FORMAT
                        for column, dtype in frame.schema.items()
                        if dtype.is_numeric()
                    },
                )
        return buffer.getvalue()
    finally:
        buffer.close()


# ---------------------------------------------------------------------------
# Worksheet names (build plan 6F.5)
# ---------------------------------------------------------------------------


def worksheet_name(label: str) -> str:
    """Return a worksheet name Excel will accept for `label`."""
    return worksheet_names((label,))[0]


def worksheet_names(labels: Iterable[str]) -> tuple[str, ...]:
    """Return one valid, unique worksheet name per label, in order.

    Build plan 6F.5 asks for three things at once, and only the first is about
    a single name:

    * **Understandable.** The Action's own label is kept as written wherever
      Excel allows it, so a worksheet reads "Product Master" rather than an
      internal token.
    * **Valid for Excel.** Characters Excel refuses become spaces, a leading or
      trailing apostrophe is dropped, the name is cut to 31 characters, and the
      reserved name "History" is never handed over.
    * **Collision-safe.** Two labels that clean or truncate to the same name
      are numbered apart. Excel compares sheet names case-insensitively, and so
      does this, because "Result" and "result" would be a duplicate to Excel
      even though they differ here.
    """
    taken: set[str] = set()
    return tuple(
        _unique_worksheet_name(_clean_worksheet_name(label), taken)
        for label in labels
    )


def _clean_worksheet_name(label: str) -> str:
    """Turn one label into a name Excel will accept, ignoring collisions."""
    cleaned = "".join(
        " " if character in INVALID_WORKSHEET_CHARACTERS else character
        for character in str(label)
    )
    # Collapses runs of whitespace introduced by the substitution above, and
    # trims the ends, so "Kept / Rejected" reads "Kept Rejected" rather than
    # "Kept   Rejected".
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned[:MAX_WORKSHEET_NAME_LENGTH].strip(" '")
    return cleaned or FALLBACK_WORKSHEET_NAME


def _unique_worksheet_name(base: str, taken: set[str]) -> str:
    """Return `base`, or `base` numbered so it is free, recording the result.

    A name Excel reserves is treated exactly as a name already in use, which is
    both true and the only outcome that keeps the workbook openable.
    """
    candidate = base
    counter = 1
    while _is_worksheet_name_unavailable(candidate, taken):
        counter += 1
        suffix = f" {counter}"
        trimmed = base[: MAX_WORKSHEET_NAME_LENGTH - len(suffix)].strip(" '")
        candidate = f"{trimmed or FALLBACK_WORKSHEET_NAME}{suffix}"
    taken.add(candidate.casefold())
    return candidate


def _is_worksheet_name_unavailable(candidate: str, taken: set[str]) -> bool:
    folded = candidate.casefold()
    return folded in taken or folded in RESERVED_WORKSHEET_NAMES


# ---------------------------------------------------------------------------
# Download filenames (build plan 6F.6)
# ---------------------------------------------------------------------------


def download_filename(
    *,
    action_id: str,
    extension: str,
    timestamp: datetime,
    output_id: str | None = None,
) -> str:
    """Return the filename one download is offered under.

    The convention is ``forgexl-<action>-<output>-<timestamp>.<ext>``, with the
    output omitted for a whole-Run workbook, e.g.::

        forgexl-product-master-builder-product-master-20260901-034512.xlsx
        forgexl-product-master-builder-20260901-034512.xlsx

    Every part is derived from the Run's own record: the Action it executed,
    the output being downloaded and the moment the Run produced it. Two
    consequences are deliberate — the same output downloaded twice arrives
    under the same name rather than a new one, and two outputs of one Run never
    collide in a downloads folder.

    `timestamp` is rendered in UTC, so a machine that changes timezone does not
    change the name of a Run it already recorded. A naive datetime is read as
    UTC, which is what every Run records.

    The result contains only ``a-z``, ``0-9``, ``-`` and one ``.``, so it is
    safe to place in a ``Content-Disposition`` header verbatim and can never
    carry a directory component out of an Action or output ID.
    """
    tokens = [FILENAME_PREFIX, _slug(action_id)]
    if output_id:
        tokens.append(_slug(output_id))
    tokens.append(_timestamp_token(timestamp))
    return f"{'-'.join(token for token in tokens if token)}.{extension}"


def _slug(value: str) -> str:
    """Reduce one identifier to lowercase words joined by hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:MAX_FILENAME_TOKEN_LENGTH].strip("-")


def _timestamp_token(moment: datetime) -> str:
    """Render `moment` as the sortable UTC stamp a filename carries."""
    in_utc = (
        moment.astimezone(timezone.utc)
        if moment.tzinfo is not None
        else moment.replace(tzinfo=timezone.utc)
    )
    return in_utc.strftime(FILENAME_TIMESTAMP_FORMAT)