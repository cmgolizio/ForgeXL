"""Verify production Next.js → loopback FastAPI → disk → XLSX/ZIP.

Run after npm run build, from backend:
    .venv/bin/python -m tests.benchmarks.workflow_http
Servers share this process's network namespace. An isolated temporary library
and synthetic golden fixtures protect all real business data.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import zipfile

import httpx
from openpyxl import load_workbook

from tests.fixtures import report_months as golden
from tests.fixtures.monthly_sources import transactions


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def checked(response):
    assert response.status_code == 200, response.text
    return response.json()


def unpack(response):
    assert response.status_code == 200 and "September 2026" in response.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        return {name: bundle.read(name) for name in bundle.namelist()}


def main():
    repository = Path(__file__).resolve().parents[3]
    api_port, web_port = port(), port()
    processes = []
    with TemporaryDirectory(prefix="forgexl-http-verification-") as temporary:
        log_path = Path(temporary) / "server.log"
        with log_path.open("w+") as log:
            environment = {**os.environ, "FORGEXL_LIBRARY_DIRECTORY": str(Path(temporary) / "library"),
                "FORGEXL_BACKEND_PORT": str(api_port), "NEXT_TELEMETRY_DISABLED": "1"}
            def start_api():
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
                    cwd=repository / "backend", env=environment, stdout=log, stderr=log)
                processes.append(process)
                return process
            try:
                backend = start_api()
                processes.append(subprocess.Popen(["node", "node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", str(web_port)],
                    cwd=repository, env=environment, stdout=log, stderr=log))
                with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=30, trust_env=False) as client:
                    def wait_ready():
                        for _ in range(80):
                            try:
                                if client.get("/forge-api/health").status_code == 200: return
                            except httpx.HTTPError: pass
                            time.sleep(.1)
                        log.flush()
                        raise AssertionError("Server did not become ready: " + log_path.read_text())
                    wait_ready()
                    page = client.get("/monthly-reports")
                    assert page.status_code == 200 and "Monthly Reports" in page.text
                    assert "/monthly-reports" in client.get("/").text
                    base = "/forge-api/api/monthly"
                    for dataset_id, rows in (
                        ("sales_history", [row for month, lines in golden.SALES_ROWS.items() if month != golden.GOLDEN_MONTH for row in lines]),
                        ("sample_history", list(golden.SAMPLE_ROWS["2026-08"]))):
                        review = checked(client.post(base + "/history/validate", data={"dataset_id": dataset_id},
                            files={"source_file": ("history.csv", transactions(rows).as_csv())}))
                        assert review["ready"], review
                        saved = checked(client.post(base + "/history/commit", json={"validation_id": review["validation_id"], "acknowledge_warnings": True}))
                        assert saved["status"] == "saved"
                    review = checked(client.post(base + "/validate", data={"period": golden.GOLDEN_MONTH}, files={
                        "sales_history": ("sales.csv", golden.sales_table(golden.GOLDEN_MONTH).as_csv()),
                        "sample_history": ("samples.csv", golden.sample_table(golden.GOLDEN_MONTH).as_csv()),
                        "account_assignments": ("owners.csv", golden.assignment_table().as_csv())}))
                    assert review["ready"], review
                    result = checked(client.post(base + "/generate", json={"validation_id": review["validation_id"], "acknowledge_warnings": True}))
                    assert result["status"] == "reports_generated"
                    run_id = result["manifest"]["run_id"]
                    original = unpack(client.get(f"/forge-api/api/runs/{run_id}/artifacts/download/zip"))
                    assert len(original) == 3
                    for payload in original.values():
                        workbook = load_workbook(BytesIO(payload), data_only=True)
                        assert len(workbook.sheetnames) == 6
                        workbook.close()
                    summary = checked(client.get(f"/forge-api/api/runs/{run_id}/outputs/company_summary/preview"))
                    assert summary["rows"][0][summary["columns"].index("Revenue")] == 995.0
                    backend.terminate(); backend.wait(timeout=5)
                    start_api(); wait_ready()
                    assert client.get(f"/forge-api/api/runs/{run_id}").status_code == 404
                    replay = checked(client.post(base + "/validate-saved", json={"period": golden.GOLDEN_MONTH, "cycle_id": result["receipt"]["cycle_id"]}))
                    repeated = checked(client.post(base + "/generate", json={"validation_id": replay["validation_id"], "acknowledge_warnings": True}))
                    assert repeated["status"] == "reports_generated"
                    assert unpack(client.get(f'/forge-api/api/runs/{repeated["manifest"]["run_id"]}/artifacts/download/zip')) == original
                    print(json.dumps({"http_proxy": "passed", "history_import": "passed", "monthly_cycle": "passed",
                        "workbooks": 3, "worksheets": 18, "company_revenue_control": 995.0,
                        "restart_exact_workbook_replay": "passed", "live_browser": "not exercised"}, indent=2))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=5)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__ == "__main__": main()
