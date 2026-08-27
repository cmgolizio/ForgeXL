"""Phase 6A contract freeze — the behaviour Phase 6B-6I must not break.

Phase 6A is defensive (build plan "Phase 6A", item 5): before the runtime
architecture is changed, pin what the finished Phase 0/1-5 implementation
actually promises, so a regression during the migration shows up as a failing
test rather than as a quietly different result.

Two rules shape this module, and they are what make it useful *during* the
migration rather than only before it:

* **It is filesystem-independent.** Nothing here creates a Run directory,
  writes a manifest, reads a Parquet file or asks for a path. Every assertion
  is about metadata, dataframes, schema shapes, error codes or HTTP routes, so
  this module must keep passing unchanged after uploads, results and exports
  move into memory. It never uses the ``runs_dir`` / ``run_paths`` fixtures,
  which disappear with the on-disk model.

* **It pins contracts, not implementations.** The values asserted here are
  recorded in ``docs/phase-6a-compatibility-audit.md`` §4. Changing one is a
  deliberate decision to make, not an incidental consequence of a refactor.

The 311 tests that existed before this phase are unaffected: they cover the
current implementation in depth, and several of them are expected to be
rewritten as the runtime changes. This module is the part that must not need
rewriting.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app import config
from app.actions import registry as registry_module
from app.actions.base import Action, ActionResult
from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import ProductMasterBuilderAction
from app.actions.registry import ActionRegistry, DuplicateActionIdError
from app.errors import (
    ActionExecutionError,
    AmbiguousWorkbookError,
    EmptyDatasetError,
    FileParseError,
    InputValidationError,
    InvalidRequestError,
    MissingArtifactError,
    MissingColumnsError,
    MissingInputError,
    RunValidationError,
    UnknownActionError,
    UnknownOutputError,
    UnknownRunError,
    UnsupportedExtensionError,
    UploadTooLargeError,
    WorkbenchError,
)
from app.main import app
from app.models.schemas import (
    MANIFEST_SCHEMA_VERSION,
    ActionDefinition,
    ActionInput,
    ActionListResponse,
    ActionOutput,
    ActionReference,
    InputMetadata,
    OutputMetadata,
    PreviewResponse,
    RunError,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)
from app.services import preview

from tests.helpers import make_action

# ---------------------------------------------------------------------------
# The frozen inventory
#
# One table describing every registered Action's complete public contract.
# `GET /api/actions`, the Action selector and every Run manifest are built from
# these values, so they are asserted from one place rather than restated per
# test.
# ---------------------------------------------------------------------------

FROZEN_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "exact_duplicate_remover",
        "version": "1.0.0",
        "name": "Exact Duplicate Remover",
        "inputs": (
            {
                "id": "source_file",
                "label": "Source File",
                "required": True,
                "accepted_extensions": (".csv", ".xlsx"),
                "required_columns": (),
            },
        ),
        "outputs": (
            {
                "id": "deduplicated_data",
                "label": "Deduplicated Data",
                "formats": ("csv", "xlsx"),
            },
        ),
        "metric_keys": frozenset(
            {"input_rows", "output_rows", "duplicates_removed"}
        ),
    },
    {
        "id": "product_master_builder",
        "version": "1.0.0",
        "name": "Product Master Builder",
        "inputs": (
            {
                "id": "sales_file",
                "label": "Sales File",
                "required": True,
                "accepted_extensions": (".csv", ".xlsx"),
                "required_columns": (
                    "SKU",
                    "Vintage",
                    "Supplier",
                    "Producer",
                    "Selection",
                    "Volume",
                ),
            },
        ),
        "outputs": (
            {
                "id": "product_master",
                "label": "Product Master",
                "formats": ("csv", "xlsx"),
            },
        ),
        "metric_keys": frozenset(
            {"input_rows", "output_rows", "duplicate_product_rows_removed"}
        ),
    },
)

FROZEN_ACTION_IDS: tuple[str, ...] = tuple(
    entry["id"] for entry in FROZEN_ACTIONS
)

#: Every server-side route the frontend or a future client may call, with the
#: methods it answers, exactly as the published OpenAPI schema reports them.
#: Phase 6G changes the *browser-side* prefix, not these.
FROZEN_ROUTES: dict[str, list[str]] = {
    "/health": ["get"],
    "/api/actions": ["get"],
    "/api/runs": ["post"],
    "/api/runs/{run_id}": ["get"],
    "/api/runs/{run_id}/outputs/{output_id}/preview": ["get"],
    "/api/runs/{run_id}/outputs/{output_id}/download/csv": ["get"],
    "/api/runs/{run_id}/outputs/{output_id}/download/xlsx": ["get"],
}

#: Every error the backend reports, with the status the API boundary returns.
#: Build plan section 22 fixes the status codes; the codes are what the UI
#: branches on.
FROZEN_ERRORS: tuple[tuple[type[WorkbenchError], str, int], ...] = (
    (WorkbenchError, "INTERNAL_ERROR", 500),
    (InvalidRequestError, "INVALID_REQUEST", 400),
    (UnknownActionError, "UNKNOWN_ACTION", 404),
    (UnknownRunError, "UNKNOWN_RUN", 404),
    (UnknownOutputError, "UNKNOWN_OUTPUT", 404),
    (MissingArtifactError, "MISSING_ARTIFACT", 404),
    (UploadTooLargeError, "FILE_TOO_LARGE", 413),
    (InputValidationError, "INVALID_INPUT", 422),
    (MissingInputError, "MISSING_INPUT", 422),
    (UnsupportedExtensionError, "UNSUPPORTED_EXTENSION", 422),
    (FileParseError, "PARSE_ERROR", 422),
    (AmbiguousWorkbookError, "AMBIGUOUS_WORKBOOK", 422),
    (EmptyDatasetError, "EMPTY_DATASET", 422),
    (MissingColumnsError, "MISSING_COLUMNS", 422),
    (ActionExecutionError, "ACTION_FAILED", 500),
)

#: Field names of every model the API returns or the manifest records.
FROZEN_SCHEMA_FIELDS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (
        ActionInput,
        (
            "id",
            "label",
            "description",
            "required",
            "accepted_extensions",
            "required_columns",
        ),
    ),
    (ActionOutput, ("id", "label", "description", "formats")),
    (
        ActionDefinition,
        ("id", "version", "name", "description", "inputs", "outputs"),
    ),
    (ActionListResponse, ("actions",)),
    (ValidationIssue, ("code", "message", "details", "slot_id")),
    (ValidationSummary, ("passed", "errors", "warnings")),
    (ActionReference, ("id", "version", "name")),
    (
        InputMetadata,
        (
            "slot_id",
            "original_filename",
            "stored_filename",
            "file_size_bytes",
            "extension",
            "parser_engine",
            "worksheet",
            "row_count",
            "column_count",
            "columns",
        ),
    ),
    (
        OutputMetadata,
        ("id", "label", "row_count", "column_count", "columns", "formats"),
    ),
    (RunError, ("code", "message", "details")),
    (
        RunManifest,
        (
            "schema_version",
            "run_id",
            "status",
            "action",
            "created_at",
            "started_at",
            "completed_at",
            "duration_ms",
            "inputs",
            "validation",
            "outputs",
            "metrics",
            "error",
        ),
    ),
    (
        PreviewResponse,
        (
            "run_id",
            "output_id",
            "columns",
            "rows",
            "offset",
            "limit",
            "total_rows",
        ),
    ),
)

#: Modules an Action must not reach for. An Action transforms data; the runner
#: owns everything that touches a file (build plan section 24).
FORBIDDEN_ACTION_IMPORTS: frozenset[str] = frozenset(
    {
        "os",
        "io",
        "pathlib",
        "shutil",
        "tempfile",
        "glob",
        "fsspec",
        "openpyxl",
        "fastexcel",
        "xlsxwriter",
        "app.services",
        "app.services.storage",
        "app.services.export",
        "app.services.parser",
        "app.services.preview",
        "app.services.runner",
        "app.config",
    }
)

#: Builtins that read or write a file. None may appear in an Action module.
FORBIDDEN_ACTION_CALLS: frozenset[str] = frozenset({"open", "exec", "eval"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    """A client bound to the real application.

    Deliberately independent of the ``runs_dir`` fixture: nothing this module
    requests touches storage, so the freeze survives the on-disk model being
    removed.
    """
    with TestClient(app) as client:
        yield client


@pytest.fixture
def registered_action_ids() -> list[str]:
    return [action.id for action in registry_module.list_actions()]


def _action(action_id: str) -> Action:
    action = registry_module.get_action(action_id)
    assert action is not None, f"{action_id} is not registered"
    return action


def _frame_for(action: Action) -> pl.DataFrame:
    """Build a small input frame satisfying `action`'s declared schema.

    Contains a repeated row, a near-duplicate that differs only by case and
    whitespace, accented text and a blank, so one frame exercises the accuracy
    rules of build plan section 3.3 for either Action.
    """
    (slot,) = action.inputs
    if slot.required_columns:
        columns = list(slot.required_columns)
    else:
        columns = ["SKU", "Producer", "Volume"]

    rows = [
        ["A-1", "Château Réal", "750ml", "x", "y", "z"],
        ["A-1", "Château Réal", "750ml", "x", "y", "z"],  # exact duplicate
        ["a-1", " Château Réal", "750ml ", "x", "y", "z"],  # near duplicate
        ["B-2", None, "", "x", "y", "z"],  # blank and null
    ]
    return pl.DataFrame(
        [row[: len(columns)] for row in rows],
        schema=columns,
        orient="row",
    )


def _imported_modules(module) -> set[str]:
    """Every module name a source file imports, from its AST."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _called_names(module) -> set[str]:
    """Every bare name called in a source file, from its AST."""
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


