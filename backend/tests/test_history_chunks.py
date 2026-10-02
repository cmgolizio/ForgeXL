"""Whole-month history selection: no implicit replacement, merge or row loss."""
from datetime import timedelta

import pytest

from app.errors import DataLibraryError
from app.models.library import now
from app.services import data_library as library, history_workflow
from tests.fixtures.monthly_sources import transaction_row, transactions

BASE = "/api/monthly/history"


def review(client, months, *, skip=False, rows=None):
    payload = transactions(rows or [transaction_row(invoice_date=f"{month}-15", total_price=index + 10)
        for index, month in enumerate(months)]).as_csv()
    return client.post(BASE + "/validate", data={"dataset_id": "sales_history", "skip_existing": str(skip).lower()},
        files={"source_file": ("three-months.csv", payload)}).json()


def save(client, checked, *, consent=True):
    return client.post(BASE + "/commit", json={"validation_id": checked["validation_id"],
        "acknowledge_warnings": consent})


def test_nonoverlapping_chunks_extend_history_without_reuploading_prior_chunks(client):
    for months in (["2025-01", "2025-02", "2025-03"], ["2025-04", "2025-05", "2025-06"]):
        checked = review(client, months)
        assert checked["ready"] and checked["periods"] == months
        assert checked["imported_row_count"] == checked["row_count"] == 3
        result = save(client, checked).json()
        assert result["status"] == "saved"
        assert list(result["committed_periods"]) == months
    assert len(library.current_versions("sales_history")) == 6


def test_overlap_blocks_whole_chunk_until_selection_and_consent_are_explicit(client):
    save(client, review(client, ["2025-01", "2025-02"]))
    before = library.current_version("sales_history", "2025-02")
    blocked = review(client, ["2025-02", "2025-03", "2025-04"])
    assert not blocked["ready"] and blocked["validation_id"] is None
    assert "HISTORY_MONTHS_ALREADY_COMMITTED" in {issue["code"] for issue in blocked["errors"]}
    assert len(library.current_versions("sales_history")) == 2
    checked = review(client, ["2025-02", "2025-03", "2025-04"], skip=True)
    assert checked["ready"] and checked["periods"] == ["2025-03", "2025-04"]
    assert checked["skipped_periods"] == ["2025-02"]
    assert checked["row_count"] == 3 and checked["imported_row_count"] == 2
    assert save(client, checked, consent=False).status_code == 400
    result = save(client, checked).json()
    assert set(result["committed_periods"]) == {"2025-03", "2025-04"}
    assert library.current_version("sales_history", "2025-02") == before
    assert len(library.list_versions("sales_history")) == 4


def test_all_stored_months_cannot_create_an_empty_import(client):
    save(client, review(client, ["2025-01", "2025-02"]))
    checked = review(client, ["2025-01", "2025-02"], skip=True)
    assert not checked["ready"] and "NO_NEW_HISTORY_MONTHS" in {i["code"] for i in checked["errors"]}
    assert len(library.current_versions("sales_history")) == 2


def test_skip_does_not_hide_invalid_dates_anywhere_in_the_source(client):
    save(client, review(client, ["2025-01"]))
    checked = review(client, [], skip=True, rows=[transaction_row(invoice_date="2025-01-invalid"),
        transaction_row(invoice_date="2025-02-15")])
    assert not checked["ready"] and checked["errors"]
    assert len(library.current_versions("sales_history")) == 1


def test_partial_chunk_can_resume_same_bytes_without_duplicating_saved_month(client, monkeypatch):
    months = ["2025-01", "2025-02", "2025-03"]
    checked = review(client, months)
    commit = library.commit_version
    def fail_second(dataset, change):
        if change.period == "2025-02": raise DataLibraryError("Synthetic interrupted save")
        return commit(dataset, change)
    with monkeypatch.context() as patch:
        patch.setattr(library, "commit_version", fail_second)
        failed = save(client, checked).json()
    assert failed["status"] == "save_failed" and list(failed["committed_periods"]) == ["2025-01"]
    resumed = review(client, months, skip=True)
    assert resumed["ready"] and resumed["periods"] == ["2025-02", "2025-03"]
    assert save(client, resumed).json()["status"] == "saved"
    assert len(library.list_versions("sales_history")) == 3
    for month in months:
        assert library.current_version("sales_history", month).row_count == 1


def test_review_is_invalidated_if_history_changes_before_save(client):
    checked = review(client, ["2025-01", "2025-02"])
    from app.services.ingestion import SourceFile, commit_monthly_sales
    commit_monthly_sales(SourceFile("other.csv", transactions([transaction_row(invoice_date="2024-12-15")]).as_csv()))
    assert save(client, checked).status_code == 400
    assert len(library.list_versions("sales_history")) == 1


def test_expired_chunk_review_releases_pending_rows(client):
    checked = review(client, ["2025-01", "2025-02"])
    pending = history_workflow.HISTORY_WORKFLOW._pending
    assert pending is not None
    pending.summary.expires_at = now() - timedelta(seconds=1)
    assert save(client, checked).status_code == 400
    assert history_workflow.HISTORY_WORKFLOW._pending is None


@pytest.mark.parametrize("flag", ["1", "yes", "TRUE"])
def test_skip_flag_is_not_truthy_string_coercion(client, flag):
    response = client.post(BASE + "/validate", data={"dataset_id": "sales_history", "skip_existing": flag},
        files={"source_file": ("file.csv", transactions([transaction_row(invoice_date="2025-01-15")]).as_csv())})
    assert response.status_code == 400
