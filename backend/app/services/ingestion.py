"""Monthly ingestion: uploaded file to committed Data Library version.

Build plan Phase 10. This is the layer between the parser, which turns bytes
into a DataFrame, and the Data Library, which stores versions:

    uploaded bytes
        -> app.services.parser          (unchanged, the Run pipeline's parser)
        -> DataFrame
        -> canonical schema check       (10A, app.models.source_schemas)
        -> reporting period detection   (10B, app.services.reporting_period)
        -> row validation               (10C.6, 10E)
        -> duplicate-source check       (10C.5)
        -> app.services.data_library.commit_version   (9C, unchanged)

Its job is stated in build plan Phase 10's purpose:

    The ingestion layer must protect the Data Library from malformed,
    duplicated, partial, or period-mismatched uploads.

Four rules shape it.

**Everything is checked before anything is written.** :func:`validate_source`
does the whole of the work that can refuse a file and writes nothing at all;
committing is what happens after it comes back clean. That is what makes the
coordinated import of build plan 10F honest: all three files are validated, and
only then is the first one committed, so "September sales were committed
successfully but the September ownership snapshot silently failed" cannot
happen by validation failure.

**The stored frame is the uploaded frame.** No column is renamed, added,
reordered, coerced or dropped, and the dates this module reads are used to
describe the version rather than to rewrite it. The Data Library holds the full
source snapshot (build plan 10E), and a report normalises on read.

**Nothing here changes Phase 9.** No model, no interface and no stored record
gained a field. Ingestion is built entirely on `commit_version` and the derived
queries the library already offers, which is what the Phase 9 entry predicted
it should need.

**Sales and samples never share anything but a shape.** Build plan 10D:
"Sales and samples must remain logically distinct datasets even if their source
schemas overlap." They have separate dataset IDs, separate schema declarations
and separate commit functions, and the coordinated import refuses the same file
supplied for both.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

import polars as pl

from app.errors import (
    EmptyUploadError,
    IngestionValidationError,
    InputValidationError,
    InvalidDatasetCommitError,
    UnknownDatasetError,
    UnknownDatasetVersionError,
)
from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    SAMPLE_HISTORY,
    DatasetCommit,
    DatasetKind,
    DatasetVersion,
    content_hash,
    known_dataset,
    parse_dataset_id,
    parse_period,
)
from app.models.schemas import ValidationIssue
from app.models.source_schemas import SOURCE_SCHEMAS, SourceSchema, schema_for
from app.services import data_library, parser, reporting_period, storage

#: How many offending rows an error message names before it stops counting.
MAX_REPORTED_ROWS = 10

#: The order a monthly cycle validates and commits its three inputs. Sales
#: first because it is the file a period is normally detected from.
CYCLE_DATASET_IDS: tuple[str, ...] = (
    SALES_HISTORY.id,
    SAMPLE_HISTORY.id,
    ACCOUNT_ASSIGNMENTS.id,
)


# ---------------------------------------------------------------------------
# What a caller hands in
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    """One uploaded file, held in memory.

    The same shape the Run pipeline uses and for the same reason (build plan
    6C.3): the bytes go from the request into memory and are parsed there.
    Nothing is written to the filesystem on the way in, and `filename` is
    metadata — it supplies the extension that chooses a parser and is recorded
    on the committed version, and it never becomes a path.

    It is deliberately **not** where a reporting period comes from. Build plan
    10B: "Do not rely solely on filenames such as `September Sales.csv`."
    """

    filename: str
    payload: bytes

    @property
    def extension(self) -> str:
        """The lowercase extension, read as text with any directory dropped."""
        return storage.extension_of(self.filename)

    @property
    def sha256(self) -> str:
        """The content hash the duplicate check compares (build plan 10C.4)."""
        return content_hash(self.payload)


# ---------------------------------------------------------------------------
# What validation produces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceValidation:
    """Everything known about one uploaded file, without having stored it.

    Carries the facts a commit needs and the issues that would stop one. It is
    the whole of build plan 10F's "validate all -> show issues" step: a caller
    can render this to a user and decide whether to commit, and committing
    re-uses exactly these facts rather than working them out again.
    """

    dataset_id: str
    schema: SourceSchema
    filename: str
    source_sha256: str
    source_byte_size: int

    #: The parsed upload, or None when it could not be parsed at all.
    parsed: parser.ParsedFile | None

    period: str | None
    periods: tuple[str, ...]
    min_date: date | None
    max_date: date | None

    #: A version of this dataset already committed from these exact bytes
    #: (build plan 10C.5), or None.
    duplicate_of: DatasetVersion | None

    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether this file can be committed."""
        return not self.errors

    @property
    def frame(self) -> pl.DataFrame | None:
        """The parsed rows, or None if parsing failed."""
        return self.parsed.frame if self.parsed is not None else None

    @property
    def row_count(self) -> int:
        return self.parsed.row_count if self.parsed is not None else 0

    @property
    def column_count(self) -> int:
        return self.parsed.column_count if self.parsed is not None else 0

    def raise_if_failed(self) -> None:
        """Raise :class:`IngestionValidationError` if anything would stop a commit.

        Nothing has been written when this raises: validation writes nothing.
        """
        if self.errors:
            raise IngestionValidationError(self.errors)


