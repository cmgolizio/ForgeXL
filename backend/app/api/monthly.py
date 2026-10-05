"""Monthly reporting HTTP orchestration; data rules stay in the services."""
from __future__ import annotations

from datetime import date, timedelta
import time

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile

from app.api.upload_form import read_run_form
from app.errors import InvalidRequestError, UnknownDatasetError
from app.models.library import KNOWN_DATASETS
from app.models.monthly_workflow import (SOURCE_IDS, GenerateRequest, SavedValidationRequest,
    WorkflowOutcome, WorkflowValidation, HistoryReview, HistoryOutcome)
from app.models.source_schemas import DEFAULT_DATE_FORMATS, schema_for
from app.services import cycle_receipts, data_library, history_workflow, monthly_workflow, storage
from app.services.ingestion import SourceFile

class StructuredRoute(APIRoute):
    """Keep malformed JSON/body fields in the workbench error contract."""
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as error:
                raise InvalidRequestError("Check the reporting request fields.",
                    details={"fields": [".".join(str(part) for part in issue["loc"])
                        for issue in error.errors()]}) from error
        return handler


router = APIRouter(prefix="/api/monthly", tags=["monthly reports"], route_class=StructuredRoute)


@router.get("/catalog")
def catalog() -> dict:
    """Source metadata only, never an entire table or a local path."""
    datasets = []
    all_periods: set[str] = set()
    available: dict[str, set[str]] = {}
    for definition in KNOWN_DATASETS:
        if definition.id not in SOURCE_IDS:
            continue
        try:
            versions = data_library.current_versions(definition.id)
        except UnknownDatasetError:
            versions = []
        versions = sorted(versions, key=lambda item: item.period or "")
        available[definition.id] = {v.period for v in versions if v.period}
        all_periods.update(available[definition.id])
        schema = schema_for(definition.id)
        datasets.append({"id": definition.id, "label": definition.label,
            "required_columns": schema.column_names if schema else (),
            "versions": [{"version_id": v.version_id, "period": v.period, "row_count": v.row_count,
                "source_filename": v.source_filename, "created_at": v.created_at,
                "supersedes": v.supersedes} for v in versions]})
    periods = [{"period": period, "ready": all(period in available[key] for key in SOURCE_IDS),
        "missing_datasets": [key for key in SOURCE_IDS if period not in available[key]],
        "cycles": cycle_receipts.CYCLE_RECEIPTS.list(period)} for period in sorted(all_periods, reverse=True)]
    previous_month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return {"datasets": datasets, "periods": periods, "default_period": previous_month,
        "action": monthly_workflow._action_reference(monthly_workflow._action()),
        "date_formats": DEFAULT_DATE_FORMATS}


@router.post("/validate", response_model=WorkflowValidation)
async def validate_monthly_uploads(request: Request) -> WorkflowValidation:
    async with read_run_form(request) as form:
        allowed = {"period", "reason"} | set(SOURCE_IDS) | {key + ".date_format" for key in SOURCE_IDS[:2]} | {key + ".replaces" for key in SOURCE_IDS}
        _fields(form, allowed)
        started = time.perf_counter()
        files = await run_in_threadpool(_files, form, SOURCE_IDS)
        summary = await run_in_threadpool(monthly_workflow.WORKFLOW.validate_uploads,
            period=_text(form, "period", required=True), files=files,
            date_formats={key: _text(form, key + ".date_format") for key in SOURCE_IDS[:2] if _text(form, key + ".date_format")},
            replacements={key: _text(form, key + ".replaces") for key in SOURCE_IDS if _text(form, key + ".replaces")},
            reason=_text(form, "reason") or None)
        # Time since the multipart body was received; network time is measured
        # by the browser or the benchmark client, never invented here.
        return summary.model_copy(update={"timings_ms": {**summary.timings_ms,
            "request_processing": round((time.perf_counter() - started) * 1000, 3)}})


@router.post("/validate-saved", response_model=WorkflowValidation)
def validate_saved(payload: SavedValidationRequest) -> WorkflowValidation:
    return monthly_workflow.WORKFLOW.validate_saved(payload.period, payload.cycle_id)


@router.post("/generate", response_model=WorkflowOutcome)
def generate(payload: GenerateRequest) -> WorkflowOutcome:
    return monthly_workflow.WORKFLOW.generate(payload.validation_id,
        acknowledge_warnings=payload.acknowledge_warnings)


@router.post("/discard")
def discard(payload: GenerateRequest) -> dict:
    monthly_workflow.WORKFLOW.discard(payload.validation_id)
    history_workflow.HISTORY_WORKFLOW.discard(payload.validation_id)
    return {"discarded": True}


@router.post("/history/validate", response_model=HistoryReview)
async def validate_history(request: Request) -> HistoryReview:
    async with read_run_form(request) as form:
        _fields(form, {"dataset_id", "source_file", "date_format", "skip_existing"})
        files = await run_in_threadpool(_files, form, ("source_file",))
        if "source_file" not in files:
            raise InvalidRequestError("Choose the historical source file.")
        skip_existing = _text(form, "skip_existing")
        if skip_existing not in ("", "true", "false"):
            raise InvalidRequestError("skip_existing must be true or false.")
        return await run_in_threadpool(history_workflow.HISTORY_WORKFLOW.validate,
            _text(form, "dataset_id", required=True), files["source_file"],
            date_format=_text(form, "date_format") or None, skip_existing=skip_existing == "true")


@router.post("/history/commit", response_model=HistoryOutcome)
def commit_history(payload: GenerateRequest) -> HistoryOutcome:
    return history_workflow.HISTORY_WORKFLOW.commit(payload.validation_id,
        acknowledge_warnings=payload.acknowledge_warnings)


def _text(form: FormData, field: str, *, required: bool = False) -> str:
    value = form.get(field)
    if value is not None and not isinstance(value, str):
        raise InvalidRequestError(f"{field} must be a text field.")
    text = (value or "").strip()
    if required and not text:
        raise InvalidRequestError(f"{field} is required.")
    return text


def _fields(form: FormData, allowed: set[str]) -> None:
    unexpected = set(form.keys()) - allowed
    if unexpected:
        raise InvalidRequestError("Unexpected monthly workflow fields.", details={"fields": sorted(unexpected)})


def _files(form: FormData, fields: tuple[str, ...]) -> dict[str, SourceFile]:
    files = {}
    for field in fields:
        value = form.get(field)
        if value is None:
            continue
        if not isinstance(value, UploadFile) or not value.filename:
            raise InvalidRequestError(f"{field} must contain a file.")
        upload = storage.read_upload(field, value.filename, value.file)
        files[field] = SourceFile(filename=value.filename, payload=upload.payload)
    return files
