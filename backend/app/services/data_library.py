"""The persistent Data Library (build plan Phase 9A, 9C-9E).

Where ForgeXL keeps business data that outlives a Run. The build plan draws the
line this module sits on:

    RunStore     -> temporary execution/runtime state
    Data Library -> persistent business datasets used across Runs

They are separate on purpose and stay separate. :mod:`app.services.run_store`
is deliberately ephemeral — restarting the backend clears run history, and that
is authorised behaviour. Sales history is the opposite: losing it on a restart
would be data loss. Nothing here touches a Run, and nothing in the Run pipeline
touches this. A Run still writes nothing to disk.

:class:`DataLibrary` is the narrow interface; :class:`LocalDataLibrary` is the
implementation, and it is Parquet files plus small JSON records on the local
filesystem (build plan 9C). No database is introduced, because none is needed
to store a few dozen monthly tables and read them back.

Three properties the implementation is built around:

* **Versions are immutable** (build plan 9D). Nothing here rewrites a committed
  version. Correcting a month means committing a *new* version that names the
  old one in ``supersedes``, so the wrong figures a report was once built from
  remain readable and the report remains reproducible.
* **A commit is all-or-nothing** (build plan 9C, 9F). A version is assembled in
  a staging directory and moved into place with a single rename, so a version
  directory either does not exist or is complete. A commit that is refused, or
  that fails part-way, leaves the library exactly as it was.
* **The layout is the catalogue.** A dataset is a directory holding
  ``dataset.json``; a version is a directory holding ``version.json`` and
  ``data.parquet``. There is no separate index file, and therefore no index
  that can disagree with the data it indexes.

Physical layout::

    <library root>/
        sales_history/
            dataset.json
            versions/
                <version id>/
                    version.json
                    data.parquet
        sample_history/
            ...
        account_assignments/
            ...
        .staging/                 transient; a commit in progress

**No path from this module is ever exposed through the API** (build plan 9C).
Callers name a dataset and a version by their logical IDs; where those live is
this module's business and nobody else's.
"""

from __future__ import annotations

import abc
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import polars as pl
from pydantic import ValidationError

from app import config
from app.errors import (
    DataLibraryError,
    InvalidDatasetCommitError,
    UnknownDatasetError,
    UnknownDatasetVersionError,
)
from app.models.library import (
    KNOWN_DATASETS,
    LIBRARY_SCHEMA_VERSION,
    Dataset,
    DatasetCommit,
    DatasetDefinition,
    DatasetKind,
    DatasetVersion,
    new_version_id,
    now,
    parse_dataset_id,
    parse_period,
    parse_version_id,
)
from app.services import results

#: Filenames inside the library. Fixed constants, never derived from user data.
DATASET_RECORD_FILENAME = "dataset.json"
VERSION_RECORD_FILENAME = "version.json"
VERSION_DATA_FILENAME = "data.parquet"
VERSIONS_DIRECTORY_NAME = "versions"

#: Where a commit is assembled before it is moved into place. Leading dot so it
#: is never mistaken for a dataset directory.
STAGING_DIRECTORY_NAME = ".staging"


class DuplicateDatasetError(ValueError):
    """Raised when a dataset is created twice.

    Creating over an existing dataset would silently discard its record while
    leaving its versions behind, so creation fails loudly instead — the same
    rule the Action registry and the Run Store apply to a repeated ID.
    """


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


