"""Review and import initial history or one additional historical month."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import threading
from typing import cast

from app.errors import InvalidRequestError, UnknownDatasetError, WorkbenchError
from app.models.library import DatasetCommit, known_dataset, new_version_id, now
from app.models.monthly_workflow import HistoryOutcome, HistoryReview
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

    def validate(self, dataset_id: str, file: SourceFile, *, date_format: str | None = None) -> HistoryReview:
        if dataset_id not in ("sales_history", "sample_history"):
            raise InvalidRequestError("History import accepts sales or samples. Assignments are monthly snapshots.")
        with self._lock:
            self._pending = None
            baseline = _versions(dataset_id)
            validation = ingestion.validate_source(dataset_id, file, date_format=date_format,
                allow_multiple_periods=not baseline)
            summary = HistoryReview(validation_id=new_version_id() if validation.ok else None,
                expires_at=now() + timedelta(minutes=15) if validation.ok else None,
                ready=validation.ok, dataset_id=dataset_id, filename=file.filename,
                row_count=validation.row_count, periods=validation.periods,
                operation="monthly" if baseline else "bootstrap",
                errors=validation.errors, warnings=validation.warnings)
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
            committed: list[str] = []
            try:
                if pending.summary.operation == "monthly":
                    committed.append(ingestion.commit_validated_source(check).version_id)
                else:
                    definition = known_dataset(check.dataset_id)
                    assert definition is not None and check.frame is not None and check.parsed is not None
                    data_library.ensure_dataset(definition)
                    dates, _, _ = reporting_period.read_dates(check.frame, check.schema,
                        check.schema.period_column or "", date_format=check.date_format)
                    months = dates.dt.strftime("%Y-%m")
                    for month in check.periods:
                        selected = months == month
                        in_month = dates.filter(selected).drop_nulls()
                        version = data_library.commit_version(check.dataset_id, DatasetCommit(
                            frame=check.frame.filter(selected), source_filename=check.filename,
                            source_byte_size=check.source_byte_size, source_sha256=check.source_sha256,
                            period=month, parser_engine=check.parsed.parser_engine, worksheet=check.parsed.worksheet,
                            date_column=check.schema.period_column if check.date_format else None,
                            date_format=check.date_format, min_date=cast(date, in_month.min()), max_date=cast(date, in_month.max())))
                        committed.append(version.version_id)
                return HistoryOutcome(status="saved", dataset_id=check.dataset_id,
                    committed_versions=tuple(committed))
            except WorkbenchError as error:
                return HistoryOutcome(status="save_failed", dataset_id=check.dataset_id,
                    committed_versions=tuple(committed), error=error.as_run_error())


def _versions(dataset_id: str) -> tuple[str, ...]:
    try:
        return tuple(item.version_id for item in data_library.list_versions(dataset_id))
    except UnknownDatasetError:
        return ()


HISTORY_WORKFLOW = HistoryWorkflow()
