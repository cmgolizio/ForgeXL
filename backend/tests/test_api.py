"""HTTP surface tests (build plan Phase 2.6, 2.7 and 4E)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.actions import registry as registry_module
from app.actions.registry import ActionRegistry
from app.api import actions as actions_api
from app.main import app

from tests.helpers import make_action


@pytest.fixture
def client():
    """A client bound to the real application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_with_actions(monkeypatch):
    """A client whose `/api/actions` reads a registry the test controls.

    Lets the endpoint be tested against known Actions without depending on
    which Actions the application happens to register today.
    """

    def _build(*actions):
        registry = ActionRegistry(actions)
        monkeypatch.setattr(registry_module, "ACTION_REGISTRY", registry)
        isolated = FastAPI()
        isolated.include_router(actions_api.router)
        return TestClient(isolated)

    return _build


# ---------------------------------------------------------------------------
# GET /health  (Phase 1 endpoint, previously covered only by manual checks)
# ---------------------------------------------------------------------------


def test_health_reports_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/actions
# ---------------------------------------------------------------------------


def test_get_actions_returns_the_registered_actions(client):
    response = client.get("/api/actions")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert list(payload) == ["actions"]
    assert [entry["id"] for entry in payload["actions"]] == [
        action.id for action in registry_module.list_actions()
    ]


def test_get_actions_serialises_every_definition_field(client_with_actions):
    response = client_with_actions(make_action("alpha", version="2.3.4")).get(
        "/api/actions"
    )

    assert response.status_code == 200
    assert response.json() == {
        "actions": [
            {
                "id": "alpha",
                "version": "2.3.4",
                "name": "Alpha",
                "description": "Test Action alpha.",
                "inputs": [
                    {
                        "id": "source_file",
                        "label": "Source File",
                        "description": None,
                        "required": True,
                        "accepted_extensions": [".csv", ".xlsx"],
                        "required_columns": [],
                    }
                ],
                "outputs": [
                    {
                        "id": "result",
                        "label": "Result",
                        "description": None,
                        "formats": ["csv", "xlsx"],
                    }
                ],
            }
        ]
    }


def test_get_actions_represents_required_columns(client_with_actions):
    client = client_with_actions(
        make_action("schema_bound", required_columns=("SKU", "Supplier", "Volume"))
    )

    payload = client.get("/api/actions").json()

    assert payload["actions"][0]["inputs"][0]["required_columns"] == [
        "SKU",
        "Supplier",
        "Volume",
    ]


def test_get_actions_preserves_registration_order(client_with_actions):
    client = client_with_actions(
        make_action("gamma"), make_action("alpha"), make_action("beta")
    )

    payload = client.get("/api/actions").json()

    assert [entry["id"] for entry in payload["actions"]] == ["gamma", "alpha", "beta"]


def test_get_actions_returns_an_empty_list_when_nothing_is_registered(
    client_with_actions,
):
    payload = client_with_actions().get("/api/actions").json()

    assert payload == {"actions": []}


def test_get_actions_is_fully_json_serialisable(client):
    """Every Action the application registers must survive serialisation."""
    payload = client.get("/api/actions").json()

    assert payload["actions"], "the application must expose at least one Action"
    for entry in payload["actions"]:
        assert set(entry) == {
            "id",
            "version",
            "name",
            "description",
            "inputs",
            "outputs",
        }
        assert entry["id"] and entry["version"] and entry["name"]
        assert entry["description"]
        assert entry["inputs"], "an Action must declare at least one input slot"
        assert entry["outputs"], "an Action must declare at least one output"
        for slot in entry["inputs"]:
            assert set(slot) == {
                "id",
                "label",
                "description",
                "required",
                "accepted_extensions",
                "required_columns",
            }
            assert slot["accepted_extensions"], "a slot must accept some extension"
            assert all(ext.startswith(".") for ext in slot["accepted_extensions"])
        for output in entry["outputs"]:
            assert set(output) == {"id", "label", "description", "formats"}


def test_actions_endpoint_allows_the_local_frontend_origin(client):
    response = client.get(
        "/api/actions", headers={"Origin": "http://127.0.0.1:3000"}
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    )


def test_actions_endpoint_does_not_allow_an_unexpected_origin(client):
    response = client.get(
        "/api/actions", headers={"Origin": "http://evil.example.com"}
    )

    assert "access-control-allow-origin" not in response.headers


def test_unknown_api_path_returns_404(client):
    assert client.get("/api/does-not-exist").status_code == 404


# ---------------------------------------------------------------------------
# Both proof Actions are exposed (build plan Phase 4E)
#
# The frontend builds its Action selector and its upload slots from nothing but
# this response, so what it contains is the whole contract between the two
# proof Actions and the UI.
# ---------------------------------------------------------------------------


def test_get_actions_exposes_both_proof_actions(client):
    payload = client.get("/api/actions").json()

    assert [entry["id"] for entry in payload["actions"]] == [
        "exact_duplicate_remover",
        "product_master_builder",
    ]


def test_get_actions_describes_the_exact_duplicate_remover(client):
    payload = client.get("/api/actions").json()
    entry = next(
        e for e in payload["actions"] if e["id"] == "exact_duplicate_remover"
    )

    assert entry["version"] == "1.0.0"
    assert entry["name"] == "Exact Duplicate Remover"
    (slot,) = entry["inputs"]
    assert slot["id"] == "source_file"
    assert slot["label"] == "Source File"
    assert slot["required"] is True
    assert slot["accepted_extensions"] == [".csv", ".xlsx"]
    assert slot["required_columns"] == []
    (output,) = entry["outputs"]
    assert output["id"] == "deduplicated_data"
    assert output["label"] == "Deduplicated Data"
    assert output["formats"] == ["csv", "xlsx"]


def test_get_actions_describes_the_product_master_builder(client):
    payload = client.get("/api/actions").json()
    entry = next(
        e for e in payload["actions"] if e["id"] == "product_master_builder"
    )

    assert entry["version"] == "1.0.0"
    assert entry["name"] == "Product Master Builder"
    (slot,) = entry["inputs"]
    assert slot["id"] == "sales_file"
    assert slot["label"] == "Sales File"
    assert slot["required"] is True
    assert slot["accepted_extensions"] == [".csv", ".xlsx"]
    # The six columns the UI must tell the user to supply, in output order.
    assert slot["required_columns"] == [
        "SKU",
        "Vintage",
        "Supplier",
        "Producer",
        "Selection",
        "Volume",
    ]
    (output,) = entry["outputs"]
    assert output["id"] == "product_master"
    assert output["label"] == "Product Master"
    assert output["formats"] == ["csv", "xlsx"]


def test_the_two_actions_declare_different_input_slot_ids(client):
    """The frontend renders slots from these IDs; it never hardcodes them."""
    payload = client.get("/api/actions").json()
    slot_ids = [
        slot["id"] for entry in payload["actions"] for slot in entry["inputs"]
    ]

    assert slot_ids == ["source_file", "sales_file"]