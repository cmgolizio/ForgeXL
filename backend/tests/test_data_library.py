"""The persistent Data Library (build plan 9A, 9C, 9D, 9E).

The seven operations build plan 9A names, the local storage 9C specifies, the
immutability 9D requires and the snapshot semantics 9E depends on.

Durability across a restart is proved separately, in
`test_library_persistence.py`, because it needs a second process to be
convincing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app import config
from app.errors import (
    DataLibraryError,
    InvalidDatasetCommitError,
    UnknownDatasetError,
    UnknownDatasetVersionError,
)
from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    SAMPLE_HISTORY,
    DatasetCommit,
    DatasetDefinition,
    DatasetKind,
    content_hash,
    new_version_id,
)
from app.services import data_library as data_library_module
from app.services.data_library import (
    DATASET_RECORD_FILENAME,
    VERSION_DATA_FILENAME,
    VERSION_RECORD_FILENAME,
    DataLibrary,
    DuplicateDatasetError,
    LocalDataLibrary,
    ensure_known_datasets,
)

SALES = pl.DataFrame(
    {
        "Account": ["Acme Wine", "Bar Néro", "Chez Sol"],
        "Amount": [120.5, 80.0, 45.25],
    }
)

OWNERSHIP = pl.DataFrame(
    {
        "Account": ["Acme Wine", "Bar Néro"],
        "Rep": ["Beth Comeaux", "Kevin Wardell"],
    }
)


def commit_of(frame: pl.DataFrame = SALES, **fields) -> DatasetCommit:
    """A complete commit; override one field per test."""
    payload = frame.write_csv().encode("utf-8")
    return DatasetCommit.from_upload(
        frame, filename="September Sales.csv", payload=payload, **fields
    )


@pytest.fixture
def library(data_library: LocalDataLibrary) -> LocalDataLibrary:
    """The test's own library, with the three declared datasets created."""
    ensure_known_datasets(data_library)
    return data_library


# ---------------------------------------------------------------------------
# 9A — datasets
# ---------------------------------------------------------------------------


def test_a_new_library_holds_nothing(data_library: LocalDataLibrary) -> None:
    assert data_library.list_datasets() == []


def test_reading_an_empty_library_creates_nothing(
    data_library: LocalDataLibrary,
) -> None:
    """Importing or querying must never bring a directory into existence."""
    data_library.list_datasets()
    data_library.has_dataset("sales_history")

    assert not data_library.root.exists()


def test_a_dataset_can_be_created_and_read_back(
    data_library: LocalDataLibrary,
) -> None:
    created = data_library.create_dataset(SALES_HISTORY)

    assert created.id == "sales_history"
    assert created.kind is DatasetKind.HISTORY
    assert created.label == "Sales History"
    assert data_library.get_dataset("sales_history") == created


def test_creating_the_same_dataset_twice_is_refused(
    data_library: LocalDataLibrary,
) -> None:
    """Silently recreating would discard the record and orphan its versions."""
    data_library.create_dataset(SALES_HISTORY)

    with pytest.raises(DuplicateDatasetError):
        data_library.create_dataset(SALES_HISTORY)


def test_every_declared_dataset_is_created_together(library: DataLibrary) -> None:
    """Build plan Phase 9: sales history, sample history, account assignments."""
    assert [dataset.id for dataset in library.list_datasets()] == [
        "account_assignments",
        "sales_history",
        "sample_history",
    ]


def test_ensuring_the_declared_datasets_twice_changes_nothing(
    library: DataLibrary,
) -> None:
    """Ingestion calls this on every import and must not care which one it is."""
    before = library.list_datasets()

    ensure_known_datasets(library)

    assert library.list_datasets() == before


def test_an_existing_dataset_record_is_never_rewritten(
    library: DataLibrary,
) -> None:
    """`created_at` is a fact about this library, not a copy of a declaration."""
    original = library.get_dataset("sales_history")

    again = library.ensure_dataset(SALES_HISTORY)

    assert again.created_at == original.created_at


