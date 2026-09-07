"""Persistence verification for the Data Library (build plan 9F).

Build plan 9F lists seven things automated tests must prove. Each has a section
below, named for it.

The first two are about surviving a backend restart, and they are the reason
this module exists separately from `test_data_library.py`. A test that builds a
second :class:`~app.services.data_library.LocalDataLibrary` over the same
directory proves the *files* are sufficient — but both objects live in one
interpreter, and an accidental module-level cache would be shared by both and
would make the test pass for the wrong reason. So the headline claim is also
proved the only way that leaves no room for that: a **separate Python process**,
started after this one committed, reads the library with nothing in memory at
all. That is what "survives a backend restart" actually means.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import polars as pl
import pytest

from app.errors import DataLibraryError, InvalidDatasetCommitError
from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    DatasetCommit,
)
from app.services import data_library as data_library_module
from app.services.data_library import (
    VERSION_DATA_FILENAME,
    VERSION_RECORD_FILENAME,
    LocalDataLibrary,
    ensure_known_datasets,
)

#: `backend/`, so a subprocess can import `app` the same way pytest.ini does.
BACKEND_ROOT = Path(__file__).resolve().parents[1]

SEPTEMBER = pl.DataFrame(
    {"Account": ["Acme Wine", "Bar Néro"], "Amount": [120.5, 80.0]}
)
OCTOBER = pl.DataFrame(
    {"Account": ["Acme Wine", "Chez Sol"], "Amount": [96.0, 45.25]}
)


def commit_of(frame: pl.DataFrame, **fields) -> DatasetCommit:
    return DatasetCommit.from_upload(
        frame,
        filename="monthly.csv",
        payload=frame.write_csv().encode("utf-8"),
        **fields,
    )


@pytest.fixture
def library(data_library: LocalDataLibrary) -> LocalDataLibrary:
    ensure_known_datasets(data_library)
    return data_library


def reopened(library: LocalDataLibrary) -> LocalDataLibrary:
    """The same directory, opened by an object that has never seen it.

    Everything a fresh backend process would have: the files, and nothing else.
    """
    return LocalDataLibrary(library.root)


def read_in_a_separate_process(root: Path, script: str) -> dict:
    """Run `script` in a new interpreter against the library at `root`.

    The script receives ``root`` as :data:`ROOT` and must ``print`` one JSON
    object, which is returned. Nothing of this process's memory is available to
    it — a fresh interpreter, importing ForgeXL from source.
    """
    program = textwrap.dedent(
        f"""
        import json
        from pathlib import Path
        from app.services.data_library import LocalDataLibrary

        ROOT = Path({str(root)!r})
        library = LocalDataLibrary(ROOT)
        """
    ) + textwrap.dedent(script)

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=BACKEND_ROOT,
        env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


# ---------------------------------------------------------------------------
# 9F.1 — a dataset version survives backend restart
# ---------------------------------------------------------------------------


def test_a_committed_version_is_readable_by_a_new_backend_process(
    library: LocalDataLibrary,
) -> None:
    """The strongest form of the claim: a different interpreter reads it back."""
    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))

    result = read_in_a_separate_process(
        library.root,
        f"""
        frame = library.load_version("sales_history", {version.version_id!r})
        print(json.dumps({{
            "columns": frame.columns,
            "rows": frame.rows(),
            "height": frame.height,
        }}))
        """,
    )

    assert result["columns"] == ["Account", "Amount"]
    assert result["height"] == 2
    assert result["rows"] == [["Acme Wine", 120.5], ["Bar Néro", 80.0]]


def test_a_committed_version_is_readable_from_a_reopened_library(
    library: LocalDataLibrary,
) -> None:
    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))

    assert reopened(library).load_version("sales_history", version.version_id).equals(
        SEPTEMBER
    )


def test_the_data_library_outlives_the_run_store(library: LocalDataLibrary) -> None:
    """The whole reason the two are separate.

    A Run Store is replaced wholesale — which is exactly what a backend restart
    does to it — and the library is untouched.
    """
    from app.services.run_store import InMemoryRunStore

    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    restarted_run_store = InMemoryRunStore()

    assert restarted_run_store.list_runs() == []
    assert library.get_version("sales_history", version.version_id) == version


# ---------------------------------------------------------------------------
# 9F.2 — dataset metadata survives backend restart
# ---------------------------------------------------------------------------


def test_dataset_metadata_is_readable_by_a_new_backend_process(
    library: LocalDataLibrary,
) -> None:
    version = library.commit_version(
        "sales_history",
        commit_of(SEPTEMBER, period="2026-09", parser_engine="polars-csv"),
    )

    result = read_in_a_separate_process(
        library.root,
        f"""
        datasets = [d.model_dump(mode="json") for d in library.list_datasets()]
        version = library.get_version("sales_history", {version.version_id!r})
        print(json.dumps({{
            "dataset_ids": [d["id"] for d in datasets],
            "kinds": {{d["id"]: d["kind"] for d in datasets}},
            "version": version.model_dump(mode="json"),
        }}))
        """,
    )

    assert result["dataset_ids"] == [
        "account_assignments",
        "sales_history",
        "sample_history",
    ]
    assert result["kinds"]["account_assignments"] == "snapshot"
    assert result["kinds"]["sales_history"] == "history"

    restored = result["version"]
    assert restored["version_id"] == version.version_id
    assert restored["period"] == "2026-09"
    assert restored["source_filename"] == "monthly.csv"
    assert restored["source_sha256"] == version.source_sha256
    assert restored["source_byte_size"] == version.source_byte_size
    assert restored["row_count"] == 2
    assert restored["column_count"] == 2
    assert restored["parser_engine"] == "polars-csv"
    assert [column["name"] for column in restored["column_schema"]] == [
        "Account",
        "Amount",
    ]


def test_every_recorded_field_survives_being_reopened(
    library: LocalDataLibrary,
) -> None:
    """Not a subset: the whole record compares equal after a reopen."""
    from datetime import date

    version = library.commit_version(
        "sales_history",
        commit_of(
            SEPTEMBER,
            period="2026-09",
            parser_engine="fastexcel-calamine",
            worksheet="September",
            min_date=date(2026, 9, 1),
            max_date=date(2026, 9, 30),
        ),
    )

    assert reopened(library).get_version("sales_history", version.version_id) == version


def test_a_dataset_record_survives_being_reopened(
    data_library: LocalDataLibrary,
) -> None:
    created = data_library.create_dataset(SALES_HISTORY)

    assert reopened(data_library).get_dataset("sales_history") == created


# ---------------------------------------------------------------------------
# 9F.3 — multiple versions of one logical dataset can coexist
# ---------------------------------------------------------------------------


def test_several_months_of_one_dataset_coexist(library: LocalDataLibrary) -> None:
    september = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    october = library.commit_version("sales_history", commit_of(OCTOBER, period="2026-10"))

    after_restart = reopened(library)

    assert [version.version_id for version in after_restart.list_versions("sales_history")] == [
        september.version_id,
        october.version_id,
    ]
    assert after_restart.load_version("sales_history", september.version_id).equals(SEPTEMBER)
    assert after_restart.load_version("sales_history", october.version_id).equals(OCTOBER)


def test_adding_a_month_leaves_the_earlier_months_untouched(
    library: LocalDataLibrary,
) -> None:
    """The recurring monthly workflow: add one period, disturb nothing."""
    september = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    directory = library.root / "sales_history" / "versions" / september.version_id
    before = {path.name: path.read_bytes() for path in sorted(directory.iterdir())}

    library.commit_version("sales_history", commit_of(OCTOBER, period="2026-10"))

    after = {path.name: path.read_bytes() for path in sorted(directory.iterdir())}
    assert after == before


# ---------------------------------------------------------------------------
# 9F.4 — an older version can be loaded explicitly
# ---------------------------------------------------------------------------


def test_a_superseded_version_can_still_be_loaded_by_id(
    library: LocalDataLibrary,
) -> None:
    """What makes an old report reproducible (build plan 9D, 11C)."""
    original = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    library.commit_version(
        "sales_history",
        commit_of(
            OCTOBER,
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="September was exported before month end.",
        ),
    )

    after_restart = reopened(library)

    assert after_restart.load_version("sales_history", original.version_id).equals(
        SEPTEMBER
    )
    assert after_restart.current_version("sales_history", "2026-09").version_id != (
        original.version_id
    )


def test_an_older_version_is_loaded_by_a_new_process_after_replacement(
    library: LocalDataLibrary,
) -> None:
    original = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    replacement = library.commit_version(
        "sales_history",
        commit_of(
            OCTOBER,
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="Restated.",
        ),
    )

    result = read_in_a_separate_process(
        library.root,
        f"""
        old = library.load_version("sales_history", {original.version_id!r})
        current = library.current_version("sales_history", "2026-09")
        print(json.dumps({{
            "old_rows": old.rows(),
            "current_version_id": current.version_id,
            "current_supersedes": current.supersedes,
            "current_reason": current.supersession_reason,
            "superseded": sorted(library.superseded_version_ids("sales_history")),
        }}))
        """,
    )

    assert result["old_rows"] == [["Acme Wine", 120.5], ["Bar Néro", 80.0]]
    assert result["current_version_id"] == replacement.version_id
    assert result["current_supersedes"] == original.version_id
    assert result["current_reason"] == "Restated."
    assert result["superseded"] == [original.version_id]


# ---------------------------------------------------------------------------
# 9F.5 — replacing a period does not silently destroy the previous version
# ---------------------------------------------------------------------------


def test_a_replaced_month_is_still_on_disk_after_a_restart(
    library: LocalDataLibrary,
) -> None:
    original = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    library.commit_version(
        "sales_history",
        commit_of(
            OCTOBER,
            period="2026-09",
            supersedes=original.version_id,
            supersession_reason="Restated.",
        ),
    )

    versions_root = library.root / "sales_history" / "versions"

    assert len(list(versions_root.iterdir())) == 2
    assert (versions_root / original.version_id / VERSION_DATA_FILENAME).is_file()
    assert len(reopened(library).versions_for_period("sales_history", "2026-09")) == 2


def test_replacing_a_month_cannot_happen_by_accident(
    library: LocalDataLibrary,
) -> None:
    """Re-importing September without saying so is refused, not absorbed."""
    original = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))

    with pytest.raises(InvalidDatasetCommitError):
        library.commit_version("sales_history", commit_of(OCTOBER, period="2026-09"))

    assert reopened(library).list_versions("sales_history") == [original]
    assert reopened(library).load_version("sales_history", original.version_id).equals(
        SEPTEMBER
    )


# ---------------------------------------------------------------------------
# 9F.6 — account snapshots remain independently retrievable
# ---------------------------------------------------------------------------


def test_each_ownership_snapshot_is_retrievable_after_a_restart(
    library: LocalDataLibrary,
) -> None:
    months = {
        "2026-09": pl.DataFrame({"Account": ["Acme Wine"], "Rep": ["Beth Comeaux"]}),
        "2026-10": pl.DataFrame({"Account": ["Acme Wine"], "Rep": ["Beth Comeaux"]}),
        "2026-11": pl.DataFrame({"Account": ["Acme Wine"], "Rep": ["Kevin Wardell"]}),
    }
    committed = {
        period: library.commit_version(
            "account_assignments", commit_of(rows, period=period)
        )
        for period, rows in months.items()
    }

    after_restart = reopened(library)

    for period, rows in months.items():
        version = after_restart.current_version("account_assignments", period)
        assert version.version_id == committed[period].version_id
        assert after_restart.load_version(
            "account_assignments", version.version_id
        ).equals(rows)


def test_a_new_process_reads_the_ownership_that_applied_then(
    library: LocalDataLibrary,
) -> None:
    """Build plan 9E's scenario, proved across a process boundary."""
    library.commit_version(
        "account_assignments",
        commit_of(
            pl.DataFrame({"Account": ["Acme Wine"], "Rep": ["Beth Comeaux"]}),
            period="2026-09",
        ),
    )
    library.commit_version(
        "account_assignments",
        commit_of(
            pl.DataFrame({"Account": ["Acme Wine"], "Rep": ["Kevin Wardell"]}),
            period="2026-11",
        ),
    )

    result = read_in_a_separate_process(
        library.root,
        """
        def owner(period):
            version = library.current_version("account_assignments", period)
            return library.load_version("account_assignments", version.version_id)["Rep"][0]

        print(json.dumps({"september": owner("2026-09"), "november": owner("2026-11")}))
        """,
    )

    assert result["september"] == "Beth Comeaux"
    assert result["november"] == "Kevin Wardell"