class DataLibrary(abc.ABC):
    """The persistent-dataset interface every implementation satisfies.

    Seven abstract operations, which are exactly the seven build plan 9A lists.
    Everything else on this class is derived from them and is implemented once,
    here, so no implementation can answer a derived question differently from
    the facts it stores.

    There is deliberately no ``delete_version`` and no ``update_version``.
    Historical versions are immutable (build plan 9D), and an interface that
    cannot rewrite one cannot rewrite one by accident.
    """

    # -- datasets ----------------------------------------------------------

    @abc.abstractmethod
    def create_dataset(self, definition: DatasetDefinition) -> Dataset:
        """Record a new dataset and return it.

        Raises:
            DuplicateDatasetError: the dataset already exists.
        """

    @abc.abstractmethod
    def get_dataset(self, dataset_id: str) -> Dataset:
        """Return the dataset's metadata.

        Raises:
            UnknownDatasetError: the ID is malformed, or no such dataset exists.
            DataLibraryError: the record exists and could not be read.
        """

    @abc.abstractmethod
    def list_datasets(self) -> list[Dataset]:
        """Return every recorded dataset, ordered by ID."""

    # -- versions ----------------------------------------------------------

    @abc.abstractmethod
    def commit_version(
        self, dataset_id: str, commit: DatasetCommit
    ) -> DatasetVersion:
        """Persist `commit` as a new immutable version and return its record.

        Raises:
            UnknownDatasetError: no such dataset.
            InvalidDatasetCommitError: the commit cannot be stored honestly.
            DataLibraryError: the write failed.
        """

    @abc.abstractmethod
    def get_version(self, dataset_id: str, version_id: str) -> DatasetVersion:
        """Return one version's metadata.

        Raises:
            UnknownDatasetError: no such dataset.
            UnknownDatasetVersionError: the ID is malformed, or no such version.
            DataLibraryError: the record exists and could not be read.
        """

    @abc.abstractmethod
    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        """Return every version of a dataset, oldest commit first."""

    @abc.abstractmethod
    def load_version(self, dataset_id: str, version_id: str) -> pl.DataFrame:
        """Return one version's rows as a DataFrame.

        The Action Engine stays DataFrame-first: a stored dataset reaches an
        Action the same way an uploaded one does (build plan Phase 11).
        """

    # -- derived -----------------------------------------------------------

    def has_dataset(self, dataset_id: str) -> bool:
        """Whether `dataset_id` is recorded. A malformed ID is simply False."""
        try:
            self.get_dataset(dataset_id)
        except UnknownDatasetError:
            return False
        return True

    def ensure_dataset(self, definition: DatasetDefinition) -> Dataset:
        """Return the recorded dataset for `definition`, creating it if absent.

        Idempotent, so ingestion can call it on every import without caring
        whether this is the first one.

        An existing record is returned as it stands rather than being rewritten
        from the definition: it is a record of what happened, and its
        ``created_at`` is a fact. A stored `kind` that disagrees with the
        declaration is a different matter and is refused, because the kind is
        what decides how the dataset's versions are read (build plan 9E).

        Raises:
            DataLibraryError: the stored dataset has a different kind.
        """
        try:
            existing = self.get_dataset(definition.id)
        except UnknownDatasetError:
            return self.create_dataset(definition)

        if existing.kind is not definition.kind:
            raise DataLibraryError(
                f"The stored {definition.id!r} dataset is a "
                f"{existing.kind.value} dataset, but ForgeXL now declares it "
                f"as a {definition.kind.value} dataset. Its versions were "
                "committed under the stored meaning and are not reinterpreted "
                "automatically.",
                details={
                    "dataset_id": definition.id,
                    "stored_kind": existing.kind.value,
                    "declared_kind": definition.kind.value,
                },
            )
        return existing

    def superseded_version_ids(self, dataset_id: str) -> frozenset[str]:
        """Version IDs that a later version has replaced (build plan 9D).

        Derived from the ``supersedes`` pointers the newer versions carry,
        never written back into the record it describes: a superseded version
        is still immutable, and a fact stored twice is a fact that can end up
        disagreeing with itself.
        """
        return frozenset(
            version.supersedes
            for version in self.list_versions(dataset_id)
            if version.supersedes is not None
        )

    def current_versions(self, dataset_id: str) -> list[DatasetVersion]:
        """Every version nothing has superseded, oldest commit first.

        This is the dataset as it stands: the versions a report reads. A
        superseded version is still stored and still loadable by ID — it is
        simply no longer the truth about its period.
        """
        superseded = self.superseded_version_ids(dataset_id)
        return [
            version
            for version in self.list_versions(dataset_id)
            if version.version_id not in superseded
        ]

    def versions_for_period(
        self, dataset_id: str, period: str
    ) -> list[DatasetVersion]:
        """Every version committed for one reporting period, oldest first.

        Includes superseded ones. That is the point: build plan 9D requires a
        replaced month to remain explainable, and the explanation is the whole
        chain.
        """
        wanted = parse_period(period)
        return [
            version
            for version in self.list_versions(dataset_id)
            if version.period == wanted
        ]

    def current_version(self, dataset_id: str, period: str) -> DatasetVersion:
        """The one live version for a reporting period (build plan 9E).

        This is what makes an account-ownership snapshot answer the question a
        report actually asks — *who owned this account in September* — rather
        than the question a single mutable file would answer, which is who owns
        it now.

        Exactly one live version per period is an invariant, not a hope:
        :meth:`commit_version` refuses a second version for a period unless it
        explicitly supersedes the one already there.

        Raises:
            UnknownDatasetVersionError: no live version covers that period.
        """
        wanted = parse_period(period)
        live = [
            version
            for version in self.current_versions(dataset_id)
            if version.period == wanted
        ]
        if not live:
            raise UnknownDatasetVersionError(
                f"No {dataset_id} data has been committed for {wanted}.",
                details={"dataset_id": dataset_id, "period": wanted},
            )
        # The commit rule above makes more than one impossible; if persistent
        # state has been edited by hand it is a fault to report, not to pick
        # a winner from.
        if len(live) > 1:
            raise DataLibraryError(
                f"{dataset_id} has {len(live)} live versions for {wanted}. "
                "Exactly one is expected; the library's stored state has been "
                "modified outside ForgeXL.",
                details={
                    "dataset_id": dataset_id,
                    "period": wanted,
                    "version_ids": [version.version_id for version in live],
                },
            )
        return live[0]


