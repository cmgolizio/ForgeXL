"""Upload intake and safe filenames.

Covers build plan 3.2 (safe filenames), 3.3 (the upload limit) and 6C.3-6C.4
(uploads read into memory).

Everything filesystem-shaped has now left this module:

* A Run's *state* moved to :mod:`app.services.run_store` in Phase 6B.
* An uploaded spreadsheet stopped reaching the disk in Phase 6C. An upload is
  read into memory and handed on as bytes; nothing is written and nothing is
  reopened.
* Result frames stayed in memory from Phase 6D, and CSV/XLSX bytes have been
  generated per request since Phase 6F, so no Run produced a file either.
* Phase 6I removed what that left behind: the run-directory tree, the
  path-building helpers and the directory-deletion helper. This module builds
  no path at all, and the backend has no configured data directory (build plan
  6I.1).

One rule still shapes it, and it is the reason the module still exists:

* **An uploaded filename is metadata, never a path.** The bytes are held under
  a generated name; the name the browser sent is recorded in the manifest and
  used nowhere else (build plan section 16).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from app import config
from app.errors import UploadTooLargeError

#: Output IDs are declared by trusted Action code, but an extension derived
#: from a client filename is still checked against this before it is used to
#: build the generated name below.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Every upload is known by this base name; only the extension varies.
STORED_UPLOAD_STEM = "source"

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