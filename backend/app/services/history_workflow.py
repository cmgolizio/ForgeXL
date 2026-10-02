"""Review historical chunks and save explicitly selected monthly partitions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import threading
from typing import cast

from app.errors import InvalidRequestError, UnknownDatasetError, WorkbenchError
from app.models.library import DatasetCommit, known_dataset, new_version_id, now
from app.models.monthly_workflow import HistoryOutcome, HistoryReview
from app.models.schemas import ValidationIssue
from app.services import data_library, ingestion, reporting_period
from app.services.ingestion import SourceFile, SourceValidation


@dataclass
class PendingHistory:
    summary: HistoryReview
    validation: SourceValidation
    baseline: tuple[str, ...]


class HistoryWorkflow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: PendingHistory | None = None

    def discard(self, validation_id: str) -> None:
        with self._lock:
            if self._pending and self._pending.summary.validation_id == validation_id:
                self._pending = None

    def validate(self, dataset_id: str, file: SourceFile, *, date_format: str | None = None,
                 skip_existing: bool = False) -> HistoryReview:
        if dataset_id not in ("sales_history", "sample_history"):
            raise InvalidRequestError("History import accepts sales or samples. Assignments are monthly snapshots.")
        with self._lock:
            self._pending = None
            baseline = _versions(dataset_id)
            validation = ingestion.validate_source(dataset_id, file, date_format=date_format,
                allow_multiple_periods=True)
            try:
                stored = {v.period for v in data_library.current_versions(dataset_id)}
            except UnknownDatasetError:
                stored = set()
            overlap = tuple(month for month in validation.periods if month in stored)
            selected = tuple(month for month in validation.periods if month not in stored)
            errors = list(validation.errors)
            warnings = list(validation.warnings)
            if overlap:
                if skip_existing:
                    # Whole-month selection, never a row merge or a correction.
                    # Schema/date/row errors still block the entire upload.
                    errors = [issue for issue in errors if issue.code not in
                        ("PERIOD_ALREADY_COMMITTED", "DUPLICATE_SOURCE_FILE")]
                    warnings.append(ValidationIssue(code="HISTORY_MONTHS_SKIPPED",
                        message="Only missing months will be saved. Existing months are left unchanged, even if this file contains different rows for them.",
                        details={"skipped_periods": list(overlap), "import_periods": list(selected)},
                        slot_id=dataset_id))
                else:
                    errors.append(ValidationIssue(code="HISTORY_MONTHS_ALREADY_COMMITTED",
                        message="This file overlaps stored months. Choose 'Import missing months only' to leave those months unchanged, or use the monthly correction workflow.",
                        details={"existing_periods": list(overlap)}, slot_id=dataset_id))
            if not selected and validation.periods:
                errors.append(ValidationIssue(code="NO_NEW_HISTORY_MONTHS",
                    message="Every month in this file is already stored. Nothing will be saved.",
                    details={"periods": list(validation.periods)}, slot_id=dataset_id))
            imported_rows = 0
            if not errors and validation.frame is not None:
                dates, _, _ = reporting_period.read_dates(validation.frame, validation.schema,
                    validation.schema.period_column or "", date_format=validation.date_format)
                imported_rows = int(dates.dt.strftime("%Y-%m").is_in(selected).sum())
            ready = not errors
            summary = HistoryReview(validation_id=new_version_id() if ready else None,
                expires_at=now() + timedelta(minutes=15) if ready else None,
                ready=ready, dataset_id=dataset_id, filename=file.filename,
                row_count=validation.row_count, imported_row_count=imported_rows,
                periods=selected, skipped_periods=overlap if skip_existing else (),
                operation="history" if baseline and len(selected) > 1 else "monthly" if baseline else "bootstrap",
                errors=tuple(errors), warnings=tuple(warnings))
            if summary.ready:
                self._pending = PendingHistory(summary, validation, baseline)
            return summary

    def commit(self, validation_id: str, *, acknowledge_warnings: bool = False) -> HistoryOutcome:
        with self._lock:
            pending = self._pending
            if (pending is None or pending.summary.validation_id != validation_id or
                pending.summary.expires_at is None or pending.summary.expires_at <= now()):
                if pending and pending.summary.expires_at and pending.summary.expires_at <= now():
                    self._pending = None
                raise InvalidRequestError("This history validation expired or was replaced. Validate again.")
            if pending.summary.warnings and not acknowledge_warnings:
                raise InvalidRequestError("Review the history warnings before saving.")
            if _versions(pending.summary.dataset_id) != pending.baseline:
                self._pending = None
                raise InvalidRequestError("Stored history changed after validation. Validate again.")
            self._pending = None
            check = pending.validation
            committed: dict[str, str] = {}
            try:
                definition = known_dataset(check.dataset_id)
                assert definition is not None and check.frame is not None and check.parsed is not None
                data_library.ensure_dataset(definition)
                dates, _, _ = reporting_period.read_dates(check.frame, check.schema,
                    check.schema.period_column or "", date_format=check.date_format)
                months = dates.dt.strftime("%Y-%m")
                for month in pending.summary.periods:
                    selected = months == month
                    in_month = dates.filter(selected).drop_nulls()
                    version = data_library.commit_version(check.dataset_id, DatasetCommit(
                        frame=check.frame.filter(selected), source_filename=check.filename,
                        source_byte_size=check.source_byte_size, source_sha256=check.source_sha256,
                        period=month, parser_engine=check.parsed.parser_engine, worksheet=check.parsed.worksheet,
                        date_column=check.schema.period_column if check.date_format else None,
                        date_format=check.date_format, min_date=cast(date, in_month.min()), max_date=cast(date, in_month.max())))
                    committed[month] = version.version_id
                return HistoryOutcome(status="saved", dataset_id=check.dataset_id,
                    committed_versions=tuple(committed.values()), committed_periods=committed)
            except WorkbenchError as error:
                return HistoryOutcome(status="save_failed", dataset_id=check.dataset_id,
                    committed_versions=tuple(committed.values()), committed_periods=committed,
                    error=error.as_run_error())


def _versions(dataset_id: str) -> tuple[str, ...]:
    try:
        return tuple(item.version_id for item in data_library.list_versions(dataset_id))
    except UnknownDatasetError:
        return ()


HISTORY_WORKFLOW = HistoryWorkflow()
