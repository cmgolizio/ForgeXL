"""Storage service tests (build plan 3.1-3.3 and 6C.3-6C.4).

Covers Run directory creation and removal, generated filenames, reading an
upload into memory and the upload limit, plus the path-safety rules of build
plan section 16.

Two things have left this module as Phase 6 has progressed:

* Run *state* moved to the Run Store in Phase 6B. The tests that covered
  manifest writing and reading live in `tests/test_run_store.py`, against
  `create_run` / `get_run` / `update_run`.
* An uploaded spreadsheet stopped reaching the disk in Phase 6C. The tests
  that asserted a stored file now assert the in-memory payload that replaced
  it — and, where the old test proved a hostile name could not escape its
  directory, the replacement proves the stronger fact that no path is built
  at all.
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
    assert paths.working.is_dir()
    assert paths.exports.is_dir()
    assert paths.root.parent == runs_dir


def test_no_inputs_directory_is_created(runs_dir: Path) -> None:
    """Build plan 6C.3: uploads are read into memory, so nothing stores them."""
    paths = storage.create_run()

    assert not (paths.root / "inputs").exists()
    assert not hasattr(paths, "inputs")


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
def test_an_unsafe_slot_id_reaches_no_path_at_all(
    runs_dir: Path, unsafe: str
) -> None:
    """Since 6C a slot ID contributes to no path, so none can be traversed.

    The Phase 3 version of this test asserted that `input_directory` refused an
    unsafe slot ID. That guard is gone because the directory it guarded is
    gone; this asserts the stronger fact that replaced it — a hostile slot ID
    is carried as a dictionary key and writes nothing anywhere.
    """
    before = sorted(path.name for path in runs_dir.rglob("*"))

    received = storage.read_upload(unsafe, "sales.csv", io.BytesIO(b"a\n1\n"))

    assert received.slot_id == unsafe
    assert received.payload == b"a\n1\n"
    assert sorted(path.name for path in runs_dir.rglob("*")) == before


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
    ("filename", "expected"),
    [
        ("sales.csv", "sales.csv"),
        ("../../evil.csv", "evil.csv"),
        ("..\\..\\evil.csv", "evil.csv"),
        ("/absolute/path/data.csv", "data.csv"),
    ],
)
def test_display_filename_never_echoes_a_path(filename: str, expected: str) -> None:
    """A user-facing message must not repeat a path-shaped name back."""
    assert storage.display_filename(filename) == expected


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
def test_a_hostile_filename_becomes_a_generated_name_and_writes_nothing(
    runs_dir: Path, hostile: str
) -> None:
    received = storage.read_upload("source_file", hostile, io.BytesIO(b"a,b\n1,2\n"))

    # The generated name is what the application uses, not the client's.
    assert received.stored_filename == "source.csv"
    assert received.original_filename == hostile
    # Nothing was written under either name, anywhere.
    assert list(runs_dir.rglob("*")) == []
    assert not (runs_dir.parent / "evil.csv").exists()


def test_the_original_filename_is_preserved_as_metadata(runs_dir: Path) -> None:
    received = storage.read_upload(
        "source_file", "Q3 Sales (final).csv", io.BytesIO(b"a\n1\n")
    )

    assert received.original_filename == "Q3 Sales (final).csv"
    assert received.stored_filename == "source.csv"


def test_stored_filename_is_derived_from_the_extension_alone() -> None:
    assert storage.stored_filename_for(".csv") == "source.csv"
    assert storage.stored_filename_for(".xlsx") == "source.xlsx"
    assert storage.stored_filename_for("") == "source"


def test_the_upload_is_held_in_memory_byte_for_byte(runs_dir: Path) -> None:
    payload = "a,b\n1,café\n2,naïve\n".encode()

    received = storage.read_upload("source_file", "data.csv", io.BytesIO(payload))

    assert received.payload == payload
    assert received.size_bytes == len(payload)
    assert received.extension == ".csv"
    # Reading an upload writes nothing (build plan 6C.3).
    assert list(runs_dir.rglob("*")) == []


def test_the_size_is_counted_rather_than_trusted(runs_dir: Path) -> None:
    """A client-declared length is never used: the bytes received are counted."""
    payload = b"x" * 4096

    received = storage.read_upload("source_file", "d.csv", io.BytesIO(payload))

    assert received.size_bytes == 4096


def test_an_empty_upload_is_read_as_zero_bytes(runs_dir: Path) -> None:
    """Emptiness is the runner's finding to report; reading it is not an error."""
    received = storage.read_upload("source_file", "empty.csv", io.BytesIO(b""))

    assert received.payload == b""
    assert received.size_bytes == 0