def test_snapshots_are_kept_apart_from_history_on_disk(
    library: LocalDataLibrary,
) -> None:
    library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    library.commit_version(
        "account_assignments",
        commit_of(pl.DataFrame({"Account": ["Acme"], "Rep": ["Beth"]}), period="2026-09"),
    )

    sales_versions = library.root / "sales_history" / "versions"
    ownership_versions = library.root / "account_assignments" / "versions"

    assert len(list(sales_versions.iterdir())) == 1
    assert len(list(ownership_versions.iterdir())) == 1
    assert sales_versions != ownership_versions


# ---------------------------------------------------------------------------
# 9F.7 — invalid or corrupt commits leave no partially valid state
# ---------------------------------------------------------------------------


def test_a_refused_commit_writes_nothing_at_all(library: LocalDataLibrary) -> None:
    """Every check runs before the first byte reaches the disk."""
    refused = [
        ("sales_history", commit_of(SEPTEMBER, period="September")),
        ("sales_history", commit_of(pl.DataFrame(), period="2026-09")),
        ("account_assignments", commit_of(SEPTEMBER)),
    ]

    for dataset_id, commit in refused:
        with pytest.raises(InvalidDatasetCommitError):
            library.commit_version(dataset_id, commit)

    for dataset_id in ("sales_history", "account_assignments"):
        assert library.list_versions(dataset_id) == []
        assert not (library.root / dataset_id / "versions").exists()