# ---------------------------------------------------------------------------
# Action registration (build plan 6A.5)
# ---------------------------------------------------------------------------


def test_the_registered_actions_are_exactly_the_frozen_inventory(
    registered_action_ids,
) -> None:
    assert tuple(registered_action_ids) == FROZEN_ACTION_IDS


def test_registration_order_is_frozen(registered_action_ids) -> None:
    # The order the registry returns is the order the Action selector renders,
    # so it is part of what the user sees.
    assert registered_action_ids == list(FROZEN_ACTION_IDS)


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_each_action_declares_its_frozen_identity(entry) -> None:
    action = _action(entry["id"])

    assert action.id == entry["id"]
    assert action.version == entry["version"]
    assert action.name == entry["name"]
    # The description is shown to the user; it must exist, but its wording is
    # not a contract.
    assert action.description.strip()


def test_action_ids_are_unique(registered_action_ids) -> None:
    assert len(set(registered_action_ids)) == len(registered_action_ids)


def test_get_action_returns_none_rather_than_guessing() -> None:
    for near_miss in ("", "Exact_Duplicate_Remover", " exact_duplicate_remover",
                      "exact_duplicate", "../exact_duplicate_remover"):
        assert registry_module.get_action(near_miss) is None


def test_duplicate_action_ids_are_rejected_not_overwritten() -> None:
    registry = ActionRegistry((make_action("alpha", name="First"),))

    with pytest.raises(DuplicateActionIdError):
        registry.register(make_action("alpha", name="Second"))

    survivor = registry.get_action("alpha")
    assert survivor is not None and survivor.name == "First"
    assert len(registry) == 1


