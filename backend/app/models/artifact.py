"""A finished file a Run produced, as runtime state (build plan 12A-12C).

ForgeXL has always had one kind of result: a table. An Action transforms
DataFrames and the runner exports them as CSV or XLSX on request. Build plan
12A adds a second kind, because some Actions produce a *file* rather than a
table:

    tabular output   a DataFrame. Previewed, paged, exported as CSV or XLSX.
    artifact         finished bytes. Downloaded as they are.

The distinction is not bureaucratic. A report workbook carries layout,
formatting, several sections and presentation logic; calling it "another
DataFrame" would give it a column schema it does not have, a preview endpoint
that could not render it, and a CSV export that would silently throw its
formatting away. Build plan 12A: "Do not pretend a finished workbook ... is
merely another DataFrame."

An :class:`Artifact` is therefore the bytes plus the six facts build plan 12C
names — ID, label, filename, media type, byte size, artifact type — and
nothing else. **No field holds a path**, because there is no path: like a
Run's result frames, an artifact lives in the Run's memory for as long as the
Run Store keeps the Run, and forgetting the Run releases it (build plan 12C,
6D.8). Nothing is written to the filesystem at any point.

Two helpers exist because build plan 12E asks for artifacts that are
"collision-safe and deterministic where appropriate", and an Action producing
one file per sales rep derives both its IDs and its filenames from data:
:func:`artifact_ids` builds one safe, unique ID per value, and
:func:`artifact_filename` builds one safe filename. They treat a collision
differently, on purpose — see :func:`artifact_ids`.

The one rule this module enforces hard is the filename rule, and build plan
12F is why. An artifact's filename is written into a ``Content-Disposition``
header and used verbatim as a ZIP entry name, so a name carrying ``..`` or a
separator would let an Action — or, through an Action, the data an Action
read — decide where a file lands when the user extracts the archive.
:func:`check_artifact_filename` refuses anything that is not a single flat
name, and :func:`artifact_filename` is the sanctioned way to *build* one out
of arbitrary text such as a person's name.

Artifacts are constructed by Actions, so this module is deliberately
dependency-light: it imports the schemas it describes itself with and nothing
else. :class:`~app.actions.base.ActionResult` re-exports :class:`Artifact`, so
an Action needs only the one import line it already has.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.schemas import ArtifactMetadata, ArtifactType

# ---------------------------------------------------------------------------
# Media types
# ---------------------------------------------------------------------------

#: The workbook format this application writes.
XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

#: The archive format the batch download uses (build plan 12F).
ZIP_MEDIA_TYPE = "application/zip"

#: Media type used when an artifact does not name one.
DEFAULT_MEDIA_TYPE = "application/octet-stream"

#: The media type each artifact kind is served as when none is given. Coarse
#: on purpose: an artifact may always state its own.
DEFAULT_MEDIA_TYPES: dict[ArtifactType, str] = {
    ArtifactType.WORKBOOK: XLSX_MEDIA_TYPE,
    ArtifactType.ARCHIVE: ZIP_MEDIA_TYPE,
    ArtifactType.DOCUMENT: "application/pdf",
    ArtifactType.TEXT: "text/plain; charset=utf-8",
    ArtifactType.OTHER: DEFAULT_MEDIA_TYPE,
}

# ---------------------------------------------------------------------------
# Identity and filename rules (build plan 12E, 12F)
# ---------------------------------------------------------------------------

#: An artifact ID is an internal token that appears in a URL path segment. The
#: same shape :mod:`app.services.storage` requires of every other generated ID.
ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: Cap on an artifact ID, so a pathological one cannot produce an unusable URL.
MAX_ARTIFACT_ID_LENGTH = 120

#: Cap on an artifact filename. Comfortably inside the 255-byte limit every
#: common filesystem imposes, with room for the extension and for a ZIP
#: extractor that adds a folder above it.
MAX_ARTIFACT_FILENAME_LENGTH = 150

#: Characters that must never appear in an artifact filename. The separators
#: are what build plan 12F is about; the rest are refused by Windows, and a
#: report bundle that cannot be extracted on Windows is not a bundle.
FORBIDDEN_FILENAME_CHARACTERS = frozenset('/\\:*?"<>|')

#: Names Windows reserves as devices, compared case-insensitively and ignoring
#: any extension. A file called ``CON.xlsx`` cannot be written there at all.
RESERVED_FILENAME_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)

#: Used when text cleans down to nothing at all.
FALLBACK_FILENAME_STEM = "artifact"

#: Characters an artifact ID keeps; everything else becomes a hyphen.
_ID_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")


class UnsafeArtifactFilenameError(ValueError):
    """An artifact filename is not a single flat name (build plan 12F).

    A :class:`ValueError` rather than a
    :class:`~app.errors.WorkbenchError`, because it is a fault in the Action
    that produced the artifact rather than anything the user did. The runner
    converts an Action's exceptions into the structured
    :class:`~app.errors.ActionExecutionError` a client sees, and the specific
    cause is written to the local log.
    """


def check_artifact_filename(filename: str) -> str:
    """Return `filename` if it is a safe flat filename, else raise.

    Safe means all of the following, and the reasons are build plan 12F's:

    * It is non-empty and is not made only of whitespace.
    * It contains no directory separator, no colon and no other character
      Windows refuses, so it can never name a location.
    * It is not ``.`` or ``..`` and does not begin with a dot, so it can
      neither traverse upwards nor unzip as a hidden file.
    * It contains no control character, which also means it can never inject a
      newline into a ``Content-Disposition`` header.
    * It does not begin or end with a space or a dot, both of which Windows
      silently strips — a difference between the name offered and the name
      written is exactly the kind of quiet substitution this project refuses.
    * Its stem is not a reserved Windows device name.
    * It fits :data:`MAX_ARTIFACT_FILENAME_LENGTH` characters.

    Raises:
        UnsafeArtifactFilenameError: naming which rule the filename broke.
    """
    if not isinstance(filename, str):
        raise UnsafeArtifactFilenameError(
            f"An artifact filename must be text, not {type(filename).__name__}."
        )

    if not filename.strip():
        raise UnsafeArtifactFilenameError(
            "An artifact filename cannot be empty."
        )

    if len(filename) > MAX_ARTIFACT_FILENAME_LENGTH:
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} is {len(filename)} characters; "
            f"the limit is {MAX_ARTIFACT_FILENAME_LENGTH}."
        )

    found = sorted(FORBIDDEN_FILENAME_CHARACTERS.intersection(filename))
    if found:
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} contains {''.join(found)!r}. An "
            "artifact filename is a single flat name, never a path "
            "(build plan 12F)."
        )

    if any(character < " " or character == "\x7f" for character in filename):
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} contains a control character."
        )

    if filename.startswith("."):
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} begins with a dot. '.' and '..' "
            "are directories, and a leading dot hides the extracted file."
        )

    if filename != filename.strip(" ."):
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} begins or ends with a space or a "
            "dot, which Windows removes without saying so."
        )

    stem = filename.split(".", 1)[0].casefold()
    if stem in RESERVED_FILENAME_STEMS:
        raise UnsafeArtifactFilenameError(
            f"Artifact filename {filename!r} uses the reserved device name "
            f"{stem.upper()!r}."
        )

    return filename


def artifact_filename(stem: str, extension: str) -> str:
    """Build a safe artifact filename out of arbitrary text.

    This is the sanctioned way to turn data into a name — a sales rep's name
    into ``Beth Comeaux - September 2026.xlsx``, say. Cleaning happens *here*,
    where an Action asks for it explicitly, and never inside
    :class:`Artifact`, which refuses an unsafe name outright. The project's
    rule against silent renaming is about values the user supplied and expects
    back unchanged; a filename an Action derives from them is a label it is
    choosing, and this function's whole contract is to choose a legal one.

    What it does, in order: normalises to NFC so two spellings of an accented
    name produce one filename, replaces every character
    :func:`check_artifact_filename` refuses with a space, drops control
    characters, collapses runs of whitespace, trims spaces and dots from both
    ends, substitutes :data:`FALLBACK_FILENAME_STEM` if nothing survives,
    suffixes a reserved device name, truncates to fit, and appends the
    extension.

    Args:
        stem: The text to derive the name from, e.g. a person's name.
        extension: The file extension, with or without its leading dot.

    Returns:
        A filename :func:`check_artifact_filename` accepts.
    """
    suffix = f".{extension.lstrip('.').strip().lower()}" if extension else ""

    cleaned = unicodedata.normalize("NFC", str(stem))
    cleaned = "".join(
        " "
        if character in FORBIDDEN_FILENAME_CHARACTERS
        or character < " "
        or character == "\x7f"
        else character
        for character in cleaned
    )
    cleaned = " ".join(cleaned.split()).strip(" .")

    if not cleaned:
        cleaned = FALLBACK_FILENAME_STEM
    if cleaned.split(".", 1)[0].casefold() in RESERVED_FILENAME_STEMS:
        cleaned = f"{cleaned} file"

    room = MAX_ARTIFACT_FILENAME_LENGTH - len(suffix)
    cleaned = cleaned[:room].strip(" .") or FALLBACK_FILENAME_STEM

    return check_artifact_filename(f"{cleaned}{suffix}")


def artifact_id(value: str) -> str:
    """Build one safe artifact ID out of arbitrary text.

    An artifact ID is an internal token that appears in a URL path segment, so
    it is reduced to lowercase ASCII words joined by hyphens — the same shape
    :func:`app.services.export.download_filename` reduces an Action ID to.
    Accents are folded away rather than escaped: ``Château Réal`` becomes
    ``chateau-real``, which is a token, not a name. The *name* is the
    filename, and that keeps its accents exactly (build plan 12C).

    Use :func:`artifact_ids` when the values come from data and two of them
    might be the same.
    """
    folded = unicodedata.normalize("NFKD", str(value))
    ascii_only = "".join(
        character
        for character in folded
        if not unicodedata.combining(character)
    ).encode("ascii", "ignore").decode("ascii")

    slug = _ID_SEPARATOR_PATTERN.sub("-", ascii_only.lower()).strip("-")
    slug = slug[:MAX_ARTIFACT_ID_LENGTH].strip("-")
    return slug or FALLBACK_FILENAME_STEM


def artifact_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Return one safe, unique artifact ID per value, in order.

    **A colliding ID is numbered apart, and a colliding filename is not.** The
    difference is who reads it. A filename is what the user receives and what
    they asked for, so renaming one behind their back would hand them a file
    called something they did not choose (build plan section 3.3) — the
    collision is reported instead, by
    :class:`~app.actions.base.ActionResult`. An ID is an internal handle that
    appears only in a URL, so two rows that reduce to the same token are given
    distinct handles here rather than failing a Run over something no one will
    ever see. It is the same split :func:`app.services.export.worksheet_names`
    already makes for worksheet names.

    Deterministic: the same values in the same order always produce the same
    IDs, which is what makes a re-run reproduce a Run's download URLs
    (build plan 12E).
    """
    taken: set[str] = set()
    generated: list[str] = []

    for value in values:
        base = artifact_id(value)
        candidate = base
        counter = 1
        while candidate in taken:
            counter += 1
            suffix = f"-{counter}"
            trimmed = base[: MAX_ARTIFACT_ID_LENGTH - len(suffix)].strip("-")
            candidate = f"{trimmed or FALLBACK_FILENAME_STEM}{suffix}"
        taken.add(candidate)
        generated.append(candidate)

    return tuple(generated)


