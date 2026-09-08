"""Phase 11 — library-backed Action inputs and reproducible Runs.

Build plan 11E lists what must be verified, and every item has a test here:

======================================================  ===========================================
build plan 11E                                          test
======================================================  ===========================================
old upload-backed Actions still work                    ``test_an_upload_backed_action_is_untouched_*``
library-backed inputs resolve correctly                 ``test_a_library_backed_slot_reaches_the_action_as_a_frame``
specific old versions can be selected                   ``test_a_superseded_version_can_still_be_selected_by_id``
a Run records exact dataset provenance                  ``test_the_manifest_records_the_resolved_version``
changing the current version does not change a Run      ``test_a_recorded_run_is_unchanged_by_a_later_commit``
missing library data fails clearly                      the whole "Failing clearly" section
======================================================  ===========================================

Two properties are asserted throughout rather than in one place, because they
are what the phase is actually for:

* **An Action never learns the library exists.** Every Action in this module
  receives ``{slot_id: DataFrame}`` and asserts nothing else, and
  ``test_contract_freeze.py`` refuses a library import in an Action module.
* **A Run still writes nothing.** Reading stored history is a read; the
  ``quarantine`` fixture is asserted empty after a library-backed Run.

The library is built here from committed versions rather than through the
ingestion layer. Phase 11 is about *reading* an exact version, and going
through ingestion would make each test depend on period detection and source
schemas, which have their own module.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest

from app.actions.base import Action, ActionResult
from app.errors import RunValidationError
from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    DatasetCommit,
    DatasetSelector,
    DatasetSelectorKind,
    DatasetVersion,
)
from app.models.schemas import (
    ActionInput,
    ActionInputSource,
    ActionOutput,
    RunStatus,
)
from app.services import data_library, input_resolution
from app.services.data_library import LocalDataLibrary, ensure_known_datasets
from app.services.runner import execute_run

from tests.helpers import csv_bytes, make_action, upload, upload_file

SALES_COLUMNS: tuple[str, ...] = ("Invoice Number", "Customer", "Total Price")


# ---------------------------------------------------------------------------
# Actions used by this module
#
# Deliberately declared here rather than registered in the application: build
# plan 11A requires the two proof Actions to stay exactly as they are, and
# `test_contract_freeze.py` asserts that they do.
# ---------------------------------------------------------------------------


class _LibraryOnlyAction(Action):
    """One library-backed slot and nothing else."""

    id = "library_only"
    version = "1.0.0"
    name = "Library Only"
    description = "Return the stored sales history version it was given."
    inputs = (
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        # The Action sees a frame. It has no way to ask which version this is,
        # which is the point of build plan 11B.
        return ActionResult(outputs={"result": inputs["sales_history"]})


class _SchemaBoundLibraryAction(Action):
    """A library-backed slot that requires columns, checked like any other."""

    id = "library_schema_bound"
    version = "1.0.0"
    name = "Library Schema Bound"
    description = "Require columns of a stored dataset version."
    inputs = (
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
            required_columns=("Invoice Number", "Customer", "Missing Column"),
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:  # pragma: no cover
        raise AssertionError("validation must stop this Action before it runs")


class _MixedSourceAction(Action):
    """One uploaded slot and one library-backed slot, side by side."""

    id = "mixed_sources"
    version = "1.0.0"
    name = "Mixed Sources"
    description = "Combine an uploaded file with a stored dataset version."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
        ),
    )
    outputs = (
        ActionOutput(id="uploaded", label="Uploaded"),
        ActionOutput(id="stored", label="Stored"),
    )

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(
            outputs={
                "uploaded": inputs["source_file"],
                "stored": inputs["sales_history"],
            }
        )


class _OptionalLibraryAction(Action):
    """A library-backed slot a Run may proceed without."""

    id = "optional_library"
    version = "1.0.0"
    name = "Optional Library"
    description = "Read stored history only if a version is named."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv",),
        ),
        ActionInput(
            id="sales_history",
            label="Sales History",
            required=False,
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["source_file"]})


class _AssignmentsAction(Action):
    """A library-backed slot reading the snapshot dataset."""

    id = "assignments_reader"
    version = "1.0.0"
    name = "Assignments Reader"
    description = "Return the account-assignment snapshot it was given."
    inputs = (
        ActionInput(
            id="assignments",
            label="Account Assignments",
            source=ActionInputSource.LIBRARY,
            dataset_id=ACCOUNT_ASSIGNMENTS.id,
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["assignments"]})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def sales_frame(period: str, *, rows: int) -> pl.DataFrame:
    """A small, deterministic month of sales.

    Each month is given a different number of rows so a test can tell which
    version it received by its height alone, without trusting an ID it also
    had to look up.
    """
    return pl.DataFrame(
        {
            "Invoice Number": [f"INV-{period}-{index:03d}" for index in range(rows)],
            "Customer": ["Acme Wine Bar"] * rows,
            "Total Price": [float(index) for index in range(rows)],
        }
    )


def commit_month(
    period: str,
    *,
    rows: int,
    dataset_id: str = SALES_HISTORY.id,
    supersedes: str | None = None,
    reason: str | None = None,
) -> DatasetVersion:
    """Commit one month into the test's own Data Library."""
    frame = sales_frame(period, rows=rows)
    payload = frame.write_csv().encode("utf-8")
    return data_library.commit_version(
        dataset_id,
        DatasetCommit.from_upload(
            frame,
            filename=f"{period} Sales.csv",
            payload=payload,
            period=period,
            parser_engine="polars-csv",
            supersedes=supersedes,
            supersession_reason=reason,
        ),
    )


