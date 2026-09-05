"""Named input slots for multi-file Actions (build plan 6H.5).

Build plan 6H.5 asks that, for an Action taking more than one file, "swapping
input files or omitting a required input produces correct validation
behavior". Both are exercised here against the real HTTP API, using the
synthetic fixtures.

The property under test is that a slot is addressed **by name**, never by
position or by upload order. That is what lets the frontend build one upload
control per declared slot with no Action-specific code (build plan §3.2), and
it is what makes swapping two files a detectable mistake rather than a silent
one: the Action receives the wrong dataset in each slot, and the schema each
slot declares is what catches it.

No real Action declares two inputs yet, so the Actions here are local to this
module — the same approach `test_export_download.py` takes for multi-output
behaviour. They carry no transformation of their own: each returns the frames
it was given, so the assertions are about routing, not about arithmetic.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput

from tests.fixtures import spreadsheets as fx
from tests.helpers import normalise_rows, upload_file

#: The fixture belonging in each slot. Their schemas do not overlap, which is
#: what makes a swap detectable rather than merely wrong.
PRIMARY_FIXTURE = fx.SIMPLE_TABLE  # has "Region", has no "SKU"
SECONDARY_FIXTURE = fx.DUPLICATE_KEYS  # has "SKU", has no "Region"


class _TwoSlotAction(Action):
    """Two required inputs, each returned as its own output.

    Returning each slot's frame unchanged makes the routing visible: if the
    files were swapped and still accepted, the outputs would hold each other's
    rows.
    """

    id = "two_slot_probe"
    version = "1.0.0"
    name = "Two Slot Probe"
    description = "Returns each named input as its own result table."
    inputs = (
        ActionInput(
            id="primary_file",
            label="Primary File",
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=("Region",),
        ),
        ActionInput(
            id="secondary_file",
            label="Secondary File",
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=("SKU",),
        ),
    )
    outputs = (
        ActionOutput(id="primary_rows", label="Primary Rows"),
        ActionOutput(id="secondary_rows", label="Secondary Rows"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(
            outputs={
                "primary_rows": inputs["primary_file"],
                "secondary_rows": inputs["secondary_file"],
            },
            metrics={
                "primary_rows": inputs["primary_file"].height,
                "secondary_rows": inputs["secondary_file"].height,
            },
        )


class _OptionalSecondSlotAction(Action):
    """One required input and one optional one.

    An omitted optional slot must not fail the Run, and must not silently
    produce an empty second table either — the Action decides what to do with
    the slot it did not receive.
    """

    id = "optional_slot_probe"
    version = "1.0.0"
    name = "Optional Slot Probe"
    description = "Returns the required input, noting whether the optional came."
    inputs = (
        ActionInput(
            id="primary_file",
            label="Primary File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
        ActionInput(
            id="extra_file",
            label="Extra File",
            required=False,
            accepted_extensions=(".csv", ".xlsx"),
        ),
    )
    outputs = (ActionOutput(id="combined", label="Combined"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(
            outputs={"combined": inputs["primary_file"]},
            metrics={"slots_received": sorted(inputs)},
        )


@pytest.fixture
def slots_client(client, registered_actions):
    registered_actions(_TwoSlotAction(), _OptionalSecondSlotAction())
    return client


def _submit(client, action_id: str, files: dict):
    return client.post("/api/runs", data={"action_id": action_id}, files=files)


def _sole_run_manifest(client, run_store) -> dict:
    """The manifest of the only Run this test made.

    A refused request answers with the failure, not with a Run ID, so a test
    that needs the failed Run's *record* asks the store which Run the request
    created. Each test gets its own store (`conftest.run_store`), so there is
    exactly one.
    """
    runs = run_store.list_runs()
    assert len(runs) == 1, [run.run_id for run in runs]

    response = client.get(f"/api/runs/{runs[0].run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _file(table: fx.Table, extension: str = fx.CSV_EXTENSION):
    return upload_file(table.filename(extension), table.payload(extension))


def _issues_by_slot(body: dict) -> dict[str, str]:
    """``{slot id: error code}`` from either shape of a validation body.

    A Run stopped by exactly one issue reports that issue directly, so the
    code is the body's own and the slot is in `details`; several issues are
    reported together under `details.issues` (build plan section 22, and
    `app.errors.RunValidationError`). Reading both here keeps every assertion
    below about the *slots that failed* rather than about which shape the
    response happened to take.
    """
    error = body["error"]
    details = error["details"]
    if "issues" in details:
        return {issue["slot_id"]: issue["code"] for issue in details["issues"]}
    return {details["slot_id"]: error["code"]}


# ---------------------------------------------------------------------------
# Each slot receives its own file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_each_named_slot_receives_its_own_file(slots_client, extension: str) -> None:
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE, extension),
            "secondary_file": _file(SECONDARY_FIXTURE, extension),
        },
    )

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["metrics"] == {
        "primary_rows": PRIMARY_FIXTURE.row_count,
        "secondary_rows": SECONDARY_FIXTURE.row_count,
    }

    outputs = {output["id"]: output for output in manifest["outputs"]}
    assert outputs["primary_rows"]["columns"] == list(PRIMARY_FIXTURE.header)
    assert outputs["secondary_rows"]["columns"] == list(SECONDARY_FIXTURE.header)


def test_each_slot_is_recorded_under_its_own_name_and_filename(
    slots_client,
) -> None:
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "secondary_file": _file(SECONDARY_FIXTURE),
        },
    )

    inputs = {item["slot_id"]: item for item in response.json()["inputs"]}
    assert inputs["primary_file"]["original_filename"] == PRIMARY_FIXTURE.filename(
        fx.CSV_EXTENSION
    )
    assert inputs["secondary_file"]["original_filename"] == (
        SECONDARY_FIXTURE.filename(fx.CSV_EXTENSION)
    )
    assert inputs["primary_file"]["row_count"] == PRIMARY_FIXTURE.row_count
    assert inputs["secondary_file"]["row_count"] == SECONDARY_FIXTURE.row_count


def test_each_slots_data_reaches_its_own_result_table(slots_client) -> None:
    """The rows themselves, not only the counts, arrive in the right table."""
    manifest = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "secondary_file": _file(SECONDARY_FIXTURE),
        },
    ).json()
    run_id = manifest["run_id"]

    primary = slots_client.get(
        f"/api/runs/{run_id}/outputs/primary_rows/preview"
    ).json()
    secondary = slots_client.get(
        f"/api/runs/{run_id}/outputs/secondary_rows/preview"
    ).json()

    assert normalise_rows(primary["rows"]) == normalise_rows(PRIMARY_FIXTURE.rows)
    assert normalise_rows(secondary["rows"]) == normalise_rows(
        SECONDARY_FIXTURE.rows
    )


def test_the_order_the_files_are_submitted_in_does_not_matter(
    slots_client,
) -> None:
    """Slots are addressed by name, so multipart field order is irrelevant."""
    in_order = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "secondary_file": _file(SECONDARY_FIXTURE),
        },
    ).json()
    reversed_order = _submit(
        slots_client,
        "two_slot_probe",
        {
            "secondary_file": _file(SECONDARY_FIXTURE),
            "primary_file": _file(PRIMARY_FIXTURE),
        },
    ).json()

    assert in_order["metrics"] == reversed_order["metrics"]
    assert [output["row_count"] for output in in_order["outputs"]] == [
        output["row_count"] for output in reversed_order["outputs"]
    ]


# ---------------------------------------------------------------------------
# 6H.5 Swapping the files between slots
# ---------------------------------------------------------------------------


def test_swapping_the_files_between_slots_is_refused(slots_client) -> None:
    """The schemas do not match once swapped, and both slots say so.

    This is the case the whole named-slot design exists to catch: the upload
    is well-formed and both files are valid spreadsheets, but each is in the
    wrong slot. Nothing guesses; each slot reports its own missing columns.
    """
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(SECONDARY_FIXTURE),
            "secondary_file": _file(PRIMARY_FIXTURE),
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert _issues_by_slot(body) == {
        "primary_file": "MISSING_COLUMNS",
        "secondary_file": "MISSING_COLUMNS",
    }


def test_a_swap_names_the_column_each_slot_actually_wanted(slots_client) -> None:
    body = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(SECONDARY_FIXTURE),
            "secondary_file": _file(PRIMARY_FIXTURE),
        },
    ).json()

    issues = {issue["slot_id"]: issue for issue in body["error"]["details"]["issues"]}
    assert issues["primary_file"]["details"]["missing_columns"] == ["Region"]
    assert issues["secondary_file"]["details"]["missing_columns"] == ["SKU"]


def test_the_same_file_in_both_slots_is_judged_against_each_slots_schema(
    slots_client,
) -> None:
    """One file, two slots, two independent verdicts.

    `EXTRA_COLUMNS` carries `SKU` but not `Region`, so it satisfies the
    secondary slot and fails the primary one. Only the primary slot is
    reported: a slot's rules are the slot's, not the request's.
    """
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(fx.EXTRA_COLUMNS),
            "secondary_file": _file(fx.EXTRA_COLUMNS),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {"primary_file": "MISSING_COLUMNS"}


# ---------------------------------------------------------------------------
# 6H.5 Omitting an input
# ---------------------------------------------------------------------------


def test_omitting_a_required_slot_is_refused_and_names_it(slots_client) -> None:
    response = _submit(
        slots_client, "two_slot_probe", {"primary_file": _file(PRIMARY_FIXTURE)}
    )

    assert response.status_code == 422
    body = response.json()
    assert _issues_by_slot(body) == {"secondary_file": "MISSING_INPUT"}
    assert body["error"]["message"] == "Secondary File is required."
    assert body["error"]["details"]["label"] == "Secondary File"


def test_omitting_both_required_slots_reports_both_at_once(slots_client) -> None:
    """One request, every problem — not one refusal per attempt."""
    response = _submit(slots_client, "two_slot_probe", {})

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {
        "primary_file": "MISSING_INPUT",
        "secondary_file": "MISSING_INPUT",
    }


def test_an_empty_file_field_counts_as_an_omitted_slot(slots_client) -> None:
    """A file control submitted with nothing chosen sends an empty filename."""
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "secondary_file": ("", b""),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {"secondary_file": "MISSING_INPUT"}


def test_omitting_an_optional_slot_succeeds(slots_client) -> None:
    response = _submit(
        slots_client,
        "optional_slot_probe",
        {"primary_file": _file(PRIMARY_FIXTURE)},
    )

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["status"] == "succeeded"
    assert manifest["metrics"]["slots_received"] == ["primary_file"]
    assert [item["slot_id"] for item in manifest["inputs"]] == ["primary_file"]


def test_supplying_an_optional_slot_delivers_it(slots_client) -> None:
    response = _submit(
        slots_client,
        "optional_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "extra_file": _file(SECONDARY_FIXTURE),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["metrics"]["slots_received"] == [
        "extra_file",
        "primary_file",
    ]


def test_a_file_sent_under_an_undeclared_slot_warns_without_failing(
    slots_client,
) -> None:
    """A frontend/backend mismatch is surfaced, not hidden and not fatal."""
    response = _submit(
        slots_client,
        "optional_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "mystery_file": _file(SECONDARY_FIXTURE),
        },
    )

    assert response.status_code == 200, response.text
    manifest = response.json()
    warnings = manifest["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == ["UNEXPECTED_INPUT"]
    assert warnings[0]["details"]["unexpected_slot_ids"] == ["mystery_file"]
    assert [item["slot_id"] for item in manifest["inputs"]] == ["primary_file"]


def test_an_undeclared_slot_is_still_reported_when_the_run_fails(
    slots_client, run_store
) -> None:
    """The warning survives the failure and is on the Run's own record.

    The 422 body reports the *failure*; the warning is on the manifest, which
    is fetched from the Run the request created.
    """
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "mystery_file": _file(SECONDARY_FIXTURE),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {"secondary_file": "MISSING_INPUT"}

    manifest = _sole_run_manifest(slots_client, run_store)
    assert manifest["status"] == "failed"
    warnings = manifest["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == ["UNEXPECTED_INPUT"]
    assert warnings[0]["details"]["unexpected_slot_ids"] == ["mystery_file"]


# ---------------------------------------------------------------------------
# Per-slot rules apply to the slot, not to the request
# ---------------------------------------------------------------------------


def test_one_bad_slot_does_not_hide_a_good_one(slots_client, run_store) -> None:
    """A valid slot is still recorded when another slot fails validation.

    Build plan 3.9: a failed Run keeps its evidence, including what it did
    successfully receive.
    """
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(PRIMARY_FIXTURE),
            "secondary_file": upload_file("notes.txt", b"not a spreadsheet"),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {
        "secondary_file": "UNSUPPORTED_EXTENSION"
    }

    manifest = _sole_run_manifest(slots_client, run_store)
    assert [item["slot_id"] for item in manifest["inputs"]] == ["primary_file"]
    assert manifest["inputs"][0]["row_count"] == PRIMARY_FIXTURE.row_count


def test_problems_in_both_slots_are_reported_together(slots_client) -> None:
    """One request reports every slot's problem, not one per attempt."""
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": upload_file("notes.txt", b"not a spreadsheet"),
            "secondary_file": upload_file("empty.csv", b""),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {
        "primary_file": "UNSUPPORTED_EXTENSION",
        "secondary_file": "EMPTY_FILE",
    }


def test_a_slots_data_is_only_checked_once_every_file_is_readable(
    slots_client,
) -> None:
    """Dataset checks wait until every slot has produced a usable file.

    A file the runner could not even accept has no columns to compare, so an
    unreadable slot short-circuits the column and emptiness checks rather than
    reporting a second, derived complaint about a file nobody could read. The
    secondary file here is missing `SKU` *and* holds no rows; neither is
    reported while the primary slot is still unusable.
    """
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": upload_file("notes.txt", b"not a spreadsheet"),
            "secondary_file": _file(fx.HEADER_ONLY),
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {
        "primary_file": "UNSUPPORTED_EXTENSION"
    }


def test_once_every_file_is_readable_both_slots_data_is_checked(
    slots_client,
) -> None:
    """The other half of the rule above: readable files are all checked."""
    response = _submit(
        slots_client,
        "two_slot_probe",
        {
            "primary_file": _file(fx.HEADER_ONLY),  # has Region, but no rows
            "secondary_file": _file(PRIMARY_FIXTURE),  # has rows, but no SKU
        },
    )

    assert response.status_code == 422
    assert _issues_by_slot(response.json()) == {
        "primary_file": "EMPTY_DATASET",
        "secondary_file": "MISSING_COLUMNS",
    }