@dataclass(frozen=True)
class CommittedSource:
    """One version a monthly import actually stored."""

    dataset_id: str
    version_id: str
    period: str | None
    row_count: int


@dataclass(frozen=True)
class ReportingCycleImport:
    """The outcome of one coordinated monthly import (build plan 10F).

    Says exactly what was and was not persisted, which is the requirement 10F
    states in those words. Three states are distinguishable and none of them is
    silent:

    * ``ok`` — every input validated and every input was committed.
    * nothing committed — validation refused the cycle, and the Data Library is
      untouched. This is the normal failure and the only one validation can
      cause, because all three files are checked before the first is written.
    * ``partial`` — the library refused a write part-way through. Rare, and
      never hidden: `committed` names what did land and `errors` says what
      stopped. Committed history is **not** rolled back to tidy this up; build
      plan 15C is explicit that valid source data survives a later failure, and
      a version that exists can simply be superseded.
    """

    period: str | None
    validations: tuple[SourceValidation, ...]
    committed: tuple[CommittedSource, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the whole cycle validated and was committed."""
        return not self.errors and len(self.committed) == len(self.validations)

    @property
    def persisted(self) -> bool:
        """Whether anything at all reached the Data Library."""
        return bool(self.committed)

    @property
    def partial(self) -> bool:
        """Whether some inputs were committed and others were not."""
        return self.persisted and len(self.committed) != len(self.validations)

    def committed_dataset_ids(self) -> tuple[str, ...]:
        return tuple(entry.dataset_id for entry in self.committed)

    def uncommitted_dataset_ids(self) -> tuple[str, ...]:
        stored = set(self.committed_dataset_ids())
        return tuple(
            validation.dataset_id
            for validation in self.validations
            if validation.dataset_id not in stored
        )


# ---------------------------------------------------------------------------
# Validation (build plan 10A, 10B, 10C.1-10C.6, 10E)
# ---------------------------------------------------------------------------


def validate_source(
    dataset_id: str,
    file: SourceFile,
    *,
    expected_period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
    allow_multiple_periods: bool = False,
    replaces: str | None = None,
) -> SourceValidation:
    """Check one uploaded file against everything that could refuse it.

    Writes nothing. Reads the Data Library only to answer "have these exact
    bytes already been imported".

    Args:
        dataset_id: The Data Library dataset this file is for.
        file: The upload, in memory.
        expected_period: The month being imported, ``YYYY-MM``. Required for a
            snapshot dataset, whose rows carry no date; optional for a history
            dataset, where it turns "which month is this" into a check.
        date_format: An explicit date format, resolving an ambiguous column.
        today: What "future-dated" is measured against; defaults to today.
        allow_multiple_periods: True only for a historical bootstrap.
        replaces: The version ID this commit deliberately corrects, if any.
            Without it, a period that already holds a live version is an
            error here rather than at commit time — which is what keeps a
            three-file cycle from committing two inputs and failing on the
            third (build plan 10F).

    Returns:
        A :class:`SourceValidation` carrying every issue found, not just the
        first. A caller importing three files needs all of them at once.
    """
    schema = _schema_for(dataset_id)
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    if expected_period is not None:
        expected_period = parse_period(expected_period)

    parsed = _parse(file, schema, errors)
    if parsed is None:
        return SourceValidation(
            dataset_id=schema.dataset_id,
            schema=schema,
            filename=file.filename,
            source_sha256=file.sha256,
            source_byte_size=len(file.payload),
            parsed=None,
            period=None,
            periods=(),
            min_date=None,
            max_date=None,
            duplicate_of=None,
            errors=tuple(errors),
        )

    columns = parsed.columns
    errors.extend(_schema_errors(schema, columns))
    warnings.extend(_schema_warnings(schema, columns))

    if parsed.row_count == 0:
        errors.append(
            ValidationIssue(
                code="EMPTY_DATASET",
                message=(
                    f"{schema.label} contains no data rows. An import must "
                    "carry the month's data."
                ),
                details={"dataset_id": schema.dataset_id},
                slot_id=schema.dataset_id,
            )
        )

    detection = reporting_period.detect_reporting_period(
        parsed.frame,
        schema,
        expected_period=expected_period,
        date_format=date_format,
        today=today,
        allow_multiple_periods=allow_multiple_periods,
        slot_id=schema.dataset_id,
    )
    errors.extend(detection.errors)
    warnings.extend(detection.warnings)

    period = _resolved_period(schema, detection, expected_period, errors)

    row_errors, row_warnings = _row_issues(schema, parsed.frame)
    errors.extend(row_errors)
    warnings.extend(row_warnings)

    duplicate = _already_imported(schema.dataset_id, file.sha256, period)

    # Both of these are true of a re-uploaded file — the month is covered
    # *because* this very file covered it — and "you have already imported this
    # file" is the more specific and more useful of the two diagnoses. So the
    # period check only speaks when the duplicate check has not.
    if duplicate is None:
        errors.extend(
            _already_covered_errors(schema, period, replaces=replaces)
        )

    if duplicate is not None:
        errors.append(
            ValidationIssue(
                code="DUPLICATE_SOURCE_FILE",
                message=(
                    f"This exact file has already been imported into "
                    f"{schema.label}"
                    + (
                        f" as the {duplicate.period} version"
                        if duplicate.period
                        else ""
                    )
                    + f", on {duplicate.created_at.date().isoformat()} as "
                    f"{duplicate.source_filename!r}. Importing it again would "
                    "record the same month twice."
                ),
                details={
                    "dataset_id": schema.dataset_id,
                    "existing_version_id": duplicate.version_id,
                    "existing_period": duplicate.period,
                    "source_sha256": file.sha256,
                },
                slot_id=schema.dataset_id,
            )
        )

    return SourceValidation(
        dataset_id=schema.dataset_id,
        schema=schema,
        filename=file.filename,
        source_sha256=file.sha256,
        source_byte_size=len(file.payload),
        parsed=parsed,
        period=period,
        periods=detection.periods,
        min_date=detection.min_date,
        max_date=detection.max_date,
        duplicate_of=duplicate,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Monthly commits (build plan 10C, 10D, 10E)
# ---------------------------------------------------------------------------


def commit_monthly_sales(
    file: SourceFile,
    *,
    expected_period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
    replaces: str | None = None,
    reason: str | None = None,
) -> DatasetVersion:
    """Add one month of sales to Sales History (build plan 10C).

    The steps 10C lists, in order: parse with the existing ForgeXL parser,
    validate the schema, validate the reporting period, hash the source, refuse
    an already-imported file, validate the rows, then commit the month as a new
    version. The commit itself is atomic and immutable — that is the Data
    Library's guarantee and is unchanged from Phase 9.

    Correcting a month is deliberate, never accidental: pass `replaces` with
    the version ID being corrected and `reason` explaining why, and the old
    version stays readable so its report can still be reproduced (build plan
    9D).

    Raises:
        IngestionValidationError: the file cannot be imported. Nothing written.
    """
    return _commit_month(
        SALES_HISTORY.id,
        file,
        expected_period=expected_period,
        date_format=date_format,
        today=today,
        replaces=replaces,
        reason=reason,
    )


def commit_monthly_samples(
    file: SourceFile,
    *,
    expected_period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
    replaces: str | None = None,
    reason: str | None = None,
) -> DatasetVersion:
    """Add one month of samples to Sample History (build plan 10D).

    The same controlled process as :func:`commit_monthly_sales`, against a
    different dataset. It is a separate function rather than a `dataset_id`
    argument on one because build plan 10D requires the two to stay logically
    distinct: samples are not sales, however alike the two exports look, and
    the one-line way to fold them together should not exist.
    """
    return _commit_month(
        SAMPLE_HISTORY.id,
        file,
        expected_period=expected_period,
        date_format=date_format,
        today=today,
        replaces=replaces,
        reason=reason,
    )


def commit_account_assignments(
    file: SourceFile,
    *,
    period: str,
    replaces: str | None = None,
    reason: str | None = None,
) -> DatasetVersion:
    """Store the account-assignment list as one month's snapshot (build plan 10E).

    `period` is required and is not optional the way it is for a history
    dataset. An assignment export states ownership *as it stands* and carries
    no date, so the month it is effective for is something only the caller
    knows — and build plan 10B's answer to an unanswerable question is an
    explicit choice, not a guess.

    The whole file is stored, not a narrowed copy of it: build plan 10E
    requires the full source snapshot so the month can be reproduced later.

    Raises:
        IngestionValidationError: the file cannot be imported. Nothing written.
    """
    return _commit_month(
        ACCOUNT_ASSIGNMENTS.id,
        file,
        expected_period=parse_period(period),
        date_format=None,
        today=None,
        replaces=replaces,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Coordinated monthly import (build plan 10F)
# ---------------------------------------------------------------------------


def validate_reporting_cycle(
    *,
    sales: SourceFile,
    samples: SourceFile,
    assignments: SourceFile,
    period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
) -> ReportingCycleImport:
    """Validate all three monthly inputs together, writing nothing.

    The "validate all -> show issues" half of build plan 10F's diagram, exposed
    on its own because 10F draws it as its own step: a caller shows the result
    and only then decides to commit.

    Beyond the per-file checks, this is the only layer that can see more than
    one file at a time, so it makes the cross-file checks build plan 10B asks
    for:

    * the three inputs must agree about which month is being imported;
    * the same file must not have been supplied for two different datasets.

    Args:
        period: The month being imported. When omitted it is taken from the
            sales file's own dates, and the other two must agree with it.

    Returns:
        A :class:`ReportingCycleImport` with `committed` empty — nothing is
        stored by validation.
    """
    resolved = parse_period(period) if period is not None else None

    sales_check = validate_source(
        SALES_HISTORY.id,
        sales,
        expected_period=resolved,
        date_format=date_format,
        today=today,
    )

    # With no month stated, the sales file decides it and the other two are
    # checked against that. Sales is the authority because it is the file the
    # reporting month is really about.
    cycle_period = resolved or sales_check.period

    samples_check = validate_source(
        SAMPLE_HISTORY.id,
        samples,
        expected_period=cycle_period,
        date_format=date_format,
        today=today,
    )
    assignments_check = validate_source(
        ACCOUNT_ASSIGNMENTS.id,
        assignments,
        expected_period=cycle_period,
        today=today,
    )

    validations = (sales_check, samples_check, assignments_check)

    # Every issue from every file, plus the cross-file ones, in one list. A
    # caller showing this to a user needs the whole picture at once, and
    # `validations` still carries each file's own issues for a per-file view.
    errors = [
        issue for validation in validations for issue in validation.errors
    ]
    errors.extend(
        _cycle_errors(validations, cycle_period=cycle_period, stated=resolved)
    )

    return ReportingCycleImport(
        period=cycle_period,
        validations=validations,
        committed=(),
        errors=tuple(errors),
        warnings=tuple(
            warning
            for validation in validations
            for warning in validation.warnings
        ),
    )


def import_reporting_cycle(
    *,
    sales: SourceFile,
    samples: SourceFile,
    assignments: SourceFile,
    period: str | None = None,
    date_format: str | None = None,
    today: date | None = None,
) -> ReportingCycleImport:
    """Validate all three monthly inputs and, if all pass, commit all three.

    Build plan 10F's transaction-like monthly import:

        Sales upload + Samples upload + Account assignment upload
            -> validate all -> show issues -> commit reporting cycle

    **Validation failure commits nothing.** Every check across all three files
    runs first, so the misleading state 10F names — September sales committed
    while the September ownership snapshot silently failed — cannot be reached
    by a file being wrong.

    This does not raise when the cycle is refused. The result *is* the
    explanation build plan 10F requires the user to receive, and a caller that
    wants an exception can call `raise_if_failed` on any validation. Check
    :attr:`ReportingCycleImport.ok` before treating the month as imported.
    """
    outcome = validate_reporting_cycle(
        sales=sales,
        samples=samples,
        assignments=assignments,
        period=period,
        date_format=date_format,
        today=today,
    )
    if outcome.errors:
        # Nothing has been written and nothing will be: validation runs to
        # completion across all three files before the first commit, so a
        # refusal always leaves the Data Library exactly as it was.
        return outcome

    committed: list[CommittedSource] = []
    failures: list[ValidationIssue] = []

    for validation in outcome.validations:
        try:
            version = _commit_validated(validation)
        except (InvalidDatasetCommitError, UnknownDatasetError) as error:
            # A refusal from the library itself, after validation passed. Say
            # exactly what did and did not land rather than unwinding what did:
            # committed history is real and build plan 15C requires it to
            # survive a later failure.
            failures.append(
                ValidationIssue(
                    code=error.code,
                    message=(
                        f"{validation.schema.label} could not be committed: "
                        f"{error.message}"
                    ),
                    details=dict(error.details),
                    slot_id=validation.dataset_id,
                )
            )
            break
        committed.append(
            CommittedSource(
                dataset_id=validation.dataset_id,
                version_id=version.version_id,
                period=version.period,
                row_count=version.row_count,
            )
        )

    return ReportingCycleImport(
        period=outcome.period,
        validations=outcome.validations,
        committed=tuple(committed),
        errors=tuple(failures),
        warnings=outcome.warnings,
    )


# ---------------------------------------------------------------------------
# Historical bootstrap (build plan 10G)
# ---------------------------------------------------------------------------


def bootstrap_history(
    dataset_id: str,
    file: SourceFile,
    *,
    date_format: str | None = None,
    today: date | None = None,
) -> tuple[DatasetVersion, ...]:
    """Load a multi-month historical file as one version per month.

    Build plan 10G's deliberate one-time path. It differs from the recurring
    monthly import in exactly one way — it accepts a file spanning several
    months — and the difference is confined to that: every other check applies
    unchanged.

    **It commits one version per month, not one version for the file.** The
    whole model of the Data Library is a version per reporting period: that is
    what lets `current_version(dataset_id, period)` answer a question about
    September, what lets a single wrong month be corrected on its own later,
    and what lets the next monthly import add October without colliding with
    anything. One multi-month blob would have none of those properties.

    It refuses a dataset that already holds versions. "One-time" is the whole
    idea (10G), and after it the ordinary workflow adds one month at a time:
    "Do not require the user to re-upload the complete historical dataset every
    month." Refusing also means the same bootstrap file run twice is stopped by
    the precondition rather than by a hash comparison, which could not work
    here — every month this produces comes from the same file and so carries
    the same source hash by construction.

    Returns:
        The committed versions, oldest month first.

    Raises:
        IngestionValidationError: the file cannot be imported. Nothing written.
        InvalidDatasetCommitError: the dataset is a snapshot, or already holds
            data. Nothing written.
    """
    schema = _schema_for(dataset_id)
    definition = known_dataset(schema.dataset_id)
    assert definition is not None  # a source schema names a declared dataset

    if definition.kind is not DatasetKind.HISTORY:
        raise InvalidDatasetCommitError(
            f"{definition.label} is a snapshot dataset, so it has no history "
            "to bootstrap. Each month's snapshot is committed for the month it "
            "is effective for.",
            details={
                "dataset_id": definition.id,
                "dataset_kind": definition.kind.value,
            },
        )

    data_library.ensure_dataset(definition)
    existing = data_library.list_versions(definition.id)
    if existing:
        raise InvalidDatasetCommitError(
            f"{definition.label} already holds {len(existing)} committed "
            "version(s), and the historical bootstrap is a one-time load into "
            "an empty dataset. Import a single month instead.",
            details={
                "dataset_id": definition.id,
                "existing_version_count": len(existing),
            },
        )

    validation = validate_source(
        definition.id,
        file,
        date_format=date_format,
        today=today,
        allow_multiple_periods=True,
    )
    validation.raise_if_failed()

    frame = validation.frame
    assert frame is not None and validation.parsed is not None

    # One reading of the date column, reused for every partition. Detection has
    # already established that it reads cleanly and which months it holds;
    # reading it again per month would be a second definition of which month a
    # row belongs to, and two definitions are how a row gets counted in one
    # month and stored in another.
    dates, _, _ = reporting_period.read_dates(
        frame, validation.schema, validation.schema.period_column or "",
        date_format=date_format,
    )
    months = dates.dt.strftime("%Y-%m")

    committed: list[DatasetVersion] = []
    for month in validation.periods:
        selected = months == month
        partition = frame.filter(selected)
        in_month = dates.filter(selected).drop_nulls()
        committed.append(
            data_library.commit_version(
                definition.id,
                DatasetCommit.from_upload(
                    partition,
                    filename=file.filename,
                    payload=file.payload,
                    period=month,
                    parser_engine=validation.parsed.parser_engine,
                    worksheet=validation.parsed.worksheet,
                    min_date=in_month.min(),
                    max_date=in_month.max(),
                ),
            )
        )
    return tuple(committed)


# ---------------------------------------------------------------------------
# Committing
# ---------------------------------------------------------------------------


def _commit_month(
    dataset_id: str,
    file: SourceFile,
    *,
    expected_period: str | None,
    date_format: str | None,
    today: date | None,
    replaces: str | None,
    reason: str | None,
) -> DatasetVersion:
    """Validate one file and commit it as one month, or raise having written nothing."""
    validation = validate_source(
        dataset_id,
        file,
        expected_period=expected_period,
        date_format=date_format,
        today=today,
        replaces=replaces,
    )
    validation.raise_if_failed()
    return _commit_validated(validation, replaces=replaces, reason=reason)


def _commit_validated(
    validation: SourceValidation,
    *,
    replaces: str | None = None,
    reason: str | None = None,
) -> DatasetVersion:
    """Commit a file that has already passed :func:`validate_source`.

    Separate from validation so the coordinated import can validate all three
    inputs before committing any of them (build plan 10F), and so a commit
    never re-derives a fact validation already established.
    """
    definition = known_dataset(validation.dataset_id)
    assert definition is not None
    frame = validation.frame
    assert frame is not None and validation.parsed is not None

    data_library.ensure_dataset(definition)

    return data_library.commit_version(
        definition.id,
        DatasetCommit(
            frame=frame,
            source_filename=validation.filename,
            source_byte_size=validation.source_byte_size,
            source_sha256=validation.source_sha256,
            period=validation.period,
            parser_engine=validation.parsed.parser_engine,
            worksheet=validation.parsed.worksheet,
            min_date=validation.min_date,
            max_date=validation.max_date,
            supersedes=replaces,
            supersession_reason=reason,
        ),
    )


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _parse(
    file: SourceFile, schema: SourceSchema, errors: list[ValidationIssue]
) -> parser.ParsedFile | None:
    """Parse the upload with the Run pipeline's own parser, or record why not.

    The same parser, deliberately. An ingested file and an uploaded file are
    read by one implementation, so the extension rules, the worksheet-ambiguity
    refusal and the duplicate-column refusal apply identically to both and
    cannot drift apart.
    """
    if not file.payload:
        errors.append(
            EmptyUploadError(
                f"{storage.display_filename(file.filename)} is empty.",
                details={"dataset_id": schema.dataset_id},
            ).as_validation_issue(schema.dataset_id)
        )
        return None

    try:
        return parser.parse_tabular_bytes(file.payload, file.extension)
    except InputValidationError as error:
        errors.append(error.as_validation_issue(schema.dataset_id))
        return None


def _schema_errors(
    schema: SourceSchema, columns: tuple[str, ...]
) -> list[ValidationIssue]:
    """Required columns the file does not have (build plan 10A)."""
    missing = schema.missing_from(columns)
    if not missing:
        return []
    return [
        ValidationIssue(
            code="SOURCE_SCHEMA_MISMATCH",
            message=(
                f"{schema.label} is missing required columns: "
                f"{_human_list(missing)}. Column names are matched exactly, so "
                "a differently spelled column is reported rather than assumed "
                f"to be the same one. The file has: {_human_list(columns)}."
            ),
            details={
                "dataset_id": schema.dataset_id,
                "missing_columns": list(missing),
                "expected_columns": list(schema.column_names),
                "found_columns": list(columns),
                "schema_confirmed": schema.confirmed,
            },
            slot_id=schema.dataset_id,
        )
    ]


def _schema_warnings(
    schema: SourceSchema, columns: tuple[str, ...]
) -> list[ValidationIssue]:
    """Columns the file carries that the schema does not declare.

    A warning, not a refusal, and the reasoning is in
    `docs/monthly-source-schemas.md`: an added column is a source-schema change
    worth surfacing (build plan 13H) and is safe to carry, while refusing it
    would block a month over a column nothing reads. The columns are stored
    with the rest of the snapshot.
    """
    unexpected = schema.unexpected_in(columns)
    if not unexpected:
        return []
    return [
        ValidationIssue(
            code="UNEXPECTED_SOURCE_COLUMNS",
            message=(
                f"{schema.label} carries {len(unexpected)} column(s) the "
                f"expected schema does not declare: {_human_list(unexpected)}. "
                "They are stored with the rest of the file and are not used. "
                "If the export has changed, the canonical schema should be "
                "updated to match."
            ),
            details={
                "dataset_id": schema.dataset_id,
                "unexpected_columns": list(unexpected),
                "expected_columns": list(schema.column_names),
                "schema_confirmed": schema.confirmed,
            },
            slot_id=schema.dataset_id,
        )
    ]


def _row_issues(
    schema: SourceSchema, frame: pl.DataFrame
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """Validate the rows themselves (build plan 10C.6 and 10E).

    Returns `(errors, warnings)`. The split is a judgement about safety, made
    the way build plan 13H frames it — fail where a condition would make a
    report unreliable, warn only where continuing is genuinely safe:

    * **Ownership defects in a snapshot are errors.** An account owned by two
      reps, an account with no name, an account with no owner: each leaves the
      one question the snapshot exists to answer without an answer.
    * **Everything else is a warning.** A blank rep on an invoice line is not
      a defect — ownership for a report comes from the snapshot — and a price
      column that arrived as text is worth reporting without blocking a month.
    """
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    warnings.extend(_number_column_warnings(schema, frame))

    definition = known_dataset(schema.dataset_id)
    if definition is not None and definition.kind is DatasetKind.SNAPSHOT:
        errors.extend(_ownership_errors(schema, frame))
    else:
        warnings.extend(_blank_identity_warnings(schema, frame))

    return errors, warnings


def _number_column_warnings(
    schema: SourceSchema, frame: pl.DataFrame
) -> list[ValidationIssue]:
    """Report a column declared numeric that did not arrive as a number.

    Usually means the export wrote the value with a currency symbol, a thousands
    separator or parentheses for a negative. Reported rather than repaired: a
    parser that turns ``(45.00)`` into ``-45`` has decided what the file meant
    (build plan section 3.3).
    """
    issues: list[ValidationIssue] = []
    for name in schema.number_columns:
        if name not in frame.columns:
            continue  # the missing-column error already names it
        dtype = frame.schema[name]
        if dtype.is_numeric():
            continue
        issues.append(
            ValidationIssue(
                code="NON_NUMERIC_SOURCE_COLUMN",
                message=(
                    f"The {name} column of {schema.label} arrived as "
                    f"{dtype} rather than as numbers. The values are stored "
                    "exactly as uploaded and nothing has been converted, but a "
                    "report cannot total a column of text."
                ),
                details={
                    "dataset_id": schema.dataset_id,
                    "column": name,
                    "dtype": str(dtype),
                    "examples": _examples(frame, name),
                },
                slot_id=schema.dataset_id,
            )
        )
    return issues


def _ownership_errors(
    schema: SourceSchema, frame: pl.DataFrame
) -> list[ValidationIssue]:
    """The account-ownership checks build plan 10E lists.

    A snapshot's whole purpose is to say who owned each account in one month
    (build plan 9E). Each condition below breaks that:

    * a blank account name — the row identifies nothing;
    * a blank rep — the row answers nothing;
    * one account naming two different reps — the row's answer is contradicted
      by another row's, and nothing can choose between them.

    An account repeated with the *same* rep is not any of those. It says the
    same true thing twice, so it is left to :func:`_ownership_warnings`.
    """
    issues: list[ValidationIssue] = []
    customer = schema.customer_column
    rep = schema.rep_column
    if customer is None or rep is None:
        return issues
    if customer not in frame.columns or rep not in frame.columns:
        return issues  # the missing-column error already names them

    issues.extend(_blank_column_error(schema, frame, customer, "account"))
    issues.extend(_blank_column_error(schema, frame, rep, "owning sales rep"))

    conflicts = (
        frame.select(customer, rep)
        .drop_nulls()
        .unique()
        .group_by(customer)
        .agg(pl.col(rep).sort().alias("reps"), pl.len().alias("rep_count"))
        .filter(pl.col("rep_count") > 1)
        .sort(customer)
    )
    if conflicts.height:
        listed = conflicts.head(MAX_REPORTED_ROWS)
        described = "; ".join(
            f"{row[0]!r} is assigned to {_human_list(tuple(str(v) for v in row[1]))}"
            for row in listed.select(customer, "reps").iter_rows()
        )
        issues.append(
            ValidationIssue(
                code="AMBIGUOUS_ACCOUNT_OWNERSHIP",
                message=(
                    f"{conflicts.height} account(s) in {schema.label} are "
                    f"assigned to more than one sales rep: {described}"
                    + ("; ..." if conflicts.height > listed.height else "")
                    + ". A snapshot records who owned each account for the "
                    "month, so an account cannot have two owners."
                ),
                details={
                    "dataset_id": schema.dataset_id,
                    "customer_column": customer,
                    "rep_column": rep,
                    "conflicting_account_count": conflicts.height,
                    "conflicts": [
                        {
                            "account": str(row[0]),
                            "reps": [str(value) for value in row[1]],
                        }
                        for row in listed.select(customer, "reps").iter_rows()
                    ],
                },
                slot_id=schema.dataset_id,
            )
        )

    return issues


def _blank_column_error(
    schema: SourceSchema, frame: pl.DataFrame, column: str, what: str
) -> list[ValidationIssue]:
    """Refuse a snapshot whose `column` is blank on any row.

    Blank means null or text that is empty once surrounding whitespace is
    disregarded. Nothing is trimmed in the stored data — this only decides
    whether a cell says anything, and a cell holding one space says nothing.
    """
    rows = _blank_row_numbers(frame, column)
    if not rows:
        return []
    return [
        ValidationIssue(
            code="MISSING_ACCOUNT_ASSIGNMENT_FIELD",
            message=(
                f"{len(rows)} row(s) in {schema.label} have no {column}, so "
                f"the {what} is unknown: {_row_list(rows)}. Every row of an "
                "ownership snapshot must name both the account and its rep."
            ),
            details={
                "dataset_id": schema.dataset_id,
                "column": column,
                "blank_row_count": len(rows),
                "rows": rows[:MAX_REPORTED_ROWS],
            },
            slot_id=schema.dataset_id,
        )
    ]


def _blank_identity_warnings(
    schema: SourceSchema, frame: pl.DataFrame
) -> list[ValidationIssue]:
    """Report blank account or rep values on transaction rows, without refusing.

    A transaction with no rep is normal — ownership comes from the month's
    snapshot, not from the invoice — and a transaction with no account is worth
    knowing about but does not make the month unusable. Both are recorded so
    build plan 13H's report-time validation has something to build on.
    """
    issues: list[ValidationIssue] = []
    for column in (schema.customer_column, schema.rep_column):
        if column is None or column not in frame.columns:
            continue
        rows = _blank_row_numbers(frame, column)
        if not rows:
            continue
        issues.append(
            ValidationIssue(
                code="BLANK_SOURCE_IDENTIFIER",
                message=(
                    f"{len(rows)} of {frame.height} rows in {schema.label} "
                    f"have no {column}: {_row_list(rows)}. The rows are stored "
                    "as uploaded."
                ),
                details={
                    "dataset_id": schema.dataset_id,
                    "column": column,
                    "blank_row_count": len(rows),
                    "row_count": frame.height,
                    "rows": rows[:MAX_REPORTED_ROWS],
                },
                slot_id=schema.dataset_id,
            )
        )
    return issues


def _cycle_errors(
    validations: Sequence[SourceValidation],
    *,
    cycle_period: str | None,
    stated: str | None,
) -> list[ValidationIssue]:
    """The checks only a whole cycle can make (build plan 10B, 10F)."""
    issues: list[ValidationIssue] = []

    # "Mismatched periods between related files." Each file was validated
    # against `cycle_period` already, so this reports the disagreement as one
    # fact about the cycle rather than leaving three separate per-file errors
    # to be pieced together.
    months = {
        validation.dataset_id: validation.period
        for validation in validations
        if validation.parsed is not None
    }
    distinct = {period for period in months.values() if period is not None}
    if len(distinct) > 1:
        issues.append(
            ValidationIssue(
                code="MISMATCHED_REPORTING_PERIODS",
                message=(
                    "The three monthly files are not for the same reporting "
                    "period: "
                    + ", ".join(
                        f"{dataset_id} {period or 'no single month'}"
                        for dataset_id, period in sorted(months.items())
                    )
                    + ". A reporting cycle imports one month."
                ),
                details={
                    "periods_by_dataset": {
                        key: value for key, value in sorted(months.items())
                    },
                    "stated_period": stated,
                },
            )
        )

    if cycle_period is None and all(
        validation.parsed is not None for validation in validations
    ):
        issues.append(
            ValidationIssue(
                code="UNDETERMINED_REPORTING_PERIOD",
                message=(
                    "The reporting period could not be established from the "
                    "uploaded files. State the month to import, as YYYY-MM."
                ),
                details={"periods_by_dataset": dict(sorted(months.items()))},
            )
        )

    # The same bytes in two slots. Sales and samples are different datasets
    # (build plan 10D) and a file supplied for both is a mix-up that every
    # per-file check would pass.
    by_hash: dict[str, list[str]] = {}
    for validation in validations:
        by_hash.setdefault(validation.source_sha256, []).append(
            validation.dataset_id
        )
    for digest, dataset_ids in by_hash.items():
        if len(dataset_ids) > 1:
            issues.append(
                ValidationIssue(
                    code="SAME_FILE_FOR_SEVERAL_DATASETS",
                    message=(
                        "The same file was supplied for "
                        f"{_human_list(tuple(sorted(dataset_ids)))}. These are "
                        "different datasets and need their own exports."
                    ),
                    details={
                        "dataset_ids": sorted(dataset_ids),
                        "source_sha256": digest,
                    },
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_for(dataset_id: str) -> SourceSchema:
    """Return the canonical schema for `dataset_id`, or refuse the ID.

    The ID is validated for its shape first, exactly as the Data Library does,
    so nothing client-supplied reaches a lookup or a path.
    """
    schema = schema_for(parse_dataset_id(dataset_id))
    if schema is None:
        raise UnknownDatasetError(
            "That dataset has no monthly source schema, so nothing can be "
            "imported into it.",
            details={
                "dataset_id": dataset_id,
                "importable_dataset_ids": [
                    known.dataset_id for known in SOURCE_SCHEMAS
                ],
            },
        )
    return schema


def _already_covered_errors(
    schema: SourceSchema, period: str | None, *, replaces: str | None
) -> list[ValidationIssue]:
    """Refuse a month the dataset already holds, unless it is being corrected.

    The Data Library enforces this too (build plan 9D), and would refuse the
    commit. Checking it *here* is what makes the coordinated import safe: a
    cycle validates all three inputs and then commits them in order, so a
    refusal discovered at commit time could leave the first one or two stored
    and the third not — exactly the partial state build plan 10F exists to
    prevent. Discovering it during validation means nothing is committed at
    all.

    A commit that names the version it replaces is the deliberate correction
    build plan 9D describes, and is not this.
    """
    if period is None or replaces is not None:
        return []
    try:
        existing = data_library.DATA_LIBRARY.current_version(
            schema.dataset_id, period
        )
    except (UnknownDatasetError, UnknownDatasetVersionError):
        return []
    return [
        ValidationIssue(
            code="PERIOD_ALREADY_COMMITTED",
            message=(
                f"{schema.label} already holds data for {period}, committed on "
                f"{existing.created_at.date().isoformat()} from "
                f"{existing.source_filename!r}. Replacing a committed month is "
                "deliberate: commit the new version as a replacement for "
                f"{existing.version_id}, with a reason, so the old version is "
                "kept and the change is explained."
            ),
            details={
                "dataset_id": schema.dataset_id,
                "period": period,
                "existing_version_id": existing.version_id,
                "existing_row_count": existing.row_count,
            },
            slot_id=schema.dataset_id,
        )
    ]


def _already_imported(
    dataset_id: str, digest: str, period: str | None
) -> DatasetVersion | None:
    """The version already committed from these exact bytes for this month (10C.5).

    Matched on the content hash **and the reporting period**, across every
    version the dataset holds — superseded ones included, because a file that
    was imported and then corrected has still been imported, and re-importing
    the original would reinstate the data the correction replaced.

    The period is part of the match because the two kinds of dataset differ in
    what identical bytes mean:

    * For a **history** dataset the bytes decide the month, so identical bytes
      are always the same month and the period adds nothing — a re-upload is
      caught either way.
    * For a **snapshot** the month is supplied by the caller, and identical
      bytes for a *different* month are entirely normal: account ownership
      often does not change from one month to the next, and the export for
      October is then byte-for-byte September's. Refusing that would force the
      user to perturb a correct file, so only the same snapshot for the same
      month is a duplicate.
    """
    try:
        versions = data_library.list_versions(dataset_id)
    except UnknownDatasetError:
        return None  # nothing has ever been committed here
    for version in versions:
        if version.source_sha256 == digest and version.period == period:
            return version
    return None


def _resolved_period(
    schema: SourceSchema,
    detection: reporting_period.PeriodDetection,
    expected_period: str | None,
    errors: list[ValidationIssue],
) -> str | None:
    """The month this file will be committed for.

    For a snapshot the caller states it and it is required — a snapshot without
    one could not be selected for a report (build plan 9E). For a history file
    it is read from the rows, with a stated month acting as a check rather than
    as the answer.
    """
    definition = known_dataset(schema.dataset_id)
    if definition is not None and definition.kind is DatasetKind.SNAPSHOT:
        if expected_period is None:
            errors.append(
                ValidationIssue(
                    code="REPORTING_PERIOD_REQUIRED",
                    message=(
                        f"{schema.label} records ownership as of one reporting "
                        "month and carries no date of its own, so the month it "
                        "applies to must be stated, as YYYY-MM."
                    ),
                    details={"dataset_id": schema.dataset_id},
                    slot_id=schema.dataset_id,
                )
            )
        return expected_period
    return detection.period


def _blank_row_numbers(frame: pl.DataFrame, column: str) -> list[int]:
    """1-based spreadsheet row numbers where `column` says nothing.

    The header is row 1, so the first data row is row 2 — the number the user
    sees in Excel when they go looking for it.
    """
    series = frame.get_column(column)
    if series.dtype == pl.String:
        blank = series.is_null() | (series.str.strip_chars() == "")
    else:
        blank = series.is_null()
    return [index + 2 for index in blank.arg_true().to_list()]


def _row_list(rows: Sequence[int]) -> str:
    shown = ", ".join(str(row) for row in rows[:MAX_REPORTED_ROWS])
    if len(rows) > MAX_REPORTED_ROWS:
        return f"rows {shown} and {len(rows) - MAX_REPORTED_ROWS} more"
    return f"row{'' if len(rows) == 1 else 's'} {shown}"


def _examples(frame: pl.DataFrame, column: str) -> list[str]:
    values = frame.get_column(column).drop_nulls().head(5).to_list()
    return [str(value) for value in values]


def _human_list(values: Iterable[str]) -> str:
    """Join `values` for a user-facing message: 'a, b and c'."""
    listed = tuple(values)
    if len(listed) <= 1:
        return "".join(listed)
    return f"{', '.join(listed[:-1])} and {listed[-1]}"