@pytest.fixture
def library(data_library: LocalDataLibrary) -> LocalDataLibrary:
    """The test's own empty library, with the declared datasets created."""
    ensure_known_datasets(data_library)
    return data_library


@pytest.fixture
def history(library: LocalDataLibrary) -> dict[str, DatasetVersion]:
    """Three committed months of sales history, keyed by period."""
    return {
        "2026-07": commit_month("2026-07", rows=2),
        "2026-08": commit_month("2026-08", rows=3),
        "2026-09": commit_month("2026-09", rows=4),
    }


@pytest.fixture
def library_client(client, registered_actions):
    """A client serving the Actions this module declares."""
    registered_actions(
        _LibraryOnlyAction(),
        _SchemaBoundLibraryAction(),
        _MixedSourceAction(),
        _OptionalLibraryAction(),
        _AssignmentsAction(),
    )
    return client


def _run(client, action_id: str, *, files=None, **references):
    return client.post(
        "/api/runs",
        data={"action_id": action_id, **references},
        files=files or {},
    )


def _sole_manifest(client, run_store) -> dict:
    """The manifest of the only Run a refused request created."""
    runs = run_store.list_runs()
    assert len(runs) == 1, [run.run_id for run in runs]
    response = client.get(f"/api/runs/{runs[0].run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _issue_codes(payload: dict) -> list[str]:
    """Every issue code in an error body, single or combined."""
    error = payload["error"]
    reported = error["details"].get("issues")
    if reported:
        return [issue["code"] for issue in reported]
    return [error["code"]]


# ---------------------------------------------------------------------------
# 11A — the input contract, extended without disturbing what existed
# ---------------------------------------------------------------------------


def test_an_input_slot_is_upload_backed_unless_it_says_otherwise() -> None:
    slot = ActionInput(
        id="source_file", label="Source File", accepted_extensions=(".csv",)
    )

    assert slot.source is ActionInputSource.UPLOAD
    assert slot.dataset_id is None


def test_the_registered_actions_did_not_have_to_change() -> None:
    """Build plan 11A states this as an instruction; here it is as a fact."""
    from app.actions import registry

    for action in registry.list_actions():
        for slot in action.inputs:
            assert slot.source is ActionInputSource.UPLOAD
            assert slot.dataset_id is None


def test_every_registered_library_slot_names_a_declared_dataset() -> None:
    """A slot may only read a dataset ForgeXL declares.

    Vacuous today — no registered Action reads the library — and deliberately
    written anyway: the moment one does (build plan Phase 13), a typo in its
    `dataset_id` becomes a failing test here rather than a Run that reports an
    unknown dataset to the user.
    """
    from app.actions import registry
    from app.models.library import known_dataset

    for action in registry.list_actions():
        for slot in action.inputs:
            if slot.source is ActionInputSource.LIBRARY:
                assert known_dataset(slot.dataset_id or "") is not None, (
                    f"{action.id}.{slot.id} reads {slot.dataset_id!r}, which "
                    "is not a declared dataset."
                )


def test_a_library_slot_must_name_its_dataset() -> None:
    with pytest.raises(ValueError):
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
        )


