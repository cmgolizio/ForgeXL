"""Run execution, retrieval, preview and download endpoints (build plan 3.12-3.16).

This module is the HTTP boundary and nothing more: it reads the request,
translates it into the vocabulary the services use, and renders what comes
back. All pipeline logic lives in :mod:`app.services.runner`.

Uploads arrive here directly from the browser. They are never proxied through
Next.js and never copied through an intermediate service (build plan section 5).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from app.actions import registry
from app.errors import (
    InvalidRequestError,
    MissingArtifactError,
    UnknownActionError,
    UnknownOutputError,
)
from app.models.schemas import OutputMetadata, PreviewResponse, RunManifest
from app.services import export, preview, run_store, storage
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
        # the runner has copied them into the Run directory.
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

    Only the requested rows are read from the internal Parquet file; the
    complete dataset is never serialised into a response.
    """
    manifest = run_store.get_run(run_id).to_manifest()
    _require_output(manifest, output_id)

    page = preview.read_preview(
        storage.run_paths(manifest.run_id), output_id, offset=offset, limit=limit
    )
    return PreviewResponse(
        run_id=manifest.run_id,
        output_id=output_id,
        columns=page.columns,
        rows=page.rows,
        offset=page.offset,
        limit=page.limit,
        total_rows=page.total_rows,
    )


@router.get("/runs/{run_id}/outputs/{output_id}/download/csv")
def download_output_csv(run_id: str, output_id: str) -> FileResponse:
    """Download one output as CSV (build plan 3.16)."""
    return _download(run_id, output_id, export.CSV_FORMAT)


@router.get("/runs/{run_id}/outputs/{output_id}/download/xlsx")
def download_output_xlsx(run_id: str, output_id: str) -> FileResponse:
    """Download one output as XLSX (build plan 3.16)."""
    return _download(run_id, output_id, export.XLSX_FORMAT)


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


def _download(run_id: str, output_id: str, export_format: str) -> FileResponse:
    """Serve a generated export from its controlled server path.

    Every path component is derived from validated IDs: the Run ID must parse
    as a UUID and the output ID must appear in that Run's manifest. Nothing the
    client sends is used as a path directly (build plan 3.16).
    """
    manifest = run_store.get_run(run_id).to_manifest()
    output = _require_output(manifest, output_id)

    if export_format not in output.formats:
        raise UnknownOutputError(
            f"That output is not available as {export_format.upper()}.",
            details={
                "run_id": manifest.run_id,
                "output_id": output_id,
                "available_formats": list(output.formats),
            },
        )

    path = storage.run_paths(manifest.run_id).export_artifact(output_id, export_format)
    if not path.is_file():
        raise MissingArtifactError(
            "That export is no longer available.",
            details={
                "run_id": manifest.run_id,
                "output_id": output_id,
                "format": export_format,
            },
        )

    return FileResponse(
        path,
        media_type=_DOWNLOAD_MEDIA_TYPES[export_format],
        filename=f"{output_id}.{export_format}",
    )