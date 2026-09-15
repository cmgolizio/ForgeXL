"""Structured application errors (build plan section 22).

Services raise these; the API boundary converts them into HTTP responses.
Every error carries a stable machine-readable ``code``, a plain-language
``message`` for the user and optional structured ``details``.

A traceback is never part of an error's public shape. Tracebacks are logged
locally during development and never returned to the browser.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.models.schemas import RunError, ValidationIssue


class WorkbenchError(Exception):
    """Base class for every error the backend reports to a client."""

    #: Stable identifier, e.g. ``"MISSING_COLUMNS"``.
    code: str = "INTERNAL_ERROR"

    #: HTTP status the API boundary returns for this error.
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}

    def as_run_error(self) -> RunError:
        """Render this error for the ``error`` field of a Run manifest."""
        return RunError(code=self.code, message=self.message, details=self.details)

    def as_validation_issue(self, slot_id: str | None = None) -> ValidationIssue:
        """Render this error as one entry in a manifest's validation summary."""
        return ValidationIssue(
            code=self.code,
            message=self.message,
            details=self.details,
            slot_id=slot_id,
        )

    def as_response_body(self) -> dict[str, Any]:
        """Render the JSON body the API returns (build plan section 22)."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


# ---------------------------------------------------------------------------
# 400 — malformed request
# ---------------------------------------------------------------------------


class InvalidRequestError(WorkbenchError):
    """The request itself is malformed, independent of the uploaded data."""

    code = "INVALID_REQUEST"
    http_status = 400


# ---------------------------------------------------------------------------
# 404 — unknown Action, Run or Output
# ---------------------------------------------------------------------------


class UnknownActionError(WorkbenchError):
    """No Action is registered under the requested ID.

    The registry never guesses a near match, so an unrecognised ID is always
    reported rather than resolved to something similar.
    """

    code = "UNKNOWN_ACTION"
    http_status = 404


class UnknownRunError(WorkbenchError):
    """No Run exists with the requested ID, or the ID is not a valid Run ID."""

    code = "UNKNOWN_RUN"
    http_status = 404


class UnknownOutputError(WorkbenchError):
    """The Run exists but produced no output with the requested ID."""

    code = "UNKNOWN_OUTPUT"
    http_status = 404


class MissingArtifactError(WorkbenchError):
    """The manifest lists the artifact but the file is not on disk."""

    code = "MISSING_ARTIFACT"
    http_status = 404


class UnknownDatasetError(WorkbenchError):
    """No Data Library dataset exists with the requested ID.

    Also raised for an ID that could never be a dataset ID at all — a path
    fragment, a name with separators — which is refused for its shape before
    the library is asked whether it holds one (build plan 9A).
    """

    code = "UNKNOWN_DATASET"
    http_status = 404


class UnknownDatasetVersionError(WorkbenchError):
    """The dataset exists but holds no version with the requested ID.

    Like :class:`UnknownRunError`, this also covers an ID that is not a valid
    version ID: version IDs are ForgeXL-generated UUIDs (build plan 9B), so
    anything else is refused rather than used to build a path.
    """

    code = "UNKNOWN_DATASET_VERSION"
    http_status = 404


# ---------------------------------------------------------------------------
# 413 — upload too large
# ---------------------------------------------------------------------------


class UploadTooLargeError(WorkbenchError):
    """An uploaded file exceeds ``MAX_UPLOAD_BYTES`` (build plan 3.3)."""

    code = "FILE_TOO_LARGE"
    http_status = 413


# ---------------------------------------------------------------------------
# 422 — the uploaded data failed validation
# ---------------------------------------------------------------------------


class InputValidationError(WorkbenchError):
    """Base class for the generic input checks in build plan 3.7.

    Each subclass is raised for one input slot and is converted into a
    :class:`~app.models.schemas.ValidationIssue` by the runner, so every
    failure is recorded in the Run manifest as well as returned.
    """

    code = "INVALID_INPUT"
    http_status = 422


class MissingInputError(InputValidationError):
    """A required input slot was not supplied."""

    code = "MISSING_INPUT"


class UnsupportedExtensionError(InputValidationError):
    """The uploaded file's extension is not one the Action accepts."""

    code = "UNSUPPORTED_EXTENSION"


