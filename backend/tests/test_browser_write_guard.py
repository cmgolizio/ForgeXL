import pytest

from app.services import data_library, history_workflow, monthly_workflow


@pytest.mark.parametrize("headers", [
    {"Origin": "https://untrusted.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_write_is_refused_before_validation_or_storage(client, headers):
    response = client.post("/api/monthly/validate", headers=headers, data={"period": "2026-09"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "CROSS_ORIGIN_REQUEST"
    assert not data_library.list_datasets()
    assert history_workflow.HISTORY_WORKFLOW._pending is None
    assert monthly_workflow.WORKFLOW._pending is None


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://127.0.0.1:3000"}])
def test_local_clients_still_reach_structured_validation(client, headers):
    response = client.post("/api/monthly/validate-saved", headers=headers, json={"period": "2026-09"})
    assert response.status_code == 200 and not response.json()["ready"]
