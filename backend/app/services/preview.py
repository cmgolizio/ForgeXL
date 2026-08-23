"""Paginated reads of an output dataset (build plan 3.14 and section 31).

The preview reads the internal Parquet file, never the CSV export and never
the Action's result in memory: only the requested rows are decoded, so paging
through a 100,000-row output stays cheap and never re-runs the Action
(build plan section 28).

The whole dataset is never serialised into a response.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.errors import InvalidRequestError, MissingArtifactError
from app.services.storage import RunPaths

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
    paths: RunPaths,
    output_id: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> PreviewPage:
    """Return rows ``offset..offset+limit`` of one output.

    Raises:
        InvalidRequestError: offset or limit is out of range.
        MissingArtifactError: the output's Parquet file is not on disk.
    """
    offset = validate_offset(offset)
    limit = validate_limit(limit)

    parquet_path = paths.working_artifact(output_id)
    if not parquet_path.is_file():
        raise MissingArtifactError(
            "That output's data is no longer available.",
            details={"run_id": paths.run_id, "output_id": output_id},
        )

    frame = pl.scan_parquet(parquet_path)
    columns = tuple(frame.collect_schema().names())
    total_rows = int(frame.select(pl.len()).collect().item())
    page = frame.slice(offset, limit).collect()

    return PreviewPage(
        columns=columns,
        rows=[list(row) for row in page.iter_rows()],
        offset=offset,
        limit=limit,
        total_rows=total_rows,
    )