def test_duplicate_action_id_error_is_a_value_error() -> None:
    assert issubclass(DuplicateActionIdError, ValueError)


def test_a_blank_action_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        ActionRegistry().register(make_action(""))


# ---------------------------------------------------------------------------
# Action input validation contract (build plan 6A.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_each_action_declares_its_frozen_input_slots(entry) -> None:
    action = _action(entry["id"])

    assert len(action.inputs) == len(entry["inputs"])
    for slot, expected in zip(action.inputs, entry["inputs"]):
        assert slot.id == expected["id"]
        assert slot.label == expected["label"]
        assert slot.required is expected["required"]
        assert slot.accepted_extensions == expected["accepted_extensions"]
        # Order matters: it is the order the Product Master presents, and the
        # order the UI lists as "required columns".
        assert slot.required_columns == expected["required_columns"]


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_each_action_declares_its_frozen_outputs(entry) -> None:
    action = _action(entry["id"])

    assert len(action.outputs) == len(entry["outputs"])
    for output, expected in zip(action.outputs, entry["outputs"]):
        assert output.id == expected["id"]
        assert output.label == expected["label"]
        assert output.formats == expected["formats"]


def test_the_two_actions_use_different_slot_ids() -> None:
    slot_ids = [
        slot.id for action in registry_module.list_actions() for slot in action.inputs
    ]
    assert len(set(slot_ids)) == len(slot_ids)


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_action_metadata_is_immutable(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs

    # Declared once as module-level constants; a Run must never be able to
    # change what an Action requires.
    with pytest.raises(Exception):
        slot.required = False  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_the_definition_reports_exactly_the_declared_metadata(entry) -> None:
    definition = _action(entry["id"]).definition()

    assert isinstance(definition, ActionDefinition)
    assert definition.id == entry["id"]
    assert definition.version == entry["version"]
    assert definition.name == entry["name"]
    assert tuple(slot.id for slot in definition.inputs) == tuple(
        slot["id"] for slot in entry["inputs"]
    )
    assert tuple(output.id for output in definition.outputs) == tuple(
        output["id"] for output in entry["outputs"]
    )


def test_validate_reports_no_issues_by_default() -> None:
    for action in registry_module.list_actions():
        (slot,) = action.inputs
        assert action.validate({slot.id: _frame_for(action)}) == []


# ---------------------------------------------------------------------------
# Action execution contract (build plan 6A.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_run_receives_frames_keyed_by_slot_id_and_returns_frames_by_output_id(
    entry,
) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs

    result = action.run({slot.id: _frame_for(action)})

    assert isinstance(result, ActionResult)
    assert set(result.outputs) == {
        output["id"] for output in entry["outputs"]
    }
    for frame in result.outputs.values():
        assert isinstance(frame, pl.DataFrame)


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_each_action_reports_exactly_its_frozen_metric_keys(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs

    metrics = action.run({slot.id: _frame_for(action)}).metrics

    assert set(metrics) == set(entry["metric_keys"])
    assert all(isinstance(value, int) for value in metrics.values())


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_the_row_metrics_are_internally_consistent(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs
    source = _frame_for(action)

    result = action.run({slot.id: source})
    (output_id,) = result.outputs
    metrics = result.metrics

    removed_key = next(key for key in metrics if key.endswith("_removed"))
    assert metrics["input_rows"] == source.height
    assert metrics["output_rows"] == result.outputs[output_id].height
    assert metrics[removed_key] == metrics["input_rows"] - metrics["output_rows"]


def test_an_action_instance_holds_no_per_run_state() -> None:
    # One instance is registered at import time and reused for every Run, so
    # running twice through the same instance must not accumulate anything.
    for action in registry_module.list_actions():
        (slot,) = action.inputs
        before = dict(vars(action))
        action.run({slot.id: _frame_for(action)})
        assert dict(vars(action)) == before


# ---------------------------------------------------------------------------
# Deterministic output (build plan 6A.5, section 3.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_repeat_execution_produces_identical_output(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs
    source = _frame_for(action)

    first = action.run({slot.id: source})
    second = action.run({slot.id: source})

    assert first.metrics == second.metrics
    for output_id, frame in first.outputs.items():
        assert frame.equals(second.outputs[output_id])


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_the_input_frame_is_never_mutated(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs
    source = _frame_for(action)
    untouched = source.clone()

    action.run({slot.id: source})

    assert source.equals(untouched)


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_near_duplicates_accents_and_blanks_survive_untouched(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs
    source = _frame_for(action)

    (frame,) = action.run({slot.id: source}).outputs.values()

    # One exact duplicate removed; the case/whitespace variant kept as its own
    # row, because nothing is trimmed, re-cased or fuzzily matched.
    assert frame.height == source.height - 1

    values = {value for column in frame.columns for value in frame[column].to_list()}
    assert "Château Réal" in values, "accented text was normalised"
    assert " Château Réal" in values, "a near duplicate was merged away"
    assert "" in values, "a blank was reinterpreted"
    assert None in values, "a null was substituted"


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_first_occurrence_order_and_column_order_are_preserved(entry) -> None:
    action = _action(entry["id"])
    (slot,) = action.inputs
    source = _frame_for(action)

    (frame,) = action.run({slot.id: source}).outputs.values()

    if slot.required_columns:
        # Product Master fixes its own output column order.
        assert tuple(frame.columns) == slot.required_columns
    else:
        assert tuple(frame.columns) == tuple(source.columns)

    first_column = frame.columns[0]
    assert frame[first_column].to_list() == ["A-1", "a-1", "B-2"]


# ---------------------------------------------------------------------------
# DataFrame-first classification (build plan 6A.4)
#
# Both registered Actions are DataFrame-compatible. These tests are what keeps
# that true: an Action that starts opening a file fails here rather than at the
# end of Phase 6D.
# ---------------------------------------------------------------------------

ACTION_MODULES = (
    ("exact_duplicate_remover", ExactDuplicateRemoverAction),
    ("product_master_builder", ProductMasterBuilderAction),
)


@pytest.mark.parametrize(
    "action_id,action_class", ACTION_MODULES, ids=[m[0] for m in ACTION_MODULES]
)
def test_no_action_module_imports_a_filesystem_module(
    action_id, action_class
) -> None:
    module = inspect.getmodule(action_class)
    assert module is not None

    forbidden = _imported_modules(module) & FORBIDDEN_ACTION_IMPORTS
    assert not forbidden, (
        f"{action_id} imports {sorted(forbidden)}. Actions transform data; the "
        "runner owns everything that touches a file."
    )


@pytest.mark.parametrize(
    "action_id,action_class", ACTION_MODULES, ids=[m[0] for m in ACTION_MODULES]
)
def test_no_action_module_opens_or_executes_anything(
    action_id, action_class
) -> None:
    module = inspect.getmodule(action_class)
    assert module is not None

    forbidden = _called_names(module) & FORBIDDEN_ACTION_CALLS
    assert not forbidden, f"{action_id} calls {sorted(forbidden)}"


@pytest.mark.parametrize("entry", FROZEN_ACTIONS, ids=FROZEN_ACTION_IDS)
def test_an_action_executes_with_no_filesystem_available(
    entry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute with the data directory pointed somewhere that does not exist.

    This is the behavioural half of the classification: if an Action needed a
    Run directory, an input file or an export path, it could not complete here.
    """
    missing = tmp_path / "definitely-not-created"
    monkeypatch.setattr(config, "DATA_DIRECTORY", missing)
    monkeypatch.setattr(config, "RUNS_DIRECTORY", missing / "runs")
    monkeypatch.chdir(tmp_path)

    action = _action(entry["id"])
    (slot,) = action.inputs
    result = action.run({slot.id: _frame_for(action)})

    assert set(result.outputs) == {output["id"] for output in entry["outputs"]}
    assert not missing.exists()


def test_the_action_run_signature_takes_named_frames() -> None:
    for action in registry_module.list_actions():
        parameters = list(inspect.signature(action.run).parameters)
        assert parameters == ["inputs"], (
            f"{action.id}.run must take one mapping of named DataFrames "
            "(build plan 6D.2)."
        )


# ---------------------------------------------------------------------------
# Error handling (build plan 6A.5, section 22)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_class,code,status",
    FROZEN_ERRORS,
    ids=[code for _, code, _ in FROZEN_ERRORS],
)
def test_the_error_taxonomy_is_frozen(error_class, code, status) -> None:
    assert error_class.code == code
    assert error_class.http_status == status


def test_every_error_renders_the_documented_body_shape() -> None:
    error = MissingColumnsError(
        "The uploaded file is missing required columns.",
        details={"missing_columns": ["Supplier", "Volume"]},
    )

    body = error.as_response_body()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "MISSING_COLUMNS"
    assert body["error"]["details"]["missing_columns"] == ["Supplier", "Volume"]


def test_no_error_body_carries_a_traceback() -> None:
    try:
        raise ValueError("underlying cause")
    except ValueError as cause:
        error = ActionExecutionError("Something failed.", details={"a": 1})
        error.__cause__ = cause

    rendered = repr(error.as_response_body())

    assert "Traceback" not in rendered
    assert "underlying cause" not in rendered


def test_a_single_validation_issue_is_reported_as_itself() -> None:
    issue = ValidationIssue(
        code="MISSING_INPUT",
        message="Sales File is required.",
        details={"label": "Sales File"},
        slot_id="sales_file",
    )

    error = RunValidationError([issue])

    assert error.code == "MISSING_INPUT"
    assert error.http_status == 422
    assert error.message == "Sales File is required."
    assert error.details["slot_id"] == "sales_file"


def test_several_validation_issues_are_reported_together() -> None:
    issues = [
        ValidationIssue(code="MISSING_INPUT", message="A is required."),
        ValidationIssue(code="MISSING_COLUMNS", message="B is missing columns."),
    ]

    error = RunValidationError(issues)

    assert error.code == "VALIDATION_FAILED"
    assert error.http_status == 422
    reported = error.details["issues"]
    assert [entry["code"] for entry in reported] == [
        "MISSING_INPUT",
        "MISSING_COLUMNS",
    ]


def test_a_validation_error_requires_at_least_one_issue() -> None:
    with pytest.raises(ValueError):
        RunValidationError([])


def test_every_error_can_become_a_run_error_and_a_validation_issue() -> None:
    error = UnsupportedExtensionError("Nope.", details={"extension": ".xlsm"})

    run_error = error.as_run_error()
    issue = error.as_validation_issue("source_file")

    assert isinstance(run_error, RunError)
    assert run_error.code == "UNSUPPORTED_EXTENSION"
    assert isinstance(issue, ValidationIssue)
    assert issue.slot_id == "source_file"


# ---------------------------------------------------------------------------
# Public HTTP surface
# ---------------------------------------------------------------------------


def test_the_http_route_inventory_is_frozen() -> None:
    """The published surface, read from the OpenAPI schema the app generates.

    Asserted against the schema rather than the internal route table because
    the schema is what a client actually sees, and because FastAPI's internal
    representation of an included router is an implementation detail.
    """
    paths = {
        path: sorted(operations)
        for path, operations in app.openapi()["paths"].items()
    }

    assert paths == FROZEN_ROUTES


def test_health_reports_ok(api_client) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_actions_serves_the_frozen_inventory(api_client) -> None:
    response = api_client.get("/api/actions")

    assert response.status_code == 200
    payload = response.json()
    assert list(payload) == ["actions"]

    served = payload["actions"]
    assert [entry["id"] for entry in served] == list(FROZEN_ACTION_IDS)

    for entry, expected in zip(served, FROZEN_ACTIONS):
        assert entry["version"] == expected["version"]
        assert entry["name"] == expected["name"]
        assert [slot["id"] for slot in entry["inputs"]] == [
            slot["id"] for slot in expected["inputs"]
        ]
        for slot, expected_slot in zip(entry["inputs"], expected["inputs"]):
            assert slot["required"] is expected_slot["required"]
            assert slot["accepted_extensions"] == list(
                expected_slot["accepted_extensions"]
            )
            assert slot["required_columns"] == list(
                expected_slot["required_columns"]
            )
        assert [output["id"] for output in entry["outputs"]] == [
            output["id"] for output in expected["outputs"]
        ]
        for output, expected_output in zip(entry["outputs"], expected["outputs"]):
            assert output["formats"] == list(expected_output["formats"])


def test_cors_never_uses_a_wildcard_origin(api_client) -> None:
    allowed = api_client.get(
        "/api/actions", headers={"Origin": "http://127.0.0.1:3000"}
    )
    refused = api_client.get(
        "/api/actions", headers={"Origin": "http://evil.example.com"}
    )

    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "access-control-allow-origin" not in refused.headers


# ---------------------------------------------------------------------------
# Schema and constant freeze
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,fields",
    FROZEN_SCHEMA_FIELDS,
    ids=[model.__name__ for model, _ in FROZEN_SCHEMA_FIELDS],
)
def test_schema_field_names_are_frozen(model, fields) -> None:
    assert tuple(model.model_fields) == fields


def test_run_status_values_are_frozen() -> None:
    assert {status.value for status in RunStatus} == {
        "running",
        "succeeded",
        "failed",
    }


def test_the_manifest_schema_version_is_one() -> None:
    # Bumping this is a deliberate decision to record, not a side effect of a
    # refactor. Phase 6 may need to; it must not do so silently.
    assert MANIFEST_SCHEMA_VERSION == 1


def test_preview_limits_are_frozen() -> None:
    assert preview.DEFAULT_PREVIEW_LIMIT == 100
    assert preview.MAX_PREVIEW_LIMIT == 500


def test_an_over_large_preview_limit_is_refused_not_clamped() -> None:
    with pytest.raises(InvalidRequestError) as raised:
        preview.validate_limit(preview.MAX_PREVIEW_LIMIT + 1)

    assert raised.value.details["maximum"] == preview.MAX_PREVIEW_LIMIT


def test_preview_rows_stay_positional_lists() -> None:
    response = PreviewResponse(
        run_id="0e8e2c9a-6b3f-4b2f-9a3f-8e2c9a6b3f4b",
        output_id="product_master",
        columns=("SKU", "Volume"),
        rows=[["A-1", "750ml"], ["B-2", None]],
        offset=0,
        limit=100,
        total_rows=2,
    )

    payload = response.model_dump(mode="json")

    assert payload["rows"] == [["A-1", "750ml"], ["B-2", None]]
    assert payload["columns"] == ["SKU", "Volume"]


def test_the_upload_limit_default_is_frozen() -> None:
    # 250 MB, configurable through FORGEXL_MAX_UPLOAD_BYTES (build plan
    # section 18). The limit survives the move to in-memory uploads.
    assert config.MAX_UPLOAD_BYTES == 250 * 1024 * 1024