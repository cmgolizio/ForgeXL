"""The generic Run pipeline (build plan 3.7-3.9, 6B-6E and section 24).

    resolve Action -> record Run -> read input -> parse input -> validate input
    -> execute Action -> keep the result frames -> finalize the Run

Everything above belongs to the runner. An Action only transforms dataframes,
so adding an Action never means reproducing any of this machinery.

The runner is deliberately independent of the web framework: it takes
:class:`PendingUpload` objects carrying a filename and a readable stream, so
the same pipeline is driven identically by the API and by tests.

**The pipeline touches no filesystem.** Phase 6C moved uploads into memory;
Phase 6D moved results there too, so the whole of build plan 6D's processing
boundary now holds:

    named uploaded inputs -> parser -> named DataFrame(s)
        -> Action Registry -> Action -> result DataFrame(s)

A Run needs no ``inputs/``, ``working/`` or ``exports/`` directory: nothing is
saved and reopened, and no intermediary spreadsheet is written merely to be
read back (build plan 6D.7). Exports are generated from the retained frames at
download time by :mod:`app.services.export`.

Run state is owned by :mod:`app.services.run_store` (build plan 6B). The runner
records a Run when it starts and hands the store a new state at every
transition; it never writes run state anywhere itself, so where that state
lives is not the runner's concern. The result frames travel with the Run, so
forgetting the Run releases them (build plan 6D.8).

Since Phase 6E the runner also describes what came out. Each result table is
measured by :mod:`app.services.results` — its schema, the rows the Run
received, and the columns it added or dropped — and the Action's own
`rows_affected`, when it reports one, is carried onto the Run so the audit
summary can state it. None of that reaches the result table itself: audit and
result metadata stay out of the user's data (build plan 6E.6).

Failure handling follows build plan 3.9: once a Run is recorded, every
outcome — including a failure — leaves the Run recorded, with its error and its
full validation results. Evidence of a failed Run is never destroyed. A failed
Run carries no result at all, so a half-finished transformation can never be
mistaken for a usable one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from app import config
from app.actions.base import Action, ActionResult
from app.errors import (
    ActionExecutionError,
    EmptyDatasetError,
    EmptyUploadError,
    InputValidationError,
    MissingColumnsError,
    MissingInputError,
    RunValidationError,
    UnsupportedExtensionError,
    WorkbenchError,
)
from app.models.run import Run, RunResult, now
from app.models.schemas import (
    ActionReference,
    InputMetadata,
    OutputMetadata,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)
from app.services import parser, results, run_store, storage
from app.services.export import EXPORT_FORMATS
from app.services.storage import BinarySource, LoadedUpload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingUpload:
    """One file submitted for one input slot, before it is stored.

    `filename` is whatever the client sent. It is metadata: it never becomes a
    path, and since Phase 6C there is no path for it to become (build plan
    3.2). The runner reads the stream into memory.
    """

    filename: str
    stream: BinarySource


@dataclass(frozen=True)
class RunOutcome:
    """The result of :func:`execute_run`.

    Carries the finalized Run and nothing else. Until Phase 6D it also carried
    the Run's directory; a Run has no directory now, so there is nothing left
    for a caller to be handed but the Run itself.
    """

    run: Run

    @property
    def manifest(self) -> RunManifest:
        """The Run rendered in the API's frozen manifest shape."""
        return self.run.to_manifest()

    @property
    def result(self) -> RunResult | None:
        """The tables the Run produced, or None if it failed."""
        return self.run.result