def test_a_library_slot_cannot_also_accept_a_file() -> None:
    with pytest.raises(ValueError):
        ActionInput(
            id="sales_history",
            label="Sales History",
            source=ActionInputSource.LIBRARY,
            dataset_id=SALES_HISTORY.id,
            accepted_extensions=(".csv",),
        )


def test_an_upload_slot_cannot_name_a_dataset() -> None:
    with pytest.raises(ValueError):
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv",),
            dataset_id=SALES_HISTORY.id,
        )


def test_an_upload_slot_must_accept_an_extension() -> None:
    with pytest.raises(ValueError):
        ActionInput(id="source_file", label="Source File")


# ---------------------------------------------------------------------------
# 11E — old upload-backed Actions still work
# ---------------------------------------------------------------------------


def test_an_upload_backed_action_is_untouched_by_the_runner() -> None:
    payload = csv_bytes(["SKU", "Volume"], [["A-1", "750ml"], ["A-2", "1.5L"]])

    outcome = execute_run(
        make_action("passthrough"), {"source_file": upload("a.csv", payload)}
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.manifest.library_inputs == ()
    assert outcome.manifest.audit.library_inputs == ()
    assert outcome.manifest.audit.rows_received == 2


def test_an_upload_backed_action_is_untouched_over_http(client) -> None:
    payload = csv_bytes(
        ["SKU", "Vintage", "Supplier", "Producer", "Selection", "Volume"],
        [["A-1", "2021", "Acme", "Château Réal", "Réserve", "750ml"]] * 2,
    )

    response = client.post(
        "/api/runs",
        data={"action_id": "product_master_builder"},
        files={"sales_file": upload_file("sales.csv", payload)},
    )

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["library_inputs"] == []
    assert manifest["audit"]["library_inputs"] == []
    assert manifest["outputs"][0]["row_count"] == 1


# ---------------------------------------------------------------------------
# 11B — a reference resolves to a DataFrame before the Action runs
# ---------------------------------------------------------------------------


def test_a_library_backed_slot_reaches_the_action_as_a_frame(history) -> None:
    outcome = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-08"}
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result.primary.height == 3
    assert tuple(outcome.result.primary.columns) == SALES_COLUMNS


def test_the_action_receives_the_stored_rows_unchanged(history) -> None:
    outcome = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-09"}
    )

    stored = data_library.load_version(
        SALES_HISTORY.id, history["2026-09"].version_id
    )
    assert outcome.result is not None
    assert outcome.result.primary.equals(stored)


def test_latest_resolves_to_the_greatest_reporting_period(history) -> None:
    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    assert record.period == "2026-09"
    assert record.version_id == history["2026-09"].version_id


def test_latest_is_the_newest_month_not_the_newest_commit(history) -> None:
    """A corrected old month is committed last and is not "latest"."""
    replacement = commit_month(
        "2026-07",
        rows=9,
        supersedes=history["2026-07"].version_id,
        reason="July was restated.",
    )

    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    assert record.version_id != replacement.version_id
    assert record.period == "2026-09"


