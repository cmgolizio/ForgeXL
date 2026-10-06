"""Bounded, ephemeral prepared CSV data. No Data Library access or filesystem IO."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock, Timer
from time import monotonic
from uuid import uuid4
from collections.abc import Iterator, Sequence

import polars as pl

from app import config
from app.actions.base import Action
from app.errors import InvalidRequestError, UploadTooLargeError, WorkbenchError
from app.models.schemas import InputMetadata
from app.services import parser, storage
from app.services.runner import PendingUpload


@dataclass(frozen=True)
class PreparedCSV:
    action_id: str
    frame: pl.DataFrame
    records: tuple[InputMetadata, ...]
    expires_at: float
    charged_bytes: int


class CSVSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, PreparedCSV] = {}
        self._timers: dict[str, Timer] = {}
        self._busy: set[str] = set()
        self._lock = RLock()
        self._reservations = 0

    @contextmanager
    def reserve(self) -> Iterator[None]:
        with self._lock:
            self._expire()
            if len(self._sessions) + self._reservations >= config.CSV_MAX_SESSIONS:
                raise InvalidRequestError("Too many CSV sessions. Release another session or wait for it to expire.")
            self._reservations += 1
        try:
            yield
        finally:
            with self._lock: self._reservations -= 1

    def _expire(self) -> None:
        for key, session in list(self._sessions.items()):
            if session.expires_at <= monotonic(): self.discard(key)

    def add(self, action: Action, uploads: Sequence[tuple[str, PendingUpload]]) -> dict:
        if not uploads or len(uploads) > config.CSV_MAX_FILES:
            raise InvalidRequestError(f"Choose between 1 and {config.CSV_MAX_FILES} CSV files.")
        slots = {slot.id: slot for slot in action.inputs}
        counts = {name: sum(slot_id == name for slot_id, _ in uploads) for name in slots}
        for name, slot in slots.items():
            if counts[name] < int(slot.required) or counts[name] > slot.max_files:
                raise InvalidRequestError(f"{slot.label} requires {'1' if slot.max_files == 1 else '1 or more'} file(s), at most {slot.max_files}.")
        if any(name not in slots for name, _ in uploads):
            raise InvalidRequestError("Unknown CSV upload field.")
        # Source first even when the multipart fields were submitted out of
        # order; append fields retain their wire order within their slot.
        ordered = [(slot.id, file) for slot in action.inputs for name, file in uploads if name == slot.id]
        frames: list[pl.DataFrame] = []
        records: list[InputMetadata] = []
        total_bytes = 0
        for index, (name, upload) in enumerate(ordered):
            if storage.extension_of(upload.filename) != ".csv":
                raise InvalidRequestError(f"{storage.display_filename(upload.filename)} must be a CSV file.")
            loaded = storage.read_upload(name, upload.filename, upload.stream, max_bytes=config.MAX_UPLOAD_BYTES)
            total_bytes += loaded.size_bytes
            if total_bytes > config.CSV_MAX_TOTAL_BYTES:
                raise UploadTooLargeError(f"Combined files exceed {config.CSV_MAX_TOTAL_BYTES} bytes.")
            try:
                parsed = parser.parse_csv_text(loaded.payload)
            except WorkbenchError as error:
                raise type(error)(f"File {index + 1} ({storage.display_filename(upload.filename)}): {error.message}", details={**error.details, "file_index": index, "filename": upload.filename}) from error
            frame = parsed.frame
            if frames and set(frame.columns) != set(frames[0].columns):
                missing = [column for column in frames[0].columns if column not in frame.columns]
                extra = [column for column in frame.columns if column not in frames[0].columns]
                raise InvalidRequestError(
                    f"File {index + 1} ({storage.display_filename(upload.filename)}) has different headers. Missing: {missing}. Extra: {extra}.",
                    details={"file_index": index, "filename": upload.filename, "missing_columns": missing, "extra_columns": extra},
                )
            frames.append(frame.select(frames[0].columns) if frames else frame)
            records.append(InputMetadata(slot_id=name, original_filename=upload.filename,
                stored_filename=f"input-{index + 1}.csv", file_size_bytes=loaded.size_bytes, extension=".csv",
                parser_engine=parsed.parser_engine, row_count=frame.height, column_count=frame.width,
                columns=tuple(frame.columns)))
        combined = pl.concat(frames, how="vertical")
        charge = max(total_bytes, int(combined.estimated_size()))
        with self._lock:
            self._expire()
            if len(self._sessions) >= config.CSV_MAX_SESSIONS or sum(item.charged_bytes for item in self._sessions.values()) + charge > config.CSV_MAX_RETAINED_BYTES:
                raise UploadTooLargeError("Prepared CSV memory limit reached. Release other CSV sessions or choose smaller files.")
            session_id = str(uuid4())
            self._sessions[session_id] = PreparedCSV(action.id, combined, tuple(records), monotonic() + config.CSV_SESSION_TTL_SECONDS, charge)
            timer = Timer(config.CSV_SESSION_TTL_SECONDS, self.discard, args=(session_id,))
            timer.daemon = True
            self._timers[session_id] = timer
            timer.start()
        return {"session_id": session_id, "columns": combined.columns, "rows_received": combined.height,
                "files": [record.model_dump() for record in records], "expires_in_seconds": config.CSV_SESSION_TTL_SECONDS}

    @contextmanager
    def use(self, session_id: str, action_id: str) -> Iterator[PreparedCSV]:
        with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if session is None:
                raise InvalidRequestError("CSV session expired, was released, or the backend restarted. Inspect the selected files again.")
            if session.action_id != action_id:
                raise InvalidRequestError("The CSV action changed. Inspect the files again.")
            if session_id in self._busy:
                raise InvalidRequestError("This CSV session is already processing.")
            self._busy.add(session_id)
        try:
            yield session
        finally:
            with self._lock: self._busy.discard(session_id)

    def reorder(self, session_id: str, order: list[int]) -> dict:
        """Reorder retained row segments without uploading or parsing again."""
        with self.use(session_id, "combine_csv") as prepared:
            if sorted(order) != list(range(len(prepared.records))) or order[0] != 0:
                raise InvalidRequestError("Append order must include each file once, with the source first.")
            offsets = [0]
            for record in prepared.records: offsets.append(offsets[-1] + record.row_count)
            frame = pl.concat([prepared.frame.slice(offsets[n], prepared.records[n].row_count) for n in order])
            records = tuple(prepared.records[n] for n in order)
            with self._lock:
                if session_id not in self._sessions:
                    raise InvalidRequestError("CSV session expired or was released. Inspect the files again.")
                self.discard(session_id)
                new_id = str(uuid4())
                remaining = max(0, prepared.expires_at - monotonic())
                self._sessions[new_id] = PreparedCSV(prepared.action_id, frame, records, prepared.expires_at, prepared.charged_bytes)
                timer = Timer(remaining, self.discard, args=(new_id,))
                timer.daemon = True
                self._timers[new_id] = timer
                timer.start()
            return {"session_id": new_id, "columns": frame.columns, "rows_received": frame.height,
                    "files": [record.model_dump() for record in records], "expires_in_seconds": int(remaining)}

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            timer = self._timers.pop(session_id, None)
            if timer: timer.cancel()

    def clear(self) -> None:
        with self._lock:
            for key in list(self._sessions): self.discard(key)


SESSIONS = CSVSessionStore()
