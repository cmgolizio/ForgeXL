"""Hostile client-supplied strings, over the real API (build plan 7D, 7E).

Two build-plan subphases, one subject: every string in this module arrives from
the client, and none of it may be trusted with anything.

**7D — filename security.** The names the build plan lists:

    ../../example.csv
    ../data.csv
    strange name.csv
    file (1).csv
    café.csv

with the requirement: *verify no path traversal is possible*. ForgeXL's answer
is stronger than sanitising the name, and this module is written to say so:
since Phase 6C an upload is never written to the filesystem at all, so there is
no path for a name to traverse. The name is metadata — recorded in the manifest
exactly as sent, so a user recognises their own file — and the application
knows the upload only as a generated `source<ext>`. The tests therefore assert
two things of every hostile name: the Run behaves normally, and nothing
appeared anywhere on disk.

`tests/test_storage.py` already proves this of the storage service in
isolation. What is added here is the whole request path, because a guarantee
that holds in a unit and not through the API is not a guarantee.

**7E — invalid IDs.** Malformed Run IDs, output IDs and Action IDs, with the
requirement: *ensure controlled 4xx responses*. "Controlled" is read strictly:
a 4xx status, a JSON body, no traceback and no server path anywhere in it. A
500 would be a defect even if nothing were leaked, because the request was
never valid enough to reach anything that could fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import ProductMasterBuilderAction

from tests.fixtures import spreadsheets as fx
from tests.helpers import upload_file

DEDUPE = ExactDuplicateRemoverAction.id
PRODUCT_MASTER = ProductMasterBuilderAction.id
DEDUPE_OUTPUT = "deduplicated_data"

#: A syntactically valid UUID naming no Run.
ABSENT_RUN_ID = "00000000-0000-4000-8000-000000000000"

#: The five names build plan 7D lists, plus the shapes they generalise to:
#: Windows separators, an absolute path, a URL-encoded traversal, a name that
#: is nothing but traversal, and one carrying a NUL byte's textual form.
HOSTILE_FILENAMES = (
    "../../example.csv",
    "../data.csv",
    "strange name.csv",
    "file (1).csv",
    "café.csv",
    "..\\..\\windows.csv",
    "/etc/passwd.csv",
    "%2e%2e%2fescaped.csv",
    "....//....//deep.csv",
    "sub/dir/nested.csv",
    "..csv",
    "a" * 300 + ".csv",
    "Ünïcode Ñame (final) v2.csv",
)


def _upload(client, filename: str, payload: bytes | None = None):
    return client.post(
        "/api/runs",
        data={"action_id": DEDUPE},
        files={
            "source_file": upload_file(
                filename, payload if payload is not None else fx.SIMPLE_TABLE.as_csv()
            )
        },
    )


def _has_no_server_path(body: str) -> bool:
    """Whether `body` names nothing that looks like a location on this machine.

    Build plan 6F.8 and section 11: the browser receives logical IDs, never
    filesystem paths. Checked against this machine's real prefixes rather than
    against a guess at what a path looks like.
    """
    return not any(
        fragment in body
        for fragment in (
            str(Path.cwd()),
            "/home/",
            "/tmp/",
            "/var/",
            "/usr/",
            "Traceback",
            "site-packages",
        )
    )


# ---------------------------------------------------------------------------
# 7D — filenames
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", HOSTILE_FILENAMES)
def test_a_hostile_filename_runs_normally_and_reaches_no_path(
    client, quarantine: Path, filename: str
) -> None:
    """The Run succeeds, and the name never became a location.

    Succeeding is the right outcome: the *data* is fine, and refusing it would
    punish a user whose file is called `file (1).csv`. What must not happen is
    the name being used for anything, and that is what the assertions check.
    """
    response = _upload(client, filename)

    assert response.status_code == 200, response.text
    manifest = response.json()
    recorded = manifest["inputs"][0]

    # The application's own name for the upload, derived from the extension
    # alone (build plan 3.2).
    assert recorded["stored_filename"] == "source.csv"
    # The client's name is kept verbatim, as metadata and nothing else.
    assert recorded["original_filename"] == filename
    # Nothing was written, under either name, anywhere the test can observe.
    assert list(quarantine.rglob("*")) == []


@pytest.mark.parametrize("filename", HOSTILE_FILENAMES)
def test_a_hostile_filename_creates_no_file_beside_the_working_directory(
    client, quarantine: Path, filename: str
) -> None:
    """A traversal that escaped would land *outside* the quarantine.

    Checking only the quarantine would miss the failure the name is aiming
    for, so its parent is checked too: `../../example.csv` resolved against
    the working directory would surface there.
    """
    before = sorted(path.name for path in quarantine.parent.iterdir())

    assert _upload(client, filename).status_code == 200

    assert sorted(path.name for path in quarantine.parent.iterdir()) == before


def test_a_hostile_filename_in_an_error_message_is_reduced_to_its_basename(
    client,
) -> None:
    """A refusal must not echo a path-shaped name back at the user.

    `.exe` is not an accepted extension, so this is the path where the
    filename reaches a message. What comes back is the basename alone.
    """
    response = _upload(client, "../../../etc/passwd.exe")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED_EXTENSION"
    assert "passwd.exe" in error["message"]
    assert "../" not in error["message"]
    assert "/etc/" not in error["message"]


def test_a_download_filename_is_never_built_from_the_uploaded_name(
    client,
) -> None:
    """Build plan 6F.6: the download is named from the Run, not from the upload.

    So a name carrying quotes or a traversal cannot reach the
    `Content-Disposition` header, where it would be a header-injection or a
    save-path problem rather than only an ugly filename.
    """
    response = _upload(client, '../../"evil";name.csv')
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]

    download = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/csv"
    )

    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert disposition == (
        f'attachment; filename="{_expected_download_name(client, run_id)}"'
    )
    assert "evil" not in disposition
    assert '"evil"' not in disposition
    assert "../" not in disposition


def _expected_download_name(client, run_id: str) -> str:
    from app.services import export, run_store

    run = run_store.get_run(run_id)
    return export.download_filename(
        action_id=run.action.id,
        output_id=DEDUPE_OUTPUT,
        extension="csv",
        timestamp=run.completed_at or run.created_at,
    )


@pytest.mark.parametrize(
    "extension", [".exe", ".sh", ".xlsm", ".xlsb", ".xls", ".ods", ".json", ".parquet"]
)
def test_an_extension_the_build_plan_rejects_is_rejected(
    client, extension: str
) -> None:
    """Build plan section 16 lists these explicitly as not supported.

    The macro-bearing workbook formats are the ones that matter most: refusing
    `.xlsm` at the door is why no macro can ever be reached, let alone run.
    """
    response = _upload(client, f"data{extension}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSUPPORTED_EXTENSION"


def test_a_csv_extension_does_not_make_arbitrary_bytes_a_csv(client) -> None:
    """Build plan 6C.5: the name chooses the reader, never the outcome."""
    response = _upload(client, "actually-binary.csv", bytes(range(256)))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PARSE_ERROR"


def test_a_slot_id_that_looks_like_a_path_is_ignored_with_a_warning(
    client,
) -> None:
    """A field name is an Action's declared slot ID or it is nothing.

    An undeclared field cannot select an input, so a path-shaped one is
    reported as unused rather than resolved to anything.
    """
    response = client.post(
        "/api/runs",
        data={"action_id": DEDUPE},
        files={
            "source_file": upload_file("ok.csv", fx.SIMPLE_TABLE.as_csv()),
            "../../etc/passwd": upload_file("x.csv", b"a\n1\n"),
        },
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == ["UNEXPECTED_INPUT"]
    assert warnings[0]["details"]["unexpected_slot_ids"] == ["../../etc/passwd"]


# ---------------------------------------------------------------------------
# 7E — invalid Run IDs
# ---------------------------------------------------------------------------

MALFORMED_RUN_IDS = (
    "not-a-uuid",
    "1",
    "null",
    "undefined",
    "  ",
    "4f27d4bb-7464-4d04-a21b",
    "4f27d4bb74644d04a21b000000000000",
    "00000000-0000-0000-0000-00000000000g",
    "'; DROP TABLE runs; --",
    "a" * 5000,
    "%00",
)


@pytest.mark.parametrize("run_id", MALFORMED_RUN_IDS)
def test_a_malformed_run_id_is_a_structured_404(client, run_id: str) -> None:
    """Build plan 7E: a controlled 4xx, in the documented error shape."""
    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_RUN"
    assert _has_no_server_path(response.text)


@pytest.mark.parametrize("run_id", MALFORMED_RUN_IDS)
@pytest.mark.parametrize(
    "suffix",
    [
        f"/outputs/{DEDUPE_OUTPUT}/preview",
        f"/outputs/{DEDUPE_OUTPUT}/download/csv",
        f"/outputs/{DEDUPE_OUTPUT}/download/xlsx",
        "/download/xlsx",
    ],
)
def test_every_run_route_refuses_a_malformed_id_the_same_way(
    client, run_id: str, suffix: str
) -> None:
    """One bad ID must not be answered differently by different routes."""
    response = client.get(f"/api/runs/{run_id}{suffix}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


@pytest.mark.parametrize(
    "run_id",
    [
        "../../etc/passwd",
        "..",
        "a/b/c",
        # Contains a separator, so it is the same case: the URL matches no
        # route rather than reaching the handler with a bad ID.
        "<script>alert(1)</script>",
        "%2e%2e%2f",
    ],
)
def test_a_traversal_shaped_run_id_never_reaches_a_route(
    client, run_id: str
) -> None:
    """A separator makes the URL match no route at all, which is also a 4xx.

    Answered by the framework rather than by ForgeXL's own handler, so the body
    is the framework's. That is still controlled, still 4xx, and still carries
    no path — which is what build plan 7E asks for. `src/lib/api.js` renders a
    body without ForgeXL's `error` object as a readable message rather than as
    `[object Object]`.
    """
    response = client.get(f"/api/runs/{run_id}")

    assert 400 <= response.status_code < 500
    assert _has_no_server_path(response.text)


def test_a_valid_but_unissued_run_id_is_a_clean_404(client) -> None:
    """Well-formed and unknown is a different fact from malformed."""
    response = client.get(f"/api/runs/{ABSENT_RUN_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


# ---------------------------------------------------------------------------
# 7E — invalid output IDs
# ---------------------------------------------------------------------------


@pytest.fixture
def run_id(client) -> str:
    """One successful Run, so output IDs are tested against a real result."""
    response = client.post(
        "/api/runs",
        data={"action_id": DEDUPE},
        files={"source_file": upload_file("data.csv", fx.SIMPLE_TABLE.as_csv())},
    )
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


@pytest.mark.parametrize(
    "output_id",
    [
        "nope",
        "deduplicated_data.csv",
        "deduplicated_data ",
        "DEDUPLICATED_DATA",
        "%00",
        "'; DROP TABLE outputs; --",
        "a" * 500,
    ],
)
@pytest.mark.parametrize(
    "suffix", ["/preview", "/download/csv", "/download/xlsx"]
)
def test_an_unknown_output_id_is_a_structured_404(
    client, run_id: str, output_id: str, suffix: str
) -> None:
    """Matched against what the Run recorded, so it never reaches anything."""
    response = client.get(f"/api/runs/{run_id}/outputs/{output_id}{suffix}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_OUTPUT"
    assert error["details"]["available_output_ids"] == [DEDUPE_OUTPUT]
    assert _has_no_server_path(response.text)


@pytest.mark.parametrize("output_id", ["..", "../../manifest", "a/b"])
def test_a_traversal_shaped_output_id_never_reaches_a_route(
    client, run_id: str, output_id: str
) -> None:
    response = client.get(f"/api/runs/{run_id}/outputs/{output_id}/preview")

    assert 400 <= response.status_code < 500
    assert _has_no_server_path(response.text)


def test_an_unoffered_download_format_is_refused(client, run_id: str) -> None:
    """A format outside the route table matches no route.

    The two formats ForgeXL generates each have their own route, so `parquet`
    is not a value that reaches a handler and gets validated — it is a URL
    that does not exist.
    """
    response = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/parquet"
    )

    assert 400 <= response.status_code < 500


# ---------------------------------------------------------------------------
# 7E — invalid Action IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action_id",
    [
        "nope",
        "EXACT_DUPLICATE_REMOVER",
        "exact_duplicate",
        "../exact_duplicate_remover",
        "exact_duplicate_remover; rm -rf /",
        "'; DROP TABLE actions; --",
        "$(whoami)",
        "`id`",
        "a" * 500,
        "<script>alert(1)</script>",
    ],
)
def test_an_unknown_action_id_is_a_structured_404(client, action_id: str) -> None:
    """Build plan section 25: the registry is a lookup, never a near match.

    Several of these are shell metacharacters. Build plan section 16 forbids
    constructing a shell command from an Action ID, and nothing does — the ID
    is a dictionary key and a miss is a miss.
    """
    response = client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("data.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_ACTION"
    assert error["details"]["action_id"] == action_id.strip()
    assert _has_no_server_path(response.text)


@pytest.mark.parametrize("action_id", ["", "   ", "\t\n"])
def test_a_blank_action_id_is_a_structured_400(client, action_id: str) -> None:
    """Blank is a malformed request, not an unknown Action (build plan 22)."""
    response = client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("data.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_an_absent_action_id_is_a_structured_400(client) -> None:
    response = client.post(
        "/api/runs",
        files={"source_file": upload_file("data.csv", fx.SIMPLE_TABLE.as_csv())},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_request_that_is_not_multipart_at_all_is_a_structured_400(
    client,
) -> None:
    """A JSON body where a form is expected is malformed, not a server error."""
    response = client.post("/api/runs", json={"action_id": DEDUPE})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# 7E — paging parameters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected_status"),
    [
        ({"offset": -1}, 400),
        ({"limit": 0}, 400),
        ({"limit": -5}, 400),
        ({"limit": 501}, 400),
        ({"limit": 10**9}, 400),
        ({"limit": "abc"}, 422),
        ({"offset": "abc"}, 422),
        ({"limit": 1.5}, 422),
    ],
)
def test_out_of_range_paging_is_a_controlled_4xx(
    client, run_id: str, query: dict, expected_status: int
) -> None:
    """Refused rather than clamped, so a caller knows what it received.

    The 400s are ForgeXL's own rule (build plan 3.15). The 422s are the
    framework refusing a value that is not an integer before it reaches that
    rule; both are controlled, and neither carries a traceback.
    """
    response = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/preview", params=query
    )

    assert response.status_code == expected_status
    assert _has_no_server_path(response.text)


def test_an_offset_past_the_end_is_an_empty_page_not_an_error(
    client, run_id: str
) -> None:
    """Asking beyond the last row is a legitimate question with an answer."""
    response = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/preview",
        params={"offset": 10**6},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["total_rows"] == fx.SIMPLE_TABLE.row_count


# ---------------------------------------------------------------------------
# Nothing anywhere leaks the server's own shape
# ---------------------------------------------------------------------------


def test_no_refusal_in_this_module_returns_a_5xx(client) -> None:
    """The summary claim of 7E, made once against a sweep of the whole set.

    An invalid identifier is refused before it reaches anything that could
    fail, so a 5xx here would mean something ran that should not have.
    """
    probes = [
        ("GET", f"/api/runs/{bad}") for bad in MALFORMED_RUN_IDS
    ] + [
        ("GET", f"/api/runs/{ABSENT_RUN_ID}/outputs/nope/preview"),
        ("GET", f"/api/runs/{ABSENT_RUN_ID}/download/xlsx"),
    ]

    for method, path in probes:
        response = client.request(method, path)
        assert response.status_code < 500, f"{path} -> {response.status_code}"
        assert _has_no_server_path(response.text), path


def test_an_action_that_crashes_reports_no_internal_detail(client) -> None:
    """Build plan section 22: a traceback is logged locally, never returned.

    The one 500 ForgeXL raises deliberately, checked for what it does *not*
    say. Its cause carries a filesystem path on purpose.
    """
    from collections.abc import Mapping

    import polars as pl

    from app.actions import registry as registry_module
    from app.actions.base import Action, ActionResult
    from app.actions.registry import ActionRegistry
    from app.models.schemas import ActionInput, ActionOutput

    class _Raising(Action):
        id = "always_raises"
        version = "1.0.0"
        name = "Always Raises"
        description = "Raises while processing."
        inputs = (
            ActionInput(
                id="source_file",
                label="Source File",
                accepted_extensions=(".csv",),
            ),
        )
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            raise RuntimeError("secret detail /home/someone/private/data.csv")

    original = registry_module.ACTION_REGISTRY
    registry_module.ACTION_REGISTRY = ActionRegistry((_Raising(),))
    try:
        response = client.post(
            "/api/runs",
            data={"action_id": "always_raises"},
            files={"source_file": upload_file("d.csv", fx.SIMPLE_TABLE.as_csv())},
        )
    finally:
        registry_module.ACTION_REGISTRY = original

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "ACTION_FAILED"
    assert "secret detail" not in response.text
    assert _has_no_server_path(response.text)