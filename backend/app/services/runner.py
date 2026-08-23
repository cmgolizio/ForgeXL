"""The generic Run pipeline (build plan 3.7-3.9, 3.11 and section 24).

    resolve Action -> create Run -> save input -> parse input -> validate input
    -> execute Action -> persist Parquet -> create exports -> finalize manifest

Everything above belongs to the runner. An Action only transforms dataframes,
so adding an Action never means reproducing any of this machinery.

The runner is deliberately independent of the web framework: it takes
:class:`PendingUpload` objects carrying a filename and a readable stream, so
the same pipeline is driven identically by the API and by tests.

Failure handling follows build plan 3.9: once a Run directory exists, every
outcome — including a failure — leaves a manifest behind. Evidence of a failed
Run is never destroyed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import polars as pl

from app import config
from app.actions.base import Action, ActionResult
from app.errors import (
    ActionExecutionError,
    EmptyDatasetError,
    InputValidationError,
    MissingColumnsError,
    MissingInputError,
    RunValidationError,
    UnsupportedExtensionError,
    WorkbenchError,
)
from app.models.schemas import (
    ActionReference,
    InputMetadata,
    OutputMetadata,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)
from app.services import export, parser, storage
from app.services.storage import BinarySource, RunPaths, StoredUpload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingUpload:
    """One file submitted for one input slot, before it is stored.

    `filename` is whatever the client sent. It is metadata: it never becomes a
    path (build plan 3.2).
    """

    filename: str
    stream: BinarySource


@dataclass(frozen=True)
class RunOutcome:
    """The result of :func:`execute_run`."""

    manifest: RunManifest
    paths: RunPaths


def execute_run(
    action: Action, uploads: Mapping[str, PendingUpload]
) -> RunOutcome:
    """Execute `action` against `uploads` and return the finalized manifest.

    Args:
        action: The Action to run, already resolved from the registry.
        uploads: Submitted files keyed by input slot ID.

    Raises:
        UploadTooLargeError: an upload exceeded the configured limit.
        RunValidationError: the inputs failed validation.
        ActionExecutionError: the Action raised while transforming valid input.

    Every one of those is raised only after a ``failed`` manifest has been
    written for the Run.
    """
    created_at = _now()
    paths = storage.create_run()
    action_reference = ActionReference(
        id=action.id, version=action.version, name=action.name
    )

    # Computed once, before anything can fail, so a Run that is rejected still
    # reports a submitted-but-unused slot.
    warnings = tuple(_unexpected_slot_warnings(action, uploads))

    manifest = RunManifest(
        run_id=paths.run_id,
        status=RunStatus.RUNNING,
        action=action_reference,
        created_at=created_at,
        started_at=created_at,
        validation=ValidationSummary(passed=True, warnings=warnings),
    )
    storage.write_manifest(paths, manifest)

    try:
        stored, input_issues = _store_and_check_slots(action, uploads, paths)
        parsed, parse_issues = _parse_inputs(action, stored)
        issues = [*input_issues, *parse_issues]

        if not issues:
            issues.extend(_validate_datasets(action, parsed))
            issues.extend(action.validate(_frames_by_slot(parsed)))

        manifest = manifest.model_copy(
            update={"inputs": tuple(_input_metadata(stored, parsed))}
        )

        if issues:
            raise RunValidationError(issues)

        result = _execute_action(action, _frames_by_slot(parsed))
        outputs = _persist_outputs(action, result, paths)

        completed_at = _now()
        manifest = manifest.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "completed_at": completed_at,
                "duration_ms": _elapsed_ms(created_at, completed_at),
                "validation": ValidationSummary(passed=True, warnings=warnings),
                "outputs": tuple(outputs),
                "metrics": dict(result.metrics),
            }
        )
        storage.write_manifest(paths, manifest)
        return RunOutcome(manifest=manifest, paths=paths)

    except WorkbenchError as error:
        _finalize_failed(paths, manifest, created_at, error, warnings)
        raise
    except Exception as error:  # pragma: no cover - defensive
        logger.exception("Run %s failed unexpectedly", paths.run_id)
        wrapped = ActionExecutionError(
            "The Run failed unexpectedly. See the local server log for details.",
            details={"run_id": paths.run_id},
        )
        _finalize_failed(paths, manifest, created_at, wrapped, warnings)
        raise wrapped from error


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _store_and_check_slots(
    action: Action,
    uploads: Mapping[str, PendingUpload],
    paths: RunPaths,
) -> tuple[dict[str, StoredUpload], list[ValidationIssue]]:
    """Preserve each supplied upload and check slots and extensions.

    Build plan 3.7: a required input slot must be present and its file's
    extension must be one the Action accepts. Both are checked for every slot
    before any parsing happens, so one request reports every slot problem at
    once rather than one per attempt.
    """
    stored: dict[str, StoredUpload] = {}
    issues: list[ValidationIssue] = []

    for slot in action.inputs:
        upload = uploads.get(slot.id)

        if upload is None or not upload.filename:
            if slot.required:
                issues.append(
                    MissingInputError(
                        f"{slot.label} is required.",
                        details={"slot_id": slot.id, "label": slot.label},
                    ).as_validation_issue(slot.id)
                )
            continue

        extension = storage.extension_of(upload.filename)
        if extension not in slot.accepted_extensions:
            issues.append(
                UnsupportedExtensionError(
                    f"{slot.label} must be "
                    f"{_human_list(slot.accepted_extensions)}. "
                    f"{storage.display_filename(upload.filename)} is not a "
                    "supported file type.",
                    details={
                        "slot_id": slot.id,
                        "extension": extension,
                        "accepted_extensions": list(slot.accepted_extensions),
                    },
                ).as_validation_issue(slot.id)
            )
            continue

        # Preserved before anything else touches it; the original bytes are
        # never modified afterwards (build plan section 16).
        stored[slot.id] = storage.store_upload(
            paths,
            slot.id,
            upload.filename,
            upload.stream,
            max_bytes=config.MAX_UPLOAD_BYTES,
        )

    return stored, issues


def _parse_inputs(
    action: Action, stored: Mapping[str, StoredUpload]
) -> tuple[dict[str, parser.ParsedFile], list[ValidationIssue]]:
    """Parse every stored upload, collecting parse failures as issues."""
    parsed: dict[str, parser.ParsedFile] = {}
    issues: list[ValidationIssue] = []

    for slot in action.inputs:
        upload = stored.get(slot.id)
        if upload is None:
            continue
        try:
            parsed[slot.id] = parser.parse_tabular_file(upload.path, upload.extension)
        except InputValidationError as error:
            issues.append(error.as_validation_issue(slot.id))

    return parsed, issues


def _validate_datasets(
    action: Action, parsed: Mapping[str, parser.ParsedFile]
) -> list[ValidationIssue]:
    """Check emptiness and required columns (build plan 3.7).

    Column names are compared exactly. ``Sales Person`` is never treated as
    equivalent to ``Salesperson``: the mismatch is reported so the user can fix
    the file, rather than guessed at.
    """
    issues: list[ValidationIssue] = []

    for slot in action.inputs:
        parsed_file = parsed.get(slot.id)
        if parsed_file is None:
            continue

        if parsed_file.row_count == 0:
            issues.append(
                EmptyDatasetError(
                    f"{slot.label} contains no data rows.",
                    details={"slot_id": slot.id},
                ).as_validation_issue(slot.id)
            )

        present = set(parsed_file.columns)
        missing = [name for name in slot.required_columns if name not in present]
        if missing:
            issues.append(
                MissingColumnsError(
                    "The uploaded file is missing required columns.",
                    details={
                        "slot_id": slot.id,
                        "missing_columns": missing,
                        "found_columns": list(parsed_file.columns),
                    },
                ).as_validation_issue(slot.id)
            )

    return issues


def _execute_action(
    action: Action, frames: Mapping[str, pl.DataFrame]
) -> ActionResult:
    """Run the Action's transformation, converting a crash into a clean error."""
    try:
        return action.run(frames)
    except Exception as error:
        logger.exception("Action %s raised during execution", action.id)
        raise ActionExecutionError(
            f"{action.name} failed while processing the data.",
            details={"action_id": action.id},
        ) from error


