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


class RunValidationError(WorkbenchError):
    """One or more validation issues stopped a Run before it executed.

    A single issue is reported directly, in the shape build plan section 22
    documents. Several issues are reported together under ``details.issues``;
    the manifest always records the complete list either way.
    """

    code = "VALIDATION_FAILED"
    http_status = 422

    def __init__(self, issues: Iterable[ValidationIssue]) -> None:
        collected = tuple(issues)
        if not collected:
            raise ValueError("RunValidationError requires at least one issue.")
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
                "The uploaded data failed validation.",
                details={"issues": [issue.model_dump() for issue in collected]},
            )


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