def test_an_uploaded_and_a_stored_input_sit_side_by_side(history) -> None:
    payload = csv_bytes(["SKU"], [["A-1"], ["A-2"], ["A-3"], ["A-4"], ["A-5"]])

    outcome = execute_run(
        _MixedSourceAction(),
        {"source_file": upload("a.csv", payload)},
        {"sales_history": "period:2026-07"},
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.result is not None
    assert outcome.result.tables["uploaded"].height == 5
    assert outcome.result.tables["stored"].height == 2
    # Both inputs are counted, each on its own record.
    assert [record.slot_id for record in outcome.manifest.inputs] == ["source_file"]
    assert [record.slot_id for record in outcome.manifest.library_inputs] == [
        "sales_history"
    ]
    assert outcome.manifest.audit.rows_received == 7
    assert outcome.manifest.outputs[0].input_row_count == 7


def test_a_snapshot_dataset_resolves_by_its_effective_month(library) -> None:
    """Build plan 9E's reason for snapshots, now reachable from an Action."""
    for period, rep in (("2026-09", "Beth"), ("2026-10", "Kevin")):
        month = pl.DataFrame({"Customer": ["Acme"], "Sales Person": [rep]})
        data_library.commit_version(
            ACCOUNT_ASSIGNMENTS.id,
            DatasetCommit.from_upload(
                month,
                filename="assignments.csv",
                payload=month.write_csv().encode("utf-8"),
                period=period,
            ),
        )

    september = execute_run(
        _AssignmentsAction(), {}, {"assignments": "period:2026-09"}
    )
    october = execute_run(
        _AssignmentsAction(), {}, {"assignments": "period:2026-10"}
    )

    assert september.result is not None and october.result is not None
    assert september.result.primary["Sales Person"].to_list() == ["Beth"]
    assert october.result.primary["Sales Person"].to_list() == ["Kevin"]


def test_an_optional_library_slot_may_be_left_unnamed(history) -> None:
    payload = csv_bytes(["SKU"], [["A-1"]])

    outcome = execute_run(
        _OptionalLibraryAction(), {"source_file": upload("a.csv", payload)}
    )

    assert outcome.manifest.status is RunStatus.SUCCEEDED
    assert outcome.manifest.library_inputs == ()


def test_a_library_backed_run_writes_nothing(history, quarantine: Path) -> None:
    """Phase 6's rule survives Phase 11: reading history is a read."""
    execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    assert list(quarantine.iterdir()) == []


def test_a_library_backed_run_records_no_new_library_state(
    history, library: LocalDataLibrary
) -> None:
    before = {
        version.version_id for version in library.list_versions(SALES_HISTORY.id)
    }

    execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    after = {
        version.version_id for version in library.list_versions(SALES_HISTORY.id)
    }
    assert after == before


# ---------------------------------------------------------------------------
# 11C — explicit version provenance
# ---------------------------------------------------------------------------


def test_the_manifest_records_the_resolved_version(history) -> None:
    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    expected = history["2026-09"]

    assert record.slot_id == "sales_history"
    assert record.dataset_id == SALES_HISTORY.id
    assert record.dataset_label == SALES_HISTORY.label
    assert record.version_id == expected.version_id
    assert record.period == "2026-09"
    assert record.version_created_at == expected.created_at
    assert record.source_filename == expected.source_filename
    assert record.source_sha256 == expected.source_sha256
    assert record.row_count == 4
    assert record.column_count == 3
    assert record.columns == SALES_COLUMNS


def test_the_manifest_records_what_was_asked_for_beside_what_was_used(
    history,
) -> None:
    """Build plan 11C forbids recording only 'sales_history = current'."""
    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    assert record.requested == "latest"
    # The identity is never the moving word.
    assert record.version_id != "latest"
    assert record.version_id == history["2026-09"].version_id


def test_the_recorded_version_is_a_forgexl_generated_id(history) -> None:
    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    # Never derived from the uploaded filename that produced the version.
    assert record.version_id not in record.source_filename
    assert DatasetSelector.parse(f"version:{record.version_id}").value == (
        record.version_id
    )


def test_the_manifest_carries_no_filesystem_path(
    history, library: LocalDataLibrary
) -> None:
    """A dataset and a version are named by ID, never by where they live."""
    outcome = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (record,) = outcome.manifest.library_inputs
    for value in record.model_dump().values():
        assert str(library.root) not in str(value)
        assert "/" not in str(value)


def test_the_audit_reports_the_stored_input(history) -> None:
    outcome = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-08"}
    )

    audit = outcome.manifest.audit
    (record,) = audit.library_inputs
    assert record.dataset_label == SALES_HISTORY.label
    assert record.requested == "period:2026-08"
    assert record.version_id == history["2026-08"].version_id
    assert record.row_count == 3
    assert audit.rows_received == 3
    assert audit.inputs == ()


def test_the_manifest_is_served_over_http(library_client, history) -> None:
    response = _run(library_client, "library_only", sales_history="latest")

    assert response.status_code == 200, response.text
    manifest = response.json()
    (record,) = manifest["library_inputs"]
    assert record["dataset_id"] == SALES_HISTORY.id
    assert record["requested"] == "latest"
    assert record["version_id"] == history["2026-09"].version_id
    assert record["period"] == "2026-09"

    # And the same record is there when the Run is fetched again.
    again = library_client.get(f"/api/runs/{manifest['run_id']}").json()
    assert again["library_inputs"] == manifest["library_inputs"]


