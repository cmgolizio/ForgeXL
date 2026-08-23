"""Storage service tests (build plan 3.1-3.3, 3.11).

Covers Run directory creation, generated filenames, the upload limit and
atomic manifest writing, plus the path-safety rules of build plan section 16.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import config
from app.errors import UnknownRunError, UploadTooLargeError
from app.models.schemas import (
    ActionReference,
    RunManifest,
    RunStatus,
    ValidationSummary,
)
from app.services import storage


def _manifest(run_id: str, status: RunStatus = RunStatus.RUNNING) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        status=status,
        action=ActionReference(id="a", version="1.0.0", name="A"),
        created_at=datetime.now(timezone.utc),
        validation=ValidationSummary(passed=True),
    )


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


def test_run_exists_is_false_until_a_manifest_is_written(runs_dir: Path) -> None:
    paths = storage.create_run()
    assert storage.run_exists(paths.run_id) is False

    storage.write_manifest(paths, _manifest(paths.run_id))
    assert storage.run_exists(paths.run_id) is True


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


def test_run_exists_reports_false_for_a_malformed_id(runs_dir: Path) -> None:
    assert storage.run_exists("../../etc") is False


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
# 3.11 Manifest writing
# ---------------------------------------------------------------------------


def test_write_manifest_produces_readable_json(run_paths: storage.RunPaths) -> None:
    storage.write_manifest(run_paths, _manifest(run_paths.run_id))

    payload = json.loads(run_paths.manifest_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == run_paths.run_id
    assert payload["schema_version"] == 1
    assert payload["status"] == "running"


def test_write_manifest_leaves_no_temporary_file_behind(
    run_paths: storage.RunPaths,
) -> None:
    storage.write_manifest(run_paths, _manifest(run_paths.run_id))

    leftovers = [p.name for p in run_paths.root.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_rewriting_a_manifest_replaces_it_atomically(
    run_paths: storage.RunPaths,
) -> None:
    storage.write_manifest(run_paths, _manifest(run_paths.run_id))
    storage.write_manifest(
        run_paths, _manifest(run_paths.run_id, RunStatus.SUCCEEDED)
    )

    reloaded = storage.read_manifest(run_paths.run_id)
    assert reloaded.status is RunStatus.SUCCEEDED
    assert json.loads(run_paths.manifest_path.read_text(encoding="utf-8"))


def test_read_manifest_round_trips_what_was_written(
    run_paths: storage.RunPaths,
) -> None:
    original = _manifest(run_paths.run_id, RunStatus.SUCCEEDED)
    storage.write_manifest(run_paths, original)

    reloaded = storage.read_manifest(run_paths.run_id)

    assert reloaded.run_id == original.run_id
    assert reloaded.action.id == original.action.id
    assert reloaded.status is RunStatus.SUCCEEDED


def test_read_manifest_raises_for_an_unknown_run(runs_dir: Path) -> None:
    with pytest.raises(UnknownRunError):
        storage.read_manifest(storage.new_run_id())


def test_read_manifest_raises_for_a_malformed_run_id(runs_dir: Path) -> None:
    with pytest.raises(UnknownRunError):
        storage.read_manifest("../../etc/passwd")


def test_read_manifest_raises_when_the_manifest_is_corrupt(
    run_paths: storage.RunPaths,
) -> None:
    run_paths.manifest_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(UnknownRunError):
        storage.read_manifest(run_paths.run_id)