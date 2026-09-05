"""Exercise the HTTP intake before the runner, including actual spool limits."""

from __future__ import annotations

import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from starlette.formparsers import MultiPartParser
from starlette.requests import ClientDisconnect, Request

from app import config
from app.api.upload_form import read_run_form
from app.errors import UploadTooLargeError
from tests.helpers import make_action


@pytest.fixture
def intake_client(client, registered_actions):
    registered_actions(make_action("intake"))
    return client


@pytest.fixture
def memory_files(monkeypatch):
    """Make any actual temporary-file creation fail, and track buffer cleanup."""
    files = []
    original = tempfile.SpooledTemporaryFile

    def spool(*args, **kwargs):
        file = original(*args, **kwargs)
        files.append(file)
        return file

    def disk_unavailable(*args, **kwargs):
        pytest.fail("Upload intake attempted to write a temporary file")

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", spool)
    monkeypatch.setattr(tempfile, "TemporaryFile", disk_unavailable)
    yield files
    assert all(file.closed for file in files), "An upload buffer was left open"


def _submit(client, payload, **kwargs):
    return client.post(
        "/api/runs", data={"action_id": "intake"},
        files={"source_file": ("data.csv", payload)}, **kwargs,
    )


def test_a_file_above_starlette_spool_size_never_uses_disk(
    intake_client, memory_files,
):
    payload = b"Value\n" + b"1234567890\n" * 100_000
    assert len(payload) > MultiPartParser.spool_max_size
    response = _submit(intake_client, payload)
    assert response.status_code == 200, response.text
    assert response.json()["outputs"][0]["row_count"] == 100_000
    assert len(memory_files) == 1


@pytest.mark.parametrize("delta, status", [(0, 200), (1, 413)])
def test_the_file_limit_is_enforced_at_the_exact_byte(
    intake_client, memory_files, monkeypatch, delta, status,
):
    payload = b"Value\n10\n"
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", len(payload) - delta)
    response = _submit(intake_client, payload)
    assert response.status_code == status, response.text
    if status == 413:
        error = response.json()["error"]
        assert error["code"] == "FILE_TOO_LARGE"
        assert error["details"]["slot_id"] == "source_file"


@pytest.mark.parametrize("field", ["source_file", "action_id"])
def test_duplicate_fields_are_rejected_without_silently_choosing_one(
    intake_client, memory_files, field,
):
    parts = [
        ("action_id", (None, "intake")),
        ("source_file", ("first.csv", b"Value\n10\n")),
        (field, ("second.csv", b"Value\n20\n") if field == "source_file"
         else (None, "intake")),
    ]
    response = intake_client.post("/api/runs", files=parts)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_duplicate_urlencoded_action_ids_are_also_rejected(intake_client):
    response = intake_client.post(
        "/api/runs", content="action_id=intake&action_id=intake",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400


def _file_part():
    return (
        b'--boundary\r\nContent-Disposition: form-data; name="source_file"; '
        b'filename="data.csv"\r\nContent-Type: text/csv\r\n\r\n'
    )


@pytest.mark.parametrize("ending", [b"", b"\r\n--boundary\r\n"])
def test_truncated_multipart_is_not_accepted(intake_client, memory_files, ending):
    response = intake_client.post(
        "/api/runs", content=_file_part() + b"Value\n10\n" + ending,
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_multipart_boundary_has_the_structured_error_shape(intake_client):
    response = intake_client.post(
        "/api/runs", content=b"broken", headers={"content-type": "multipart/form-data"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("failure", ["limit", "disconnect"])
def test_stream_failure_closes_partial_buffers_and_stops_receiving(
    memory_files, monkeypatch, failure,
):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 8)
    events = [
        {"type": "http.request", "body": _file_part() + b"1234", "more_body": True},
        ({"type": "http.disconnect"} if failure == "disconnect" else
         {"type": "http.request", "body": b"56789", "more_body": True}),
    ]

    async def receive():
        assert events, "Intake kept reading after the failure"
        return events.pop(0)

    request = Request({
        "type": "http", "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
    }, receive)

    async def parse():
        async with read_run_form(request):
            pytest.fail("Incomplete upload reached the runner")

    with pytest.raises(ClientDisconnect if failure == "disconnect" else UploadTooLargeError):
        asyncio.run(parse())
    assert not events
    assert memory_files and all(file.closed for file in memory_files)


def test_buffers_close_when_the_action_fails(intake_client, memory_files, monkeypatch):
    from app.api import runs

    def fail(*args):
        from app.errors import ActionExecutionError
        raise ActionExecutionError("Expected failure")

    monkeypatch.setattr(runs, "execute_run", fail)
    response = _submit(intake_client, b"Value\n10\n")
    assert response.status_code == 500


def test_health_stays_responsive_while_an_action_is_running(client, registered_actions):
    started, release = Event(), Event()
    action = make_action("intake")
    original_run = action.run

    def run(inputs):
        started.set()
        assert release.wait(5), "Test did not release the Action"
        return original_run(inputs)

    action.run = run
    registered_actions(action)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_submit, client, b"Value\n10\n")
        try:
            assert started.wait(3)
            health = pool.submit(client.get, "/health")
            assert health.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 200