# ---------------------------------------------------------------------------
# 11D — determinism
# ---------------------------------------------------------------------------


def test_a_superseded_version_can_still_be_selected_by_id(history) -> None:
    """Build plan 11E: "specific old versions can be selected"."""
    original = history["2026-08"]
    commit_month(
        "2026-08",
        rows=7,
        supersedes=original.version_id,
        reason="August was restated.",
    )

    outcome = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": f"version:{original.version_id}"}
    )

    assert outcome.result is not None
    assert outcome.result.primary.height == 3
    (record,) = outcome.manifest.library_inputs
    assert record.version_id == original.version_id


def test_a_period_selector_follows_the_replacement(history) -> None:
    """`period:` asks what the dataset says now; `version:` asks for one version."""
    replacement = commit_month(
        "2026-08",
        rows=7,
        supersedes=history["2026-08"].version_id,
        reason="August was restated.",
    )

    outcome = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-08"}
    )

    (record,) = outcome.manifest.library_inputs
    assert record.version_id == replacement.version_id
    assert outcome.result is not None
    assert outcome.result.primary.height == 7


def test_a_recorded_run_is_unchanged_by_a_later_commit(
    library_client, history, run_store
) -> None:
    """Build plan 11E's fifth item, end to end through the API."""
    first = _run(library_client, "library_only", sales_history="latest").json()
    recorded = dict(first["library_inputs"][0])

    commit_month("2026-10", rows=6)
    commit_month(
        "2026-09",
        rows=99,
        supersedes=history["2026-09"].version_id,
        reason="September was restated.",
    )

    refetched = library_client.get(f"/api/runs/{first['run_id']}").json()

    assert refetched["library_inputs"][0] == recorded
    assert refetched["audit"]["library_inputs"] == first["audit"]["library_inputs"]
    # A new Run asking the same moving question gets a different answer, which
    # is exactly why the old Run had to record the resolved ID.
    second = _run(library_client, "library_only", sales_history="latest").json()
    assert second["library_inputs"][0]["version_id"] != recorded["version_id"]
    assert second["library_inputs"][0]["period"] == "2026-10"


def test_naming_the_recorded_version_reproduces_the_run(history) -> None:
    first = execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})
    (record,) = first.manifest.library_inputs

    commit_month(
        "2026-09",
        rows=99,
        supersedes=history["2026-09"].version_id,
        reason="September was restated.",
    )

    reproduced = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": f"version:{record.version_id}"}
    )

    assert first.result is not None and reproduced.result is not None
    assert reproduced.result.primary.equals(first.result.primary)
    assert reproduced.manifest.library_inputs[0].version_id == record.version_id


def test_a_moving_selector_is_resolved_before_execution(history) -> None:
    """The Action is handed rows, never a reference — build plan 11D."""
    seen: list[object] = []

    class _Probe(_LibraryOnlyAction):
        id = "probe"

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            seen.append(inputs["sales_history"])
            return ActionResult(outputs={"result": inputs["sales_history"]})

    execute_run(_Probe(), {}, {"sales_history": "latest"})

    (received,) = seen
    assert isinstance(received, pl.DataFrame)
    assert received.height == 4


def test_the_selector_knows_which_of_its_forms_move() -> None:
    assert DatasetSelector.parse("latest").is_moving is True
    assert DatasetSelector.parse("period:2026-09").is_moving is True
    assert (
        DatasetSelector.parse(
            "version:0e8e2c9a-6b3f-4b2f-9a3f-8e2c9a6b3f4b"
        ).is_moving
        is False
    )


def test_the_same_reference_produces_the_same_result_twice(history) -> None:
    first = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-07"}
    )
    second = execute_run(
        _LibraryOnlyAction(), {}, {"sales_history": "period:2026-07"}
    )

    assert first.result is not None and second.result is not None
    assert first.result.primary.equals(second.result.primary)
    assert (
        first.manifest.library_inputs[0].version_id
        == second.manifest.library_inputs[0].version_id
    )


# ---------------------------------------------------------------------------
# 11E — missing library data fails clearly
# ---------------------------------------------------------------------------


