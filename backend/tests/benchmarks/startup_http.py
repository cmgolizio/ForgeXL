"""Exercise actual npm start, loopback readiness, port refusal and shutdown."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
from tempfile import TemporaryDirectory
import time

import httpx

from tests.benchmarks.workflow_http import port


def main():
    root = Path(__file__).resolve().parents[3]
    api_port, web_port = port(), port()
    with TemporaryDirectory(prefix="forgexl-startup-check-") as temporary:
        library = Path(temporary) / "library"
        environment = {**os.environ, "FORGEXL_LIBRARY_DIRECTORY": str(library),
            "FORGEXL_BACKEND_PORT": str(api_port), "FORGEXL_WEB_PORT": str(web_port)}
        environment.pop("FORGEXL_BACKEND_ORIGIN", None)
        with (Path(temporary) / "startup.log").open("w+") as log:
            process = subprocess.Popen(["npm", "start"], cwd=root, env=environment,
                stdout=log, stderr=log, start_new_session=True)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=2, trust_env=False) as client:
                    for _ in range(150):
                        if process.poll() is not None: break
                        try:
                            response = client.get("/forge-api/health")
                            if response.status_code == 200 and response.json()["status"] == "ok": break
                        except httpx.HTTPError: pass
                        time.sleep(.1)
                    else: raise AssertionError("Startup readiness timed out")
                    log.flush()
                    assert process.poll() is None, (Path(temporary) / "startup.log").read_text()
                    assert client.get("/monthly-reports").status_code == 200
                    assert client.get("/forge-api/api/monthly/catalog").json()["periods"] == []
                    assert not library.exists(), "Startup must not create the business library"
                    second = subprocess.run(["npm", "start"], cwd=root, env=environment,
                        capture_output=True, text=True, timeout=20)
                    assert second.returncode != 0 and "unavailable" in second.stderr
                    assert client.get("/forge-api/health").status_code == 200, "A second launch must not kill the first"
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            for number in (web_port, api_port):
                with socket.socket() as check:
                    check.settimeout(1)
                    assert check.connect_ex(("127.0.0.1", number)) != 0, "An owned server was left running"
            assert not library.exists()
            print(json.dumps({"npm_start_both_servers": "passed", "monthly_page_and_catalog": "passed",
                "duplicate_launch_preserves_existing_server": "passed", "shutdown_releases_both_ports": "passed",
                "startup_business_data_writes": 0, "finder_double_click": "Mac check pending"}, indent=2))


if __name__ == "__main__": main()
