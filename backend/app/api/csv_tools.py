"""CSV intake and configuration boundaries; Actions only receive parsed frames."""
from __future__ import annotations

from threading import Event

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app import config
from app.actions import registry
from app.actions.base import Action
from app.api.upload_form import read_run_form
from app.errors import InvalidRequestError
from app.models.csv_tools import DiscardCSV, ProcessCSV, ReorderCSV
from app.models.schemas import RunManifest
from app.services import csv_tools, run_store
from app.services.csv_filters import validate_options
from app.services.runner import PendingUpload, execute_run

router = APIRouter(prefix="/api/csv", tags=["csv tools"])


def csv_action(action_id: str) -> Action:
    action = registry.get_action(action_id)
    if action is None or not action.accepts_options:
        raise InvalidRequestError("Choose a discovered CSV-tools Action.")
    return action


@router.post("/inspect")
async def inspect_csv(request: Request) -> dict:
    # Only the declared append field may repeat; source/action_id stay single.
    # All fields are still validated against the selected Action after intake.
    cancelled = Event()
    result: dict = {}

    def prepare(action: Action, uploads: list[tuple[str, PendingUpload]]) -> dict:
        inspected = csv_tools.SESSIONS.add(action, uploads)
        result.update(inspected)
        if cancelled.is_set(): csv_tools.SESSIONS.discard(inspected["session_id"])
        return inspected

    try:
        with csv_tools.SESSIONS.reserve():
            async with read_run_form(request, repeated_files=frozenset({"combine_additional"}),
                                     max_files=config.CSV_MAX_FILES, aggregate_limit=config.CSV_MAX_TOTAL_BYTES) as form:
                action_id = form.get("action_id")
                if not isinstance(action_id, str): raise InvalidRequestError("action_id is required.")
                action = csv_action(action_id)
                allowed = {"action_id", *(slot.id for slot in action.inputs)}
                uploads = []
                for field, value in form.multi_items():
                    if field not in allowed or (field != "action_id" and not isinstance(value, UploadFile)):
                        raise InvalidRequestError(f"Unknown or invalid CSV form field: {field}.")
                    if isinstance(value, UploadFile):
                        if field == "action_id": raise InvalidRequestError("action_id must be text.")
                        uploads.append((field, PendingUpload(value.filename or "", value.file)))
                await run_in_threadpool(prepare, action, uploads)
                if await request.is_disconnected():
                    raise InvalidRequestError("CSV inspection disconnected; inspect the files again.")
                return result
    except BaseException:
        cancelled.set()
        if result: csv_tools.SESSIONS.discard(result["session_id"])
        raise


@router.post("/runs", response_model=RunManifest)
async def process_csv(payload: ProcessCSV, request: Request) -> RunManifest:
    action = csv_action(payload.action_id)
    cancelled = Event()
    created: list[str] = []

    def process() -> RunManifest:
        with csv_tools.SESSIONS.use(payload.session_id, action.id) as prepared:
            options = validate_options(payload.options.model_dump(), prepared.frame, combine=action.id == "combine_csv")
            try:
                outcome = execute_run(action, {}, prepared=({"csv_data": prepared.frame}, prepared.records), options=options.model_dump())
            except Exception:
                if cancelled.is_set(): csv_tools.SESSIONS.discard(payload.session_id)
                raise
            created.append(outcome.run.run_id)
            if cancelled.is_set():
                run_store.delete_run(outcome.run.run_id)
                csv_tools.SESSIONS.discard(payload.session_id)
            return outcome.manifest
    try:
        manifest = await run_in_threadpool(process)
        if await request.is_disconnected(): raise InvalidRequestError("CSV processing disconnected; retry inspection.")
        return manifest
    except BaseException as error:
        abandoned = not isinstance(error, Exception) or await request.is_disconnected()
        if abandoned: cancelled.set()
        for run_id in created: run_store.delete_run(run_id)
        # Correctable validation/action errors keep prepared data for retry;
        # abandoned requests relinquish both preparation and successful result.
        if abandoned:
            csv_tools.SESSIONS.discard(payload.session_id)
        raise


@router.post("/discard")
def discard_csv(payload: DiscardCSV) -> dict:
    csv_tools.SESSIONS.discard(payload.session_id)
    return {"discarded": True}


@router.post("/reorder")
async def reorder_csv(payload: ReorderCSV, request: Request) -> dict:
    cancelled = Event()
    result: dict = {}

    def reorder() -> dict:
        inspected = csv_tools.SESSIONS.reorder(payload.session_id, payload.order)
        result.update(inspected)
        if cancelled.is_set(): csv_tools.SESSIONS.discard(inspected["session_id"])
        return inspected
    try:
        await run_in_threadpool(reorder)
        if await request.is_disconnected():
            raise InvalidRequestError("CSV ordering disconnected; inspect the files again.")
        return result
    except BaseException:
        cancelled.set()
        if result: csv_tools.SESSIONS.discard(result["session_id"])
        raise
