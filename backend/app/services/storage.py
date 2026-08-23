"""Run directories, safe file storage and manifest persistence.

Covers build plan 3.1 (storage service), 3.2 (safe filenames), 3.3 (upload
limit) and 3.11 (atomic manifest writing).

Two rules shape this module:

* **The API never supplies a filesystem path.** Callers pass logical IDs — a
  Run ID, a slot ID, an output ID — and this module derives every path from
  the configured runs directory. A Run ID is accepted only after it parses as
  a UUID, and slot/output IDs only after they match a strict token pattern, so
  no client-supplied value can escape the runs directory.
* **An uploaded filename is metadata, never a path.** The bytes are written to
  a generated name; the name the browser sent is recorded in the manifest and
  used nowhere else (build plan section 16).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app import config
from app.errors import UnknownRunError, UploadTooLargeError
from app.models.schemas import RunManifest

#: Slot and output IDs are declared by trusted Action code, but they are still
#: checked before they contribute to a path: no dots, separators or spaces, so
#: no ID can traverse out of its Run directory.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Every stored upload gets this base name; only the extension varies.
STORED_UPLOAD_STEM = "source"

MANIFEST_FILENAME = "manifest.json"

_INPUTS_DIRNAME = "inputs"
_WORKING_DIRNAME = "working"
_EXPORTS_DIRNAME = "exports"

#: Copy uploads in 1 MiB chunks so a large file is never held in memory.
_COPY_CHUNK_BYTES = 1024 * 1024


class BinarySource(Protocol):
    """The minimum an upload must provide: chunked binary reads.

    Declared as a protocol so this service stays independent of the web
    framework — tests pass a plain file object, the API passes the uploaded
    file's stream.
    """

    def read(self, size: int = ..., /) -> bytes: ...


@dataclass(frozen=True)
class StoredUpload:
    """One preserved upload, described for the manifest."""

    slot_id: str
    original_filename: str
    stored_filename: str
    path: Path
    size_bytes: int
    extension: str


@dataclass(frozen=True)
class RunPaths:
    """Every path belonging to one Run, derived from its ID.

    Instances are created by :func:`create_run` or :func:`run_paths`; nothing
    else should build these paths by hand.
    """

    run_id: str
    root: Path

    @property
    def inputs(self) -> Path:
        return self.root / _INPUTS_DIRNAME

    @property
    def working(self) -> Path:
        return self.root / _WORKING_DIRNAME

    @property
    def exports(self) -> Path:
        return self.root / _EXPORTS_DIRNAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    def input_directory(self, slot_id: str) -> Path:
        """Directory holding the upload for one input slot."""
        return self.inputs / _safe_id(slot_id, "input slot")

    def working_artifact(self, output_id: str) -> Path:
        """Internal Parquet representation of one output (build plan 28)."""
        return self.working / f"{_safe_id(output_id, 'output')}.parquet"

    def export_artifact(self, output_id: str, export_format: str) -> Path:
        """User-facing export of one output, e.g. ``exports/product_master.csv``."""
        return self.exports / (
            f"{_safe_id(output_id, 'output')}.{_safe_id(export_format, 'format')}"
        )


def _safe_id(value: str, label: str) -> str:
    """Return `value` unchanged, or raise if it could not be part of a path."""
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe {label} id: {value!r}")
    return value


def new_run_id() -> str:
    """Generate the UUID identifying one Run (build plan section 9.3)."""
    return str(uuid.uuid4())


def parse_run_id(raw: str) -> str:
    """Validate a client-supplied Run ID before it is used as a path.

    Accepts only the canonical string form of a UUID. Anything else — a
    traversal attempt, a truncated ID, a name with separators — raises
    :class:`~app.errors.UnknownRunError` rather than reaching the filesystem.
    """
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise UnknownRunError(
            "That is not a valid Run ID.", details={"run_id": raw}
        ) from None
    if str(parsed) != raw.lower():
        raise UnknownRunError("That is not a valid Run ID.", details={"run_id": raw})
    return str(parsed)


def runs_directory() -> Path:
    """Root directory holding one subdirectory per Run.

    Read through a function rather than captured at import time so tests can
    redirect it and never touch the real ``data/runs``.
    """
    return config.RUNS_DIRECTORY


def run_paths(run_id: str) -> RunPaths:
    """Return the paths for `run_id` without creating anything."""
    validated = parse_run_id(run_id)
    return RunPaths(run_id=validated, root=runs_directory() / validated)


def create_run(run_id: str | None = None) -> RunPaths:
    """Create the directory tree for a new Run and return its paths.

    Builds ``inputs/``, ``working/`` and ``exports/`` up front so later stages
    never have to decide whether a directory exists.
    """
    paths = run_paths(run_id or new_run_id())
    for directory in (paths.root, paths.inputs, paths.working, paths.exports):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def run_exists(run_id: str) -> bool:
    """Whether a Run directory with a manifest exists for `run_id`."""
    try:
        return run_paths(run_id).manifest_path.is_file()
    except UnknownRunError:
        return False


def extension_of(filename: str) -> str:
    """Return the lowercase extension of an uploaded filename, e.g. ``.csv``.

    The filename is treated purely as text. Any directory component a client
    included is discarded before the extension is read, so a name such as
    ``../../evil.csv`` yields ``.csv`` and nothing more.
    """
    basename = PurePosixPath(filename.replace("\\", "/")).name
    return Path(basename).suffix.lower()


def stored_filename_for(extension: str) -> str:
    """Return the generated on-disk name for an upload with `extension`.

    The client's filename is never reused: every upload is stored as
    ``source<ext>`` inside its own slot directory (build plan 3.2).
    """
    if extension and not SAFE_ID_PATTERN.fullmatch(extension.lstrip(".")):
        raise ValueError(f"Unsafe upload extension: {extension!r}")
    return f"{STORED_UPLOAD_STEM}{extension}"


def store_upload(
    paths: RunPaths,
    slot_id: str,
    original_filename: str,
    source: BinarySource,
    *,
    max_bytes: int | None = None,
) -> StoredUpload:
    """Copy an upload into its Run directory under a generated name.

    The stream is copied in chunks and the running total is checked against the
    limit, so an oversized upload is rejected during the copy rather than after
    it has been read into memory (build plan 3.3). A partial file left by a
    rejected upload is removed before the error propagates.

    Raises:
        UploadTooLargeError: the upload exceeds `max_bytes`.
    """
    limit = config.MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    extension = extension_of(original_filename)
    stored_name = stored_filename_for(extension)

    destination_directory = paths.input_directory(slot_id)
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination = destination_directory / stored_name

    written = 0
    try:
        with destination.open("wb") as target:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise UploadTooLargeError(
                        f"{display_filename(original_filename)} is larger than the "
                        f"{_human_size(limit)} upload limit.",
                        details={
                            "slot_id": slot_id,
                            "limit_bytes": limit,
                            "original_filename": original_filename,
                        },
                    )
                target.write(chunk)
    except UploadTooLargeError:
        destination.unlink(missing_ok=True)
        raise

    return StoredUpload(
        slot_id=slot_id,
        original_filename=original_filename,
        stored_filename=stored_name,
        path=destination,
        size_bytes=written,
        extension=extension,
    )


def display_filename(filename: str) -> str:
    """Basename of an uploaded filename, for use in a user-facing message.

    Any directory component the client included is dropped, so an error message
    never echoes back a path-shaped name.
    """
    return PurePosixPath(filename.replace("\\", "/")).name or filename


def _human_size(value: int) -> str:
    """Render a byte count for a user-facing message.

    Whole megabytes above 1 MB, bytes below it: a 250 MB limit should read
    "250 MB", and a limit configured smaller than a megabyte must not round
    down to a meaningless "0 MB".
    """
    megabyte = 1024 * 1024
    if value >= megabyte:
        return f"{value // megabyte} MB"
    return f"{value} byte{'' if value == 1 else 's'}"


def write_manifest(paths: RunPaths, manifest: RunManifest) -> Path:
    """Write a Run's manifest atomically (build plan 3.11).

    The JSON is written to a temporary file in the same directory and then
    renamed over the destination, so an interrupted process can never leave a
    half-written ``manifest.json`` behind. ``os.replace`` is atomic within a
    filesystem.
    """
    paths.root.mkdir(parents=True, exist_ok=True)
    destination = paths.manifest_path
    temporary = destination.with_name(f"{MANIFEST_FILENAME}.{os.getpid()}.tmp")

    payload = manifest.model_dump(mode="json")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def read_manifest(run_id: str) -> RunManifest:
    """Load the manifest for `run_id`.

    Raises:
        UnknownRunError: the ID is malformed, or no such Run was recorded.
    """
    paths = run_paths(run_id)
    manifest_path = paths.manifest_path
    if not manifest_path.is_file():
        raise UnknownRunError(
            "No Run exists with that ID.", details={"run_id": paths.run_id}
        )
    try:
        return RunManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise UnknownRunError(
            "That Run's manifest could not be read.",
            details={"run_id": paths.run_id},
        ) from exc