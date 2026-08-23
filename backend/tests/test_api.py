"""HTTP surface tests (build plan Phase 2.6 and 2.7)."""

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