def test_an_empty_library_fails_with_a_sentence_naming_the_dataset(
    library,
) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (issue,) = raised.value.issues
    assert issue.code == "UNKNOWN_DATASET_VERSION"
    assert "Sales History" in issue.message
    assert issue.slot_id == "sales_history"


def test_a_dataset_never_written_to_fails_clearly(
    data_library: LocalDataLibrary,
) -> None:
    """Not even the dataset record exists; the message still names it."""
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": "latest"})

    (issue,) = raised.value.issues
    assert issue.code == "UNKNOWN_DATASET"
    assert "Sales History" in issue.message
    assert "has been imported" in issue.message


def test_a_month_that_was_never_imported_fails_clearly(history) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": "period:2026-12"})

    (issue,) = raised.value.issues
    assert issue.code == "UNKNOWN_DATASET_VERSION"
    assert "2026-12" in issue.message
    # Named for the reader, not for the store.
    assert SALES_HISTORY.label in issue.message
    assert SALES_HISTORY.id not in issue.message


def test_a_version_id_that_does_not_exist_fails_clearly(history) -> None:
    absent = "0e8e2c9a-6b3f-4b2f-9a3f-8e2c9a6b3f4b"

    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": f"version:{absent}"})

    (issue,) = raised.value.issues
    assert issue.code == "UNKNOWN_DATASET_VERSION"
    assert SALES_HISTORY.label in issue.message
    assert issue.details["version_id"] == absent


@pytest.mark.parametrize(
    "reference",
    [
        "newest",
        "current",
        "2026-09",
        "period:2026-13",
        "period:September",
        "version:../../etc/passwd",
        "version:not-a-uuid",
        "latest:2026-09",
    ],
)
def test_an_unreadable_reference_is_refused_not_guessed(history, reference) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": reference})

    (issue,) = raised.value.issues
    assert issue.code == "INVALID_DATASET_SELECTOR"
    assert issue.slot_id == "sales_history"


def test_a_required_library_slot_with_no_reference_is_missing(history) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {})

    (issue,) = raised.value.issues
    assert issue.code == "MISSING_INPUT"
    assert issue.details["dataset_id"] == SALES_HISTORY.id
    # The message says how to name one rather than only that one is missing.
    assert "period:YYYY-MM" in issue.message


def test_a_blank_reference_is_treated_as_absent(history) -> None:
    with pytest.raises(RunValidationError) as raised:
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": "   "})

    (issue,) = raised.value.issues
    assert issue.code == "MISSING_INPUT"


def test_a_stored_version_is_held_to_the_actions_required_columns(history) -> None:
    """A dataset is not trusted more for having been stored."""
    with pytest.raises(RunValidationError) as raised:
        execute_run(
            _SchemaBoundLibraryAction(), {}, {"sales_history": "latest"}
        )

    (issue,) = raised.value.issues
    assert issue.code == "MISSING_COLUMNS"
    assert issue.details["missing_columns"] == ["Missing Column"]
    # The user is not sent looking for an uploaded file that does not exist.
    assert "stored dataset version" in issue.message


def test_a_failed_library_run_is_recorded_with_its_evidence(
    history, run_store
) -> None:
    with pytest.raises(RunValidationError):
        execute_run(_LibraryOnlyAction(), {}, {"sales_history": "period:2026-12"})

    (run,) = run_store.list_runs()
    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert [issue.code for issue in run.validation.errors] == [
        "UNKNOWN_DATASET_VERSION"
    ]


def test_a_run_that_resolved_before_failing_still_records_the_version(
    history, run_store
) -> None:
    """The Run failed on the schema, after the version had been resolved."""
    with pytest.raises(RunValidationError):
        execute_run(_SchemaBoundLibraryAction(), {}, {"sales_history": "latest"})

    (run,) = run_store.list_runs()
    assert run.status is RunStatus.FAILED
    assert [record.version_id for record in run.library_inputs] == [
        history["2026-09"].version_id
    ]


def test_a_missing_reference_is_reported_over_http(
    library_client, history, run_store
) -> None:
    response = _run(library_client, "library_only")

    assert response.status_code == 422
    assert _issue_codes(response.json()) == ["MISSING_INPUT"]
    assert _sole_manifest(library_client, run_store)["status"] == "failed"


def test_an_unknown_month_is_reported_over_http(library_client, history) -> None:
    response = _run(library_client, "library_only", sales_history="period:2026-12")

    assert response.status_code == 422
    assert _issue_codes(response.json()) == ["UNKNOWN_DATASET_VERSION"]


