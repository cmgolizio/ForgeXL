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
    assert repeated["manifest"]["action"]["version"] == "0.4.0"
    assert repeated["receipt"]["schema_version"] == 1


def test_explicit_saved_history_reuse_imports_missing_months_and_preserves_roster(client):
    # Reproduce the user path: saved sales, a differing master, and new samples.
    original = master_files()
    sales = original['sales_history']
    seeded = client.post(PREFIX + '/history/validate', data={'dataset_id': 'sales_history'},
        files={'source_file': sales}).json()
    assert seeded['ready']
    assert client.post(PREFIX + '/history/commit', json={'validation_id': seeded['validation_id'],
        'acknowledge_warnings': True}).json()['status'] == 'saved'
    before = {v.period: (v.version_id, library.load_version('sales_history', v.version_id))
        for v in library.current_versions('sales_history')}
    files = master_files()
    files['sales_history'] = ('different-master.csv', sales[1].replace(b',100\n', b',999\n'))
    blocked = client.post(PREFIX + '/validate', data={'period': PERIOD}, files=files).json()
    assert not blocked['ready'] and not blocked['reps']
    checks = {item['label']: item['detail'] for item in blocked['checks']}
    assert checks['Sales reps detected'] == 'Not checked until source errors are resolved'
    assert checks['Historical comparisons'] == 'Not checked until source errors are resolved'
    review = client.post(PREFIX + '/validate', data={'period': PERIOD,
        'sales_history.use_saved_months': 'true'}, files=files).json()
    assert review['ready'], review
    assert review['reps'] == ['Rep One']
    assert len(review['sources'][0]['reused_periods']) == 36
    warning = next(item for item in review['warnings'] if item['code'] == 'HISTORY_DIFFERENCES_IGNORED')
    assert len(warning['details']['months']) == 36
    assert all(item['existing_version_id'] == before[item['period']][0] for item in warning['details']['months'])
    assert client.post(PREFIX + '/generate', json={'validation_id': review['validation_id']}).status_code == 400
    result = generate(client, review)
    assert all(not key.startswith('sales_history') for key in result['committed_versions'])
    assert len(library.current_versions('sample_history')) == 36
    for month, (identity, frame) in before.items():
        assert month is not None
        assert library.current_version('sales_history', month).version_id == identity
        assert library.load_version('sales_history', identity).equals(frame)
    page = client.get(f"/api/runs/{result['manifest']['run_id']}/outputs/company_summary/preview").json()
    assert page['rows'][0][page['columns'].index('Revenue')] == 100


def test_explicit_reuse_still_saves_new_sales_months(client):
    files = master_files()
    earlier = {key: (name, payload.replace(b'2026-09-15', b'2026-08-15'))
        for key, (name, payload) in files.items()}
    # Seed complete months through August, then upload September with changes.
    review = client.post(PREFIX + '/validate', data={'period': '2026-08'}, files=earlier).json()
    generate(client, review)
    previous = {v.period: v.version_id for v in library.current_versions('sales_history')}
    name, payload = files['sales_history']
    files['sales_history'] = (name, payload.replace(b',100\n', b',999\n'))
    review = client.post(PREFIX + '/validate', data={'period': PERIOD,
        'sales_history.use_saved_months': 'true', 'sample_history.use_saved_months': 'true'}, files=files).json()
    assert review['ready'], review
    assert review['sources'][0]['imported_periods'] == [PERIOD]
    result = generate(client, review)
    assert library.current_version('sales_history', PERIOD).version_id == result['committed_versions']['sales_history']
    for month, identity in previous.items():
        assert month is not None
        assert library.current_version('sales_history', month).version_id == identity


def test_reuse_does_not_hide_invalid_uploaded_rows(client):
    generate(client, validate(client, master_files()))
    files = master_files()
    name, payload = files['sales_history']
    files['sales_history'] = (name, payload.replace(b',100\n', b',unreadable\n'))
    review = client.post(PREFIX + '/validate', data={'period': PERIOD,
        'sales_history.use_saved_months': 'true'}, files=files).json()
    assert not review['ready']
    assert 'NON_NUMERIC_MEASURE' in {item['code'] for item in review['errors']}
    assert len(library.list_versions('sales_history')) == 36


def test_reuse_options_reject_invalid_or_contradictory_requests(client):
    for value in ('yes', '1', 'TRUE'):
        assert client.post(PREFIX + '/validate', data={'period': PERIOD,
            'sales_history.use_saved_months': value}, files=master_files()).status_code == 400
    assert client.post(PREFIX + '/validate', data={'period': PERIOD,
        'sales_history.use_saved_months': 'true'}).status_code == 400
    assert client.post(PREFIX + '/validate', data={'period': PERIOD,
        'sales_history.use_saved_months': 'true', 'sales_history.replaces': 'some-version'},
        files=master_files()).status_code == 400
    assert client.post(PREFIX + '/validate', data={'period': PERIOD,
        'other.use_saved_months': 'true'}, files=master_files()).status_code == 400
    repeated = [('period', (None, PERIOD)), ('sales_history.use_saved_months', (None, 'true')),
        ('sales_history.use_saved_months', (None, 'true'))]
    assert client.post(PREFIX + '/validate', files=repeated).status_code == 400
