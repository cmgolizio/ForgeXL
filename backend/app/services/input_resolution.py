"""Turning a Data Library reference into a DataFrame (build plan Phase 11).

Build plan 11B draws this module's whole job as a chain:

    dataset reference
            ↓
    Data Library
            ↓
    exact immutable dataset version
            ↓
    Polars DataFrame
            ↓
    Action

and then states the rule that makes it a module of its own:

    Actions must not open Data Library files themselves.

So the resolution happens *before* execution, outside the Action, and what the
Action receives is what it has always received — a DataFrame keyed by its own
input slot ID. An Action cannot tell a library-backed slot from an uploaded
one, which is the point: adding persistent inputs did not change the Action
contract (build plan Phase 11 purpose, "The Action must still receive
DataFrames").

**A moving selector stops moving here.** Build plan 11D allows ``latest`` and
``period:2026-09`` at selection time and requires the immutable version ID
before execution. :func:`resolve_slot` is the single place that conversion
happens, and it returns the resolved
:class:`~app.models.library.DatasetVersion` objects alongside the frame, so
the runner records what was actually read rather than what was asked for
(build plan 11C).

**A slot may read more than one version.** Phase 11 resolved exactly one, and
that was all Phase 11 needed. Build plan 13B requires a report Action's inputs
to "include the historical information required by the report specification",
and the year-over-year and year-to-date windows of build plan 13C span more
months than one monthly version holds — the Data Library stores one version
per month by construction (build plan 10G). The ``history`` selector added in
Phase 13 therefore resolves a *set*: every live version of the dataset, in
period order, optionally bounded at a month.

Nothing about build plan 11's guarantees changed to make that work:

* every version is still immutable and still resolved before execution;
* every version read is still recorded on the Run individually, by its own ID
  (11C), so the provenance of a report is the full list of months it was built
  from rather than a summary of them;
* naming those IDs back reproduces the Run exactly (11D);
* the Action still receives ``{slot_id: DataFrame}`` and still cannot tell
  where a slot's rows came from (11B).

The merge itself is a plain vertical concatenation in period order, and it
refuses rather than reconciles when two months disagree about their columns —
see :func:`_merge_versions`.

Nothing here writes. The Data Library is read, and a Run remains something
that persists nothing at all — reading stored history does not make a Run a
thing that stores.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import polars as pl

from app.errors import (
    InconsistentDatasetVersionsError,
    InvalidDatasetSelectorError,
    UnknownDatasetError,
    UnknownDatasetVersionError,
)
from app.models.library import (
    DatasetSelector,
    DatasetSelectorKind,
    DatasetVersion,
    known_dataset,
    parse_dataset_id,
)
from app.models.schemas import (
    ActionInput,
    ActionInputSource,
    AuditLibraryInput,
    LibraryInputMetadata,
)
from app.services import data_library

#: The failures that mean "what you asked the library for is not there", as
#: opposed to "the library is broken". The runner turns these into validation
#: issues and fails the Run with them; a
#: :class:`~app.errors.DataLibraryError` deliberately is not one of them and
#: propagates as the server fault it is (build plan 9F).
RESOLUTION_FAILURES: tuple[type[Exception], ...] = (
    InvalidDatasetSelectorError,
    UnknownDatasetError,
    UnknownDatasetVersionError,
    InconsistentDatasetVersionsError,
)


@dataclass(frozen=True)
class ResolvedLibraryInput:
    """One library-backed slot, resolved to an exact version and its rows.

    Carries both halves of build plan 11C: the version that was read, and the
    reference that was asked for. The runner keeps the frame for execution and
    records :meth:`as_metadata` on the Run, so the Run's record explains the
    input without holding its rows.
    """

    slot_id: str
    dataset_id: str
    dataset_label: str

    #: What the caller asked for. May be a moving concept; it is never what
    #: the Run records as the identity of the data it used.
    selector: DatasetSelector

    #: What that resolved to. Immutable, and the thing a later re-run names to
    #: reproduce this Run exactly (build plan 11D).
    version: DatasetVersion

    #: The version's rows, read once, held only for the life of the Run.
    frame: pl.DataFrame

    def as_metadata(self) -> LibraryInputMetadata:
        """Render this input for the Run manifest (build plan 11C)."""
        return LibraryInputMetadata(
            slot_id=self.slot_id,
            dataset_id=self.dataset_id,
            dataset_label=self.dataset_label,
            requested=self.selector.as_text(),
            version_id=self.version.version_id,
            period=self.version.period,
            version_created_at=self.version.created_at,
            source_filename=self.version.source_filename,
            source_sha256=self.version.source_sha256,
            row_count=self.frame.height,
            column_count=self.frame.width,
            columns=tuple(self.frame.columns),
        )

    def as_audit(self) -> AuditLibraryInput:
        """Render this input for the Run's audit summary (build plan 6E.5)."""
        return AuditLibraryInput(
            slot_id=self.slot_id,
            dataset_id=self.dataset_id,
            dataset_label=self.dataset_label,
            requested=self.selector.as_text(),
            version_id=self.version.version_id,
            period=self.version.period,
            row_count=self.frame.height,
            column_count=self.frame.width,
        )