# ---------------------------------------------------------------------------
# The local implementation (build plan 9C)
# ---------------------------------------------------------------------------


class LocalDataLibrary(DataLibrary):
    """Parquet plus JSON records under one local directory.

    The root is resolved once, at construction, so the library a caller reaches
    never depends on the process working directory — which tests and scripts do
    change.

    Nothing is created until something is written. Constructing this class,
    listing datasets or asking whether one exists all leave an absent library
    absent; the directory appears when the first dataset is created.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()
        # Every write here is a check-then-write against state on disk: "does
        # this dataset already exist", "does this period already have a live
        # version". Uvicorn runs synchronous endpoints in a thread pool, so two
        # commits really can arrive at once, and two that interleave could both
        # decide September was free and both publish. The lock is the same
        # guard `InMemoryRunStore` uses, for the same reason.
        #
        # It covers this process only. Two backends sharing one library
        # directory are not protected, and are not a supported configuration —
        # ForgeXL is a local single-user application.
        self._write_lock = threading.Lock()

    @property
    def root(self) -> Path:
        """The directory this library stores everything under.

        Internal. It is never returned through the API — build plan 9C is
        explicit that physical paths stay out of the frontend contract.
        """
        return self._root

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{type(self).__name__}({str(self._root)!r})"

    # -- datasets ----------------------------------------------------------

    def create_dataset(self, definition: DatasetDefinition) -> Dataset:
        record_path = self._dataset_record(definition.id)
        with self._write_lock:
            if record_path.exists():
                raise DuplicateDatasetError(
                    f"Dataset {definition.id!r} already exists in the Data Library."
                )

            dataset = Dataset.of(definition, created_at=now())
            try:
                record_path.parent.mkdir(parents=True, exist_ok=True)
                _write_bytes_atomically(record_path, _to_json(dataset))
            except OSError as error:
                raise DataLibraryError(
                    f"The {definition.id} dataset could not be created.",
                    details={"dataset_id": definition.id, "reason": str(error)},
                ) from error
        return dataset

    def get_dataset(self, dataset_id: str) -> Dataset:
        validated = parse_dataset_id(dataset_id)
        record_path = self._dataset_record(validated)
        if not record_path.is_file():
            raise UnknownDatasetError(
                "No dataset exists with that ID.",
                details={"dataset_id": validated},
            )
        return _read_record(record_path, Dataset, what=f"dataset {validated}")

    def list_datasets(self) -> list[Dataset]:
        if not self._root.is_dir():
            return []
        datasets = [
            self.get_dataset(entry.name)
            for entry in sorted(self._root.iterdir(), key=lambda item: item.name)
            if entry.is_dir()
            and not entry.name.startswith(".")
            and (entry / DATASET_RECORD_FILENAME).is_file()
        ]
        return datasets

    # -- versions ----------------------------------------------------------

    def commit_version(
        self, dataset_id: str, commit: DatasetCommit
    ) -> DatasetVersion:
        """Write one new version, completely or not at all.

        The order matters and is the whole of build plan 9F's "invalid or
        corrupt commits do not leave partially valid persistent state":

        1. every check that can refuse the commit runs first, against state
           already on disk, and none of them writes anything;
        2. the version's files are assembled in a staging directory, so a
           failure part-way through leaves nothing where a version would be
           looked for;
        3. one rename publishes the finished directory. A reader sees either no
           such version or the whole of it, never half.
        """
        dataset = self.get_dataset(dataset_id)

        # The whole of check-then-write is inside the lock: the checks read
        # what is already committed, so a commit that ran between them and the
        # rename could invalidate them.
        with self._write_lock:
            version = self._prepare_version(dataset, commit)

            staging = self._staging_root() / uuid.uuid4().hex
            target = self._version_directory(dataset.id, version.version_id)
            try:
                staging.mkdir(parents=True)
                _write_file(
                    staging / VERSION_DATA_FILENAME, commit.frame.write_parquet
                )
                _write_bytes(staging / VERSION_RECORD_FILENAME, _to_json(version))
                target.parent.mkdir(parents=True, exist_ok=True)
                # Atomic publish. `os.rename` refuses an existing directory, and
                # the version ID is freshly generated, so this cannot silently
                # replace a committed version (build plan 9D).
                os.rename(staging, target)
            except Exception as error:
                raise DataLibraryError(
                    f"Version {version.version_id} of {dataset.id} could not be "
                    "written. The Data Library is unchanged.",
                    details={
                        "dataset_id": dataset.id,
                        "version_id": version.version_id,
                        "reason": str(error),
                    },
                ) from error
            finally:
                shutil.rmtree(staging, ignore_errors=True)

        return version

    def get_version(self, dataset_id: str, version_id: str) -> DatasetVersion:
        dataset = self.get_dataset(dataset_id)
        validated = parse_version_id(version_id)
        record_path = (
            self._version_directory(dataset.id, validated) / VERSION_RECORD_FILENAME
        )
        if not record_path.is_file():
            raise UnknownDatasetVersionError(
                f"No version of {dataset.id} exists with that ID.",
                details={"dataset_id": dataset.id, "version_id": validated},
            )
        version = _read_record(
            record_path,
            DatasetVersion,
            what=f"version {validated} of dataset {dataset.id}",
        )
        # A record must agree with where it was found. They can only disagree
        # if the library's directories have been moved or copied by hand, and
        # the consequence of not checking would be the worst kind of silent
        # wrongness: one month's figures served under another month's ID.
        if version.version_id != validated or version.dataset_id != dataset.id:
            raise DataLibraryError(
                "A Data Library record does not match where it is stored. The "
                "library's directories have been modified outside ForgeXL.",
                details={
                    "dataset_id": dataset.id,
                    "version_id": validated,
                    "record_dataset_id": version.dataset_id,
                    "record_version_id": version.version_id,
                },
            )
        return version

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        dataset = self.get_dataset(dataset_id)
        versions_root = self._versions_root(dataset.id)
        if not versions_root.is_dir():
            return []

        # A version is a directory whose name is a version ID and which holds a
        # record. Anything else in here was not written by ForgeXL — a commit
        # publishes its directory in one rename, so a half-written one cannot
        # appear — and listing it as a version, or failing the whole listing
        # over it, would both be wrong. It is not a version, so it is not
        # listed.
        versions = [
            self.get_version(dataset.id, entry.name)
            for entry in sorted(versions_root.iterdir(), key=lambda item: item.name)
            if entry.is_dir()
            and _is_version_id(entry.name)
            and (entry / VERSION_RECORD_FILENAME).is_file()
        ]
        # Commit order, with the ID as a stable tiebreak so the sequence is
        # deterministic even for two versions stamped in the same microsecond.
        versions.sort(key=lambda version: (version.created_at, version.version_id))
        return versions

    def load_version(self, dataset_id: str, version_id: str) -> pl.DataFrame:
        version = self.get_version(dataset_id, version_id)
        data_path = (
            self._version_directory(dataset_id, version.version_id)
            / VERSION_DATA_FILENAME
        )
        try:
            return pl.read_parquet(data_path)
        except Exception as error:
            raise DataLibraryError(
                f"The data for version {version.version_id} of {dataset_id} "
                "could not be read.",
                details={
                    "dataset_id": dataset_id,
                    "version_id": version.version_id,
                    "reason": str(error),
                },
            ) from error

    # -- checks ------------------------------------------------------------

    def _prepare_version(
        self, dataset: Dataset, commit: DatasetCommit
    ) -> DatasetVersion:
        """Validate a commit and build the record it would store.

        Every refusal happens here, before anything is written.
        """
        frame = commit.frame
        if frame.width == 0:
            raise InvalidDatasetCommitError(
                f"The data committed to {dataset.label} has no columns.",
                details={"dataset_id": dataset.id},
            )

        period = self._checked_period(dataset, commit)
        self._check_dates(dataset, commit)
        self._check_supersession(dataset, commit, period)

        try:
            return DatasetVersion(
                dataset_id=dataset.id,
                version_id=new_version_id(),
                dataset_kind=dataset.kind,
                period=period,
                created_at=now(),
                source_filename=commit.source_filename,
                source_byte_size=commit.source_byte_size,
                source_sha256=commit.source_sha256,
                parser_engine=commit.parser_engine,
                worksheet=commit.worksheet,
                row_count=frame.height,
                column_count=frame.width,
                column_schema=results.column_schema(frame),
                min_date=commit.min_date,
                max_date=commit.max_date,
                supersedes=commit.supersedes,
                supersession_reason=commit.supersession_reason,
            )
        except ValidationError as error:
            raise InvalidDatasetCommitError(
                f"The version committed to {dataset.label} is not valid.",
                details={"dataset_id": dataset.id, "reason": _first_reason(error)},
            ) from error

    def _checked_period(self, dataset: Dataset, commit: DatasetCommit) -> str | None:
        """Validate the reporting period, and require one where it is meaningless
        to be without one.

        A snapshot states the truth *as of* a month (build plan 9E). A snapshot
        with no month cannot be selected for a report and cannot be superseded
        by a later month's snapshot, so it is refused rather than stored as
        something nothing can ever use.
        """
        if commit.period is not None:
            return parse_period(commit.period)

        if dataset.kind is DatasetKind.SNAPSHOT:
            raise InvalidDatasetCommitError(
                f"{dataset.label} is a snapshot dataset: every version records "
                "the reporting period it is effective for. Commit it with a "
                "period such as 2026-09.",
                details={"dataset_id": dataset.id, "dataset_kind": dataset.kind.value},
            )
        return None

    def _check_dates(self, dataset: Dataset, commit: DatasetCommit) -> None:
        if (
            commit.min_date is not None
            and commit.max_date is not None
            and commit.min_date > commit.max_date
        ):
            raise InvalidDatasetCommitError(
                "The earliest date in the data cannot be later than the latest.",
                details={
                    "dataset_id": dataset.id,
                    "min_date": commit.min_date.isoformat(),
                    "max_date": commit.max_date.isoformat(),
                },
            )

    def _check_supersession(
        self, dataset: Dataset, commit: DatasetCommit, period: str | None
    ) -> None:
        """Enforce build plan 9D's replacement rules against what is stored.

        Two rules, and they are the same rule seen from either side:

        * A period that already has a live version can only be re-committed by
          a version that explicitly replaces it. Otherwise the library would
          hold two versions of September with nothing to say which is true —
          the silent overwrite 9D forbids, arrived at by addition instead.
        * A replacement must name a real, still-live version of the same
          dataset covering the same period. A chain that forks, or that points
          at nothing, cannot explain what happened.
        """
        live_for_period = (
            [
                version
                for version in self.current_versions(dataset.id)
                if version.period == period
            ]
            if period is not None
            else []
        )

        if commit.supersedes is None:
            if live_for_period:
                existing = live_for_period[0]
                raise InvalidDatasetCommitError(
                    f"{dataset.label} already holds data for {period}. "
                    "Replacing it is deliberate: commit the new version as a "
                    "replacement for the existing one, with a reason, so the "
                    "old version is kept and the change is explained.",
                    details={
                        "dataset_id": dataset.id,
                        "period": period,
                        "existing_version_id": existing.version_id,
                    },
                )
            return

        superseded = self.get_version(dataset.id, commit.supersedes)

        if superseded.period != period:
            raise InvalidDatasetCommitError(
                "A replacement must cover the same reporting period as the "
                f"version it replaces. Version {superseded.version_id} covers "
                f"{superseded.period or 'no single period'}; this commit "
                f"covers {period or 'no single period'}.",
                details={
                    "dataset_id": dataset.id,
                    "period": period,
                    "superseded_version_id": superseded.version_id,
                    "superseded_period": superseded.period,
                },
            )

        already = {
            version.supersedes: version.version_id
            for version in self.list_versions(dataset.id)
            if version.supersedes is not None
        }
        if superseded.version_id in already:
            raise InvalidDatasetCommitError(
                f"Version {superseded.version_id} of {dataset.label} has "
                f"already been replaced by {already[superseded.version_id]}. "
                "Replace the version that is current instead.",
                details={
                    "dataset_id": dataset.id,
                    "superseded_version_id": superseded.version_id,
                    "replaced_by_version_id": already[superseded.version_id],
                },
            )

    # -- paths -------------------------------------------------------------
    #
    # Every path in the library is built here, from validated IDs only. A
    # dataset ID is a lowercase identifier and a version ID is a UUID, both
    # checked before they reach these helpers, so no caller-supplied value can
    # steer a read or a write out of the library root.

    def _dataset_directory(self, dataset_id: str) -> Path:
        return self._root / parse_dataset_id(dataset_id)

    def _dataset_record(self, dataset_id: str) -> Path:
        return self._dataset_directory(dataset_id) / DATASET_RECORD_FILENAME

    def _versions_root(self, dataset_id: str) -> Path:
        return self._dataset_directory(dataset_id) / VERSIONS_DIRECTORY_NAME

    def _version_directory(self, dataset_id: str, version_id: str) -> Path:
        return self._versions_root(dataset_id) / parse_version_id(version_id)

    def _staging_root(self) -> Path:
        return self._root / STAGING_DIRECTORY_NAME


# ---------------------------------------------------------------------------
# Reading and writing records
# ---------------------------------------------------------------------------


def _is_version_id(name: str) -> bool:
    """Whether `name` could be a version ID ForgeXL issued."""
    try:
        return parse_version_id(name) == name
    except UnknownDatasetVersionError:
        return False


def _to_json(record: Dataset | DatasetVersion) -> bytes:
    """Serialise one library record. Indented, because a human reads these."""
    return record.model_dump_json(indent=2).encode("utf-8") + b"\n"


#: The two record shapes the library persists. Constrained rather than bound,
#: so :func:`_read_record` returns the exact model it was asked for.
RecordT = TypeVar("RecordT", Dataset, DatasetVersion)


def _read_record(path: Path, model: type[RecordT], *, what: str) -> RecordT:
    """Read and validate one JSON record, or say clearly that it is unreadable.

    A record that no longer validates is corrupt persistent state, and build
    plan 9F requires that to be visible. Returning a partially populated object
    or an empty result would both be worse than the fault: an empty answer
    reads as "this month was never imported".
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DataLibraryError(
            f"The Data Library record for {what} could not be read.",
            details={"reason": str(error)},
        ) from error

    try:
        record = model.model_validate_json(raw)
    except ValidationError as error:
        raise DataLibraryError(
            f"The Data Library record for {what} is not valid and was not "
            "written by this version of ForgeXL.",
            details={"reason": _first_reason(error)},
        ) from error

    if record.schema_version > LIBRARY_SCHEMA_VERSION:
        raise DataLibraryError(
            f"The Data Library record for {what} was written by a newer "
            f"version of ForgeXL (record format {record.schema_version}; this "
            f"build reads {LIBRARY_SCHEMA_VERSION}).",
            details={
                "record_schema_version": record.schema_version,
                "supported_schema_version": LIBRARY_SCHEMA_VERSION,
            },
        )
    return record