def execute_run(
    action: Action, uploads: Mapping[str, PendingUpload]
) -> RunOutcome:
    """Execute `action` against `uploads` and return the finalized Run.

    Args:
        action: The Action to run, already resolved from the registry.
        uploads: Submitted files keyed by input slot ID.

    Raises:
        UploadTooLargeError: an upload exceeded the configured limit.
        RunValidationError: the inputs failed validation.
        ActionExecutionError: the Action raised while transforming valid input.

    Every one of those is raised only after the Run has been recorded as
    ``failed`` in the Run Store.
    """
    created_at = now()
    action_reference = ActionReference(
        id=action.id, version=action.version, name=action.name
    )

    # Computed once, before anything can fail, so a Run that is rejected still
    # reports a submitted-but-unused slot.
    warnings = tuple(_unexpected_slot_warnings(action, uploads))

    run = run_store.create_run(
        Run.create(action_reference, created_at=created_at, warnings=warnings)
    )
    try:
        loaded, input_issues = _read_and_check_slots(action, uploads)
        parsed, parse_issues = _parse_inputs(action, loaded)
        issues = [*input_issues, *parse_issues]
        warnings += tuple(
            ValidationIssue(
                code="MIXED_COLUMN_TYPES",
                message=(
                    "Excel columns with mixed cell types were preserved as text: "
                    + ", ".join(item.mixed_columns)
                    + "."
                ),
                slot_id=slot_id,
                details={"columns": list(item.mixed_columns)},
            )
            for slot_id, item in parsed.items()
            if item.mixed_columns
        )

        input_records = tuple(_input_metadata(loaded, parsed))
        # The uploaded bytes have served their purpose: everything downstream
        # works from the dataframes and this metadata. Releasing them here
        # keeps a Run from holding a second copy of every input for the rest
        # of its life.
        loaded.clear()

        if not issues:
            issues.extend(_validate_datasets(action, parsed))
            issues.extend(action.validate(_frames_by_slot(parsed)))

        run = run_store.update_run(run.with_changes(inputs=input_records))

        if issues:
            raise RunValidationError(issues)

        result = _execute_action(action, _frames_by_slot(parsed))
        outputs, tables = _collect_outputs(action, result, input_records)

        completed_at = now()
        run = run_store.update_run(
            run.with_changes(
                status=RunStatus.SUCCEEDED,
                completed_at=completed_at,
                updated_at=completed_at,
                duration_ms=_elapsed_ms(created_at, completed_at),
                validation=ValidationSummary(passed=True, warnings=warnings),
                outputs=outputs,
                metrics=dict(result.metrics),
                result=tables,
                rows_affected=result.rows_affected,
            )
        )
        return RunOutcome(run=run)

    except WorkbenchError as error:
        _finalize_failed(run, created_at, error, warnings)
        raise
    except Exception as error:  # pragma: no cover - defensive
        logger.exception("Run %s failed unexpectedly", run.run_id)
        wrapped = ActionExecutionError(
            "The Run failed unexpectedly. See the local server log for details.",
            details={"run_id": run.run_id},
        )
        _finalize_failed(run, created_at, wrapped, warnings)
        raise wrapped from error


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def _read_and_check_slots(
    action: Action,
    uploads: Mapping[str, PendingUpload],
) -> tuple[dict[str, LoadedUpload], list[ValidationIssue]]:
    """Read each supplied upload into memory and check the basic properties.

    Build plan 3.7 and 6C.4, per slot: a required slot must be present, its
    file's extension must be one the Action accepts, the file must not be
    empty, and it must fit inside the configured upload limit. Every slot is
    checked before any parsing happens, so one request reports every slot
    problem at once rather than one per attempt.

    Reading the bytes is the first thing done with an accepted file, and the
    only thing done with it here: the file is not written anywhere, so there
    is nothing to clean up when a later slot turns out to be invalid.
    """
    loaded: dict[str, LoadedUpload] = {}
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

        # Read into memory, bounded by the limit. An oversized upload raises
        # rather than becoming an issue: it is a refusal of the request
        # (413), not a finding about the data (build plan 3.3).
        received = storage.read_upload(
            slot.id,
            upload.filename,
            upload.stream,
            max_bytes=config.MAX_UPLOAD_BYTES,
        )

        if received.size_bytes == 0:
            issues.append(
                EmptyUploadError(
                    f"{storage.display_filename(upload.filename)} is empty.",
                    details={
                        "slot_id": slot.id,
                        "original_filename": upload.filename,
                    },
                ).as_validation_issue(slot.id)
            )
            continue

        loaded[slot.id] = received

    return loaded, issues


def _parse_inputs(
    action: Action, loaded: Mapping[str, LoadedUpload]
) -> tuple[dict[str, parser.ParsedFile], list[ValidationIssue]]:
    """Parse every upload from memory, collecting parse failures as issues."""
    parsed: dict[str, parser.ParsedFile] = {}
    issues: list[ValidationIssue] = []

    for slot in action.inputs:
        upload = loaded.get(slot.id)
        if upload is None:
            continue
        try:
            parsed[slot.id] = parser.parse_tabular_bytes(
                upload.payload, upload.extension
            )
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