@dataclass(frozen=True)
class ResolvedLibrarySlot:
    """One library-backed slot, resolved to the versions it reads.

    A slot filled by ``latest``, ``period:`` or ``version:`` holds exactly one
    entry in :attr:`versions`; a slot filled by ``history`` holds one per
    month, oldest first. :attr:`frame` is what the Action receives either way,
    which is why the Action cannot tell the two apart (build plan 11B).

    The metadata is per *version*, never per slot: build plan 11C asks a Run
    to record "the exact dataset versions used", and one record summarising
    several months would be a summary rather than the identities. A history
    slot therefore contributes several entries to the manifest's
    ``library_inputs``, each naming its own month, source file and hash, and
    every one of them is a version ID a later Run can name back.
    """

    slot_id: str
    dataset_id: str
    dataset_label: str

    #: What the caller asked for. May be a moving concept; it is never what
    #: the Run records as the identity of the data it used.
    selector: DatasetSelector

    #: What that resolved to, oldest period first. Never empty.
    versions: tuple[ResolvedLibraryInput, ...]

    #: Every version's rows as one table, in period order.
    frame: pl.DataFrame

    @property
    def version(self) -> DatasetVersion:
        """The newest version this slot read.

        Present for the single-version case, where "the version" is an
        unambiguous thing to ask for. A history slot has no single version and
        callers that care read :attr:`versions`.
        """
        return self.versions[-1].version

    def as_metadata(self) -> tuple[LibraryInputMetadata, ...]:
        """Render every version read, for the Run manifest (build plan 11C)."""
        return tuple(item.as_metadata() for item in self.versions)

    def as_audit(self) -> tuple[AuditLibraryInput, ...]:
        """Render every version read, for the Run's audit summary."""
        return tuple(item.as_audit() for item in self.versions)


def resolve_slot(slot: ActionInput, reference: str) -> ResolvedLibrarySlot:
    """Resolve one library-backed input slot into its rows.

    Args:
        slot: The Action's declared slot. Must be library-backed; the dataset
            it reads is declared by the Action, never by the client, so no
            client-supplied string ever chooses which dataset is opened.
        reference: What the client asked for, as text — ``latest``,
            ``period:YYYY-MM``, ``version:<version id>``, ``history`` or
            ``history:YYYY-MM``.

    Raises:
        InvalidDatasetSelectorError: `reference` is not one of those forms.
        UnknownDatasetError: the dataset has nothing committed to it.
        UnknownDatasetVersionError: no version answers the reference.
        InconsistentDatasetVersionsError: the versions a ``history`` selector
            named do not describe the same columns.
        DataLibraryError: the library's stored state could not be read.
    """
    if slot.source is not ActionInputSource.LIBRARY:
        raise ValueError(
            f"Input slot {slot.id!r} is not library-backed; "
            "resolve_slot is only for slots that read the Data Library."
        )

    dataset_id = parse_dataset_id(slot.dataset_id or "")
    selector = DatasetSelector.parse(reference)
    label = dataset_label(dataset_id)

    versions = resolve_versions(dataset_id, selector, label=label)
    resolved = tuple(
        ResolvedLibraryInput(
            slot_id=slot.id,
            dataset_id=dataset_id,
            dataset_label=label,
            selector=selector,
            version=version,
            frame=data_library.load_version(dataset_id, version.version_id),
        )
        for version in versions
    )

    return ResolvedLibrarySlot(
        slot_id=slot.id,
        dataset_id=dataset_id,
        dataset_label=label,
        selector=selector,
        versions=resolved,
        frame=_merge_versions(resolved, label=label),
    )