def test_a_dataset_whose_stored_kind_changed_is_reported(
    library: DataLibrary,
) -> None:
    """Its versions were committed under the stored meaning (build plan 9E)."""
    redeclared = DatasetDefinition(
        id="sales_history",
        kind=DatasetKind.SNAPSHOT,
        label="Sales History",
        description="Redeclared with a different meaning.",
    )

    with pytest.raises(DataLibraryError) as failure:
        library.ensure_dataset(redeclared)

    assert failure.value.details["stored_kind"] == "history"
    assert failure.value.details["declared_kind"] == "snapshot"


def test_an_unknown_dataset_is_reported_rather_than_guessed(
    library: DataLibrary,
) -> None:
    with pytest.raises(UnknownDatasetError):
        library.get_dataset("sales")


def test_a_dataset_id_shaped_like_a_path_never_reaches_the_filesystem(
    library: DataLibrary,
) -> None:
    for hostile in ("../../etc", "/etc/passwd", "sales_history/versions", ".."):
        with pytest.raises(UnknownDatasetError):
            library.get_dataset(hostile)


def test_the_staging_directory_is_not_mistaken_for_a_dataset(
    library: LocalDataLibrary,
) -> None:
    library.commit_version("sales_history", commit_of(period="2026-09"))

    assert "" not in [dataset.id for dataset in library.list_datasets()]
    assert len(library.list_datasets()) == 3


# ---------------------------------------------------------------------------
# 9A/9B — committing and loading a version
# ---------------------------------------------------------------------------


def test_a_version_can_be_committed_and_loaded_back(library: DataLibrary) -> None:
    version = library.commit_version("sales_history", commit_of(period="2026-09"))

    loaded = library.load_version("sales_history", version.version_id)

    assert loaded.equals(SALES)


def test_a_committed_version_records_its_source_and_shape(
    library: DataLibrary,
) -> None:
    payload = SALES.write_csv().encode("utf-8")

    version = library.commit_version(
        "sales_history",
        DatasetCommit.from_upload(
            SALES,
            filename="September Sales.csv",
            payload=payload,
            period="2026-09",
            parser_engine="polars-csv",
            min_date=date(2026, 9, 1),
            max_date=date(2026, 9, 30),
        ),
    )

    assert version.dataset_id == "sales_history"
    assert version.dataset_kind is DatasetKind.HISTORY
    assert version.period == "2026-09"
    assert version.source_filename == "September Sales.csv"
    assert version.source_byte_size == len(payload)
    assert version.source_sha256 == content_hash(payload)
    assert version.parser_engine == "polars-csv"
    assert version.row_count == 3
    assert version.column_count == 2
    assert version.columns == ("Account", "Amount")
    assert version.min_date == date(2026, 9, 1)
    assert version.max_date == date(2026, 9, 30)


def test_a_committed_version_records_its_column_types(library: DataLibrary) -> None:
    """Build plan 9B: column schema, not only column names."""
    version = library.commit_version("sales_history", commit_of(period="2026-09"))

    dtypes = {column.name: column.dtype for column in version.column_schema}

    assert dtypes == {"Account": "String", "Amount": "Float64"}


def test_a_version_can_be_retrieved_by_id(library: DataLibrary) -> None:
    committed = library.commit_version("sales_history", commit_of(period="2026-09"))

    assert library.get_version("sales_history", committed.version_id) == committed


def test_versions_are_listed_oldest_first(library: DataLibrary) -> None:
    september = library.commit_version("sales_history", commit_of(period="2026-09"))
    october = library.commit_version("sales_history", commit_of(period="2026-10"))
    november = library.commit_version("sales_history", commit_of(period="2026-11"))

    listed = [version.version_id for version in library.list_versions("sales_history")]

    assert listed == [september.version_id, october.version_id, november.version_id]


def test_a_dataset_with_no_versions_lists_none(library: DataLibrary) -> None:
    assert library.list_versions("sample_history") == []


def test_an_unknown_version_is_reported(library: DataLibrary) -> None:
    with pytest.raises(UnknownDatasetVersionError):
        library.get_version("sales_history", new_version_id())


def test_a_version_id_shaped_like_a_path_never_reaches_the_filesystem(
    library: DataLibrary,
) -> None:
    for hostile in ("../../../etc/passwd", "September.csv", "..", ""):
        with pytest.raises(UnknownDatasetVersionError):
            library.get_version("sales_history", hostile)


