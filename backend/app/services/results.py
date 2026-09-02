"""Describing what an Action produced (build plan 6E.1, 6E.4).

An Action hands the runner result DataFrames. This module turns one of those
frames into the metadata a client needs in order to understand and render it:
its schema, how it compares to what was uploaded, and the counts that describe
it.

Everything here is *measured*. `input_row_count` is the number of rows the Run
actually received; `columns_added` and `columns_removed` are set differences
over real column names. Nothing infers what a transformation meant to do — a
row-count difference is reported as two row counts, never relabelled as an
effect (build plan section 3.3).

Kept separate from :mod:`app.services.export` and :mod:`app.services.preview`
because both of those, and the runner, need the same description of a frame,
and describing a result twice is how two descriptions come to disagree.

Nothing here is written to disk, and no value produced here is ever added to a
result table: audit and result metadata stay out of the user's data
(build plan 6E.6).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import polars as pl

from app.models.schemas import ColumnKind, ColumnSchema, OutputMetadata


def column_kind(dtype: pl.DataType) -> ColumnKind:
    """Classify a Polars type into the coarse category a table renders by.

    Booleans are checked before numbers because Polars treats ``Boolean`` as
    neither numeric nor temporal but a client must not right-align it as a
    number. Anything unrecognised is :attr:`ColumnKind.OTHER` rather than a
    guess, so a Polars type this application has never seen still renders.
    """
    if dtype == pl.Boolean:
        return ColumnKind.BOOLEAN
    if dtype.is_temporal():
        return ColumnKind.TEMPORAL
    if dtype.is_numeric():
        return ColumnKind.NUMBER
    if dtype == pl.String:
        return ColumnKind.TEXT
    return ColumnKind.OTHER


def column_schema(frame: pl.DataFrame) -> tuple[ColumnSchema, ...]:
    """Describe `frame`'s columns, in column order (build plan 6E.4).

    The Polars type name is reported exactly as the engine gives it, so the
    schema is evidence of what the data is rather than a tidied summary.
    """
    return tuple(
        ColumnSchema(name=name, dtype=str(dtype), kind=column_kind(dtype))
        for name, dtype in frame.schema.items()
    )


def input_columns(column_lists: Iterable[Sequence[str]]) -> tuple[str, ...]:
    """Return every column name the Run received, in first-appearance order.

    Ordered by appearance rather than sorted so that the comparison below is
    deterministic and reads in the order the user's own file does.
    """
    seen: dict[str, None] = {}
    for columns in column_lists:
        for name in columns:
            seen.setdefault(name, None)
    return tuple(seen)


def columns_added(
    result_columns: Sequence[str], received: Sequence[str]
) -> tuple[str, ...]:
    """Result columns that appeared in no input — the Action created them."""
    known = set(received)
    return tuple(name for name in result_columns if name not in known)


def columns_removed(
    result_columns: Sequence[str], received: Sequence[str]
) -> tuple[str, ...]:
    """Input columns that are not in this result.

    For an Action that selects a subset of a wider upload — the Product Master
    Builder does exactly that — this is the list of columns the user's file had
    and the result does not.
    """
    kept = set(result_columns)
    return tuple(name for name in received if name not in kept)


def describe_output(
    *,
    output_id: str,
    label: str,
    formats: Sequence[str],
    frame: pl.DataFrame,
    received_columns: Sequence[str],
    received_rows: int,
) -> OutputMetadata:
    """Build the complete result metadata for one output (build plan 6E.1).

    Args:
        output_id: The Action's declared output ID.
        label: The Action's declared output label.
        formats: Export formats this output is offered in.
        frame: The result table itself. Only its shape and schema are read;
            no row leaves it here.
        received_columns: Every column name the Run received, in
            first-appearance order.
        received_rows: Rows the Run received across all of its inputs.
    """
    result_columns = tuple(frame.columns)
    return OutputMetadata(
        id=output_id,
        label=label,
        row_count=frame.height,
        column_count=frame.width,
        columns=result_columns,
        formats=tuple(formats),
        column_schema=column_schema(frame),
        input_row_count=received_rows,
        columns_added=columns_added(result_columns, received_columns),
        columns_removed=columns_removed(result_columns, received_columns),
    )