def test_an_unreadable_reference_is_reported_over_http(
    library_client, history
) -> None:
    response = _run(library_client, "library_only", sales_history="newest")

    assert response.status_code == 422
    assert _issue_codes(response.json()) == ["INVALID_DATASET_SELECTOR"]


# ---------------------------------------------------------------------------
# Mismatched submissions are warned about, never silently obeyed
# ---------------------------------------------------------------------------


def test_a_file_sent_for_a_library_slot_is_ignored_not_read(
    library_client, history, run_store
) -> None:
    """The file is not silently used as the slot's data, and is not silent."""
    response = _run(
        library_client,
        "mixed_sources",
        files={
            "source_file": upload_file("a.csv", csv_bytes(["SKU"], [["A-1"]])),
            "sales_history": upload_file("b.csv", csv_bytes(["SKU"], [["B-1"]])),
        },
    )

    # No version was named, so the library-backed slot is unfilled and the Run
    # fails — the uploaded file did not quietly stand in for it.
    assert response.status_code == 422
    assert _issue_codes(response.json()) == ["MISSING_INPUT"]

    manifest = _sole_manifest(library_client, run_store)
    warnings = manifest["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == ["UNEXPECTED_INPUT"]
    assert warnings[0]["details"]["unexpected_slot_ids"] == ["sales_history"]
    # Only the genuine upload was read.
    assert [record["slot_id"] for record in manifest["inputs"]] == ["source_file"]


def test_a_reference_sent_for_an_upload_slot_is_reported_as_ignored(
    client, registered_actions, run_store
) -> None:
    registered_actions(make_action("passthrough"))

    response = client.post(
        "/api/runs", data={"action_id": "passthrough", "source_file": "latest"}
    )

    # A reference cannot stand in for a file any more than the reverse.
    assert response.status_code == 422
    assert _issue_codes(response.json()) == ["MISSING_INPUT"]

    warnings = _sole_manifest(client, run_store)["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == [
        "UNEXPECTED_DATASET_REFERENCE"
    ]
    assert warnings[0]["details"]["unexpected_slot_ids"] == ["source_file"]


def test_an_undeclared_reference_field_is_reported_as_ignored(
    client, registered_actions
) -> None:
    registered_actions(make_action("passthrough"))
    payload = csv_bytes(["SKU"], [["A-1"]])

    response = client.post(
        "/api/runs",
        data={"action_id": "passthrough", "mystery": "latest"},
        files={"source_file": upload_file("a.csv", payload)},
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["validation"]["warnings"]
    assert [warning["code"] for warning in warnings] == [
        "UNEXPECTED_DATASET_REFERENCE"
    ]


# ---------------------------------------------------------------------------
# The resolution service on its own
# ---------------------------------------------------------------------------


def test_resolve_slot_refuses_an_upload_backed_slot() -> None:
    slot = ActionInput(
        id="source_file", label="Source File", accepted_extensions=(".csv",)
    )

    with pytest.raises(ValueError):
        input_resolution.resolve_slot(slot, "latest")


def test_resolve_version_returns_one_immutable_version(history) -> None:
    version = input_resolution.resolve_version(
        SALES_HISTORY.id, DatasetSelector(kind=DatasetSelectorKind.LATEST)
    )

    assert isinstance(version, DatasetVersion)
    assert version.version_id == history["2026-09"].version_id


def test_latest_version_prefers_a_version_that_states_a_month(library) -> None:
    """A period-less version never outranks one that names its month."""
    frame = sales_frame("2026-09", rows=1)
    undated = data_library.commit_version(
        SALES_HISTORY.id,
        DatasetCommit.from_upload(
            frame,
            filename="unscoped.csv",
            payload=frame.write_csv().encode("utf-8"),
        ),
    )
    dated = commit_month("2026-01", rows=1)

    latest = input_resolution.latest_version(SALES_HISTORY.id)

    assert latest.version_id == dated.version_id
    assert undated.period is None


def test_the_dataset_label_comes_from_the_declaration() -> None:
    assert input_resolution.dataset_label(SALES_HISTORY.id) == SALES_HISTORY.label
    assert input_resolution.dataset_label("not_a_dataset") == "not_a_dataset"