def test_committing_to_an_unknown_dataset_is_refused(library: DataLibrary) -> None:
    with pytest.raises(UnknownDatasetError):
        library.commit_version("invented_dataset", commit_of(period="2026-09"))


def test_values_survive_the_round_trip_exactly(library: DataLibrary) -> None:
    """Accents, blanks and precision are stored, not normalised."""
    source = pl.DataFrame(
        {
            "Producer": ["Château Lafite", "Weingut Müller", None],
            "Volume": ["750ml", "", "1.5L"],
            "Amount": [1234.5678, 0.000123, -42.0],
        }
    )

    version = library.commit_version(
        "sales_history", commit_of(source, period="2026-09")
    )

    assert library.load_version("sales_history", version.version_id).equals(source)


def test_an_empty_dataset_can_be_committed_but_an_empty_shape_cannot(
    library: DataLibrary,
) -> None:
    """A month with no rows is a fact; a table with no columns is not data."""
    header_only = pl.DataFrame(schema={"Account": pl.String, "Amount": pl.Float64})

    version = library.commit_version(
        "sales_history", commit_of(header_only, period="2026-09")
    )
    assert version.row_count == 0
    assert version.column_count == 2

    with pytest.raises(InvalidDatasetCommitError):
        library.commit_version(
            "sample_history", commit_of(pl.DataFrame(), period="2026-09")
        )


def test_a_period_that_is_not_a_calendar_month_is_refused(
    library: DataLibrary,
) -> None:
    with pytest.raises(InvalidDatasetCommitError):
        library.commit_version("sales_history", commit_of(period="September"))


def test_dates_that_contradict_each_other_are_refused(library: DataLibrary) -> None:
    with pytest.raises(InvalidDatasetCommitError):
        library.commit_version(
            "sales_history",
            commit_of(
                period="2026-09",
                min_date=date(2026, 9, 30),
                max_date=date(2026, 9, 1),
            ),
        )


# ---------------------------------------------------------------------------
# 9C — local storage layout
# ---------------------------------------------------------------------------


def test_a_dataset_is_a_directory_holding_its_record(
    library: LocalDataLibrary,
) -> None:
    record = library.root / "sales_history" / DATASET_RECORD_FILENAME

    assert record.is_file()


def test_a_version_is_a_directory_holding_parquet_and_its_record(
    library: LocalDataLibrary,
) -> None:
    """Build plan 9C: Parquet plus small structured metadata."""
    version = library.commit_version("sales_history", commit_of(period="2026-09"))

    directory = library.root / "sales_history" / "versions" / version.version_id

    assert sorted(item.name for item in directory.iterdir()) == [
        VERSION_DATA_FILENAME,
        VERSION_RECORD_FILENAME,
    ]


def test_the_stored_data_is_readable_parquet(library: LocalDataLibrary) -> None:
    """Read with Polars directly, so the format is proved rather than assumed."""
    version = library.commit_version("sales_history", commit_of(period="2026-09"))

    path = (
        library.root
        / "sales_history"
        / "versions"
        / version.version_id
        / VERSION_DATA_FILENAME
    )

    assert pl.read_parquet(path).equals(SALES)


def test_nothing_is_left_staged_after_a_commit(library: LocalDataLibrary) -> None:
    library.commit_version("sales_history", commit_of(period="2026-09"))

    staging = library.root / ".staging"

    assert not staging.exists() or list(staging.iterdir()) == []


def test_the_library_root_is_resolved_once_and_survives_a_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative working directory must not decide which library is reached."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    library = LocalDataLibrary(tmp_path / "library")
    library.create_dataset(SALES_HISTORY)

    monkeypatch.chdir(elsewhere)

    assert library.get_dataset("sales_history").id == "sales_history"
    assert list(elsewhere.iterdir()) == []


def test_the_default_library_location_is_absolute_and_inside_data() -> None:
    """Build plan 9C's conceptual layout, and git ignores `data/` entirely."""
    assert config.LIBRARY_DIRECTORY.is_absolute()
    assert config.LIBRARY_DIRECTORY.parts[-2:] == ("data", "library")


def test_the_application_library_is_a_single_replaceable_instance() -> None:
    """The seam a persistent or remote implementation would be installed at."""
    assert isinstance(data_library_module.DATA_LIBRARY, DataLibrary)


