"""Validate → save sources → pin versions → run the existing report Action.

No business calculations live here. Ingestion, resolution and report preparation
remain the authorities. A receipt persists only the selected immutable sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import threading
import time
from typing import Mapping

import polars as pl

from app.actions import registry
from app.actions.base import Action
from app.errors import (DataLibraryError, InvalidRequestError, InvalidDatasetCommitError,
                        UnknownDatasetError, UnknownDatasetVersionError, WorkbenchError)
from app.models.library import (DatasetSelector, DatasetVersion,
                                known_dataset, new_version_id, now, parse_period)
from app.models.monthly_workflow import (SOURCE_IDS, CoverageReview, CycleReceipt,
    SourceReview, WorkflowCheck, WorkflowOutcome, WorkflowValidation)
from app.models.report_spec import REPORT_ACTION_ID, WindowKey
from app.models.schemas import ActionReference, ValidationIssue
from app.services import cycle_receipts, data_library, ingestion, input_resolution, results
from app.services.ingestion import SourceFile, SourceValidation
from app.services.input_resolution import ResolvedLibraryInput
from app.services.monthly_report import PreparedReport, missing_months, prepare
from app.services.runner import execute_run

VALIDATION_LIFETIME = timedelta(minutes=15)


@dataclass
class PendingCycle:
    summary: WorkflowValidation
    imports: dict[str, SourceValidation]
    replacements: dict[str, str]
    reason: str | None
    versions: dict[str, tuple[str, ...]]
    baseline: dict[str, tuple[str, ...]]
    receipt: CycleReceipt | None


class MonthlyWorkflow:
    """One pending review per local user; a new review releases the previous one."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: PendingCycle | None = None

    def discard(self, validation_id: str) -> None:
        with self._lock:
            if self._pending and self._pending.summary.validation_id == validation_id:
                self._pending = None

    def validate_uploads(self, *, period: str, files: Mapping[str, SourceFile],
                         date_formats: Mapping[str, str] | None = None,
                         replacements: Mapping[str, str] | None = None,
                         reason: str | None = None) -> WorkflowValidation:
        with self._lock:
            self._pending = None
            period = _period(period)
            dates = dict(date_formats or {})
            replacing = dict(replacements or {})
            _check_fields(set(files) | set(dates) | set(replacing))
            if any(key not in files for key in replacing):
                raise InvalidRequestError("A correction needs its replacement file.")
            action = _action()
            errors: list[ValidationIssue] = []
            warnings: list[ValidationIssue] = []
            imports: dict[str, SourceValidation] = {}
            reviews: list[SourceReview] = []
            versions: dict[str, tuple[str, ...]] = {}
            baseline = _baseline(period)
            frames: dict[str, pl.DataFrame] = {}
            started = time.perf_counter()
            for dataset_id in SOURCE_IDS:
                current = _current_month(dataset_id, period)
                file = files.get(dataset_id)
                if file is None:
                    if current is None:
                        errors.append(_issue("MISSING_INPUT", f"{_label(dataset_id)} is required for {period}.", dataset_id))
                        continue
                    reviews.append(_review_version(current))
                else:
                    check = ingestion.validate_source(dataset_id, file, expected_period=period,
                        date_format=dates.get(dataset_id), replaces=replacing.get(dataset_id))
                    imports[dataset_id] = check
                    errors.extend(check.errors)
                    warnings.extend(check.warnings)
                    operation = "replace" if dataset_id in replacing else "import"
                    reviews.append(SourceReview(dataset_id=dataset_id, label=_label(dataset_id),
                        filename=check.filename, row_count=check.row_count, period=check.period,
                        version_id=current.version_id if current else None,
                        operation=operation, errors=check.errors, warnings=check.warnings))
                    if dataset_id in replacing:
                        if current is None or current.version_id != replacing[dataset_id]:
                            errors.append(_issue("STALE_CORRECTION", "The correction must name the current version for this reporting month. Refresh stored periods and validate again.", dataset_id))
                        if not (reason or "").strip():
                            errors.append(_issue("CORRECTION_REASON_REQUIRED", "Explain why the saved monthly data is being replaced.", dataset_id))
            supplied_hashes = [check.source_sha256 for check in imports.values()]
            if len(supplied_hashes) != len(set(supplied_hashes)):
                errors.append(_issue("SAME_FILE_FOR_SEVERAL_DATASETS", "Sales, samples and assignments need separate source files."))
            parsing_ms = _ms(started)
            loading_started = time.perf_counter()
            if not errors:
                for dataset_id in SOURCE_IDS:
                    selected = _selected_versions(dataset_id, period)
                    check = imports.get(dataset_id)
                    if check is not None:
                        selected = tuple(v for v in selected if v.period != period)
                    versions[dataset_id] = tuple(v.version_id for v in selected)
                    try:
                        frames[dataset_id] = _frames(dataset_id, selected, check)
                    except WorkbenchError as error:
                        errors.append(error.as_validation_issue(dataset_id))
            loading_ms = _ms(loading_started)
            return self._finish_review(period, action, reviews, frames, errors, warnings,
                imports=imports, replacements=replacing, reason=reason,
                versions=versions, baseline=baseline, receipt=None,
                source_selection="Current stored history plus the reviewed monthly inputs",
                timings={"upload_parsing_validation": parsing_ms, "historical_loading": loading_ms},
                started=started)

    def validate_saved(self, period: str, cycle_id: str | None = None) -> WorkflowValidation:
        with self._lock:
            self._pending = None
            period = _period(period)
            action = _action()
            receipt = cycle_receipts.CYCLE_RECEIPTS.get(period, cycle_id) if cycle_id else None
            started = time.perf_counter()
            errors: list[ValidationIssue] = []
            reviews: list[SourceReview] = []
            versions: dict[str, tuple[str, ...]] = {}
            frames: dict[str, pl.DataFrame] = {}
            for dataset_id in SOURCE_IDS:
                try:
                    selected = (tuple(data_library.get_version(dataset_id, version_id)
                        for version_id in receipt.versions[dataset_id]) if receipt else
                        _selected_versions(dataset_id, period))
                    selected = tuple(sorted(selected, key=lambda v: v.period or ""))
                    periods = [v.period for v in selected]
                    if (not selected or periods[-1] != period or None in periods or
                        len(periods) != len(set(periods)) or any(p and p > period for p in periods) or
                        (dataset_id == "account_assignments" and len(selected) != 1)):
                        raise UnknownDatasetVersionError(f"{_label(dataset_id)} needs an exact {period} source state.", details={"period": period})
                    versions[dataset_id] = tuple(v.version_id for v in selected)
                    reviews.append(_review_version(selected[-1]))
                    frames[dataset_id] = _frames(dataset_id, selected)
                except WorkbenchError as error:
                    errors.append(error.as_validation_issue(dataset_id))
            return self._finish_review(period, action, reviews, frames, errors,
                ([_issue("ACTION_VERSION_CHANGED", f"This cycle was originally generated with Action {receipt.action.version}; the installed Action is {action.version}. Exact sources are preserved, but report calculations may differ.")]
                    if receipt and receipt.action.version != action.version else []),
                imports={}, replacements={}, reason=None, versions=versions,
                baseline=_baseline(period), receipt=receipt,
                source_selection=("Exact sources saved with this reporting cycle" if receipt else
                    "Current stored versions; generating will capture this selection"),
                timings={"historical_loading": _ms(started)}, started=started)

    def _finish_review(self, period: str, action: Action, reviews: list[SourceReview],
                       frames: dict[str, pl.DataFrame], errors: list[ValidationIssue],
                       warnings: list[ValidationIssue], *, imports: dict[str, SourceValidation],
                       replacements: dict[str, str], reason: str | None,
                       versions: dict[str, tuple[str, ...]], baseline: dict[str, tuple[str, ...]],
                       receipt: CycleReceipt | None, source_selection: str,
                       timings: dict[str, float], started: float) -> WorkflowValidation:
        check_started = time.perf_counter()
        prepared: PreparedReport | None = None
        if not errors:
            for slot in action.inputs:
                frame = frames[slot.id]
                missing = sorted(set(slot.required_columns) - set(frame.columns))
                if missing or not frame.height:
                    errors.append(_issue("MISSING_COLUMNS" if missing else "EMPTY_DATASET",
                        f"{slot.label} is missing required data.", slot.id, {"missing_columns": missing}))
        if not errors:
            prepared = prepare(frames[SOURCE_IDS[0]], frames[SOURCE_IDS[1]], frames[SOURCE_IDS[2]],
                sales_slot=SOURCE_IDS[0], samples_slot=SOURCE_IDS[1], assignments_slot=SOURCE_IDS[2])
            errors.extend(prepared.errors)
            warnings.extend(prepared.warnings)
            if prepared.period and prepared.period.month != period:
                errors.append(_issue("MISMATCHED_REPORTING_PERIODS", "The selected source dates do not reach the requested reporting month."))
        coverage: list[CoverageReview] = []
        if prepared and prepared.period:
            for dataset_id, frame, windows in (
                (SOURCE_IDS[0], prepared.sales, (WindowKey.ROLLING_YEAR, WindowKey.PRIOR_ROLLING_YEAR)),
                (SOURCE_IDS[1], prepared.samples, (WindowKey.ROLLING_YEAR,))):
                for window in windows:
                    interval = prepared.period.window(window)
                    coverage.append(CoverageReview(dataset_id=dataset_id, window=interval.label,
                        missing_months=missing_months(frame, interval)))
        warnings = _unique_issues(warnings)
        checks = []
        for dataset_id in SOURCE_IDS:
            source = next((item for item in reviews if item.dataset_id == dataset_id), None)
            failed = any(issue.slot_id == dataset_id for issue in errors) or source is None
            checks.append(WorkflowCheck(label=_label(dataset_id), status="error" if failed else "passed",
                detail=(f"{source.row_count:,} monthly rows · {source.operation}" if source else "Source required")))
        checks.extend((WorkflowCheck(label="Reporting period", status="error" if errors else "passed", detail=period),
            WorkflowCheck(label="Sales reps detected", status="passed" if prepared and prepared.reps else "error",
                detail=f"{len(prepared.reps) if prepared else 0} workbooks"),
            WorkflowCheck(label="Historical comparisons", status="error" if not prepared else
                "warning" if any(item.missing_months for item in coverage) else "passed",
                detail="Unavailable R12 totals remain blank" if any(item.missing_months for item in coverage) else
                    "Calendar months present; source completeness still needs a spot-check")))
        ready = not errors
        expires = now() + VALIDATION_LIFETIME if ready else None
        timings["report_validation"] = _ms(check_started)
        timings["validation_total"] = _ms(started)
        summary = WorkflowValidation(validation_id=new_version_id() if ready else None,
            expires_at=expires, period=period, ready=ready, sources=tuple(reviews),
            reps=prepared.reps if prepared else (), checks=tuple(checks), coverage=tuple(coverage),
            errors=tuple(errors), warnings=tuple(warnings), action=_action_reference(action),
            source_selection=source_selection, timings_ms=timings)
        if ready:
            self._pending = PendingCycle(summary, imports, replacements, reason, versions, baseline, receipt)
        return summary

    def generate(self, validation_id: str, *, acknowledge_warnings: bool = False) -> WorkflowOutcome:
        with self._lock:
            pending = self._pending
            if (pending is None or pending.summary.validation_id != validation_id or
                pending.summary.expires_at is None or pending.summary.expires_at <= now()):
                if pending and pending.summary.expires_at and pending.summary.expires_at <= now():
                    self._pending = None
                raise InvalidRequestError("This validation expired or was replaced. Validate the inputs again.", details={"code": "VALIDATION_EXPIRED"})
            if pending.summary.warnings and not acknowledge_warnings:
                raise InvalidRequestError("Review and acknowledge the warnings before generating reports.")
            action = _action()
            if _action_reference(action) != pending.summary.action:
                self._pending = None
                raise InvalidRequestError("The report Action changed after validation. Validate again.")
            # Exact receipts intentionally survive later corrections. A live
            # import/current-source review must not commit against changed data.
            if pending.receipt is None and _baseline(pending.summary.period) != pending.baseline:
                self._pending = None
                raise InvalidRequestError("Stored sources changed after validation. Refresh and validate again.")
            self._pending = None  # Single-use token prevents duplicate submissions.
            started = time.perf_counter()
            commit_started = time.perf_counter()
            committed: dict[str, str] = {}
            refs = dict(pending.versions)
            try:
                for dataset_id in SOURCE_IDS:
                    validation = pending.imports.get(dataset_id)
                    if validation is None:
                        continue
                    version = ingestion.commit_validated_source(validation,
                        replaces=pending.replacements.get(dataset_id), reason=pending.reason if dataset_id in pending.replacements else None)
                    committed[dataset_id] = version.version_id
                    refs[dataset_id] = (*refs[dataset_id], version.version_id)
                receipt = pending.receipt or CycleReceipt(cycle_id=new_version_id(), period=pending.summary.period,
                    created_at=now(), action=_action_reference(action), versions=refs,
                    origin="monthly_import" if pending.imports else "stored_sources",
                    correction_reason=pending.reason if pending.replacements else None)
                if pending.receipt is None:
                    cycle_receipts.CYCLE_RECEIPTS.save(receipt)
            except WorkbenchError as error:
                return WorkflowOutcome(period=pending.summary.period, status="commit_failed",
                    sources_committed=all(key in committed for key in pending.imports),
                    committed_versions=committed, error=error.as_run_error(),
                    timings_ms={"persistent_commit": _ms(commit_started), "generation_total": _ms(started)})
            commit_ms = _ms(commit_started)
            run_started = time.perf_counter()
            try:
                outcome = execute_run(action, {}, receipt.selectors())
                return WorkflowOutcome(period=receipt.period, status="reports_generated", sources_committed=True,
                    committed_versions=committed, receipt=receipt, manifest=outcome.manifest,
                    timings_ms={"persistent_commit": commit_ms, "report_generation": _ms(run_started), "generation_total": _ms(started)})
            except WorkbenchError as error:
                return WorkflowOutcome(period=receipt.period, status="generation_failed", sources_committed=True,
                    committed_versions=committed, receipt=receipt, error=error.as_run_error(),
                    timings_ms={"persistent_commit": commit_ms, "report_generation": _ms(run_started), "generation_total": _ms(started)})


