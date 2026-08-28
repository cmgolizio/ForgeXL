"""Storage service tests (build plan 3.1-3.3).

Covers Run directory creation and removal, generated filenames and the upload
limit, plus the path-safety rules of build plan section 16.

Run *state* is no longer stored here — since Phase 6B it lives in the Run
Store, and the tests that covered manifest writing and reading now live in
`tests/test_run_store.py` against `create_run` / `get_run` / `update_run`.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app import config
from app.errors import UnknownRunError, UploadTooLargeError
from app.services import storage


# ---------------------------------------------------------------------------
# 3.1 Run directories
# ---------------------------------------------------------------------------


def test_create_run_creates_the_full_directory_tree(runs_dir: Path) -> None:
    paths = storage.create_run()

    assert paths.root.is_dir()
    assert paths.inputs.is_dir()
    assert paths.working.is_dir()
    assert paths.exports.is_dir()
    assert paths.root.parent == runs_dir


def test_each_run_gets_its_own_isolated_directory(runs_dir: Path) -> None:
    first = storage.create_run()
    second = storage.create_run()

    assert first.run_id != second.run_id
    assert first.root != second.root


def test_new_run_id_is_a_uuid() -> None:
    run_id = storage.new_run_id()

    assert storage.parse_run_id(run_id) == run_id


# ---------------------------------------------------------------------------
# 3.1 / section 16 — no client value ever becomes a path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "not-a-uuid",
        "../../etc/passwd",
        "..",
        "/etc/passwd",
        "4f27d4bb-7464-4d04-a21b",
        "4f27d4bb74644d04a21bdeadbeef0000",
        "4f27d4bb-7464-4d04-a21b-000000000000/../../..",
    ],
)
def test_parse_run_id_rejects_anything_that_is_not_a_uuid(candidate: str) -> None:
    with pytest.raises(UnknownRunError):
        storage.parse_run_id(candidate)


@pytest.mark.parametrize("unsafe", ["../escape", "a/b", "a.b", "", "-leading"])
def test_input_directory_rejects_an_unsafe_slot_id(
    run_paths: storage.RunPaths, unsafe: str
) -> None:
    with pytest.raises(ValueError):
        run_paths.input_directory(unsafe)


@pytest.mark.parametrize("unsafe", ["../escape", "a/b", "a.b", ""])
def test_artifact_paths_reject_an_unsafe_output_id(
    run_paths: storage.RunPaths, unsafe: str
) -> None:
    with pytest.raises(ValueError):
        run_paths.working_artifact(unsafe)
    with pytest.raises(ValueError):
        run_paths.export_artifact(unsafe, "csv")


# ---------------------------------------------------------------------------
# 3.2 Safe filenames
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("sales.csv", ".csv"),
        ("SALES.CSV", ".csv"),
        ("book.xlsx", ".xlsx"),
        ("../../evil.csv", ".csv"),
        ("..\\..\\evil.csv", ".csv"),
        ("/absolute/path/data.csv", ".csv"),
        ("file (1).csv", ".csv"),
        ("café.csv", ".csv"),
        ("archive.tar.gz", ".gz"),
        ("noextension", ""),
    ],
)
def test_extension_of_reads_only_the_extension(filename: str, expected: str) -> None:
    assert storage.extension_of(filename) == expected


@pytest.mark.parametrize(
    "hostile",
    [
        "../../evil.csv",
        "..\\..\\evil.csv",
        "/etc/passwd.csv",
        "sub/dir/data.csv",
        "café.csv",
        "file (1).csv",
        "strange name.csv",
    ],
)
def test_a_hostile_filename_never_escapes_its_slot_directory(
    run_paths: storage.RunPaths, hostile: str
) -> None:
    stored = storage.store_upload(
        run_paths, "source_file", hostile, io.BytesIO(b"a,b\n1,2\n")
    )

    # The generated name is used, not the client's.
    assert stored.stored_filename == "source.csv"
    assert stored.path.name == "source.csv"
    assert stored.path.parent == run_paths.input_directory("source_file")
    # The written file is inside the Run directory, whatever the name claimed.
    assert run_paths.root.resolve() in stored.path.resolve().parents


def test_the_original_filename_is_preserved_as_metadata(
    run_paths: storage.RunPaths,
) -> None:
    stored = storage.store_upload(
        run_paths, "source_file", "Q3 Sales (final).csv", io.BytesIO(b"a\n1\n")
    )

    assert stored.original_filename == "Q3 Sales (final).csv"
    assert stored.stored_filename == "source.csv"


def test_the_stored_bytes_match_the_upload_exactly(
    run_paths: storage.RunPaths,
) -> None:
    payload = "a,b\n1,café\n2,naïve\n".encode()

    stored = storage.store_upload(
        run_paths, "source_file", "data.csv", io.BytesIO(payload)
    )

    assert stored.path.read_bytes() == payload
    assert stored.size_bytes == len(payload)


def test_two_slots_are_stored_side_by_side(run_paths: storage.RunPaths) -> None:
    first = storage.store_upload(
        run_paths, "current_sales", "a.csv", io.BytesIO(b"a\n1\n")
    )
    second = storage.store_upload(
        run_paths, "historical_sales", "b.csv", io.BytesIO(b"b\n2\n")
    )

    assert first.path != second.path
    assert first.path.read_bytes() == b"a\n1\n"
    assert second.path.read_bytes() == b"b\n2\n"


# ---------------------------------------------------------------------------
# 3.3 Upload limit
# ---------------------------------------------------------------------------


def test_an_upload_at_the_limit_is_accepted(run_paths: storage.RunPaths) -> None:
    payload = b"x" * 64

    stored = storage.store_upload(
        run_paths, "source_file", "d.csv", io.BytesIO(payload), max_bytes=64
    )

    assert stored.size_bytes == 64


def test_an_oversized_upload_is_rejected(run_paths: storage.RunPaths) -> None:
    with pytest.raises(UploadTooLargeError) as raised:
        storage.store_upload(
            run_paths,
            "source_file",
            "big.csv",
            io.BytesIO(b"x" * 65),
            max_bytes=64,
        )

    assert raised.value.http_status == 413
    assert raised.value.code == "FILE_TOO_LARGE"


def test_a_rejected_upload_leaves_no_partial_file(
    run_paths: storage.RunPaths,
) -> None:
    with pytest.raises(UploadTooLargeError):
        storage.store_upload(
            run_paths,
            "source_file",
            "big.csv",
            io.BytesIO(b"x" * (4 * 1024 * 1024)),
            max_bytes=1024,
        )

    assert not (run_paths.input_directory("source_file") / "source.csv").exists()


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (250 * 1024 * 1024, "250 MB"),
        (1024 * 1024, "1 MB"),
        (8, "8 bytes"),
        (1, "1 byte"),
    ],
)
def test_the_limit_is_stated_in_readable_units(
    run_paths: storage.RunPaths, limit: int, expected: str
) -> None:
    """A sub-megabyte limit must not round down to a meaningless '0 MB'."""
    with pytest.raises(UploadTooLargeError) as raised:
        storage.store_upload(
            run_paths,
            "source_file",
            "big.csv",
            io.BytesIO(b"x" * (limit + 1)),
            max_bytes=limit,
        )

    assert f"{expected} upload limit" in raised.value.message


def test_the_limit_defaults_to_the_configured_maximum(
    run_paths: storage.RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(UploadTooLargeError):
        storage.store_upload(
            run_paths, "source_file", "d.csv", io.BytesIO(b"x" * 9)
        )


# ---------------------------------------------------------------------------
# 6B.6 Releasing a Run's files
# ---------------------------------------------------------------------------


def test_delete_run_directory_removes_the_whole_run(runs_dir: Path) -> None:
    paths = storage.create_run()
    storage.store_upload(
        paths, "source_file", "sales.csv", io.BytesIO(b"a,b\n1,2\n")
    )

    assert storage.delete_run_directory(paths.run_id) is True
    assert not paths.root.exists()
    assert list(runs_dir.iterdir()) == []


def test_delete_run_directory_reports_false_for_an_unknown_run(
    runs_dir: Path,
) -> None:
    assert storage.delete_run_directory(storage.new_run_id()) is False


def test_delete_run_directory_reports_false_for_a_malformed_id(
    runs_dir: Path,
) -> None:
    assert storage.delete_run_directory("../../etc") is False


def test_delete_run_directory_cannot_reach_outside_the_runs_directory(
    runs_dir: Path,
) -> None:
    """A traversal-shaped ID is refused before anything is removed."""
    sibling = runs_dir.parent / "keep_me"
    sibling.mkdir()

    assert storage.delete_run_directory(f"..%2F{sibling.name}") is False
    assert storage.delete_run_directory(f"../{sibling.name}") is False
    assert sibling.is_dir()


def test_deleting_one_run_leaves_the_others_alone(runs_dir: Path) -> None:
    kept = storage.create_run()
    removed = storage.create_run()

    assert storage.delete_run_directory(removed.run_id) is True
    assert kept.root.is_dir()