def test_no_record_written_to_disk_contains_a_filesystem_path(
    library: LocalDataLibrary,
) -> None:
    """Build plan 9C: physical paths are not part of what the library records."""
    library.commit_version("sales_history", commit_of(period="2026-09"))

    for record in library.root.rglob("*.json"):
        text = record.read_text()
        for fragment in ("/home/", "/Users/", "/tmp", "versions/", ".parquet"):
            assert fragment not in text, f"{record.name} names {fragment}"


# ---------------------------------------------------------------------------
# 9D — immutable historical versions
# ---------------------------------------------------------------------------


def test_a_second_version_of_a_period_must_be_a_deliberate_replacement(
    library: DataLibrary,
) -> None:
    """Build plan 9D: do not silently overwrite previously stored history.

    Two live versions of September would be an overwrite arrived at by
    addition — the library would hold two answers with nothing to choose
    between them.
    """
    first = library.commit_version("sales_history", commit_of(period="2026-09"))

    with pytest.raises(InvalidDatasetCommitError) as failure:
        library.commit_version("sales_history", commit_of(period="2026-09"))

    assert failure.value.details["existing_version_id"] == first.version_id
    assert library.list_versions("sales_history") == [first]


def test_replacing_a_period_preserves_the_version_it_replaces(
    library: DataLibrary,
) -> None:
    """Build plan 9D, all four requirements at once."""
    original = library.commit_version("sales_history", commit_of(period="2026-09"))
    corrected_rows = SALES.with_columns(pl.col("Amount") * 2)

    replacement = library.commit_version(
        "sales_history",
        commit_of(
            corrected_rows,
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="The first export was taken before month end.",
        ),
    )

    # 1. the old version is preserved
    assert library.get_version("sales_history", original.version_id) == original
    assert library.load_version("sales_history", original.version_id).equals(SALES)
    # 2. a new version exists
    assert replacement.version_id != original.version_id
    assert library.load_version(
        "sales_history", replacement.version_id
    ).equals(corrected_rows)
    # 3. the replacement is marked
    assert replacement.supersedes == original.version_id
    assert library.superseded_version_ids("sales_history") == {original.version_id}
    # 4. the change is explained
    assert "before month end" in (replacement.supersession_reason or "")


def test_a_superseded_version_is_no_longer_current_but_is_still_stored(
    library: DataLibrary,
) -> None:
    original = library.commit_version("sales_history", commit_of(period="2026-09"))
    replacement = library.commit_version(
        "sales_history",
        commit_of(
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="Restated.",
        ),
    )

    assert library.current_version("sales_history", "2026-09") == replacement
    assert [version.version_id for version in library.list_versions("sales_history")] == [
        original.version_id,
        replacement.version_id,
    ]
    assert [
        version.version_id
        for version in library.versions_for_period("sales_history", "2026-09")
    ] == [original.version_id, replacement.version_id]


def test_a_committed_version_is_never_rewritten(library: LocalDataLibrary) -> None:
    """The bytes on disk, before and after the month is replaced (build plan 9D)."""
    original = library.commit_version("sales_history", commit_of(period="2026-09"))
    directory = library.root / "sales_history" / "versions" / original.version_id
    before = {
        path.name: path.read_bytes() for path in sorted(directory.iterdir())
    }

    library.commit_version(
        "sales_history",
        commit_of(
            SALES.head(1),
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="Restated.",
        ),
    )

    after = {path.name: path.read_bytes() for path in sorted(directory.iterdir())}

    assert after == before


def test_the_library_offers_no_way_to_delete_or_edit_a_version() -> None:
    """An interface that cannot rewrite history cannot do so by accident."""
    forbidden = {"delete_version", "update_version", "overwrite_version"}

    assert forbidden.isdisjoint(dir(DataLibrary))


def test_a_replacement_must_name_a_version_that_exists(
    library: DataLibrary,
) -> None:
    library.commit_version("sales_history", commit_of(period="2026-09"))

    with pytest.raises(UnknownDatasetVersionError):
        library.commit_version(
            "sales_history",
            commit_of(
                period="2026-09",
                supersedes=new_version_id(),
                supersession_reason="Replacing something that is not there.",
            ),
        )