def resolve_versions(
    dataset_id: str, selector: DatasetSelector, *, label: str | None = None
) -> tuple[DatasetVersion, ...]:
    """Return every immutable version `selector` names, oldest period first.

    One entry for every selector but ``history``, which is the only one that
    names a set. Either way what leaves this function is
    :class:`~app.models.library.DatasetVersion` objects and never a selector,
    which is build plan 11D's rule.
    """
    name = label or dataset_label(dataset_id)

    if selector.kind is not DatasetSelectorKind.HISTORY:
        return (resolve_version(dataset_id, selector, label=name),)

    _require_dataset(dataset_id, name)
    return history_versions(dataset_id, selector.value, label=name)


def history_versions(
    dataset_id: str, through: str | None = None, *, label: str | None = None
) -> tuple[DatasetVersion, ...]:
    """Every live version of `dataset_id`, oldest month first.

    Args:
        dataset_id: The dataset to read.
        through: The last month to include, or None for every month there is.
            **The bounding month must itself have a live version.** A bound
            that quietly fell back to an earlier month would mean a report
            built for August while its Run said September, and the only place
            that mistake is visible is here, where the request and the stored
            months are both in hand.
        label: The dataset's display name, for the message.

    Only versions that state a reporting period take part; a version without
    one cannot be placed in the sequence, and the ingestion layer never
    commits one (build plan 10C-10G).

    Raises:
        UnknownDatasetVersionError: the dataset holds no live version, or the
            bounding month has none.
    """
    name = label or dataset_label(dataset_id)
    live = [
        version
        for version in data_library.current_versions(dataset_id)
        if version.period is not None
    ]
    if not live:
        raise UnknownDatasetVersionError(
            f"No {name} data has been committed to the Data Library yet.",
            details={
                "dataset_id": dataset_id,
                "selector": DatasetSelector(
                    kind=DatasetSelectorKind.HISTORY, value=through
                ).as_text(),
            },
        )

    ordered = sorted(
        live, key=lambda version: (version.period or "", version.created_at)
    )
    if through is None:
        return tuple(ordered)

    months = [version.period for version in ordered]
    if through not in months:
        raise UnknownDatasetVersionError(
            f"{name} has no committed data for {through}. Import that month, "
            "or name a month that has been imported.",
            details={
                "dataset_id": dataset_id,
                "period": through,
                "available_periods": months,
            },
        )

    return tuple(
        version
        for version in ordered
        if version.period is not None and version.period <= through
    )


def _merge_versions(
    resolved: tuple[ResolvedLibraryInput, ...], *, label: str
) -> pl.DataFrame:
    """Read several versions of one dataset as one table.

    One version is returned as it stands — the overwhelmingly common case, and
    the only one that existed before Phase 13 — so nothing about a
    single-version slot passes through a merge at all.

    Several are concatenated in the order they were resolved, which is period
    order, after each is selected into the newest version's column order. The
    reorder is safe because the column *names* are identical by the time it
    happens; the check above is what guarantees that.

    Columns must match exactly. Two months whose exports carry different
    columns describe different things, and merging them would mean inventing
    values for one month or dropping a column from the other (build plan
    section 3.3). Types may differ and are widened: the same column stored as
    an integer in a CSV month and as a float in a workbook month is one
    column, and no value changes.
    """
    if len(resolved) == 1:
        return resolved[0].frame

    newest = resolved[-1]
    expected = tuple(newest.frame.columns)
    reference = set(expected)

    for item in resolved:
        present = set(item.frame.columns)
        if present == reference:
            continue
        raise InconsistentDatasetVersionsError(
            f"The {label} months this Run read do not describe the same "
            "columns, so they cannot be read as one table. "
            f"{item.version.period or item.version.version_id} and "
            f"{newest.version.period or newest.version.version_id} differ.",
            details={
                "dataset_id": item.dataset_id,
                "period": item.version.period,
                "compared_with": newest.version.period,
                "missing_columns": sorted(reference - present),
                "unexpected_columns": sorted(present - reference),
            },
        )

    return pl.concat(
        [item.frame.select(expected) for item in resolved],
        how="vertical_relaxed",
    )