class EmptyUploadError(InputValidationError):
    """The uploaded file contains no bytes at all (build plan 6C.4).

    Distinguished from :class:`FileParseError`, which means bytes arrived but
    could not be read, and from :class:`EmptyDatasetError`, which means the
    file parsed but holds no rows. Reporting all three as "parse error" would
    tell the user nothing about which one they have.
    """

    code = "EMPTY_FILE"


class FileParseError(InputValidationError):
    """The file could not be read as tabular data.

    Raised instead of retrying with progressively stranger parser settings: a
    parse failure is reported, never hidden (build plan 3.5).
    """

    code = "PARSE_ERROR"


class AmbiguousWorkbookError(InputValidationError):
    """The workbook contains more than one worksheet holding data.

    The POC refuses to pick one for the user (build plan section 17).
    """

    code = "AMBIGUOUS_WORKBOOK"


class EmptyDatasetError(InputValidationError):
    """The file parsed but contains no rows, and the Action requires data."""

    code = "EMPTY_DATASET"


class MissingColumnsError(InputValidationError):
    """The dataset is missing columns the Action requires.

    Column names are compared exactly: ``Sales Person`` is not treated as
    equivalent to ``Salesperson`` (build plan 3.7).
    """

    code = "MISSING_COLUMNS"


class DuplicateColumnsError(InputValidationError):
    """The uploaded file's header row uses one name for two columns.

    Every parser this project uses resolves the clash by *renaming* the later
    column — Polars produces ``SKU_duplicated_0``, fastexcel ``SKU_1`` — and
    then carries on. Build plan section 3.3 forbids exactly that: the
    application must "never silently rename required columns" and must "never
    silently choose a semantically different field". With two columns called
    ``SKU``, an Action that selects ``SKU`` gets the first one and the second
    one's values are dropped from the result without anybody being told.

    The application cannot know which of the two the user meant, so it refuses
    and says so — the same answer, for the same reason, that
    :class:`AmbiguousWorkbookError` gives a workbook holding two data sheets
    (build plan section 17).
    """

    code = "DUPLICATE_COLUMNS"


class InvalidDatasetSelectorError(InputValidationError):
    """The Data Library reference submitted for an input slot is not readable.

    A library-backed input slot is filled by naming *which* stored version to
    read (build plan 11A). The accepted forms are ``latest``,
    ``period:YYYY-MM`` and ``version:<version id>``; anything else is refused
    here rather than interpreted.

    Deliberately an :class:`InputValidationError` and not a 400. The request
    was well formed — a form field carried a value — and what is wrong is the
    value the user supplied for an input, exactly as a missing required column
    is. It fails the Run and is recorded as a validation issue on it, so the
    Run's own record says what was asked for and why it could not be honoured.

    It is a *shape* refusal only: whether the named version exists is the Data
    Library's answer, and it arrives as
    :class:`UnknownDatasetVersionError` (build plan 11E, "missing library data
    fails clearly").
    """

    code = "INVALID_DATASET_SELECTOR"


class IssueReportingError(WorkbenchError):
    """Base for an error that carries a list of :class:`ValidationIssue`.

    Two of these exist — one for a Run's inputs, one for a monthly ingestion —
    and both report a list the same way, because it is the same question: what
    was wrong with what the user supplied.

    A single issue is reported **as itself**, in the shape build plan section
    22 documents, so the UI branches on the specific code (``MISSING_COLUMNS``)
    rather than on a generic wrapper. Several issues are reported together
    under ``details.issues``. Either way the complete list stays available on
    ``.issues``, so a manifest or an import report records all of them.
    """

    #: The message used when several issues are reported at once. A subclass
    #: sets it to say which input failed.
    summary_message: str = "The uploaded data failed validation."

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        collected = tuple(issues)
        if not collected:
            raise ValueError(
                f"{type(self).__name__} requires at least one issue."
            )
        self.issues = collected

        if len(collected) == 1:
            only = collected[0]
            details = dict(only.details)
            if only.slot_id is not None:
                details.setdefault("slot_id", only.slot_id)
            super().__init__(only.message, details=details)
            # Report the specific failure rather than the generic wrapper.
            self.code = only.code
        else:
            super().__init__(
                self.summary_message,
                details={"issues": [issue.model_dump() for issue in collected]},
            )