def _first_reason(error: ValidationError) -> str:
    """One readable line from a Pydantic failure, never the whole report."""
    problems = error.errors()
    if not problems:  # pragma: no cover - defensive
        return str(error)
    first = problems[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "record"
    return f"{location}: {first.get('msg', 'invalid')}"


def _write_bytes(path: Path, payload: bytes) -> None:
    """Write a staged file and flush it to the device before it is published."""
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_file(path: Path, writer: Callable[[Path], object]) -> None:
    """Let `writer` produce a staged file, then flush it to the device.

    Used for the Parquet payload, which Polars writes itself.
    """
    writer(path)
    file_descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    """Replace `path` with `payload` in one step, or leave it untouched.

    Writing into the destination directly would leave a half-written record
    behind if the process stopped mid-write, and a half-written record is
    exactly the corrupt persistent state build plan 9C's atomic-write rule
    exists to prevent. A temporary file in the same directory, flushed, then
    renamed over the destination, is atomic on the platforms ForgeXL runs on.
    """
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_bytes(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The application's Data Library
# ---------------------------------------------------------------------------

#: The library the application uses. Replacing the local implementation means
#: assigning a different :class:`DataLibrary` here; no caller changes, because
#: none of them knows what this is. The test suite swaps it per test, which is
#: the same mechanism and the practical proof the abstraction holds.
DATA_LIBRARY: DataLibrary = LocalDataLibrary(config.LIBRARY_DIRECTORY)


def create_dataset(definition: DatasetDefinition) -> Dataset:
    """Record `definition` in the application's Data Library."""
    return DATA_LIBRARY.create_dataset(definition)


def get_dataset(dataset_id: str) -> Dataset:
    """Return the application's dataset with `dataset_id`."""
    return DATA_LIBRARY.get_dataset(dataset_id)


def list_datasets() -> list[Dataset]:
    """Return every dataset the application's Data Library holds."""
    return DATA_LIBRARY.list_datasets()


def commit_version(dataset_id: str, commit: DatasetCommit) -> DatasetVersion:
    """Commit a new version of `dataset_id` to the application's Data Library."""
    return DATA_LIBRARY.commit_version(dataset_id, commit)


def get_version(dataset_id: str, version_id: str) -> DatasetVersion:
    """Return one version's metadata from the application's Data Library."""
    return DATA_LIBRARY.get_version(dataset_id, version_id)


def list_versions(dataset_id: str) -> list[DatasetVersion]:
    """Return every version of `dataset_id`, oldest commit first."""
    return DATA_LIBRARY.list_versions(dataset_id)


def load_version(dataset_id: str, version_id: str) -> pl.DataFrame:
    """Load one version of `dataset_id` as a DataFrame."""
    return DATA_LIBRARY.load_version(dataset_id, version_id)


def ensure_known_datasets(library: DataLibrary | None = None) -> list[Dataset]:
    """Make sure every dataset ForgeXL declares exists, and return them.

    Idempotent and safe to call repeatedly. Deliberately *not* called at import
    time: importing a module should never create a directory, and a library
    that has never been written to should stay absent until something is
    actually committed to it.
    """
    target = DATA_LIBRARY if library is None else library
    return [target.ensure_dataset(definition) for definition in KNOWN_DATASETS]