def test_a_commit_that_fails_part_way_through_leaves_no_version(
    library: LocalDataLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason a version is staged and renamed rather than written in place.

    The failure is injected between the two files a version consists of, which
    is the worst moment: the Parquet payload exists and its record does not.
    Nothing of it may appear where a version is looked for.
    """
    september = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))

    real_write = data_library_module._write_bytes

    def fail_on_the_record(path: Path, payload: bytes) -> None:
        if path.name == VERSION_RECORD_FILENAME:
            raise OSError("no space left on device")
        real_write(path, payload)

    monkeypatch.setattr(data_library_module, "_write_bytes", fail_on_the_record)

    with pytest.raises(DataLibraryError):
        library.commit_version("sales_history", commit_of(OCTOBER, period="2026-10"))

    monkeypatch.undo()
    after_restart = reopened(library)

    assert after_restart.list_versions("sales_history") == [september]
    assert after_restart.load_version("sales_history", september.version_id).equals(
        SEPTEMBER
    )
    staging = library.root / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_a_failed_commit_leaves_the_versions_directory_holding_only_whole_versions(
    library: LocalDataLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        data_library_module,
        "_write_bytes",
        lambda path, payload: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(DataLibraryError):
        library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))

    monkeypatch.undo()
    versions_root = library.root / "sales_history" / "versions"

    assert not versions_root.exists() or list(versions_root.iterdir()) == []


def test_a_corrupt_version_record_is_reported_rather_than_ignored(
    library: LocalDataLibrary,
) -> None:
    """An empty answer would read as "this month was never imported"."""
    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    record = (
        library.root
        / "sales_history"
        / "versions"
        / version.version_id
        / VERSION_RECORD_FILENAME
    )
    record.write_text("{ this is not json")

    with pytest.raises(DataLibraryError):
        library.get_version("sales_history", version.version_id)
    with pytest.raises(DataLibraryError):
        library.list_versions("sales_history")


def test_a_version_record_missing_a_required_field_is_reported(
    library: LocalDataLibrary,
) -> None:
    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    record = (
        library.root
        / "sales_history"
        / "versions"
        / version.version_id
        / VERSION_RECORD_FILENAME
    )
    payload = json.loads(record.read_text())
    del payload["source_sha256"]
    record.write_text(json.dumps(payload))

    with pytest.raises(DataLibraryError) as failure:
        library.get_version("sales_history", version.version_id)

    assert "source_sha256" in failure.value.details["reason"]


def test_a_record_written_by_a_newer_build_is_refused(
    library: LocalDataLibrary,
) -> None:
    """Reading it as if it were this format is how silent misreads happen."""
    library.create_dataset  # noqa: B018 - reference kept for readers
    record = library.root / "sales_history" / "dataset.json"
    payload = json.loads(record.read_text())
    payload["schema_version"] = 99
    record.write_text(json.dumps(payload))

    with pytest.raises(DataLibraryError) as failure:
        library.get_dataset("sales_history")

    assert failure.value.details["record_schema_version"] == 99


def test_a_version_whose_data_file_is_unreadable_is_reported(
    library: LocalDataLibrary,
) -> None:
    version = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    data_file = (
        library.root
        / "sales_history"
        / "versions"
        / version.version_id
        / VERSION_DATA_FILENAME
    )
    data_file.write_bytes(b"not a parquet file")

    with pytest.raises(DataLibraryError):
        library.load_version("sales_history", version.version_id)


def test_a_half_written_directory_is_not_seen_as_a_version(
    library: LocalDataLibrary,
) -> None:
    """What the staging-and-rename design makes impossible, asserted directly.

    Simulated here by creating the shapes a partial write would have left — a
    directory with no record, and a directory whose name is not a version ID —
    to prove that even if one appeared it would not be read as a version, and
    would not break the listing either.
    """
    committed = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    versions_root = library.root / "sales_history" / "versions"

    no_record = versions_root / "3f8c1b9e-2a4d-4c6f-9b1e-77d5a0c3e412"
    no_record.mkdir()
    (no_record / VERSION_DATA_FILENAME).write_bytes(b"partial")

    not_an_id = versions_root / "september-2026"
    not_an_id.mkdir()
    (not_an_id / VERSION_RECORD_FILENAME).write_bytes(b"{}")

    assert library.list_versions("sales_history") == [committed]


def test_a_record_moved_to_another_version_directory_is_reported(
    library: LocalDataLibrary,
) -> None:
    """Serving one month's figures under another month's ID is the silent
    wrongness worth being loud about."""
    september = library.commit_version("sales_history", commit_of(SEPTEMBER, period="2026-09"))
    october = library.commit_version("sales_history", commit_of(OCTOBER, period="2026-10"))
    versions_root = library.root / "sales_history" / "versions"

    (versions_root / october.version_id / VERSION_RECORD_FILENAME).write_bytes(
        (versions_root / september.version_id / VERSION_RECORD_FILENAME).read_bytes()
    )

    with pytest.raises(DataLibraryError) as failure:
        library.get_version("sales_history", october.version_id)

    assert failure.value.details["record_version_id"] == september.version_id


def test_the_declared_datasets_are_not_created_by_reading_the_library(
    data_library: LocalDataLibrary,
) -> None:
    """`ensure_known_datasets` is called deliberately, never as a side effect."""
    assert data_library.list_datasets() == []
    assert not data_library.root.exists()

    ensure_known_datasets(data_library)

    assert len(data_library.list_datasets()) == 3


def test_the_application_library_is_redirected_away_from_the_repository(
    data_library: LocalDataLibrary,
) -> None:
    """The guard the whole suite depends on.

    If this fixture ever stopped taking effect, every test above would be
    writing into the repository's own `data/library`.
    """
    from app import config

    assert data_library_module.DATA_LIBRARY is data_library
    assert config.LIBRARY_DIRECTORY not in data_library.root.parents
    assert data_library.root != config.LIBRARY_DIRECTORY


def test_the_account_assignments_dataset_is_declared_as_a_snapshot() -> None:
    """Guards the one declaration build plan 9E's whole argument rests on."""
    assert ACCOUNT_ASSIGNMENTS.kind.value == "snapshot"