class RunValidationError(IssueReportingError):
    """One or more validation issues stopped a Run before it executed.

    A single issue is reported directly, in the shape build plan section 22
    documents. Several issues are reported together under ``details.issues``;
    the manifest always records the complete list either way.
    """

    code = "VALIDATION_FAILED"
    http_status = 422
    summary_message = "The uploaded data failed validation."


class InvalidDatasetCommitError(WorkbenchError):
    """A commit to the Data Library was refused before anything was written.

    Raised for a commit that cannot be persisted honestly: a period that is not
    a calendar month, a snapshot without the period it is effective for, an
    empty result, or a supersession naming a version that does not exist.

    422 rather than 400 for the same reason
    :class:`RunValidationError` is: the request was understood, and what it
    describes cannot be stored. Nothing has been written when this is raised —
    every check runs before the first byte reaches the disk, which is what
    build plan 9F means by an invalid commit leaving no partially valid state.
    """

    code = "INVALID_DATASET_COMMIT"
    http_status = 422


class IngestionValidationError(IssueReportingError):
    """One or more issues stopped a monthly file from reaching the Data Library.

    The ingestion counterpart of :class:`RunValidationError`, and constructed
    the same way: one issue is reported **as itself**, so a client sees
    ``SOURCE_SCHEMA_MISMATCH`` or ``MULTIPLE_REPORTING_PERIODS`` rather than a
    generic wrapper; several are reported together under ``details.issues``.

    Every refusal in :mod:`app.services.ingestion` arrives through this one
    class, and each issue's ``code`` names the specific failure — the same
    convention :mod:`app.services.runner` already uses for ``MIXED_COLUMN_TYPES``
    and ``UNEXPECTED_INPUT``, which are issue codes without an exception class
    of their own.

    Nothing has been written to the Data Library when this is raised. Build
    plan 10F requires every check to run before the first commit, so a refusal
    always means the library is exactly as it was.
    """

    code = "INGESTION_VALIDATION_FAILED"
    http_status = 422
    summary_message = "The uploaded file cannot be imported."


class ExportTooLargeError(WorkbenchError):
    """The result cannot be represented in the requested export format.

    Raised only by the XLSX export, and only for a limit the *file format*
    imposes: a worksheet holds 1,048,576 rows and 16,384 columns, and one cell
    holds 32,767 characters. A result past any of those cannot become a
    workbook without losing data.

    It is reported rather than absorbed because both ways of absorbing it are
    forbidden. xlsxwriter's own answer to an over-long cell is to truncate it
    and carry on, which is build plan section 3.3's "silently convert invalid
    data into valid-looking data" — the user would open a spreadsheet whose
    values are quietly shorter than the ones they uploaded. Letting the
    underlying error escape instead produced a bare ``500 Internal Server
    Error`` with no structured body, which the UI can only report as the
    backend being unreachable (build plan section 22).

    422 rather than 500: nothing failed unexpectedly. The request was
    understood and the data is intact — it simply cannot be expressed in this
    format, and the same result downloads as CSV without limit, which is what
    the message says. Of the statuses build plan section 22 lists, 422 is the
    one that means "understood, but cannot be processed as asked".
    """

    code = "EXPORT_TOO_LARGE"
    http_status = 422


# ---------------------------------------------------------------------------
# 500 — the Action itself failed
# ---------------------------------------------------------------------------


class ActionExecutionError(WorkbenchError):
    """The Action raised while transforming valid, validated input.

    The underlying exception is logged locally; only this structured summary
    reaches the browser.
    """

    code = "ACTION_FAILED"
    http_status = 500


class DataLibraryError(WorkbenchError):
    """The Data Library's persistent state could not be read or written.

    This is the corrupt-or-unavailable case, not the not-found case: a record
    that no longer validates, a record written by a newer build than this one,
    or a write the filesystem refused. It is deliberately loud. Persistent
    business data that cannot be read is a fault to report, never something to
    paper over with an empty result — an empty answer would look exactly like
    "this month was never imported" (build plan 9F).
    """

    code = "DATA_LIBRARY_ERROR"
    http_status = 500