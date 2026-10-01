"""Phase 12 completion criteria — artifacts over the real HTTP API.

`test_artifacts.py` proves the artifact *model*: the collision rules, the
filename rule and the release rule. `test_workbook.py` proves the *renderer*,
by reopening the bytes it produced. This module proves the two things neither
of those can: the download contract around them (build plan 12G) and the phase
exit criterion stated as one sentence —

    a test Action produces several polished XLSX artifacts plus a ZIP bundle
    through generic ForgeXL infrastructure.

Everything here goes through the running application over HTTP, so a route that
returned the right bytes under the wrong name, or shadowed the other route, or
leaked a server path into a header, fails here rather than in a browser.

The Action used below is declared in this module and is deliberately not one of
the registered ones: build plan 12B keeps artifacts optional and
`test_contract_freeze.py` asserts that neither proof Action produces one.

Nothing is written to disk at any point. The ``quarantine`` directory is
asserted empty afterwards, which is what makes "built in memory" evidence
rather than a claim.

**Restored in Phase 13.** This module is listed in the Phase 12 entry of
`docs/implementation-status.md` and was absent from commit 0f772b5, which is
why the suite reported 1,860 tests rather than the 1,903 that entry documents.
See Known Issue 106.
"""

from __future__ import annotations

import io
import re
import warnings
import zipfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import fastexcel
import openpyxl
import polars as pl
import pytest

from app.actions.base import Action, ActionResult, Artifact
from app.models.artifact import (
    XLSX_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    artifact_filename,
    artifact_ids,
)
from app.models.run import new_run_id
from app.models.schemas import ActionInput, ActionOutput, ArtifactType
from app.services import run_store
from app.services.workbook import CellFormat, Column, Sheet, render_workbook

from tests.helpers import csv_bytes, upload_file

# ---------------------------------------------------------------------------
# The Action this module drives
#
# Three reports, one per name in the uploaded file, rendered by the Phase 12
# report renderer. It is the smallest thing that exercises every part of the
# artifact path at once: several artifacts, real workbook bytes, IDs derived
# from data, and an accented name that must survive into a filename and must
# not survive into an ID.
# ---------------------------------------------------------------------------

HEADER = ("Rep", "Account", "Revenue")
ROWS = (
    ("Beth Comeaux", "Acme Wine Bar", 1250.50),
    ("Beth Comeaux", "Corner Bottle", 300.25),
    ("Kevin Wardell", "Bistro Lumière", 880.00),
    ("Château Réal", "Harbour Cellars", -45.00),
)

#: The reporting period the demo Action labels its reports with. A literal, so
#: the expected filenames below are literals too.
PERIOD = "September 2026"


class _ReportAction(Action):
    """Render one formatted workbook per rep, plus a summary table."""

    id = "artifact_demo"
    version = "1.0.0"
    name = "Artifact Demo"
    description = "Produce one formatted workbook per rep in the uploaded file."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=HEADER,
        ),
    )
    outputs = (ActionOutput(id="summary", label="Summary"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        source = inputs["source_file"]
        summary = (
            source.group_by("Rep")
            .agg(pl.col("Revenue").sum().alias("Revenue"))
            .sort("Rep")
        )

        names = summary["Rep"].to_list()
        # The sanctioned helpers, not a hand-rolled slug: an ID is a URL token
        # and a filename is a name (build plan 12E).
        ids = artifact_ids(names)

        artifacts = []
        for identifier, name in zip(ids, names, strict=True):
            rows = source.filter(pl.col("Rep") == name).select(
                "Account", "Revenue"
            )
            payload = render_workbook(
                [
                    Sheet(
                        name="Accounts",
                        frame=rows,
                        title=f"{name} — Accounts",
                        subtitle=PERIOD,
                        columns=(
                            Column("Account"),
                            Column("Revenue", format=CellFormat.CURRENCY),
                        ),
                        total_row={
                            "Revenue": rows["Revenue"].sum(),
                        },
                    )
                ]
            )
            artifacts.append(
                Artifact.workbook(
                    id=identifier,
                    label=f"{name} — {PERIOD}",
                    filename=artifact_filename(f"{name} - {PERIOD}", "xlsx"),
                    payload=payload,
                )
            )

        return ActionResult(
            outputs={"summary": summary},
            metrics={"reports": len(artifacts)},
            artifacts=tuple(artifacts),
        )


class _TableOnlyAction(Action):
    """An Action that produces no artifact at all — the ordinary case."""

    id = "artifact_none"
    version = "1.0.0"
    name = "Artifact None"
    description = "Return the uploaded table and nothing else."
    inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["source_file"]})


