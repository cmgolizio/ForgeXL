"""Bundling a Run's artifacts into one ZIP archive (build plan 12F).

An Action that produces one workbook per sales rep produces a dozen files, and
downloading a dozen files one link at a time is not a feature. This module
turns the artifacts a Run holds into a single archive.

It follows the same rules every other generated download follows:

* **Nothing is written.** The archive is assembled in a memory buffer and the
  buffer is released with the call, exactly as CSV and XLSX exports are
  (build plan 6F.2, 6F.7). No temporary file is created, not even in the OS
  temporary directory.
* **No server path appears anywhere in it.** Every entry is a flat filename
  the artifact already declared (build plan 6F.8).

And it adds the rule build plan 12F exists for. **An entry name can never be
anything but a flat filename.** :func:`app.models.artifact.check_artifact_filename`
already refuses a separator, a ``..`` or a control character when an artifact
is constructed, and this module checks again on the way into the archive.
Checking twice is deliberate: the first check protects the
``Content-Disposition`` header, and this one protects the user's filesystem
when they extract the archive somewhere. A ZIP entry called
``../../.ssh/authorized_keys`` is the oldest trick there is, and an archive
writer is exactly where it has to be impossible rather than merely unlikely.

The archive is **deterministic**: the same artifacts bundled with the same
timestamp produce byte-identical output (build plan 12E). Entry order follows
the order the Action listed its artifacts in, every entry is stamped with the
one timestamp the caller supplies rather than with "now", and no extra
metadata is recorded. Re-downloading a Run's bundle therefore returns the same
bytes rather than a file that merely contains the same things.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone

from app.models.artifact import Artifact, check_artifact_filename

#: The archive format's media type, used by the download route.
ZIP_MEDIA_TYPE = "application/zip"

#: The extension a bundle is offered under.
ZIP_EXTENSION = "zip"

#: Entries are deflated. A formatted workbook is already a ZIP of compressed
#: XML, so this buys little on a single artifact and a useful amount on a
#: bundle of similar ones; either way it is what a user's unzip tool expects.
COMPRESSION = zipfile.ZIP_DEFLATED

#: Unix permissions recorded on each entry: rw-r--r--, shifted into the high
#: half of `external_attr` where the ZIP format keeps them. Stated explicitly
#: so extraction produces an ordinary readable file rather than whatever the
#: writer's umask happened to be.
_ENTRY_PERMISSIONS = 0o644 << 16

#: The earliest moment the ZIP format can record. A timestamp before it cannot
#: be stored, so it is clamped rather than silently wrapping to 2044.
_EARLIEST_ZIP_MOMENT = datetime(1980, 1, 1, tzinfo=timezone.utc)


def to_zip_bytes(
    artifacts: Sequence[Artifact], *, timestamp: datetime
) -> bytes:
    """Bundle `artifacts` into one ZIP archive, built in memory.

    Args:
        artifacts: The files to bundle, in the order they should appear.
        timestamp: The moment every entry is stamped with. The Run's own
            completion time, so the archive is a fact about that Run and two
            downloads of it are identical.

    Returns:
        The archive's bytes.

    Raises:
        ValueError: `artifacts` is empty, or two of them share a filename.
        UnsafeArtifactFilenameError: an artifact's filename is not a flat name.
    """
    entries = tuple(artifacts)
    if not entries:
        raise ValueError("An archive must contain at least one artifact.")

    seen: dict[str, str] = {}
    for artifact in entries:
        check_artifact_filename(artifact.filename)
        folded = artifact.filename.casefold()
        if folded in seen:
            raise ValueError(
                f"Artifacts {seen[folded]!r} and {artifact.id!r} would both be "
                f"written as {artifact.filename!r}; one would overwrite the "
                "other when the archive is extracted."
            )
        seen[folded] = artifact.id

    moment = _zip_moment(timestamp)

    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", compression=COMPRESSION) as archive:
            for artifact in entries:
                info = zipfile.ZipInfo(artifact.filename, date_time=moment)
                info.compress_type = COMPRESSION
                info.external_attr = _ENTRY_PERMISSIONS
                archive.writestr(info, artifact.payload)
        return buffer.getvalue()
    finally:
        buffer.close()


def _zip_moment(timestamp: datetime) -> tuple[int, int, int, int, int, int]:
    """Render `timestamp` as the six-part local time a ZIP entry records.

    Converted to UTC first, so a machine that changes timezone does not change
    the bytes of an archive it already produced. The ZIP format cannot record
    anything before 1980, and it stores seconds in two-second units, so the
    moment is clamped and truncated here rather than being handed to
    :mod:`zipfile` to mangle.
    """
    in_utc = (
        timestamp.astimezone(timezone.utc)
        if timestamp.tzinfo is not None
        else timestamp.replace(tzinfo=timezone.utc)
    )
    if in_utc < _EARLIEST_ZIP_MOMENT:
        in_utc = _EARLIEST_ZIP_MOMENT
    return (
        in_utc.year,
        in_utc.month,
        in_utc.day,
        in_utc.hour,
        in_utc.minute,
        in_utc.second - in_utc.second % 2,
    )