def _collect_outputs(
    action: Action,
    result: ActionResult,
    input_records: Sequence[InputMetadata],
) -> tuple[tuple[OutputMetadata, ...], RunResult]:
    """Describe each declared output and keep its frame (build plan 6D.5, 6E.1).

    Walks the Action's declared outputs in declaration order, so the first one
    an Action declares is the primary result table and the rest, if any, are
    secondary. An Action that declares an output and does not produce it fails
    the Run: a missing result is never quietly dropped.

    Since Phase 6E each output is described in full by
    :func:`app.services.results.describe_output`: its schema, the rows the Run
    received, and the columns this result added or dropped relative to what was
    uploaded. Those are measured against `input_records` — the inputs that
    actually parsed — rather than against what the Action declares it wants, so
    the description is of the data, not of the intention.

    Nothing is written. The frames the Action returned are the frames the Run
    keeps, and CSV/XLSX are generated from them when a download asks for them
    (build plan 6D.7).

    The reported formats stay the runner's :data:`EXPORT_FORMATS`, exactly as
    before: which formats an output is offered in is not something Phase 6D or
    6E changes.
    """
    outputs: list[OutputMetadata] = []
    tables: dict[str, pl.DataFrame] = {}

    received_columns = results.input_columns(
        record.columns for record in input_records
    )
    received_rows = sum(record.row_count for record in input_records)

    for declared in action.outputs:
        frame = result.outputs.get(declared.id)
        if frame is None:
            raise ActionExecutionError(
                f"{action.name} did not produce its declared output "
                f"{declared.label!r}.",
                details={"action_id": action.id, "output_id": declared.id},
            )

        tables[declared.id] = frame
        outputs.append(
            results.describe_output(
                output_id=declared.id,
                label=declared.label,
                formats=EXPORT_FORMATS,
                frame=frame,
                received_columns=received_columns,
                received_rows=received_rows,
            )
        )

    return tuple(outputs), RunResult.of(tables)


def _finalize_failed(
    run: Run,
    created_at: datetime,
    error: WorkbenchError,
    warnings: tuple[ValidationIssue, ...] = (),
) -> Run:
    """Record a failed Run, keeping its evidence (build plan 3.9)."""
    completed_at = now()
    validation_errors: tuple[ValidationIssue, ...] = (
        error.issues if isinstance(error, RunValidationError) else ()
    )
    return run_store.update_run(
        run.with_changes(
            status=RunStatus.FAILED,
            completed_at=completed_at,
            updated_at=completed_at,
            duration_ms=_elapsed_ms(created_at, completed_at),
            validation=ValidationSummary(
                passed=not validation_errors,
                errors=validation_errors,
                warnings=warnings,
            ),
            error=error.as_run_error(),
            # Build plan 6D.8: a failed Run must not leave a partially valid
            # result behind. Set explicitly rather than left to the default,
            # because a Run can fail after its Action has already produced
            # frames — an Action that omits a declared output is exactly that
            # case.
            outputs=(),
            result=None,
            # Same rule: a Run that produced nothing has affected nothing it
            # can report (build plan 6D.8).
            rows_affected=None,
        )
    )


def delete_run(run_id: str) -> bool:
    """Forget a Run and release the state it holds (build plan 6B.6, 6D.8).

    Returns True if a Run was forgotten. Everything a Run holds — its metadata
    and its result frames — travels with the Run, so forgetting the record is
    what releases the memory: once the store drops its reference, the frames
    are unreachable and Python reclaims them. There is nothing on disk to
    remove, because Phase 6C/6D stopped writing a Run's files at all.
    """
    return run_store.delete_run(run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frames_by_slot(
    parsed: Mapping[str, parser.ParsedFile],
) -> dict[str, pl.DataFrame]:
    return {slot_id: item.frame for slot_id, item in parsed.items()}


def _input_metadata(
    loaded: Mapping[str, LoadedUpload], parsed: Mapping[str, parser.ParsedFile]
) -> list[InputMetadata]:
    """Describe each received upload for the manifest (build plan 6C.8).

    Records everything the manifest reports about an input — its slot, the
    filename the client sent, the generated name, the byte count, the
    extension, the engine and worksheet it was read with, and its shape.

    A file that was received but failed to parse still appears, with zero
    counts: a failed Run should show what was uploaded, not an empty inputs
    list.
    """
    records: list[InputMetadata] = []
    for slot_id, upload in loaded.items():
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


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


def _human_list(values: Sequence[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    return f"{', '.join(values[:-1])} or {values[-1]}"