def test_a_replacement_must_cover_the_same_period(library: DataLibrary) -> None:
    september = library.commit_version("sales_history", commit_of(period="2026-09"))

    with pytest.raises(InvalidDatasetCommitError) as failure:
        library.commit_version(
            "sales_history",
            commit_of(
                period="2026-10",
                supersedes=september.version_id,
                supersession_reason="Wrong month.",
            ),
        )

    assert failure.value.details["superseded_period"] == "2026-09"


def test_a_version_can_only_be_replaced_once(library: DataLibrary) -> None:
    """A forked supersession chain could not explain what actually happened."""
    original = library.commit_version("sales_history", commit_of(period="2026-09"))
    first_fix = library.commit_version(
        "sales_history",
        commit_of(
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="First correction.",
        ),
    )

    with pytest.raises(InvalidDatasetCommitError) as failure:
        library.commit_version(
            "sales_history",
            commit_of(
                period="2026-09",
                supersedes=original.version_id,
                supersession_reason="Second correction of the same version.",
            ),
        )

    assert failure.value.details["replaced_by_version_id"] == first_fix.version_id


def test_a_replacement_can_itself_be_replaced(library: DataLibrary) -> None:
    """Correcting a correction keeps the whole chain readable."""
    first = library.commit_version("sales_history", commit_of(period="2026-09"))
    second = library.commit_version(
        "sales_history",
        commit_of(
            period="2026-09",
            supersedes=first.version_id,
            supersession_reason="First correction.",
        ),
    )
    third = library.commit_version(
        "sales_history",
        commit_of(
            period="2026-09",
            supersedes=second.version_id,
            supersession_reason="Second correction.",
        ),
    )

    assert library.current_version("sales_history", "2026-09") == third
    assert len(library.versions_for_period("sales_history", "2026-09")) == 3
    assert library.superseded_version_ids("sales_history") == {
        first.version_id,
        second.version_id,
    }


