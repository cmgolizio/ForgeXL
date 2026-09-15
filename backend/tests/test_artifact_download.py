"""Artifact downloads and the batch ZIP, over the real HTTP API.

Covers build plan 12F and 12G, and ends with the Phase 12 exit criterion:

| build plan item                        | tested by                              |
| -------------------------------------- | -------------------------------------- |
| 12F a Run's artifacts as one ZIP       | `TestTheBatchArchive`                  |
| 12F the archive is generated safely    | `TestTheArchiveIsSafe`                 |
| 12G listing generated artifacts        | `TestTheManifestListsArtifacts`        |
| 12G downloading one artifact           | `TestDownloadingOneArtifact`           |
| 12G downloading all of them            | `TestTheBatchArchive`                  |
| exit criterion                         | `TestThePhase12ExitCriterion`          |

The Action driving it is a *test* Action, which is what the exit criterion
asks for: neither registered Action produces artifacts, and build plan 12B is
explicit that none has to.

Every workbook is reopened from the response bytes with the application's own
Excel engine, and every archive is reopened with :mod:`zipfile`, so a file
ForgeXL could not itself read fails here. Nothing is written to disk at any
point, and the ``quarantine`` directory is asserted empty to prove it.
"""

from __future__ import annotations

import io
import re
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
    UnsafeArtifactFilenameError,
    artifact_filename,
    artifact_ids,
)
from app.models.run import new_run_id
from app.models.schemas import ActionInput, ActionOutput, ArtifactType
from app.services import archive, run_store
from app.services.workbook import (
    CellFormat,
    Column,
    ConditionalFormat,
    ConditionalRule,
    Sheet,
    render_workbook,
)

from tests.helpers import csv_bytes, upload_file

HEADER = ("Rep", "Region", "Cases", "Revenue")
ROWS = (
    ("Beth Comeaux", "Gulf", 120, 15340.5),
    ("Kevin Wardell", "Delta", 88, -900.25),
    ("Jennifer Jones", "Gulf", 205, 42110.0),
    ("Beth Comeaux", "Delta", 40, 5120.0),
)


def _source_csv() -> bytes:
    return csv_bytes(HEADER, ROWS)


class _RepReportsAction(Action):
    """One polished workbook per sales rep, plus a summary table.

    The Action build plan Phase 12's exit criterion describes, written against
    nothing but the generic infrastructure: it declares an ordinary tabular
    output, renders each workbook through :mod:`app.services.workbook`, and
    hands the files back as artifacts. It never writes a file, never names a
    directory and never imports the spreadsheet engine.
    """

    id = "rep_reports"
    version = "1.0.0"
    name = "Rep Reports"
    description = "Produces one formatted workbook per sales rep."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="summary", label="Summary"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        frame = inputs["source_file"]
        summary = (
            frame.group_by("Rep", maintain_order=True)
            .agg(pl.col("Cases").sum(), pl.col("Revenue").sum())
        )

        reps = summary.get_column("Rep").to_list()
        artifacts = []
        for rep, generated_id in zip(reps, artifact_ids(reps), strict=True):
            rows = frame.filter(pl.col("Rep") == rep)
            payload = render_workbook(
                [
                    Sheet(
                        name=f"{rep} Detail",
                        frame=rows,
                        title=f"{rep} — September 2026",
                        subtitle="Sales detail by region",
                        columns=(
                            Column("Region", format=CellFormat.TEXT),
                            Column("Cases", format=CellFormat.INTEGER),
                            Column("Revenue", format=CellFormat.CURRENCY),
                        ),
                        total_row={
                            "Cases": int(rows.get_column("Cases").sum()),
                            "Revenue": float(rows.get_column("Revenue").sum()),
                        },
                        conditional_formats=(
                            ConditionalFormat(
                                "Revenue", ConditionalRule.NEGATIVE_RED
                            ),
                        ),
                        table_style="Table Style Medium 2",
                    ),
                    Sheet(name=f"{rep} Summary", frame=summary),
                ]
            )
            artifacts.append(
                Artifact.workbook(
                    id=generated_id,
                    label=f"{rep} — September 2026",
                    filename=artifact_filename(
                        f"{rep} - September 2026", "xlsx"
                    ),
                    payload=payload,
                )
            )

        return ActionResult(
            outputs={"summary": summary},
            metrics={"reports_written": len(artifacts)},
            artifacts=artifacts,
        )