def resolve_version(
    dataset_id: str, selector: DatasetSelector, *, label: str | None = None
) -> DatasetVersion:
    """Return the one immutable version `selector` names.

    This is where build plan 11D's rule is enforced: whatever kind of selector
    arrives, a single :class:`~app.models.library.DatasetVersion` leaves, and
    it is that version's ID the Run goes on to record.

    ``version:`` reaches a superseded version deliberately. A version that has
    been corrected is still exactly what an older report was built from, and
    build plan 11E requires that "specific old versions can be selected".
    ``period:`` and ``latest`` see only live versions, because those ask what
    the dataset says *now*.
    """
    name = label or dataset_label(dataset_id)
    _require_dataset(dataset_id, name)

    if selector.kind is DatasetSelectorKind.VERSION:
        assert selector.value is not None  # guaranteed by DatasetSelector
        with _named(dataset_id, name):
            return data_library.get_version(dataset_id, selector.value)

    if selector.kind is DatasetSelectorKind.PERIOD:
        assert selector.value is not None
        with _named(dataset_id, name):
            return data_library.current_version(dataset_id, selector.value)

    return latest_version(dataset_id, label=name)


def latest_version(
    dataset_id: str, *, label: str | None = None
) -> DatasetVersion:
    """The newest live version of a dataset.

    "Newest" is the greatest reporting period, not the most recent commit.
    They differ exactly when a past month is corrected: correcting March after
    June was imported commits a March version last, and answering "latest"
    with March would be wrong. A version carrying no period at all — nothing
    the ingestion layer produces, but the model permits it — sorts below every
    version that has one, and commit order breaks any remaining tie.

    Raises:
        UnknownDatasetVersionError: the dataset holds no live version.
    """
    name = label or dataset_label(dataset_id)
    live = data_library.current_versions(dataset_id)
    if not live:
        raise UnknownDatasetVersionError(
            f"No {name} data has been committed to the Data Library yet.",
            details={"dataset_id": dataset_id, "selector": "latest"},
        )
    return max(
        live,
        key=lambda version: (
            version.period is not None,
            version.period or "",
            version.created_at,
            version.version_id,
        ),
    )


def dataset_label(dataset_id: str) -> str:
    """A human-readable name for `dataset_id`, for use in a message.

    Read from the declaration rather than from the stored record, so a message
    about a dataset that has never been written to still names it properly.
    Falls back to the ID for a dataset ForgeXL does not declare, which the
    resolution below reports as unknown anyway.
    """
    definition = known_dataset(dataset_id)
    return definition.label if definition is not None else dataset_id


@contextmanager
def _named(dataset_id: str, label: str) -> Iterator[None]:
    """Report a missing version by the dataset's display name.

    The Data Library's own messages name a dataset by its ID, which is right
    for a message about stored state and wrong for one a user reads. Until
    Phase 11 no library failure reached a user at all; now that one can, the
    substitution happens here rather than by rewording the library, so the two
    layers each say the thing that suits their own reader.

    Only the name is replaced. The failure, its code and its details are the
    library's own.
    """
    try:
        yield
    except UnknownDatasetVersionError as error:
        raise UnknownDatasetVersionError(
            error.message.replace(dataset_id, label),
            details={**error.details, "dataset_label": label},
        ) from error


def _require_dataset(dataset_id: str, label: str) -> None:
    """Fail clearly when nothing has ever been committed to the dataset.

    ``get_dataset`` already reports an unknown dataset, but its message is
    about a record that is missing. What has actually happened when an Action
    asks for the latest sales history and there is none is that the data was
    never imported, and build plan 11E asks for exactly that to fail clearly.
    """
    try:
        data_library.get_dataset(dataset_id)
    except UnknownDatasetError as error:
        raise UnknownDatasetError(
            f"No {label} data has been imported into the Data Library yet.",
            details={"dataset_id": dataset_id, **error.details},
        ) from error