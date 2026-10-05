"""User workflow regressions: two inputs, multi-year history, exact replay."""
from io import BytesIO
import zipfile

from app.services import data_library as library
from tests.fixtures.monthly_sources import transaction_row, transactions

PREFIX = "/api/monthly"
PERIOD = "2026-09"


def master_files():
    months = [f"{year}-{month:02}" for year in range(2023, 2027) for month in range(1, 13)
              if "2023-10" <= f"{year}-{month:02}" <= PERIOD]
    return {key: (key + ".csv", transactions([transaction_row(invoice_date=month + "-15",
        invoice_type=kind, invoice_number=key + month, sales_person="Rep One", quantity=1,
        total_price=amount) for month in months]).as_csv())
        for key, kind, amount in (("sales_history", "Invoice", 100), ("sample_history", "Sample Invoice", 5))}


def validate(client, files):
    response = client.post(PREFIX + "/validate", data={"period": PERIOD}, files=files)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"], result
    return result


def generate(client, review):
    response = client.post(PREFIX + "/generate", json={"validation_id": review["validation_id"], "acknowledge_warnings": True})
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["status"] == "reports_generated", outcome
    return outcome


def test_three_years_generate_without_assignment_data_and_replay(client):
    review = validate(client, master_files())
    assert review["reps"] == ["Rep One"]
    assert len(review["sources"]) == 2
    assert review["sources"][0]["imported_periods"] == [f"{year}-{month:02}" for year in range(2023, 2027)
        for month in range(1, 13) if "2023-10" <= f"{year}-{month:02}" <= PERIOD]
    assert library.list_datasets() == []
    result = generate(client, review)
    assert len(library.current_versions("sales_history")) == 36
    assert len(library.current_versions("sample_history")) == 36
    assert set(result["receipt"]["versions"]) == {"sales_history", "sample_history"}
    run_id = result["manifest"]["run_id"]
    page = client.get(f"/api/runs/{run_id}/outputs/company_summary/preview").json()
    assert page["rows"][0][page["columns"].index("Revenue")] == 100
    with zipfile.ZipFile(BytesIO(client.get(f"/api/runs/{run_id}/artifacts/download/zip").content)) as archive:
        assert archive.namelist() == ["Rep One - September 2026.xlsx"]
    saved = client.post(PREFIX + "/validate-saved", json={"period": PERIOD, "cycle_id": result["receipt"]["cycle_id"]}).json()
    replay = generate(client, saved)
    assert replay["committed_versions"] == {}
    assert replay["receipt"] == result["receipt"]


def test_identical_history_reupload_reuses_months_without_double_counting(client):
    generate(client, validate(client, master_files()))
    before = {key: [v.version_id for v in library.current_versions(key)] for key in ("sales_history", "sample_history")}
    review = validate(client, master_files())
    assert all(len(source["reused_periods"]) == 36 and not source["imported_periods"] for source in review["sources"])
    result = generate(client, review)
    assert result["committed_versions"] == {}
    assert before == {key: [v.version_id for v in library.current_versions(key)] for key in before}


def test_conflicting_history_is_refused_before_any_commit(client):
    generate(client, validate(client, master_files()))
    files = master_files()
    filename, payload = files["sales_history"]
    files["sales_history"] = filename, payload.replace(b",100\n", b",999\n")
    review = client.post(PREFIX + "/validate", data={"period": PERIOD}, files=files).json()
    assert not review["ready"]
    assert "HISTORY_MONTH_CONFLICT" in {issue["code"] for issue in review["errors"]}
    assert len(library.list_versions("sales_history")) == 36


def test_assignment_upload_is_not_a_supported_option(client):
    catalog = client.get(PREFIX + "/catalog").json()
    assert [source["id"] for source in catalog["datasets"]] == ["sales_history", "sample_history"]
    action = next(item for item in client.get("/api/actions").json()["actions"] if item["id"] == "monthly_sales_rep_report")
    assert [slot["id"] for slot in action["inputs"]] == ["sales_history", "sample_history"]
    assert action["workflow_path"] == "/monthly-reports"
    response = client.post(PREFIX + "/validate", data={"period": PERIOD}, files={"account_assignments": ("owners.csv", b"Customer,Sales Person\nA,B\n")})
    assert response.status_code == 400


def test_report_cutoff_excludes_later_months_but_preserves_their_history(client):
    review = client.post(PREFIX + "/validate", data={"period": "2026-08"}, files=master_files()).json()
    assert review["ready"], review
    result = generate(client, review)
    assert result["period"] == "2026-08"
    for key, ids in result["receipt"]["versions"].items():
        for version in ids:
            saved_period = library.get_version(key, version).period
            assert saved_period is not None and saved_period <= "2026-08"
    assert library.current_version("sales_history", "2026-09").row_count == 1


def test_invalid_data_after_report_cutoff_is_not_saved(client):
    files = master_files()
    files["sales_history"] = ("invalid-later.csv", transactions([
        transaction_row(invoice_date="2026-08-15", total_price=100),
        transaction_row(invoice_date="2026-09-15", total_price="not a number")]).as_csv())
    review = client.post(PREFIX + "/validate", data={"period": "2026-08"}, files=files).json()
    assert not review["ready"]
    assert "NON_NUMERIC_MEASURE" in {issue["code"] for issue in review["errors"]}
    assert library.list_datasets() == []


def test_old_three_source_receipt_replays_without_needing_its_snapshot(client):
    from app.models.library import new_version_id
    from app.models.monthly_workflow import CycleReceipt
    from app.services import cycle_receipts
    result = generate(client, validate(client, master_files()))
    payload = {**result["receipt"], "schema_version": 1, "cycle_id": new_version_id(),
        "action": {**result["receipt"]["action"], "version": "0.2.0"},
        "versions": {**result["receipt"]["versions"], "account_assignments": [new_version_id()]}}
    legacy = CycleReceipt.model_validate(payload)
    cycle_receipts.CYCLE_RECEIPTS.save(legacy)
    review = client.post(PREFIX + "/validate-saved", json={"period": PERIOD, "cycle_id": legacy.cycle_id}).json()
    assert review["ready"], review
    assert "ACTION_VERSION_CHANGED" in {issue["code"] for issue in review["warnings"]}
    assert set(legacy.selectors()) == {"sales_history", "sample_history"}
    repeated = generate(client, review)
    assert repeated["manifest"]["action"]["version"] == "0.3.0"
    assert repeated["receipt"]["schema_version"] == 1