def _action() -> Action:
    action = registry.get_action(REPORT_ACTION_ID)
    if action is None:
        raise DataLibraryError("The monthly report Action is not registered.")
    return action


def _action_reference(action: Action) -> ActionReference:
    return ActionReference(id=action.id, name=action.name, version=action.version)


def _period(raw: str) -> str:
    try:
        period = parse_period(raw)
    except InvalidDatasetCommitError as error:
        raise InvalidRequestError(error.message, details=error.details) from error
    if int(period[:4]) < 3:
        raise InvalidRequestError("The reporting year must allow two years of comparison history.")
    return period


def _check_fields(fields: set[str]) -> None:
    unexpected = fields - set(SOURCE_IDS)
    if unexpected:
        raise InvalidRequestError("Unexpected monthly source fields.", details={"fields": sorted(unexpected)})


def _label(dataset_id: str) -> str:
    definition = known_dataset(dataset_id)
    return definition.label if definition else dataset_id


def _current_month(dataset_id: str, period: str) -> DatasetVersion | None:
    try:
        return data_library.current_version(dataset_id, period)
    except (UnknownDatasetError, UnknownDatasetVersionError):
        return None


def _selected_versions(dataset_id: str, period: str) -> tuple[DatasetVersion, ...]:
    try:
        live = data_library.current_versions(dataset_id)
    except UnknownDatasetError:
        return ()
    selected = tuple(sorted((v for v in live if v.period and
        (v.period == period if dataset_id == "account_assignments" else v.period <= period)), key=lambda v: v.period or ""))
    periods = [v.period for v in selected]
    if len(periods) != len(set(periods)):
        raise DataLibraryError("Stored history has multiple current versions for one month.")
    return selected


