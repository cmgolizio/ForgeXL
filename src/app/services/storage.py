"""Run directories and safe file storage.

Covers build plan 3.1 (storage service), 3.2 (safe filenames) and 3.3 (upload
limit).

A Run's *state* is no longer stored here. Since Phase 6B it lives in
:mod:`app.services.run_store`, so this module holds only the files a Run still
puts on disk: the preserved upload (until Phase 6C reads uploads in memory) and
the generated artifacts (until Phase 6D/6F generate them in memory).

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

import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app import config
from app.errors import UnknownRunError, UploadTooLargeError

# Run identity belongs to the Run itself, not to the filesystem. Imported
# rather than redefined so there is exactly one Run ID convention.
from app.models.run import new_run_id, parse_run_id

#: Slot and output IDs are declared by trusted Action code, but they are still
#: checked before they contribute to a path: no dots, separators or spaces, so
#: no ID can traverse out of its Run directory.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Every stored upload gets this base name; only the extension varies.
STORED_UPLOAD_STEM = "source"

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


def delete_run_directory(run_id: str) -> bool:
    """Remove a Run's directory and everything in it, if it exists.

    Called when a Run is deleted, so releasing a Run's runtime state also
    releases the files it still keeps on disk (build plan 6B.6). Returns True
    if a directory was removed.

    Only a validated Run ID reaches this, and the directory is confirmed to be
    a direct child of the runs directory before anything is removed: nothing
    outside ``data/runs/<run-id>/`` can ever be deleted here. This function
    disappears once Phase 6C/6F stop writing Run files at all.
    """
    try:
        root = run_paths(run_id).root
    except UnknownRunError:
        return False
    if not root.is_dir() or root.parent != runs_directory():
        return False
    shutil.rmtree(root)
    return True


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