class _TableOnlyAction(Action):
    """An Action from before Phase 12: tables and nothing else."""

    id = "table_only"
    version = "1.0.0"
    name = "Table Only"
    description = "Returns its input unchanged."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["source_file"]})


class _OneArtifactAction(_TableOnlyAction):
    """A Run with exactly one artifact, for the single-file cases."""

    id = "one_artifact"
    name = "One Artifact"

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(
            outputs={"result": inputs["source_file"]},
            artifacts=(
                Artifact.archive(
                    id="bundle",
                    label="Prepared Bundle",
                    filename="Prepared Bundle.zip",
                    payload=b"PK\x05\x06" + b"\x00" * 18,
                ),
            ),
        )


@pytest.fixture
def artifact_client(client, registered_actions):
    registered_actions(
        _RepReportsAction(), _TableOnlyAction(), _OneArtifactAction()
    )
    return client


def _run(client, action_id: str = "rep_reports") -> dict:
    response = client.post(
        "/api/runs",
        data={"action_id": action_id},
        files={"source_file": upload_file("source.csv", _source_csv())},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _artifact_url(run_id: str, artifact_id: str) -> str:
    return f"/api/runs/{run_id}/artifacts/{artifact_id}/download"


def _zip_url(run_id: str) -> str:
    return f"/api/runs/{run_id}/artifacts/download/zip"


#: A fixed moment, so an archive built from it can be compared byte for byte.
MOMENT = datetime(2026, 9, 15, 12, 30, 45, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 12G — the manifest lists what a Run produced
# ---------------------------------------------------------------------------


class TestTheManifestListsArtifacts:
    def test_a_run_reports_one_artifact_per_rep(self, artifact_client) -> None:
        manifest = _run(artifact_client)

        assert [entry["filename"] for entry in manifest["artifacts"]] == [
            "Beth Comeaux - September 2026.xlsx",
            "Kevin Wardell - September 2026.xlsx",
            "Jennifer Jones - September 2026.xlsx",
        ]

    def test_each_entry_carries_the_six_declared_facts(
        self, artifact_client
    ) -> None:
        first = _run(artifact_client)["artifacts"][0]

        assert set(first) == {
            "id",
            "label",
            "filename",
            "media_type",
            "size_bytes",
            "artifact_type",
        }
        assert first["artifact_type"] == "workbook"
        assert first["size_bytes"] > 0

    def test_the_reported_size_is_the_downloaded_size(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        entry = manifest["artifacts"][0]

        response = artifact_client.get(
            _artifact_url(manifest["run_id"], entry["id"])
        )
        assert len(response.content) == entry["size_bytes"]

    def test_re_fetching_the_run_reports_the_same_artifacts(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        refetched = artifact_client.get(
            f"/api/runs/{manifest['run_id']}"
        ).json()

        assert refetched["artifacts"] == manifest["artifacts"]

    def test_a_table_only_run_reports_none(self, artifact_client) -> None:
        manifest = _run(artifact_client, "table_only")

        assert manifest["artifacts"] == []

    def test_no_response_carries_a_server_path(self, artifact_client) -> None:
        """Build plan 6F.8, unchanged by this phase."""
        manifest = _run(artifact_client)
        body = artifact_client.get(f"/api/runs/{manifest['run_id']}").text

        for fragment in ("/home/", "/tmp", "/var/", "C:\\"):
            assert fragment not in body


# ---------------------------------------------------------------------------
# 12G — downloading one artifact
# ---------------------------------------------------------------------------


class TestDownloadingOneArtifact:
    def test_the_bytes_are_a_real_workbook(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        entry = manifest["artifacts"][0]

        response = artifact_client.get(
            _artifact_url(manifest["run_id"], entry["id"])
        )

        assert response.status_code == 200
        assert response.content[:2] == b"PK"
        reader = fastexcel.read_excel(response.content)
        assert reader.sheet_names == [
            "Beth Comeaux Detail",
            "Beth Comeaux Summary",
        ]

    def test_the_workbook_holds_that_rep_s_rows(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], manifest["artifacts"][0]["id"])
        )

        frame = (
            fastexcel.read_excel(response.content)
            .load_sheet_by_name("Beth Comeaux Detail", header_row=3)
            .to_polars()
        )
        assert frame.get_column("Region").to_list()[:2] == ["Gulf", "Delta"]

    def test_the_media_type_is_the_artifact_s_own(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], manifest["artifacts"][0]["id"])
        )

        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_the_file_arrives_under_the_actions_own_filename(
        self, artifact_client
    ) -> None:
        """The point of an artifact: the name the Action chose, unchanged."""
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], manifest["artifacts"][0]["id"])
        )

        disposition = response.headers["content-disposition"]
        assert 'filename="Beth Comeaux - September 2026.xlsx"' in disposition

    def test_a_non_ascii_filename_survives_the_header(
        self, artifact_client, registered_actions
    ) -> None:
        """A report for "Château Réal" must arrive spelled that way.

        RFC 6266: the ASCII parameter is a fallback and ``filename*`` carries
        the real name. Without the second one the name arrives mangled.
        """

        class _Accented(_TableOnlyAction):
            id = "accented"

            def run(self, inputs):
                return ActionResult(
                    outputs={"result": inputs["source_file"]},
                    artifacts=(
                        Artifact.workbook(
                            id="chateau",
                            label="Château Réal",
                            filename="Château Réal — Q3.xlsx",
                            payload=b"PK\x03\x04",
                        ),
                    ),
                )

        registered_actions(_Accented())
        manifest = _run(artifact_client, "accented")
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], "chateau")
        )

        disposition = response.headers["content-disposition"]
        assert "filename*=UTF-8''" in disposition
        assert "Ch%C3%A2teau%20R%C3%A9al" in disposition
        assert disposition.isascii()

    def test_downloading_twice_returns_identical_bytes(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        url = _artifact_url(manifest["run_id"], manifest["artifacts"][0]["id"])

        assert artifact_client.get(url).content == artifact_client.get(url).content

    def test_an_unknown_artifact_id_is_refused(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], "not_a_report")
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "UNKNOWN_ARTIFACT"

    def test_the_refusal_names_what_the_run_does_have(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], "not_a_report")
        )

        available = response.json()["error"]["details"][
            "available_artifact_ids"
        ]
        assert "beth-comeaux" in available

    @pytest.mark.parametrize(
        "artifact_id",
        ["..", "..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "a%00b"],
    )
    def test_a_path_shaped_artifact_id_reaches_nothing(
        self, artifact_client, artifact_id: str
    ) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(
            _artifact_url(manifest["run_id"], artifact_id)
        )

        assert response.status_code == 404

    def test_an_unknown_run_is_refused(self, artifact_client) -> None:
        response = artifact_client.get(
            _artifact_url(new_run_id(), "beth-comeaux")
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "UNKNOWN_RUN"

    def test_a_forgotten_run_offers_nothing(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        run_store.delete_run(manifest["run_id"])

        response = artifact_client.get(
            _artifact_url(manifest["run_id"], "beth-comeaux")
        )
        assert response.status_code == 404

    def test_a_run_that_no_longer_holds_its_result_reports_that(
        self, artifact_client
    ) -> None:
        """Recorded but released is MISSING_ARTIFACT, not UNKNOWN_ARTIFACT."""
        manifest = _run(artifact_client)
        run = run_store.get_run(manifest["run_id"])
        run_store.update_run(run.with_changes(result=None))

        response = artifact_client.get(
            _artifact_url(manifest["run_id"], "beth-comeaux")
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MISSING_ARTIFACT"


# ---------------------------------------------------------------------------
# 12F — the batch archive
# ---------------------------------------------------------------------------


class TestTheBatchArchive:
    def test_every_artifact_is_in_the_archive(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        assert response.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert names == [
            entry["filename"] for entry in manifest["artifacts"]
        ]

    def test_each_entry_is_the_file_it_downloads_on_its_own(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client)
        bundle = zipfile.ZipFile(
            io.BytesIO(artifact_client.get(_zip_url(manifest["run_id"])).content)
        )

        for entry in manifest["artifacts"]:
            single = artifact_client.get(
                _artifact_url(manifest["run_id"], entry["id"])
            )
            assert bundle.read(entry["filename"]) == single.content

    def test_the_archive_is_intact(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        bundle = zipfile.ZipFile(
            io.BytesIO(artifact_client.get(_zip_url(manifest["run_id"])).content)
        )

        assert bundle.testzip() is None

    def test_the_archive_is_named_by_the_forgexl_convention(
        self, artifact_client
    ) -> None:
        """The bundle is ForgeXL's file, so it takes ForgeXL's name (6F.6).

        Unlike a single artifact, which arrives under the name its Action
        chose: nobody chose a name for the bundle, and inventing one from the
        first report inside it would be a guess.
        """
        manifest = _run(artifact_client)
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        found = re.search(
            r'filename="([^"]+)"', response.headers["content-disposition"]
        )
        assert found is not None
        assert re.fullmatch(
            r"forgexl-rep-reports-\d{8}-\d{6}\.zip", found.group(1)
        )

    def test_the_media_type_is_zip(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        assert response.headers["content-type"].startswith("application/zip")

    def test_downloading_the_bundle_twice_returns_identical_bytes(
        self, artifact_client
    ) -> None:
        """Build plan 12E: deterministic where appropriate."""
        manifest = _run(artifact_client)
        url = _zip_url(manifest["run_id"])

        assert artifact_client.get(url).content == artifact_client.get(url).content

    def test_a_run_with_one_artifact_still_bundles(
        self, artifact_client
    ) -> None:
        """Whether the bundle is worth offering is the client's decision."""
        manifest = _run(artifact_client, "one_artifact")
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        assert response.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
            "Prepared Bundle.zip"
        ]

    def test_a_run_with_no_artifacts_has_nothing_to_bundle(
        self, artifact_client
    ) -> None:
        manifest = _run(artifact_client, "table_only")
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MISSING_ARTIFACT"

    def test_an_unknown_run_has_nothing_to_bundle(
        self, artifact_client
    ) -> None:
        response = artifact_client.get(_zip_url(new_run_id()))

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "UNKNOWN_RUN"

    def test_the_zip_route_is_not_shadowed_by_the_artifact_route(
        self, artifact_client
    ) -> None:
        """``/artifacts/download/zip`` must never be read as artifact "download"."""
        manifest = _run(artifact_client)
        response = artifact_client.get(_zip_url(manifest["run_id"]))

        assert response.headers["content-type"].startswith("application/zip")

    def test_nothing_is_written_while_bundling(
        self, artifact_client, quarantine: Path
    ) -> None:
        manifest = _run(artifact_client)
        artifact_client.get(_zip_url(manifest["run_id"]))

        assert list(quarantine.iterdir()) == []


class TestTheArchiveIsSafe:
    """Build plan 12F: no nested or traversing path, ever."""

    def _artifact(self, filename: str) -> Artifact:
        return Artifact.workbook(
            id="a", label="A", filename=filename, payload=b"x"
        )

    def test_every_entry_is_a_flat_name(self, artifact_client) -> None:
        manifest = _run(artifact_client)
        names = zipfile.ZipFile(
            io.BytesIO(artifact_client.get(_zip_url(manifest["run_id"])).content)
        ).namelist()

        for name in names:
            assert "/" not in name
            assert "\\" not in name
            assert not name.startswith(".")
            assert ".." not in name

    def test_the_writer_checks_the_filename_again(self) -> None:
        """Defence in depth: the model checked it, and so does this.

        Constructed past the model's own guard, because the point is that the
        archive writer does not rely on someone else having checked.
        """
        smuggled = self._artifact("safe.xlsx")
        object.__setattr__(smuggled, "filename", "../../evil.xlsx")

        with pytest.raises(UnsafeArtifactFilenameError):
            archive.to_zip_bytes([smuggled], timestamp=MOMENT)

    def test_two_entries_may_not_share_a_name(self) -> None:
        one = self._artifact("Report.xlsx")
        two = Artifact.workbook(
            id="b", label="B", filename="report.XLSX", payload=b"y"
        )

        with pytest.raises(ValueError, match="overwrite"):
            archive.to_zip_bytes([one, two], timestamp=MOMENT)

    def test_an_empty_archive_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one artifact"):
            archive.to_zip_bytes([], timestamp=MOMENT)

    def test_the_same_artifacts_and_timestamp_give_the_same_bytes(
        self,
    ) -> None:
        artifacts = [
            Artifact.workbook(
                id=f"a{index}",
                label=f"A{index}",
                filename=f"Report {index}.xlsx",
                payload=b"x" * 64,
            )
            for index in range(3)
        ]
        assert archive.to_zip_bytes(
            artifacts, timestamp=MOMENT
        ) == archive.to_zip_bytes(artifacts, timestamp=MOMENT)

    def test_a_timestamp_the_format_cannot_hold_is_clamped(self) -> None:
        """Rather than silently wrapping into the 2040s."""
        payload = archive.to_zip_bytes(
            [self._artifact("a.xlsx")],
            timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )

        (info,) = zipfile.ZipFile(io.BytesIO(payload)).infolist()
        assert info.date_time[0] == 1980

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        naive = datetime(2026, 9, 15, 12, 0, 0)
        aware = naive.replace(tzinfo=timezone.utc)

        assert archive.to_zip_bytes(
            [self._artifact("a.xlsx")], timestamp=naive
        ) == archive.to_zip_bytes([self._artifact("a.xlsx")], timestamp=aware)

    def test_entries_are_extractable_as_ordinary_files(self) -> None:
        payload = archive.to_zip_bytes(
            [self._artifact("a.xlsx")], timestamp=MOMENT
        )

        (info,) = zipfile.ZipFile(io.BytesIO(payload)).infolist()
        assert (info.external_attr >> 16) & 0o777 == 0o644


# ---------------------------------------------------------------------------
# The Phase 12 exit criterion
# ---------------------------------------------------------------------------


class TestThePhase12ExitCriterion:
    """"A test Action can generate multiple polished XLSX artifacts plus a ZIP
    bundle through generic ForgeXL infrastructure."

    One test, walked end to end, asserting each clause of that sentence.
    """

    def test_a_test_action_produces_polished_workbooks_and_a_zip(
        self, artifact_client, quarantine: Path
    ) -> None:
        manifest = _run(artifact_client)

        # ... multiple artifacts ...
        assert len(manifest["artifacts"]) == 3
        assert {entry["artifact_type"] for entry in manifest["artifacts"]} == {
            ArtifactType.WORKBOOK.value
        }

        # ... which are polished XLSX: several worksheets, a title above the
        # table, formatted numbers, a frozen header and a totals row ...
        first = manifest["artifacts"][0]
        workbook = artifact_client.get(
            _artifact_url(manifest["run_id"], first["id"])
        ).content
        opened = openpyxl.load_workbook(io.BytesIO(workbook))
        assert len(opened.sheetnames) == 2
        detail = opened[opened.sheetnames[0]]
        assert detail["A1"].value == "Beth Comeaux — September 2026"
        assert detail["A4"].value == "Region"
        assert detail["C5"].number_format == "$#,##0.00;($#,##0.00)"
        assert detail.freeze_panes == "A5"
        assert detail["B7"].value == 160
        assert detail["B7"].font.bold is True

        # ... plus a ZIP bundle holding all of them ...
        bundle = artifact_client.get(_zip_url(manifest["run_id"])).content
        assert zipfile.ZipFile(io.BytesIO(bundle)).namelist() == [
            entry["filename"] for entry in manifest["artifacts"]
        ]

        # ... through generic infrastructure: the Action is not registered,
        # no route names it, and the tabular side of the Run is untouched.
        assert manifest["outputs"][0]["id"] == "summary"
        assert manifest["outputs"][0]["row_count"] == 3
        assert manifest["metrics"] == {"reports_written": 3}

        # ... and nothing was written to disk to do any of it.
        assert list(quarantine.iterdir()) == []

    def test_the_tabular_side_of_the_run_still_works(
        self, artifact_client
    ) -> None:
        """An artifact-producing Run previews and exports like any other."""
        manifest = _run(artifact_client)
        run_id = manifest["run_id"]

        preview = artifact_client.get(
            f"/api/runs/{run_id}/outputs/summary/preview"
        )
        assert preview.status_code == 200
        assert preview.json()["total_rows"] == 3

        csv = artifact_client.get(
            f"/api/runs/{run_id}/outputs/summary/download/csv"
        )
        assert csv.status_code == 200
        assert csv.text.splitlines()[0] == "Rep,Cases,Revenue"

        xlsx = artifact_client.get(f"/api/runs/{run_id}/download/xlsx")
        assert xlsx.status_code == 200
        assert xlsx.content[:2] == b"PK"

    def test_adding_the_action_required_no_change_to_the_routes(
        self, artifact_client
    ) -> None:
        """Build plan 12G: nothing is hardcoded per Action or per rep.

        The artifact routes are addressed by Run ID and artifact ID alone, and
        the IDs came from the data. No route, no service and no frontend file
        knows this Action exists.
        """
        manifest = _run(artifact_client)
        paths = artifact_client.get("/openapi.json").json()["paths"]

        assert "/api/runs/{run_id}/artifacts/{artifact_id}/download" in paths
        assert "/api/runs/{run_id}/artifacts/download/zip" in paths
        assert not any("rep" in path for path in paths)
