"""Repeated company-sized Phase 15 HTTP workflow measurements.

Run from backend: .venv/bin/python -m tests.benchmarks.monthly --repeats 3
Synthetic CSVs only. Each repetition uses fresh stores under a temporary root.
Stage measurements are disjoint; HTTP total also includes orchestration and
metadata work. Initial history setup is measured separately from the cycle.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient
import polars as pl

from app.actions import monthly_sales_rep_report as action
from app.main import app
from app.services import archive, cycle_receipts, data_library, history_workflow, ingestion, monthly_workflow, parser, run_store
from app.services.data_library import LocalDataLibrary
from app.services.run_store import InMemoryRunStore
from tests.fixtures.monthly_sources import assignments, transaction_row, transactions

PERIOD = "2026-08"
REPS = tuple(f"Synthetic Rep {index:02d}" for index in range(15))
ACCOUNTS = tuple(f"Synthetic Account {index:03d}" for index in range(900))
MONTHS = tuple(f"{2024 + (8 + index) // 12}-{(8 + index) % 12 + 1:02d}" for index in range(24))


def rows(period: str, count: int, *, samples: bool = False):
    for index in range(count):
        credit = index % 13 == 0
        account = index % len(ACCOUNTS)
        quantity = (-1 if credit else 1) * (index % 6 + 1)
        product = (index * 17 + (int(period[:4]) * 12 + int(period[5:])) * 41) % 1500
        yield transaction_row(invoice_date=f"{period}-{index % 28 + 1:02d}",
            invoice_type=("Sample Credit Invoice" if credit else "Sample Invoice") if samples else ("Credit Invoice" if credit else "Invoice"),
            invoice_number=f'{"S" if samples else "I"}-{period}-{index:06d}',
            customer=ACCOUNTS[account], sales_person=REPS[account % len(REPS)],
            supplier=f"Synthetic Supplier {product % 50:02d}", sku=f"SKU-{product:04d}",
            producer=f"Synthetic Producer {product % 100:03d}", selection=f"Selection {product:04d}",
            quantity=quantity, item_price=25, total_price=quantity * 25)


def fixtures():
    return {
        "sales_history": transactions(list(rows(PERIOD, 3000))).as_csv(),
        "sample_history": transactions(list(rows(PERIOD, 250, samples=True))).as_csv(),
        "account_assignments": assignments([(name, REPS[index % len(REPS)]) for index, name in enumerate(ACCOUNTS)]).as_csv(),
    }, {
        "sales_history": transactions([row for month in MONTHS[:-1] for row in rows(month, 3000)]).as_csv(),
        "sample_history": transactions([row for month in MONTHS[:-1] for row in rows(month, 250, samples=True)]).as_csv(),
    }


def checked(response):
    assert response.status_code == 200, response.text
    return response.json()


def measure_once(current, initial):
    values: dict[str, float] = defaultdict(float)
    def timed(label, function):
        def wrapper(*args, **kwargs):
            started = perf_counter()
            try: return function(*args, **kwargs)
            finally: values[label] += (perf_counter() - started) * 1000
        return wrapper

    parse = parser.parse_tabular_bytes
    validate_source = ingestion.validate_source
    def validation(*args, **kwargs):
        started, previous = perf_counter(), values["parsing"]
        try: return validate_source(*args, **kwargs)
        finally: values["validation"] += (perf_counter() - started) * 1000 - (values["parsing"] - previous)

    with TemporaryDirectory(prefix="forgexl-monthly-benchmark-") as root, ExitStack() as stack:
        library = LocalDataLibrary(Path(root) / "library")
        stack.enter_context(patch.object(data_library, "DATA_LIBRARY", library))
        stack.enter_context(patch.object(cycle_receipts, "CYCLE_RECEIPTS", cycle_receipts.CycleReceiptStore(Path(root) / "cycles")))
        stack.enter_context(patch.object(history_workflow, "HISTORY_WORKFLOW", history_workflow.HistoryWorkflow()))
        stack.enter_context(patch.object(monthly_workflow, "WORKFLOW", monthly_workflow.MonthlyWorkflow()))
        stack.enter_context(patch.object(run_store, "RUN_STORE", InMemoryRunStore()))
        client = stack.enter_context(TestClient(app))
        setup = perf_counter()
        for dataset_id, payload in initial.items():
            review = checked(client.post("/api/monthly/history/validate", data={"dataset_id": dataset_id}, files={"source_file": (dataset_id + ".csv", payload)}))
            assert review["ready"], review
            saved = checked(client.post("/api/monthly/history/commit", json={"validation_id": review["validation_id"], "acknowledge_warnings": True}))
            assert saved["status"] == "saved", saved
        setup_ms = (perf_counter() - setup) * 1000
        for module, name, label in (
            (parser, "parse_tabular_bytes", "parsing"),
            (data_library, "commit_version", "persistent_commit"),
            (cycle_receipts.CYCLE_RECEIPTS, "save", "persistent_commit"),
            (data_library, "load_version", "historical_loading"),
            (monthly_workflow, "prepare", "validation"),
            (action, "prepare", "validation"),
            (action, "build_tables", "report_calculation"),
            (action, "render_rep_workbooks", "xlsx_generation"),
            (archive, "to_zip_bytes", "zip_generation")):
            stack.enter_context(patch.object(module, name, timed(label, getattr(module, name))))
        stack.enter_context(patch.object(ingestion, "validate_source", validation))
        started = perf_counter()
        review = checked(client.post("/api/monthly/validate", data={"period": PERIOD}, files={key: (key + ".csv", payload) for key, payload in current.items()}))
        assert review["ready"] and len(review["reps"]) == 15, review
        result = checked(client.post("/api/monthly/generate", json={"validation_id": review["validation_id"], "acknowledge_warnings": True}))
        assert result["status"] == "reports_generated", result
        bundle = client.get(f'/api/runs/{result["manifest"]["run_id"]}/artifacts/download/zip')
        assert bundle.status_code == 200
        values["total_cycle"] = (perf_counter() - started) * 1000
        values["initial_history_setup"] = setup_ms
        return dict(values), len(bundle.content)


def main():
    cli = argparse.ArgumentParser(); cli.add_argument("--repeats", type=int, default=3)
    options = cli.parse_args()
    if options.repeats < 2: cli.error("Repeat meaningful measurements at least twice.")
    current, initial = fixtures()
    measurements = [measure_once(current, initial) for _ in range(options.repeats)]
    print(json.dumps({"schema_version": 1, "period": PERIOD, "format": "CSV",
        "sales_rows": 72000, "sample_rows": 6000, "accounts": 900, "products": 1500, "suppliers": 50, "reps": 15,
        "history_months": 24, "repeats": options.repeats,
        "python": platform.python_version(), "polars": pl.__version__, "platform": platform.platform(),
        "monthly_upload_bytes": sum(map(len, current.values())),
        "zip_bytes": [size for _, size in measurements],
        "timings_ms": {label: {"median": round(statistics.median([item[label] for item, _ in measurements]), 3),
            "min": round(min(item[label] for item, _ in measurements), 3),
            "max": round(max(item[label] for item, _ in measurements), 3)} for label in measurements[0][0]},
        "runs_ms": [item for item, _ in measurements]}, indent=2))


if __name__ == "__main__": main()