def _persist_outputs(
    action: Action, result: ActionResult, paths: RunPaths
) -> list[OutputMetadata]:
    """Write Parquet plus the CSV and XLSX exports for each declared output."""
    outputs: list[OutputMetadata] = []

    for declared in action.outputs:
        frame = result.outputs.get(declared.id)
        if frame is None:
            raise ActionExecutionError(
                f"{action.name} did not produce its declared output "
                f"{declared.label!r}.",
                details={"action_id": action.id, "output_id": declared.id},
            )

        written = export.write_output(paths, declared.id, frame)
        outputs.append(
            OutputMetadata(
                id=declared.id,
                label=declared.label,
                row_count=written.row_count,
                column_count=written.column_count,
                columns=written.columns,
                formats=written.formats,
            )
        )

    return outputs


def _finalize_failed(
    paths: RunPaths,
    manifest: RunManifest,
    created_at: datetime,
    error: WorkbenchError,
    warnings: tuple[ValidationIssue, ...] = (),
) -> RunManifest:
    """Record a failed Run, keeping its directory and evidence (build plan 3.9)."""
    completed_at = _now()
    validation_errors: tuple[ValidationIssue, ...] = (
        error.issues if isinstance(error, RunValidationError) else ()
    )
    failed = manifest.model_copy(
        update={
            "status": RunStatus.FAILED,
            "completed_at": completed_at,
            "duration_ms": _elapsed_ms(created_at, completed_at),
            "validation": ValidationSummary(
                passed=not validation_errors,
                errors=validation_errors,
                warnings=warnings,
            ),
            "error": error.as_run_error(),
        }
    )
    storage.write_manifest(paths, failed)
    return failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frames_by_slot(
    parsed: Mapping[str, parser.ParsedFile],
) -> dict[str, pl.DataFrame]:
    return {slot_id: item.frame for slot_id, item in parsed.items()}


