"""Upload intake, run directories and safe filenames.

Covers build plan 3.1 (storage service), 3.2 (safe filenames), 3.3 (upload
limit) and 6C.3-6C.4 (uploads read into memory).

Two things have left this module as Phase 6 has progressed:

* A Run's *state* moved to :mod:`app.services.run_store` in Phase 6B.
* An uploaded spreadsheet stopped reaching the disk in Phase 6C. An upload is
  now read into memory and handed on as bytes; nothing is written and nothing
  is reopened. What remains on disk is only the generated artifacts, until
  Phase 6D/6F produce those in memory too.

Two rules still shape this module:

* **The API never supplies a filesystem path.** Callers pass logical IDs — a
  Run ID, an output ID — and this module derives every path from the
  configured runs directory. A Run ID is accepted only after it parses as a
  UUID, and output IDs only after they match a strict token pattern, so no
  client-supplied value can escape the runs directory. Since 6C a slot ID
  contributes to no path at all.
* **An uploaded filename is metadata, never a path.** The bytes are held under
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

#: Every upload is known by this base name; only the extension varies.
STORED_UPLOAD_STEM = "source"

_WORKING_DIRNAME = "working"
_EXPORTS_DIRNAME = "exports"

#: Read uploads in 1 MiB chunks so the limit is enforced during the read
#: rather than after the whole file has been accumulated.
_READ_CHUNK_BYTES = 1024 * 1024


class BinarySource(Protocol):
    """The minimum an upload must provide: chunked binary reads.

    Declared as a protocol so this service stays independent of the web
    framework — tests pass a plain file object, the API passes the uploaded
    file's stream.
    """

    def read(self, size: int = ..., /) -> bytes: ...


@dataclass(frozen=True)
class LoadedUpload:
    """One upload held in memory, with the metadata the manifest records.

    ``stored_filename`` is the generated name this input is known by, derived
    from its extension alone (build plan 3.2). Since Phase 6C nothing is
    written to disk under it, but it is still the evidence that the client's
    filename never became a name the application used: that is the rule
    build plan section 16 states, and the manifest field that records it is
    part of the API contract frozen in Phase 6A.
    """

    slot_id: str
    original_filename: str
    stored_filename: str
    payload: bytes
    extension: str

    @property
    def size_bytes(self) -> int:
        """Bytes actually received, counted rather than trusted from a header."""
        return len(self.payload)


@dataclass(frozen=True)
class RunPaths:
    """Every path belonging to one Run, derived from its ID.

    Instances are created by :func:`create_run` or :func:`run_paths`; nothing
    else should build these paths by hand.
    """

    run_id: str
    root: Path

    @property
    def working(self) -> Path:
        return self.root / _WORKING_DIRNAME

    @property
    def exports(self) -> Path:
        return self.root / _EXPORTS_DIRNAME

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

    Builds ``working/`` and ``exports/`` up front so later stages never have to
    decide whether a directory exists. There is no ``inputs/`` directory since
    Phase 6C: uploads are read into memory and never written.
    """
    paths = run_paths(run_id or new_run_id())
    for directory in (paths.root, paths.working, paths.exports):
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
    """Return the generated name for an upload with `extension`.

    The client's filename is never reused: every upload is known as
    ``source<ext>`` (build plan 3.2).
    """
    if extension and not SAFE_ID_PATTERN.fullmatch(extension.lstrip(".")):
        raise ValueError(f"Unsafe upload extension: {extension!r}")
    return f"{STORED_UPLOAD_STEM}{extension}"


def read_upload(
    slot_id: str,
    original_filename: str,
    source: BinarySource,
    *,
    max_bytes: int | None = None,
) -> LoadedUpload:
    """Read an upload into memory, bounded by the configured limit.

    Build plan 6C.3: the bytes go from the request straight into a memory
    buffer. Nothing is written to the filesystem and nothing is reopened.

    The stream is read in chunks and each chunk is measured before it is kept,
    so the buffer never grows past the limit: an oversized upload is refused
    during the read rather than after it has been accumulated, and can never
    become a memory error (build plan 3.3). The partial read is dropped before
    the error propagates.

    Raises:
        UploadTooLargeError: the upload exceeds `max_bytes`.
    """
    limit = config.MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    extension = extension_of(original_filename)
    stored_name = stored_filename_for(extension)

    buffer = bytearray()
    while True:
        chunk = source.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(buffer) + len(chunk) > limit:
            buffer.clear()
            raise UploadTooLargeError(
                f"{display_filename(original_filename)} is larger than the "
                f"{_human_size(limit)} upload limit.",
                details={
                    "slot_id": slot_id,
                    "limit_bytes": limit,
                    "original_filename": original_filename,
                },
            )
        buffer.extend(chunk)

    return LoadedUpload(
        slot_id=slot_id,
        original_filename=original_filename,
        stored_filename=stored_name,
        payload=bytes(buffer),
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