# ---------------------------------------------------------------------------
# The artifact itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    """One finished file a Run produced (build plan 12A-12C).

    Plain Python rather than Pydantic, for the same reason
    :class:`~app.actions.base.ActionResult` is: it carries bytes, which are
    never serialised into a response body and which Pydantic would only copy.
    :meth:`to_metadata` is the boundary where it becomes API-facing data.

    Frozen, and validated on construction: an artifact that exists is an
    artifact whose ID is usable in a URL and whose filename is safe to write
    into a header and into a ZIP.
    """

    #: Unique within its Run. Appears in the download URL, so it is restricted
    #: to characters that need no escaping there.
    id: str

    #: What the UI calls this file, e.g. "Beth Comeaux — September 2026".
    label: str

    #: The flat name the file downloads as. Never a path (build plan 12F).
    filename: str

    #: The bytes. The only place they exist; nothing is written to disk.
    payload: bytes

    #: Coarse category, for presentation (build plan 12C).
    artifact_type: ArtifactType = ArtifactType.OTHER

    #: IANA media type. Defaults to the one for `artifact_type`.
    media_type: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not ARTIFACT_ID_PATTERN.fullmatch(
            self.id
        ):
            raise ValueError(
                f"Artifact id {self.id!r} must start with a letter or digit "
                "and contain only letters, digits, '_', '-' and '.'."
            )
        if len(self.id) > MAX_ARTIFACT_ID_LENGTH:
            raise ValueError(
                f"Artifact id {self.id!r} is longer than "
                f"{MAX_ARTIFACT_ID_LENGTH} characters."
            )
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError(f"Artifact {self.id!r} must have a label.")
        if not isinstance(self.payload, (bytes, bytearray)):
            raise ValueError(
                f"Artifact {self.id!r} must carry bytes, not "
                f"{type(self.payload).__name__}."
            )

        check_artifact_filename(self.filename)

        object.__setattr__(self, "payload", bytes(self.payload))
        if not self.media_type:
            object.__setattr__(
                self,
                "media_type",
                DEFAULT_MEDIA_TYPES.get(self.artifact_type, DEFAULT_MEDIA_TYPE),
            )

    @property
    def size_bytes(self) -> int:
        """Bytes this artifact holds, counted rather than declared."""
        return len(self.payload)

    def to_metadata(self) -> ArtifactMetadata:
        """Describe this artifact for the manifest, without its bytes."""
        return ArtifactMetadata(
            id=self.id,
            label=self.label,
            filename=self.filename,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            artifact_type=self.artifact_type,
        )

    @classmethod
    def workbook(
        cls, *, id: str, label: str, filename: str, payload: bytes
    ) -> Artifact:
        """Build an XLSX artifact, with the workbook media type filled in."""
        return cls(
            id=id,
            label=label,
            filename=filename,
            payload=payload,
            artifact_type=ArtifactType.WORKBOOK,
        )

    @classmethod
    def archive(
        cls, *, id: str, label: str, filename: str, payload: bytes
    ) -> Artifact:
        """Build a ZIP artifact, with the archive media type filled in."""
        return cls(
            id=id,
            label=label,
            filename=filename,
            payload=payload,
            artifact_type=ArtifactType.ARCHIVE,
        )