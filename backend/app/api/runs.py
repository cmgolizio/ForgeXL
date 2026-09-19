"""Run execution, retrieval, preview and download endpoints (build plan 3.12-3.16).

This module is the HTTP boundary and nothing more: it reads the request,
translates it into the vocabulary the services use, and renders what comes
back. All pipeline logic lives in :mod:`app.services.runner`.

Uploads reach this module through the Next.js same-origin transport at
`/forge-api/*` (build plan 6G.3): the browser never addresses FastAPI, so a
second laptop on the LAN needs nothing but a browser. That hop is transport
only — the Route Handler streams the request body straight through without
reading or parsing it (6G.4) — so the file is still transferred once and is
still copied through no intermediate service that understands it (build plan
section 5). The bytes this endpoint receives are the bytes the browser sent.

Since Phase 6D nothing here touches the filesystem. A Run holds its result
tables in memory, so the preview slices one of those frames and a download
renders one into bytes on the way out; no path is built, opened or served.

Phase 6F completes the download side (6F.6): every export is offered under the
ForgeXL filename convention, built from the Run's own record, and a Run with
several result tables can be downloaded as one workbook. No response carries a
server path, because there is no server path to carry (6F.8).

Phase 12 adds two routes, the first since Phase 6F: one downloads a single
artifact a Run produced, the other bundles every artifact into one ZIP
(build plan 12F, 12G). Both follow the rules the export routes already follow —
the bytes are what the Run holds or are assembled per request, nothing is read
from or written to the filesystem, and no response names a server location.

Phase 11 adds no route and changes no response shape. A library-backed input
slot is filled by a text field beside the uploaded files, naming which stored
dataset version to read; the manifest gains ``library_inputs`` recording the
exact versions the Run resolved to (build plan 11C). A Run using only uploads
is byte-identical to what it was.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.actions import registry
from app.api.upload_form import read_run_form
from app.errors import (
    InvalidRequestError,
    MissingArtifactError,
    UnknownActionError,
    UnknownArtifactError,
    UnknownOutputError,
)
from app.models.artifact import Artifact
from app.models.run import Run
from app.models.schemas import OutputMetadata, PreviewResponse, RunManifest
from app.services import archive, export, preview, run_store
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
    """Execute one Action against its inputs.

    The request is ``multipart/form-data`` carrying ``action_id`` plus one
    field per Action input slot, named with that slot's ID. An upload-backed
    slot carries a file; a library-backed slot carries text naming the stored
    version to read — ``latest``, ``period:YYYY-MM`` or
    ``version:<version id>`` (build plan 11A). Both are submitted under their
    slot names rather than as one anonymous list, so an Action with several
    inputs needs no special handling (build plan 3.12).

    The multipart body is parsed here and nowhere else. Nothing upstream reads
    it: the same-origin transport in front of this endpoint forwards the stream
    untouched, which is why an upload up to ``config.MAX_UPLOAD_BYTES`` arrives
    whole and an oversized one is refused *here*, with the structured 413 of
    build plan 3.3, rather than being silently truncated on the way in.

    The Run executes synchronously; the POC deliberately has no job queue.
    """
    async with read_run_form(request) as form:
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

        # Every remaining text field names a Data Library version for one of
        # the Action's library-backed input slots (build plan 11A). It is a
        # *reference*, not data: which dataset is read is declared by the
        # Action, and this only says which version of it. A field naming a
        # slot the Action does not read that way is reported as an ignored
        # reference by the runner, exactly as a stray file is.
        dataset_references = {
            field: value
            for field, value in form.multi_items()
            if not isinstance(value, UploadFile) and field != ACTION_ID_FIELD
        }

        # Runs inside the form context: the uploaded streams stay open until
        # the runner has read them into memory (build plan 6C.3). Nothing the
        # user uploads is written to the server's filesystem.
        # Keep the response synchronous while allowing health/preview requests
        # to proceed during CPU-bound parsing and Action execution.
        outcome = await run_in_threadpool(
            execute_run, action, uploads, dataset_references
        )
        return outcome.manifest


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
        media_type=_DOWNLOAD_MEDIA_TYPES[export.XLSX_FORMAT],
        filename=export.download_filename(
            action_id=run.action.id,
            extension=export.XLSX_FORMAT,
            timestamp=_result_timestamp(run),
        ),
    )


@router.get("/runs/{run_id}/artifacts/download/zip")
def download_run_artifacts_zip(run_id: str) -> Response:
    """Download every artifact of one Run as a single ZIP (build plan 12F).

    Declared **before** the single-artifact route below so the two can never be
    confused: ``/artifacts/download/zip`` is three segments and the other is
    two, but stating the specific route first means the reading order matches
    the matching order.

    A Run with one artifact still bundles: whether the bundle is worth offering
    is the client's decision, and the frontend only shows it when there is more
    than one. A Run with no artifact at all has nothing to bundle, which is a
    missing artifact rather than an unknown one.
    """
    run = run_store.get_run(run_id)
    artifacts = _run_artifacts(run)

    return _attachment(
        archive.to_zip_bytes(artifacts, timestamp=_result_timestamp(run)),
        media_type=archive.ZIP_MEDIA_TYPE,
        filename=export.download_filename(
            action_id=run.action.id,
            extension=archive.ZIP_EXTENSION,
            timestamp=_result_timestamp(run),
        ),
    )


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
def download_artifact(run_id: str, artifact_id: str) -> Response:
    """Download one artifact a Run produced (build plan 12G).

    The bytes are the ones the Action produced and the Run has held since;
    nothing is rendered here and nothing is read from disk. The file is offered
    under the artifact's own filename rather than under the generic ForgeXL
    export convention, because that name is the point of an artifact: a report
    called "Beth Comeaux - September 2026.xlsx" must arrive under that name.
    """
    run = run_store.get_run(run_id)
    artifact = _require_artifact(run, artifact_id)

    return _attachment(
        artifact.payload,
        media_type=artifact.media_type,
        filename=artifact.filename,
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


def _require_artifact(run: Run, artifact_id: str) -> Artifact:
    """Return one artifact of `run` by ID, or raise.

    Matched against what the Run recorded, exactly as an output ID is: the
    client's string selects among the artifacts this Run produced and reaches
    nothing else. An ID the Run never recorded is unknown (404); one it
    recorded but no longer holds — a Run whose result has been released — is a
    missing artifact, which is the same distinction the output routes make.
    """
    known = {record.id for record in run.artifacts}
    if artifact_id not in known:
        raise UnknownArtifactError(
            "That Run has no such artifact.",
            details={
                "run_id": run.run_id,
                "artifact_id": artifact_id,
                "available_artifact_ids": sorted(known),
            },
        )

    artifact = run.result.artifact(artifact_id) if run.result is not None else None
    if artifact is None:
        raise MissingArtifactError(
            "That artifact is no longer available.",
            details={"run_id": run.run_id, "artifact_id": artifact_id},
        )
    return artifact


def _run_artifacts(run: Run) -> list[Artifact]:
    """Return every artifact of `run`, in the order the Run recorded them.

    Walks the Run's recorded metadata rather than the held mapping, so the
    archive's entries appear in the order the Action listed its artifacts
    (build plan 12E) rather than in whatever order a dictionary happens to
    iterate.
    """
    if not run.artifacts:
        raise MissingArtifactError(
            "That Run produced no artifacts.", details={"run_id": run.run_id}
        )
    return [_require_artifact(run, record.id) for record in run.artifacts]


def _result_timestamp(run: Run) -> datetime:
    """The moment a download's filename is stamped with.

    The Run's completion, so re-downloading an output produces the same
    filename rather than a new one each time. A Run still running has not
    completed, so its creation stands in.
    """
    return run.completed_at or run.created_at


def _attachment(payload: bytes, *, media_type: str, filename: str) -> Response:
    """Send `payload` as a download named `filename`.

    Two kinds of name reach this function and both are safe by construction,
    for different reasons. An **export** filename comes from
    :func:`app.services.export.download_filename`, which emits only ``a-z``,
    ``0-9``, ``-`` and a single ``.``. An **artifact** filename is the Action's
    own — it may hold spaces and accents, because that is the point of it — and
    :func:`app.models.artifact.check_artifact_filename` refused every separator
    and every control character when the artifact was built, so it cannot carry
    a directory component or inject a header line.

    Nothing the client supplied reaches this header either way, and nothing
    here names a server location (build plan 6F.6, 6F.8).
    """
    return Response(
        content=payload,
        media_type=media_type,
        headers={"content-disposition": _content_disposition(filename)},
    )


def _content_disposition(filename: str) -> str:
    """Render the download header for `filename`, non-ASCII names included.

    An ASCII name — which is every generated export filename, by construction
    (build plan 6F.6) — is sent in the single quoted parameter it has always
    been sent in. Nothing about those downloads changed in Phase 12.

    An artifact's filename is the Action's own and may legitimately be
    "Château Réal — September 2026.xlsx". The quoted parameter is defined over
    ASCII only, so a name like that sent through it alone arrives mangled.
    RFC 6266 and RFC 5987 answer with a second parameter: ``filename*``,
    carrying the real name UTF-8 percent-encoded, which every current browser
    prefers. The quoted parameter stays as the fallback, with each non-ASCII
    character replaced by ``?`` — a readable approximation for a reader too old
    to understand the other one.

    Nothing the client supplied reaches here, and
    :func:`app.models.artifact.check_artifact_filename` has already refused
    every control character, so no filename can introduce a header line.
    """
    if filename.isascii():
        return f'attachment; filename="{_quoted(filename)}"'

    fallback = _quoted(filename.encode("ascii", "replace").decode("ascii"))
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{_percent_encoded(filename)}"
    )


def _quoted(filename: str) -> str:
    """Escape the two characters an HTTP quoted-string cannot hold verbatim."""
    return filename.replace("\\", "\\\\").replace('"', '\\"')


#: Characters RFC 5987 lets an extended parameter carry unencoded. The
#: unreserved set of RFC 3986, which is a subset of what RFC 5987 permits:
#: encoding a little more than strictly necessary is always valid, and one set
#: to reason about is better than two.
_UNENCODED_FILENAME_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _percent_encoded(filename: str) -> str:
    """Percent-encode `filename`'s UTF-8 bytes for an RFC 5987 parameter.

    Written out rather than taken from :func:`urllib.parse.quote`, so the
    backend imports no part of ``urllib`` at all. That is a rule
    ``test_local_exposure.py`` enforces across the whole source tree: an
    application that never imports an HTTP client cannot reach one by accident,
    and the rule is worth more than the six lines it costs here.
    """
    return "".join(
        chr(byte)
        if chr(byte) in _UNENCODED_FILENAME_CHARACTERS
        else f"%{byte:02X}"
        for byte in filename.encode("utf-8")
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
        media_type=_DOWNLOAD_MEDIA_TYPES[export_format],
        filename=export.download_filename(
            action_id=run.action.id,
            output_id=output_id,
            extension=export_format,
            timestamp=_result_timestamp(run),
        ),
    )