"""Run execution, retrieval, preview and download endpoints (build plan 3.12-3.16).

This module is the HTTP boundary and nothing more: it reads the request,
translates it into the vocabulary the services use, and renders what comes
back. All pipeline logic lives in :mod:`app.services.runner`.

Uploads arrive here directly from the browser. They are never proxied through
Next.js and never copied through an intermediate service (build plan section 5).

Since Phase 6D nothing here touches the filesystem. A Run holds its result
tables in memory, so the preview slices one of those frames and a download
renders one into bytes on the way out; no path is built, opened or served.

Phase 6F completes the download side (6F.6): every export is offered under the
ForgeXL filename convention, built from the Run's own record, and a Run with
several result tables can be downloaded as one workbook. No response carries a
server path, because there is no server path to carry (6F.8).
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
from fastapi import APIRouter, Query, Request, Response
from starlette.datastructures import UploadFile

from app.actions import registry
from app.errors import (
    InvalidRequestError,
    MissingArtifactError,
    UnknownActionError,
    UnknownOutputError,
)
from app.models.run import Run
from app.models.schemas import OutputMetadata, PreviewResponse, RunManifest
from app.services import export, preview, run_store
from app.services.runner import PendingUpload, execute_run

router = APIRouter(prefix="/api", tags=["runs"])

ACTION_ID_FIELD = "action_id"

#: Content types for the two user-facing export formats.
_DOWNLOAD_MEDIA_TYPES = {
    export.CSV_FORMAT: "text/csv",
    export.XLSX_FORMAT: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}


@router.post("/runs", response_model=RunManifest)
async def create_run(request: Request) -> RunManifest:
    """Execute one Action against uploaded files.

    The request is ``multipart/form-data`` carrying ``action_id`` plus one file
    field per Action input slot, named with that slot's ID. Files are submitted
    under their slot names rather than as one anonymous list, so an Action with
    several inputs needs no special handling (build plan 3.12).

    The Run executes synchronously; the POC deliberately has no job queue.
    """
    async with request.form() as form:
        raw_action_id = form.get(ACTION_ID_FIELD)
        if not isinstance(raw_action_id, str) or not raw_action_id.strip():
            raise InvalidRequestError(
                "action_id is required.", details={"field": ACTION_ID_FIELD}
            )
        action_id = raw_action_id.strip()

        action = registry.get_action(action_id)
        if action is None:
            raise UnknownActionError(
                "That Action does not exist.", details={"action_id": action_id}
            )

        uploads = {
            field: PendingUpload(filename=value.filename or "", stream=value.file)
            for field, value in form.multi_items()
            if isinstance(value, UploadFile)
        }

        # Runs inside the form context: the uploaded streams stay open until
        # the runner has read them into memory (build plan 6C.3). Nothing the
        # user uploads is written to the server's filesystem.
        return execute_run(action, uploads).manifest


@router.get("/runs/{run_id}", response_model=RunManifest)
def get_run(run_id: str) -> RunManifest:
    """Return the manifest for one Run (build plan 3.13).

    Read from the Run Store, which owns run state (build plan 6B). A malformed
    or unknown Run ID produces 404. The manifest never contains filesystem
    paths, only logical IDs (build plan section 11).
    """
    return run_store.get_run(run_id).to_manifest()


@router.get(
    "/runs/{run_id}/outputs/{output_id}/preview", response_model=PreviewResponse
)
def get_output_preview(
    run_id: str,
    output_id: str,
    offset: int = Query(default=0, description="First row to return."),
    limit: int = Query(
        default=preview.DEFAULT_PREVIEW_LIMIT,
        description=f"Rows to return, at most {preview.MAX_PREVIEW_LIMIT}.",
    ),
) -> PreviewResponse:
    """Return one page of an output dataset (build plan 3.15).

    Only the requested rows are sliced out of the Run's result table; the
    complete dataset is never serialised into a response.
    """
    run = run_store.get_run(run_id)
    _require_output(run.to_manifest(), output_id)

    page = preview.read_preview(
        _result_table(run, output_id), offset=offset, limit=limit
    )
    return PreviewResponse(
        run_id=run.run_id,
        output_id=output_id,
        columns=page.columns,
        rows=page.rows,
        offset=page.offset,
        limit=page.limit,
        total_rows=page.total_rows,
        column_schema=page.column_schema,
    )


@router.get("/runs/{run_id}/outputs/{output_id}/download/csv")
def download_output_csv(run_id: str, output_id: str) -> Response:
    """Download one output as CSV (build plan 3.16, 6F.1)."""
    return _download(run_id, output_id, export.CSV_FORMAT)


@router.get("/runs/{run_id}/outputs/{output_id}/download/xlsx")
def download_output_xlsx(run_id: str, output_id: str) -> Response:
    """Download one output as XLSX (build plan 3.16, 6F.2)."""
    return _download(run_id, output_id, export.XLSX_FORMAT)


@router.get("/runs/{run_id}/download/xlsx")
def download_run_xlsx(run_id: str) -> Response:
    """Download every result table of one Run as a single workbook.

    Build plan 6F.4: an Action may return more than one table, and those tables
    belong together. Each becomes its own worksheet, in the order the Action
    declares its outputs, so the primary result is the first sheet. An Action
    with one output produces a one-worksheet workbook, which is the same file
    its per-output download produces.

    The per-output endpoints are unchanged and remain the way to fetch one
    table on its own, or to fetch anything as CSV — a CSV file holds one table
    by definition, so there is no whole-Run CSV.
    """
    run = run_store.get_run(run_id)
    sheets = _result_sheets(run)

    return _attachment(
        export.to_workbook_bytes(sheets),
        export.XLSX_FORMAT,
        filename=export.download_filename(
            action_id=run.action.id,
            extension=export.XLSX_FORMAT,
            timestamp=_result_timestamp(run),
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_output(manifest: RunManifest, output_id: str) -> OutputMetadata:
    """Return the manifest entry for `output_id`, or raise 404.

    The output ID is matched against what the Run actually recorded rather than
    against the filesystem, so an unknown ID is answered from the manifest and
    never reaches a path.
    """
    for output in manifest.outputs:
        if output.id == output_id:
            return output
    raise UnknownOutputError(
        "That Run has no such output.",
        details={
            "run_id": manifest.run_id,
            "output_id": output_id,
            "available_output_ids": [output.id for output in manifest.outputs],
        },
    )


def _result_table(run: Run, output_id: str) -> pl.DataFrame:
    """Return the Run's retained table for `output_id`.

    The output has already been confirmed against the Run's manifest, so a
    table that is nevertheless absent means the Run no longer holds its
    result — a failed Run, or one whose result has been released. That is a
    missing artifact, not an unknown output.
    """
    table = run.result.table(output_id) if run.result is not None else None
    if table is None:
        raise MissingArtifactError(
            "That output's data is no longer available.",
            details={"run_id": run.run_id, "output_id": output_id},
        )
    return table


def _result_sheets(run: Run) -> list[tuple[str, pl.DataFrame]]:
    """Return every result table of `run`, labelled, in declaration order.

    Walks the Run's recorded outputs rather than its result mapping, so the
    worksheets appear in the order the Action declares them and each one is
    labelled the way the rest of the interface labels it.

    A Run with no result at all — a failed Run, or one whose result has been
    released — has nothing to export, which is a missing artifact rather than
    an unknown output.
    """
    if not run.outputs or run.result is None:
        raise MissingArtifactError(
            "That Run has no result data available.",
            details={"run_id": run.run_id},
        )
    return [
        (output.label, _result_table(run, output.id)) for output in run.outputs
    ]


def _result_timestamp(run: Run) -> datetime:
    """The moment a download's filename is stamped with.

    The Run's completion, so re-downloading an output produces the same
    filename rather than a new one each time. A Run still running has not
    completed, so its creation stands in.
    """
    return run.completed_at or run.created_at


def _attachment(payload: bytes, export_format: str, *, filename: str) -> Response:
    """Send `payload` as a download named `filename`.

    `filename` comes from :func:`app.services.export.download_filename`, which
    emits only ``a-z``, ``0-9``, ``-`` and a single ``.`` — nothing the client
    supplied reaches this header, and nothing here names a server location
    (build plan 6F.6, 6F.8).
    """
    return Response(
        content=payload,
        media_type=_DOWNLOAD_MEDIA_TYPES[export_format],
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _download(run_id: str, output_id: str, export_format: str) -> Response:
    """Generate one export from the Run's result table and send it.

    Nothing is read from or written to the filesystem: the bytes are rendered
    from the retained DataFrame for this request and released with the
    response. The Run ID must parse as a UUID and the output ID must appear in
    that Run's manifest, so nothing the client sends is used to reach data it
    did not ask for (build plan 3.16).
    """
    run = run_store.get_run(run_id)
    output = _require_output(run.to_manifest(), output_id)

    if export_format not in output.formats:
        raise UnknownOutputError(
            f"That output is not available as {export_format.upper()}.",
            details={
                "run_id": run.run_id,
                "output_id": output_id,
                "available_formats": list(output.formats),
            },
        )

    payload = export.to_bytes(
        _result_table(run, output_id), export_format, name=output.label
    )

    return _attachment(
        payload,
        export_format,
        filename=export.download_filename(
            action_id=run.action.id,
            output_id=output_id,
            extension=export_format,
            timestamp=_result_timestamp(run),
        ),
    )