def _input_metadata(
    stored: Mapping[str, StoredUpload], parsed: Mapping[str, parser.ParsedFile]
) -> list[InputMetadata]:
    """Describe each preserved upload for the manifest.

    A file that was stored but failed to parse still appears, with zero counts:
    a failed Run should show what was uploaded, not an empty inputs list.
    """
    records: list[InputMetadata] = []
    for slot_id, upload in stored.items():
        parsed_file = parsed.get(slot_id)
        records.append(
            InputMetadata(
                slot_id=slot_id,
                original_filename=upload.original_filename,
                stored_filename=upload.stored_filename,
                file_size_bytes=upload.size_bytes,
                extension=upload.extension,
                parser_engine=parsed_file.parser_engine if parsed_file else None,
                worksheet=parsed_file.worksheet if parsed_file else None,
                row_count=parsed_file.row_count if parsed_file else 0,
                column_count=parsed_file.column_count if parsed_file else 0,
                columns=parsed_file.columns if parsed_file else (),
            )
        )
    return records


def _unexpected_slot_warnings(
    action: Action, uploads: Mapping[str, PendingUpload]
) -> list[ValidationIssue]:
    """Warn about submitted fields the Action does not declare.

    Ignoring them silently would hide a frontend/backend mismatch; failing the
    Run over them would be harsher than the situation warrants. A warning never
    fails a Run (build plan section 6.2).
    """
    declared = {slot.id for slot in action.inputs}
    unexpected = sorted(slot_id for slot_id in uploads if slot_id not in declared)
    if not unexpected:
        return []
    return [
        ValidationIssue(
            code="UNEXPECTED_INPUT",
            message=(
                f"{action.name} does not use "
                f"{_human_list(tuple(unexpected))}; the file was ignored."
            ),
            details={"unexpected_slot_ids": unexpected},
        )
    ]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


def _human_list(values: Sequence[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    return f"{', '.join(values[:-1])} or {values[-1]}"