#: The filenames the demo Action produces, in the order it produces them.
#: Sorted by rep name, which is what `summary` is sorted by.
EXPECTED_FILENAMES = (
    f"Beth Comeaux - {PERIOD}.xlsx",
    f"Château Réal - {PERIOD}.xlsx",
    f"Kevin Wardell - {PERIOD}.xlsx",
)

#: The IDs those same three reports are reachable by. Accents folded away,
#: because an ID is a URL path segment (build plan 12E).
EXPECTED_IDS = ("beth-comeaux", "chateau-real", "kevin-wardell")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def report_client(client, registered_actions):
    """A client whose registry holds the two Actions above and nothing else."""
    registered_actions(_ReportAction(), _TableOnlyAction())
    return client


def _run(client, action_id: str = _ReportAction.id) -> dict:
    """Execute the Action over HTTP and return its manifest."""
    response = client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("reps.csv", csv_bytes(HEADER, ROWS))},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _artifact_ids(manifest: Mapping[str, object]) -> list[str]:
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    return [entry["id"] for entry in artifacts]


def _zip_entries(payload: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return list(archive.infolist())


def _sheet_names(payload: bytes) -> list[str]:
    return list(fastexcel.read_excel(payload).sheet_names)


def _reopen(payload: bytes) -> openpyxl.Workbook:
    """Reopen rendered bytes, so every assertion is about the real file."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=".*extension is not supported.*"
        )
        return openpyxl.load_workbook(io.BytesIO(payload))


def _cell_values(payload: bytes, name: str) -> set[object]:
    """Every non-empty cell value of one worksheet, with its stored type.

    Read through openpyxl rather than through the application's own Excel
    engine on purpose: a currency figure must still be a *number* in the file
    (build plan 14F), and a frame read with no header row would report every
    cell as text and hide that.
    """
    worksheet = _reopen(payload)[name]
    values: list[object] = [
        cell.value
        for row in worksheet.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    return set(values)


# ---------------------------------------------------------------------------
# The manifest describes what the Run produced (build plan 12C)
# ---------------------------------------------------------------------------


def test_the_manifest_lists_every_artifact_in_the_actions_order(
    report_client,
) -> None:
    manifest = _run(report_client)

    assert _artifact_ids(manifest) == list(EXPECTED_IDS)


def test_the_manifest_carries_the_six_declared_facts(report_client) -> None:
    manifest = _run(report_client)

    (first, *_rest) = manifest["artifacts"]
    assert set(first) == {
        "id",
        "label",
        "filename",
        "media_type",
        "size_bytes",
        "artifact_type",
    }


def test_an_artifact_reports_the_workbook_media_type(report_client) -> None:
    manifest = _run(report_client)

    for entry in manifest["artifacts"]:
        assert entry["media_type"] == XLSX_MEDIA_TYPE
        assert entry["artifact_type"] == ArtifactType.WORKBOOK.value


def test_the_manifest_keeps_accents_in_a_filename_and_folds_them_in_an_id(
    report_client,
) -> None:
    manifest = _run(report_client)

    filenames = [entry["filename"] for entry in manifest["artifacts"]]
    assert filenames == list(EXPECTED_FILENAMES)
    assert "Château Réal - September 2026.xlsx" in filenames
    assert "chateau-real" in _artifact_ids(manifest)


def test_a_table_only_run_reports_no_artifacts(report_client) -> None:
    manifest = _run(report_client, _TableOnlyAction.id)

    assert manifest["artifacts"] == []


def test_no_manifest_value_looks_like_a_server_path(report_client) -> None:
    """Build plan 12C: no field holds a path, because there is none."""
    manifest = _run(report_client)

    text = repr(manifest)
    for fragment in ("/home/", "/Users/", "/tmp", "data/runs", ".parquet"):
        assert fragment not in text


# ---------------------------------------------------------------------------
# Downloading one artifact (build plan 12G)
# ---------------------------------------------------------------------------


def test_one_artifact_downloads_under_the_actions_own_filename(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    for entry in manifest["artifacts"]:
        response = report_client.get(
            f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
        )

        assert response.status_code == 200
        assert entry["filename"] in response.headers["content-disposition"] or (
            "filename*=UTF-8''" in response.headers["content-disposition"]
        )


def test_a_downloaded_artifact_is_a_readable_workbook(report_client) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )

    assert response.status_code == 200
    assert _sheet_names(response.content) == ["Accounts"]


def test_a_downloaded_workbook_holds_the_calculated_values(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )
    values = _cell_values(response.content, "Accounts")

    assert "Acme Wine Bar" in values
    assert 1250.50 in values
    assert 300.25 in values
    # The totals row the Action calculated, written as a literal value.
    assert 1550.75 in values


def test_a_rendered_report_stores_no_formula(report_client) -> None:
    """Build plan 12D: a total is a value, never ``=SUBTOTAL(...)``."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )

    with zipfile.ZipFile(io.BytesIO(response.content)) as workbook:
        sheets = [
            name
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ]
        assert sheets
        for name in sheets:
            assert b"<f>" not in workbook.read(name)


def test_the_reported_size_is_the_downloaded_size(report_client) -> None:
    """`size_bytes` is measured from the payload, never declared."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    for entry in manifest["artifacts"]:
        response = report_client.get(
            f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
        )
        assert len(response.content) == entry["size_bytes"]


def test_the_media_type_served_is_the_media_type_reported(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    for entry in manifest["artifacts"]:
        response = report_client.get(
            f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
        )
        assert response.headers["content-type"].startswith(entry["media_type"])


def test_a_non_ascii_filename_travels_as_an_rfc_6266_parameter(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/chateau-real/download"
    )
    disposition = response.headers["content-disposition"]

    assert "filename*=UTF-8''" in disposition
    # The ASCII parameter stays beside it, for a client that reads only that.
    assert 'filename="' in disposition
    assert "Ch%C3%A2teau" in disposition


def test_an_ascii_filename_adds_no_extended_parameter(report_client) -> None:
    """Every export filename is ASCII by construction; none of them changed."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )
    disposition = response.headers["content-disposition"]

    assert "filename*" not in disposition
    assert disposition == (
        f'attachment; filename="Beth Comeaux - {PERIOD}.xlsx"'
    )


def test_a_content_disposition_header_carries_no_newline(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    for entry in manifest["artifacts"]:
        response = report_client.get(
            f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
        )
        disposition = response.headers["content-disposition"]
        assert "\n" not in disposition and "\r" not in disposition


def test_downloading_the_same_artifact_twice_returns_the_same_bytes(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    first = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )
    second = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )

    assert first.content == second.content


# ---------------------------------------------------------------------------
# The failure surface (build plan 12G)
# ---------------------------------------------------------------------------


def test_an_unknown_run_is_reported_as_an_unknown_run(report_client) -> None:
    response = report_client.get(
        f"/api/runs/{new_run_id()}/artifacts/beth-comeaux/download"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


def test_a_malformed_run_id_is_reported_rather_than_looked_up(
    report_client,
) -> None:
    response = report_client.get(
        "/api/runs/not-a-uuid/artifacts/beth-comeaux/download"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


def test_an_artifact_the_run_never_produced_is_unknown(report_client) -> None:
    manifest = _run(report_client)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/nobody/download"
    )

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "UNKNOWN_ARTIFACT"
    assert body["details"]["available_artifact_ids"] == list(EXPECTED_IDS)


def test_an_unknown_artifact_is_not_reported_as_an_unknown_output(
    report_client,
) -> None:
    """Build plan 12A keeps tables and files apart, so their 404s stay apart."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    artifact = report_client.get(
        f"/api/runs/{run_id}/artifacts/summary/download"
    )
    output = report_client.get(
        f"/api/runs/{run_id}/outputs/beth-comeaux/download/csv"
    )

    assert artifact.json()["error"]["code"] == "UNKNOWN_ARTIFACT"
    assert output.json()["error"]["code"] == "UNKNOWN_OUTPUT"


def test_an_artifact_the_run_no_longer_holds_is_missing_not_unknown(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    stored = run_store.get_run(run_id)
    assert stored is not None
    run_store.update_run(stored.with_changes(result=None))

    response = report_client.get(
        f"/api/runs/{run_id}/artifacts/beth-comeaux/download"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


# ---------------------------------------------------------------------------
# The batch ZIP (build plan 12F)
# ---------------------------------------------------------------------------


def test_the_zip_bundles_every_artifact_of_the_run(report_client) -> None:
    manifest = _run(report_client)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    assert response.status_code == 200
    assert [entry.filename for entry in _zip_entries(response.content)] == list(
        EXPECTED_FILENAMES
    )


def test_the_zip_is_served_as_an_archive(report_client) -> None:
    manifest = _run(report_client)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    assert response.headers["content-type"].startswith(ZIP_MEDIA_TYPE)


def test_the_bundle_takes_forgexls_own_filename_convention(
    report_client,
) -> None:
    """The bundle is ForgeXL's file; the reports inside are the Action's."""
    manifest = _run(report_client)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    disposition = response.headers["content-disposition"]
    assert re.fullmatch(
        r'attachment; filename="forgexl-artifact-demo-\d{8}-\d{6}\.zip"',
        disposition,
    ), disposition


def test_each_zip_entry_holds_the_artifacts_own_bytes(report_client) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    bundle = report_client.get(f"/api/runs/{run_id}/artifacts/download/zip")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        for entry in manifest["artifacts"]:
            single = report_client.get(
                f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
            )
            assert archive.read(entry["filename"]) == single.content


def test_a_workbook_extracted_from_the_zip_still_opens(report_client) -> None:
    manifest = _run(report_client)

    bundle = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        payload = archive.read(EXPECTED_FILENAMES[0])

    assert _sheet_names(payload) == ["Accounts"]


def test_every_zip_entry_name_is_flat(report_client) -> None:
    """Build plan 12F: an entry name can never decide where a file lands."""
    manifest = _run(report_client)

    bundle = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    for entry in _zip_entries(bundle.content):
        assert "/" not in entry.filename
        assert "\\" not in entry.filename
        assert ".." not in entry.filename
        assert not entry.filename.startswith(".")


def test_downloading_the_bundle_twice_returns_identical_bytes(
    report_client,
) -> None:
    """Deterministic: entries are stamped with the Run's completion time."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    first = report_client.get(f"/api/runs/{run_id}/artifacts/download/zip")
    second = report_client.get(f"/api/runs/{run_id}/artifacts/download/zip")

    assert first.content == second.content


def test_the_bundle_is_stamped_with_the_runs_own_time_not_with_now(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    stored = run_store.get_run(run_id)
    assert stored is not None
    completed = datetime(2026, 9, 15, 12, 30, 44, tzinfo=timezone.utc)
    run_store.update_run(stored.with_changes(completed_at=completed))

    bundle = report_client.get(f"/api/runs/{run_id}/artifacts/download/zip")

    for entry in _zip_entries(bundle.content):
        assert entry.date_time[:5] == (2026, 9, 15, 12, 30)


def test_a_run_with_no_artifact_has_nothing_to_bundle(report_client) -> None:
    manifest = _run(report_client, _TableOnlyAction.id)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


def test_the_zip_route_is_not_shadowed_by_the_artifact_route(
    report_client,
) -> None:
    """``/artifacts/download/zip`` must never be read as artifact ``download``."""
    manifest = _run(report_client)

    response = report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(ZIP_MEDIA_TYPE)


def test_an_unknown_run_has_no_bundle(report_client) -> None:
    response = report_client.get(
        f"/api/runs/{new_run_id()}/artifacts/download/zip"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RUN"


# ---------------------------------------------------------------------------
# The exit criterion, and the rules that must survive it
# ---------------------------------------------------------------------------


def test_the_phase_12_exit_criterion_end_to_end(
    report_client, quarantine: Path
) -> None:
    """One Action; several polished workbooks; one ZIP; nothing on disk."""
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    assert len(manifest["artifacts"]) == 3

    for entry in manifest["artifacts"]:
        single = report_client.get(
            f"/api/runs/{run_id}/artifacts/{entry['id']}/download"
        )
        assert single.status_code == 200
        assert _sheet_names(single.content) == ["Accounts"]

    bundle = report_client.get(f"/api/runs/{run_id}/artifacts/download/zip")
    assert bundle.status_code == 200
    assert len(_zip_entries(bundle.content)) == 3

    # The tabular output is unaffected by any of it.
    summary = report_client.get(
        f"/api/runs/{run_id}/outputs/summary/download/csv"
    )
    assert summary.status_code == 200

    assert list(quarantine.iterdir()) == []


def test_producing_artifacts_does_not_disturb_the_result_table(
    report_client,
) -> None:
    manifest = _run(report_client)
    run_id = manifest["run_id"]

    preview = report_client.get(f"/api/runs/{run_id}/outputs/summary/preview")

    assert preview.status_code == 200
    body = preview.json()
    assert body["columns"] == ["Rep", "Revenue"]
    assert body["total_rows"] == 3


def test_the_run_still_writes_nothing(report_client, quarantine: Path) -> None:
    manifest = _run(report_client)
    report_client.get(
        f"/api/runs/{manifest['run_id']}/artifacts/download/zip"
    )

    assert list(quarantine.iterdir()) == []