def _baseline(period: str) -> dict[str, tuple[str, ...]]:
    return {dataset_id: tuple(v.version_id for v in _selected_versions(dataset_id, period)) for dataset_id in SOURCE_IDS}


def _review_version(version: DatasetVersion) -> SourceReview:
    return SourceReview(dataset_id=version.dataset_id, label=_label(version.dataset_id),
        filename=version.source_filename, row_count=version.row_count, period=version.period,
        version_id=version.version_id, operation="reuse")


def _frames(dataset_id: str, selected: tuple[DatasetVersion, ...],
            incoming: SourceValidation | None = None) -> pl.DataFrame:
    selector = DatasetSelector.parse("history")
    items = [ResolvedLibraryInput(slot_id=dataset_id, dataset_id=dataset_id, dataset_label=_label(dataset_id),
        selector=selector, version=version, frame=data_library.load_version(dataset_id, version.version_id)) for version in selected]
    if incoming:
        assert incoming.frame is not None and incoming.parsed is not None
        definition = known_dataset(dataset_id)
        assert definition is not None
        # A transient descriptor lets preflight use the same lossless date and
        # merge policy as the runner. This identity is never saved or returned.
        descriptor = DatasetVersion(dataset_id=dataset_id, version_id=new_version_id(), dataset_kind=definition.kind,
            period=incoming.period, created_at=now(), source_filename=incoming.filename,
            source_byte_size=incoming.source_byte_size, source_sha256=incoming.source_sha256,
            parser_engine=incoming.parsed.parser_engine, worksheet=incoming.parsed.worksheet,
            date_column=incoming.schema.period_column if incoming.date_format else None,
            date_format=incoming.date_format, row_count=incoming.row_count, column_count=incoming.column_count,
            column_schema=results.column_schema(incoming.frame), min_date=incoming.min_date, max_date=incoming.max_date)
        items.append(ResolvedLibraryInput(slot_id=dataset_id, dataset_id=dataset_id, dataset_label=_label(dataset_id),
            selector=selector, version=descriptor, frame=incoming.frame))
    if not items:
        raise UnknownDatasetVersionError(f"No {_label(dataset_id)} is available.")
    return input_resolution.merge_versions(tuple(ResolvedLibraryInput(slot_id=item.slot_id,
        dataset_id=item.dataset_id, dataset_label=item.dataset_label, selector=item.selector,
        version=item.version, frame=input_resolution.interpret_dates(item)) for item in items), label=_label(dataset_id))


def _issue(code: str, message: str, slot_id: str | None = None,
           details: dict | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, slot_id=slot_id, details=details or {})


def _unique_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[str] = set()
    unique = []
    for issue in issues:
        key = issue.model_dump_json()
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


WORKFLOW = MonthlyWorkflow()