def test_two_commits_for_one_period_at_once_cannot_both_succeed(
    library: LocalDataLibrary,
) -> None:
    """Uvicorn runs synchronous endpoints in a thread pool, so this can happen.

    Without a lock both threads could read "September is free" before either
    published, and the library would end up with two live versions of the same
    month — the ambiguity build plan 9D exists to prevent, reached by a race
    rather than by an overwrite.
    """
    import threading

    start = threading.Barrier(4)
    outcomes: list[object] = []
    guard = threading.Lock()

    def commit() -> None:
        start.wait(timeout=10)
        try:
            result: object = library.commit_version(
                "sales_history", commit_of(period="2026-09")
            )
        except InvalidDatasetCommitError as refusal:
            result = refusal
        with guard:
            outcomes.append(result)

    threads = [threading.Thread(target=commit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    succeeded = [item for item in outcomes if not isinstance(item, Exception)]

    assert len(outcomes) == 4
    assert len(succeeded) == 1
    assert len(library.list_versions("sales_history")) == 1
    assert library.current_version("sales_history", "2026-09") == succeeded[0]


def test_a_replacement_without_a_reason_never_reaches_the_disk(
    library: LocalDataLibrary,
) -> None:
    original = library.commit_version("sales_history", commit_of(period="2026-09"))

    with pytest.raises(InvalidDatasetCommitError):
        library.commit_version(
            "sales_history",
            commit_of(period="2026-09", supersedes=original.version_id),
        )

    assert library.list_versions("sales_history") == [original]


# ---------------------------------------------------------------------------
# 9E — account ownership snapshots
# ---------------------------------------------------------------------------


def test_a_snapshot_version_must_state_the_period_it_applies_to(
    library: DataLibrary,
) -> None:
    """Build plan 9E: ownership is stored by effective reporting period."""
    with pytest.raises(InvalidDatasetCommitError) as failure:
        library.commit_version("account_assignments", commit_of(OWNERSHIP))

    assert failure.value.details["dataset_kind"] == "snapshot"
    assert library.list_versions("account_assignments") == []


def test_each_month_of_ownership_is_retrievable_on_its_own(
    library: DataLibrary,
) -> None:
    """The scenario build plan 9E describes, end to end.

    An account belongs to one rep in September and another in November.
    Regenerating September's report must use September's ownership.
    """
    september_rows = pl.DataFrame(
        {"Account": ["Acme Wine"], "Rep": ["Beth Comeaux"]}
    )
    november_rows = pl.DataFrame(
        {"Account": ["Acme Wine"], "Rep": ["Kevin Wardell"]}
    )

    september = library.commit_version(
        "account_assignments", commit_of(september_rows, period="2026-09")
    )
    october = library.commit_version(
        "account_assignments", commit_of(september_rows, period="2026-10")
    )
    november = library.commit_version(
        "account_assignments", commit_of(november_rows, period="2026-11")
    )

    assert library.current_version("account_assignments", "2026-09") == september
    assert library.current_version("account_assignments", "2026-10") == october
    assert library.current_version("account_assignments", "2026-11") == november

    september_owner = library.load_version(
        "account_assignments", september.version_id
    )["Rep"].to_list()
    november_owner = library.load_version(
        "account_assignments", november.version_id
    )["Rep"].to_list()

    assert september_owner == ["Beth Comeaux"]
    assert november_owner == ["Kevin Wardell"]


def test_a_later_snapshot_does_not_replace_an_earlier_one(
    library: DataLibrary,
) -> None:
    """A snapshot dataset is not one mutable file (build plan 9E)."""
    september = library.commit_version(
        "account_assignments", commit_of(OWNERSHIP, period="2026-09")
    )

    library.commit_version(
        "account_assignments", commit_of(OWNERSHIP, period="2026-11")
    )

    assert library.superseded_version_ids("account_assignments") == frozenset()
    assert library.current_version("account_assignments", "2026-09") == september
    assert len(library.current_versions("account_assignments")) == 2


def test_a_month_that_was_never_imported_is_reported_as_missing(
    library: DataLibrary,
) -> None:
    library.commit_version(
        "account_assignments", commit_of(OWNERSHIP, period="2026-09")
    )

    with pytest.raises(UnknownDatasetVersionError):
        library.current_version("account_assignments", "2026-10")


def test_a_history_version_may_span_several_months(library: DataLibrary) -> None:
    """Build plan 10G's bootstrap; only a snapshot is required to name a month."""
    version = library.commit_version(
        "sales_history",
        commit_of(period=None, min_date=date(2024, 1, 1), max_date=date(2026, 8, 31)),
    )

    assert version.period is None
    assert library.current_versions("sales_history") == [version]


def test_sales_and_samples_do_not_share_versions(library: DataLibrary) -> None:
    """Build plan 10D: logically distinct even where the schemas overlap."""
    sales = library.commit_version("sales_history", commit_of(period="2026-09"))
    samples = library.commit_version("sample_history", commit_of(period="2026-09"))

    assert library.list_versions("sales_history") == [sales]
    assert library.list_versions("sample_history") == [samples]
    with pytest.raises(UnknownDatasetVersionError):
        library.get_version("sample_history", sales.version_id)


# ---------------------------------------------------------------------------
# The Data Library is not the Run Store
# ---------------------------------------------------------------------------


def test_the_data_library_holds_no_run_state(library: DataLibrary) -> None:
    """Build plan, "Run State and Business Data Are Different".

    The two abstractions share no method, no record and no directory.
    """
    from app.services.run_store import RunStore

    library_operations = {
        name for name in dir(DataLibrary) if not name.startswith("_")
    }
    run_store_operations = {name for name in dir(RunStore) if not name.startswith("_")}

    assert library_operations.isdisjoint(run_store_operations)


def test_committing_to_the_library_records_no_run(
    library: DataLibrary, run_store
) -> None:
    library.commit_version("sales_history", commit_of(period="2026-09"))

    assert run_store.list_runs() == []


def test_the_run_pipeline_still_writes_nothing(
    client, registered_actions, quarantine: Path, data_library: LocalDataLibrary
) -> None:
    """Phase 6's rule is untouched by Phase 9: a Run creates no file at all."""
    from tests.helpers import csv_bytes, make_action, upload_file

    registered_actions(make_action("passthrough"))
    payload = csv_bytes(["SKU"], [["A-1"], ["A-2"]])

    response = client.post(
        "/api/runs",
        data={"action_id": "passthrough"},
        files={"source_file": upload_file("source.csv", payload)},
    )

    assert response.status_code == 200
    assert list(quarantine.iterdir()) == []
    assert not data_library.root.exists()