def test_two_slots_are_read_independently(runs_dir: Path) -> None:
    first = storage.read_upload("current_sales", "a.csv", io.BytesIO(b"a\n1\n"))
    second = storage.read_upload("historical_sales", "b.csv", io.BytesIO(b"b\n2\n"))

    assert first.slot_id == "current_sales"
    assert second.slot_id == "historical_sales"
    assert first.payload == b"a\n1\n"
    assert second.payload == b"b\n2\n"


def test_an_upload_larger_than_one_chunk_is_read_whole(runs_dir: Path) -> None:
    """The chunked read must reassemble the payload exactly."""
    payload = bytes(range(256)) * 20_000  # ~5 MB, several 1 MiB chunks

    received = storage.read_upload("source_file", "big.csv", io.BytesIO(payload))

    assert received.payload == payload


# ---------------------------------------------------------------------------
# 3.3 Upload limit
# ---------------------------------------------------------------------------


def test_an_upload_at_the_limit_is_accepted(runs_dir: Path) -> None:
    payload = b"x" * 64

    received = storage.read_upload(
        "source_file", "d.csv", io.BytesIO(payload), max_bytes=64
    )

    assert received.size_bytes == 64


def test_an_oversized_upload_is_rejected(runs_dir: Path) -> None:
    with pytest.raises(UploadTooLargeError) as raised:
        storage.read_upload(
            "source_file", "big.csv", io.BytesIO(b"x" * 65), max_bytes=64
        )

    assert raised.value.http_status == 413
    assert raised.value.code == "FILE_TOO_LARGE"


def test_a_rejected_upload_is_not_retained_in_memory(runs_dir: Path) -> None:
    """The partial read is dropped before the error propagates."""
    with pytest.raises(UploadTooLargeError) as raised:
        storage.read_upload(
            "source_file",
            "big.csv",
            io.BytesIO(b"x" * (4 * 1024 * 1024)),
            max_bytes=1024,
        )

    assert "big.csv" in raised.value.message
    assert list(runs_dir.rglob("*")) == []


def test_the_limit_stops_the_read_before_the_whole_file_is_accumulated(
    runs_dir: Path,
) -> None:
    """The buffer must never grow past the limit (build plan 3.3 under 6C).

    A stream that counts what it served proves the read stopped early rather
    than accumulating 64 MB and checking afterwards.
    """

    class _CountingStream:
        def __init__(self, total: int) -> None:
            self.remaining = total
            self.served = 0

        def read(self, size: int = -1, /) -> bytes:
            if self.remaining <= 0:
                return b""
            count = self.remaining if size < 0 else min(size, self.remaining)
            self.remaining -= count
            self.served += count
            return b"x" * count

    limit = 2 * 1024 * 1024
    stream = _CountingStream(64 * 1024 * 1024)

    with pytest.raises(UploadTooLargeError):
        storage.read_upload("source_file", "big.csv", stream, max_bytes=limit)

    # At most one chunk beyond the limit was ever read.
    assert stream.served <= limit + (1024 * 1024)


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (250 * 1024 * 1024, "250 MB"),
        (1024 * 1024, "1 MB"),
        (8, "8 bytes"),
        (1, "1 byte"),
    ],
)
def test_the_limit_is_stated_in_readable_units(limit: int, expected: str) -> None:
    """A sub-megabyte limit must not round down to a meaningless '0 MB'."""
    with pytest.raises(UploadTooLargeError) as raised:
        storage.read_upload(
            "source_file",
            "big.csv",
            io.BytesIO(b"x" * (limit + 1)),
            max_bytes=limit,
        )

    assert f"{expected} upload limit" in raised.value.message


def test_the_limit_defaults_to_the_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)

    with pytest.raises(UploadTooLargeError):
        storage.read_upload("source_file", "d.csv", io.BytesIO(b"x" * 9))


def test_the_error_message_never_echoes_a_path_shaped_name() -> None:
    with pytest.raises(UploadTooLargeError) as raised:
        storage.read_upload(
            "source_file",
            "../../secret/big.csv",
            io.BytesIO(b"x" * 9),
            max_bytes=8,
        )

    assert "big.csv is larger" in raised.value.message
    assert "../.." not in raised.value.message


# ---------------------------------------------------------------------------
# 6B.6 Releasing a Run's files
# ---------------------------------------------------------------------------


def test_delete_run_directory_removes_the_whole_run(runs_dir: Path) -> None:
    paths = storage.create_run()
    (paths.exports / "result.csv").write_bytes(b"a\n1\n")

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