"""Paginated reads of an output dataset (build plan 3.14 and section 31).

The preview slices the result DataFrame the Run is holding: only the requested
rows leave the frame, so paging through a 100,000-row output stays cheap and
never re-runs the Action.

Until Phase 6D the source was the internal ``working/<id>.parquet`` file that
build plan section 28 required. A Run keeps no files now, so the frame it
already holds is the source, and no intermediary spreadsheet is generated
merely to be read back (build plan 6D.7).

The whole dataset is never serialised into a response. The limit rules —
100 by default, 500 at most, an over-large limit refused rather than clamped —
are unchanged public contract.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.errors import InvalidRequestError

#: Rows returned when the caller does not ask for a specific page size.
DEFAULT_PREVIEW_LIMIT = 100

#: Hard ceiling on one page, so a client cannot request the entire dataset.
MAX_PREVIEW_LIMIT = 500


@dataclass(frozen=True)
class PreviewPage:
    """One page of an output dataset."""

    columns: tuple[str, ...]
    rows: list[list[object]]
    offset: int
    limit: int
    total_rows: int


def validate_offset(offset: int) -> int:
    """Reject a negative offset (build plan 3.15)."""
    if offset < 0:
        raise InvalidRequestError(
            "offset must be zero or greater.", details={"offset": offset}
        )
    return offset


def validate_limit(limit: int) -> int:
    """Reject a limit outside 1..``MAX_PREVIEW_LIMIT`` (build plan 3.15).

    An over-large limit is refused rather than quietly clamped: the caller
    should know it asked for more than the API will serve.
    """
    if limit < 1:
        raise InvalidRequestError(
            "limit must be at least 1.", details={"limit": limit}
        )
    if limit > MAX_PREVIEW_LIMIT:
        raise InvalidRequestError(
            f"limit may not exceed {MAX_PREVIEW_LIMIT}.",
            details={"limit": limit, "maximum": MAX_PREVIEW_LIMIT},
        )
    return limit


def read_preview(
    frame: pl.DataFrame,
    *,
    offset: int = 0,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> PreviewPage:
    """Return rows ``offset..offset+limit`` of one result table.

    `frame` is the table the Run is holding. Only the requested slice is
    converted to Python values; the rest of the frame is never materialised.

    Raises:
        InvalidRequestError: offset or limit is out of range.
    """
    offset = validate_offset(offset)
    limit = validate_limit(limit)

    page = frame.slice(offset, limit)

    return PreviewPage(
        columns=tuple(frame.columns),
        rows=[list(row) for row in page.iter_rows()],
        offset=offset,
        limit=limit,
        total_rows=frame.height,
    )