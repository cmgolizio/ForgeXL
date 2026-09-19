"""Artifacts: the model, the result contract, and the Run that holds them.

Covers build plan 12A, 12B, 12C and 12E:

| build plan item                          | tested by                                        |
| ---------------------------------------- | ------------------------------------------------ |
| 12A dataset outputs vs artifacts         | `TestOutputsAndArtifactsStaySeparate`            |
| 12B extend `ActionResult` safely         | `TestTheResultContractIsExtendedSafely`          |
| 12C artifact metadata                    | `TestArtifactMetadata`                           |
| 12C no filesystem paths                  | `TestNothingNamesAPath`                          |
| 12C artifacts live in the Run's memory   | `TestArtifactsLiveAndDieWithTheRun`              |
| 12E multiple artifacts per Run           | `TestManyArtifactsPerRun`                        |
| 12E collision-safe IDs and filenames     | `TestCollisionsAreRefused`                       |
| 12F filename safety (the rule, not ZIP)  | `TestFilenamesAreFlatNames`                      |

The ZIP bundle and the HTTP routes are `test_artifact_download.py`; the rich
XLSX renderer is `test_workbook.py`.

Nothing here writes a file. The autouse ``quarantine`` fixture puts every test
in an empty working directory, and the tests that could plausibly write assert
it is still empty afterwards.
"""

from __future__ import annotations

import gc
import weakref
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from app.actions.base import Action, ActionResult, Artifact
from app.errors import ActionExecutionError
from app.models.artifact import (
    ARTIFACT_ID_PATTERN,
    DEFAULT_MEDIA_TYPES,
    MAX_ARTIFACT_FILENAME_LENGTH,
    MAX_ARTIFACT_ID_LENGTH,
    UnsafeArtifactFilenameError,
    artifact_filename,
    artifact_id,
    artifact_ids,
    check_artifact_filename,
)
from app.models.run import RunResult
from app.models.schemas import (
    ActionInput,
    ActionOutput,
    ArtifactMetadata,
    ArtifactType,
)
from app.services import run_store
from app.services.runner import execute_run

from tests.helpers import csv_bytes, upload

HEADER = ("Rep", "Cases")
ROWS = (("Beth Comeaux", 120), ("Kevin Wardell", 88), ("Jennifer Jones", 205))


def _source_csv() -> bytes:
    return csv_bytes(HEADER, ROWS)


def _workbook(name: str, *, payload: bytes = b"PK\x03\x04 pretend") -> Artifact:
    """A stand-in artifact. The renderer has its own module of tests."""
    return Artifact.workbook(
        id=name.lower().replace(" ", "_"),
        label=name,
        filename=f"{name}.xlsx",
        payload=payload,
    )


class _ReportAction(Action):
    """Returns one table and as many artifacts as it is asked for.

    Deliberately not one of the registered Actions: build plan 12B requires
    that neither of those changes, and `test_contract_freeze.py` pins that.
    """

    id = "report_action"
    version = "1.0.0"
    name = "Report Action"
    description = "Produces a summary table plus one workbook per rep."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="summary", label="Summary"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        frame = inputs["source_file"]
        return ActionResult(
            outputs={"summary": frame},
            metrics={"reports_written": frame.height},
            artifacts=tuple(
                _workbook(rep, payload=f"report for {rep}".encode())
                for rep in frame.get_column("Rep")
            ),
        )


class _TableOnlyAction(Action):
    """An Action written the way every Action was written before Phase 12."""

    id = "table_only"
    version = "1.0.0"
    name = "Table Only"
    description = "Returns its input unchanged and nothing else."
    inputs = (
        ActionInput(
            id="source_file", label="Source File", accepted_extensions=(".csv",)
        ),
    )
    outputs = (ActionOutput(id="result", label="Result"),)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        return ActionResult(outputs={"result": inputs["source_file"]})


def _execute(action: Action):
    return execute_run(action, {"source_file": upload("s.csv", _source_csv())})


# ---------------------------------------------------------------------------
# 12A — dataset outputs and artifacts are different things
# ---------------------------------------------------------------------------


class TestOutputsAndArtifactsStaySeparate:
    """Build plan 12A: a finished workbook is not merely another DataFrame."""

    def test_a_run_reports_both_and_does_not_confuse_them(self) -> None:
        manifest = _execute(_ReportAction()).manifest

        assert [output.id for output in manifest.outputs] == ["summary"]
        assert [artifact.id for artifact in manifest.artifacts] == [
            "beth_comeaux",
            "kevin_wardell",
            "jennifer_jones",
        ]

    def test_an_artifact_is_not_listed_as_an_output(self) -> None:
        manifest = _execute(_ReportAction()).manifest

        output_ids = {output.id for output in manifest.outputs}
        artifact_ids = {artifact.id for artifact in manifest.artifacts}
        assert output_ids.isdisjoint(artifact_ids)

    def test_an_artifact_carries_no_row_or_column_counts(self) -> None:
        """The facts that belong to a table do not belong to a file.

        `OutputMetadata` has `row_count`, `column_schema` and the two column
        lists because a result table has rows and columns. An artifact has
        bytes. Giving it a column schema would be the pretence build plan 12A
        refuses.
        """
        fields = set(ArtifactMetadata.model_fields)
        assert not fields & {
            "row_count",
            "column_count",
            "columns",
            "column_schema",
            "input_row_count",
            "columns_added",
            "columns_removed",
            "formats",
        }


# ---------------------------------------------------------------------------
# 12B — extending the result contract safely
# ---------------------------------------------------------------------------


class TestTheResultContractIsExtendedSafely:
    """Build plan 12B: an Action that returns only DataFrames stays valid."""

    def test_a_result_with_only_outputs_is_still_a_valid_result(self) -> None:
        result = ActionResult(outputs={"result": pl.DataFrame({"a": [1]})})

        assert result.artifacts == ()
        assert result.metrics == {}
        assert result.rows_affected is None

    def test_a_table_only_run_reports_no_artifacts(self) -> None:
        manifest = _execute(_TableOnlyAction()).manifest

        assert manifest.artifacts == ()
        assert manifest.audit.artifacts == ()

    def test_a_table_only_manifest_is_unchanged_in_every_other_respect(
        self,
    ) -> None:
        """The addition is invisible to a Run that does not use it.

        `artifacts` defaults to empty, so nothing else about the serialised
        manifest moved — which is why `MANIFEST_SCHEMA_VERSION` stayed at 2.
        """
        payload = _execute(_TableOnlyAction()).manifest.model_dump(mode="json")

        assert payload["schema_version"] == 2
        assert payload["artifacts"] == []
        assert payload["outputs"][0]["row_count"] == len(ROWS)

    def test_positional_construction_still_means_what_it_meant(self) -> None:
        """`artifacts` was appended, not inserted.

        A caller written before Phase 12 that passed metrics positionally must
        still be passing metrics.
        """
        frame = pl.DataFrame({"a": [1]})
        result = ActionResult(frame_outputs := {"result": frame}, {"count": 1}, 1)

        assert result.outputs is frame_outputs
        assert result.metrics == {"count": 1}
        assert result.rows_affected == 1
        assert result.artifacts == ()

    def test_an_action_is_never_required_to_produce_an_artifact(self) -> None:
        """The whole Run pipeline works with none, and reports none."""
        outcome = _execute(_TableOnlyAction())

        assert outcome.run.artifacts == ()
        assert outcome.run.result is not None
        assert outcome.run.result.artifacts == {}

    def test_a_sequence_of_artifacts_is_frozen_into_a_tuple(self) -> None:
        """A caller cannot append to a result after handing it over."""
        supplied = [_workbook("One")]
        result = ActionResult(
            outputs={"result": pl.DataFrame({"a": [1]})}, artifacts=supplied
        )
        supplied.append(_workbook("Two"))

        assert isinstance(result.artifacts, tuple)
        assert [artifact.label for artifact in result.artifacts] == ["One"]


# ---------------------------------------------------------------------------
# 12C — artifact metadata
# ---------------------------------------------------------------------------


class TestArtifactMetadata:
    """Build plan 12C: ID, label, filename, media type, byte size, type."""

    def test_the_six_declared_facts_are_reported(self) -> None:
        artifact = Artifact.workbook(
            id="beth",
            label="Beth Comeaux — September 2026",
            filename="Beth Comeaux - September 2026.xlsx",
            payload=b"0123456789",
        )

        assert artifact.to_metadata() == ArtifactMetadata(
            id="beth",
            label="Beth Comeaux — September 2026",
            filename="Beth Comeaux - September 2026.xlsx",
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            size_bytes=10,
            artifact_type=ArtifactType.WORKBOOK,
        )

    def test_the_size_is_measured_not_declared(self) -> None:
        """A byte count that could disagree with the download is not a fact."""
        assert _workbook("A", payload=b"x" * 4096).size_bytes == 4096

    @pytest.mark.parametrize("artifact_type", list(ArtifactType))
    def test_every_artifact_type_has_a_default_media_type(
        self, artifact_type: ArtifactType
    ) -> None:
        artifact = Artifact(
            id="a",
            label="A",
            filename="a.bin",
            payload=b"x",
            artifact_type=artifact_type,
        )

        assert artifact.media_type == DEFAULT_MEDIA_TYPES[artifact_type]
        assert artifact.media_type

    def test_a_stated_media_type_is_kept(self) -> None:
        artifact = Artifact(
            id="a",
            label="A",
            filename="a.csv",
            payload=b"x",
            artifact_type=ArtifactType.TEXT,
            media_type="text/csv",
        )

        assert artifact.media_type == "text/csv"

    def test_the_archive_helper_declares_the_zip_type(self) -> None:
        artifact = Artifact.archive(
            id="bundle", label="Bundle", filename="bundle.zip", payload=b"PK"
        )

        assert artifact.artifact_type is ArtifactType.ARCHIVE
        assert artifact.media_type == "application/zip"

    def test_an_artifact_with_no_label_is_refused(self) -> None:
        with pytest.raises(ValueError, match="label"):
            Artifact(id="a", label="  ", filename="a.xlsx", payload=b"x")

    def test_an_artifact_whose_payload_is_not_bytes_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bytes"):
            Artifact(
                id="a",
                label="A",
                filename="a.xlsx",
                payload="text",  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        "artifact_id", ["", "has space", "../evil", "a/b", "-leading", "a?b"]
    )
    def test_an_unusable_artifact_id_is_refused(self, artifact_id: str) -> None:
        """An artifact ID becomes a URL path segment (build plan 12G)."""
        with pytest.raises(ValueError, match="Artifact id"):
            Artifact(
                id=artifact_id, label="A", filename="a.xlsx", payload=b"x"
            )

    def test_the_metadata_is_frozen(self) -> None:
        metadata = _workbook("A").to_metadata()

        with pytest.raises(ValidationError):
            metadata.label = "changed"


class TestNothingNamesAPath:
    """Build plan 12C: "Do not expose local filesystem paths"."""

    def test_the_metadata_model_has_no_path_field(self) -> None:
        assert not any(
            "path" in name or name in {"location", "directory"}
            for name in ArtifactMetadata.model_fields
        )

    def test_no_manifest_value_looks_like_a_path(self) -> None:
        payload = _execute(_ReportAction()).manifest.model_dump_json()

        for fragment in ("/home/", "/tmp", "/var/", "C:\\", "\\\\"):
            assert fragment not in payload


# ---------------------------------------------------------------------------
# 12E — many artifacts, collision-safe
# ---------------------------------------------------------------------------


class TestManyArtifactsPerRun:
    """Build plan 12E: one Action, many files."""

    def test_one_run_produces_one_file_per_row(self) -> None:
        manifest = _execute(_ReportAction()).manifest

        assert [artifact.filename for artifact in manifest.artifacts] == [
            "Beth Comeaux.xlsx",
            "Kevin Wardell.xlsx",
            "Jennifer Jones.xlsx",
        ]

    def test_the_order_is_the_order_the_action_listed(self) -> None:
        """Not a dictionary's iteration order, and not sorted behind its back."""
        outcome = _execute(_ReportAction())
        assert outcome.run.result is not None

        assert [artifact.id for artifact in outcome.run.artifacts] == list(
            outcome.run.result.artifacts
        )

    def test_the_audit_lists_every_file(self) -> None:
        audit = _execute(_ReportAction()).manifest.audit

        assert [entry.filename for entry in audit.artifacts] == [
            "Beth Comeaux.xlsx",
            "Kevin Wardell.xlsx",
            "Jennifer Jones.xlsx",
        ]
        assert {entry.artifact_type for entry in audit.artifacts} == {
            ArtifactType.WORKBOOK
        }

    def test_repeating_the_run_produces_the_same_ids_and_filenames(self) -> None:
        """Build plan 12E: deterministic where appropriate.

        Same input, same Action version, same names — so a re-run that is
        meant to reproduce a report reproduces the names of its files too.
        """
        first = _execute(_ReportAction()).manifest
        second = _execute(_ReportAction()).manifest

        assert [(a.id, a.filename) for a in first.artifacts] == [
            (a.id, a.filename) for a in second.artifacts
        ]

    def test_the_bytes_are_the_bytes_the_action_produced(self) -> None:
        outcome = _execute(_ReportAction())
        assert outcome.run.result is not None
        artifact = outcome.run.result.artifact("beth_comeaux")

        assert artifact is not None
        assert artifact.payload == b"report for Beth Comeaux"


class TestCollisionsAreRefused:
    """Build plan 12E: artifact IDs and filenames must be collision-safe."""

    def test_two_artifacts_may_not_share_an_id(self) -> None:
        with pytest.raises(ValueError, match="same id"):
            ActionResult(
                outputs={"r": pl.DataFrame({"a": [1]})},
                artifacts=(
                    Artifact.workbook(
                        id="same", label="One", filename="one.xlsx", payload=b"x"
                    ),
                    Artifact.workbook(
                        id="same", label="Two", filename="two.xlsx", payload=b"y"
                    ),
                ),
            )

    def test_two_artifacts_may_not_share_a_filename(self) -> None:
        with pytest.raises(ValueError, match="same filename"):
            ActionResult(
                outputs={"r": pl.DataFrame({"a": [1]})},
                artifacts=(
                    _workbook("Report"),
                    Artifact.workbook(
                        id="other",
                        label="Other",
                        filename="Report.xlsx",
                        payload=b"y",
                    ),
                ),
            )

    def test_filenames_collide_case_insensitively(self) -> None:
        """macOS and Windows treat these as one file; so does this."""
        with pytest.raises(ValueError, match="same filename"):
            ActionResult(
                outputs={"r": pl.DataFrame({"a": [1]})},
                artifacts=(
                    _workbook("Report"),
                    Artifact.workbook(
                        id="other",
                        label="Other",
                        filename="REPORT.XLSX",
                        payload=b"y",
                    ),
                ),
            )

    def test_neither_is_renamed_to_make_room(self) -> None:
        """The collision is reported, never worked around.

        Worksheet names *are* de-collided, because Excel forces it and the
        label they came from was lossy anyway. An artifact filename is what
        the Action chose, so renaming one would hand the user a file called
        something they did not ask for (build plan section 3.3).
        """
        colliding = (_workbook("Report"), _workbook("Report"))
        with pytest.raises(ValueError):
            ActionResult(
                outputs={"r": pl.DataFrame({"a": [1]})}, artifacts=colliding
            )

        assert [artifact.filename for artifact in colliding] == [
            "Report.xlsx",
            "Report.xlsx",
        ]

    def test_a_colliding_run_fails_as_an_action_failure(self) -> None:
        """The user sees a structured error, not a traceback."""

        class _Colliding(_ReportAction):
            id = "colliding_reports"

            def run(self, inputs):
                return ActionResult(
                    outputs={"summary": inputs["source_file"]},
                    artifacts=(_workbook("Same"), _workbook("Same")),
                )

        with pytest.raises(ActionExecutionError) as raised:
            _execute(_Colliding())

        assert raised.value.code == "ACTION_FAILED"
        assert "Traceback" not in raised.value.message


class TestBuildingIdsFromData:
    """Build plan 12E: IDs derived from data, collision-safe and deterministic."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Beth Comeaux", "beth-comeaux"),
            ("Château Réal", "chateau-real"),
            ("SKU/1234", "sku-1234"),
            ("  padded  ", "padded"),
            ("", "artifact"),
            ("!!!", "artifact"),
            ("日本語", "artifact"),
            ("A - B", "a-b"),
        ],
    )
    def test_text_becomes_a_url_safe_token(
        self, value: str, expected: str
    ) -> None:
        assert artifact_id(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["Château Réal", "!!!", "   ", "日本語", "x" * 500, "../../etc"],
    )
    def test_every_generated_id_is_one_an_artifact_accepts(
        self, value: str
    ) -> None:
        generated = artifact_id(value)

        assert ARTIFACT_ID_PATTERN.fullmatch(generated)
        assert len(generated) <= MAX_ARTIFACT_ID_LENGTH
        assert Artifact.workbook(
            id=generated, label="A", filename="a.xlsx", payload=b"x"
        ).id == generated

    def test_colliding_values_are_numbered_apart(self) -> None:
        """An ID is an internal handle, so a collision is resolved, not fatal."""
        assert artifact_ids(
            ["Beth Comeaux", "Beth Comeaux", "Beth Comeaux"]
        ) == ("beth-comeaux", "beth-comeaux-2", "beth-comeaux-3")

    def test_the_first_value_keeps_the_unnumbered_id(self) -> None:
        assert artifact_ids(["A", "B", "A"]) == ("a", "b", "a-2")

    def test_numbering_stays_inside_the_length_limit(self) -> None:
        long_name = "x" * MAX_ARTIFACT_ID_LENGTH
        generated = artifact_ids([long_name, long_name])

        assert all(
            len(value) <= MAX_ARTIFACT_ID_LENGTH for value in generated
        )
        assert generated[0] != generated[1]

    def test_the_same_values_always_produce_the_same_ids(self) -> None:
        """So a re-run reproduces the Run's download URLs (build plan 12E)."""
        values = ["Beth Comeaux", "Château Réal", "Beth Comeaux"]

        assert artifact_ids(values) == artifact_ids(values)

    def test_generated_ids_satisfy_the_result_contract(self) -> None:
        """The whole point: an Action can build them and not be refused."""
        reps = ["Beth Comeaux", "Beth Comeaux", "Château Réal"]
        artifacts = [
            Artifact.workbook(
                id=generated,
                label=rep,
                filename=artifact_filename(f"{rep} {index}", "xlsx"),
                payload=b"x",
            )
            for index, (rep, generated) in enumerate(
                zip(reps, artifact_ids(reps), strict=True)
            )
        ]

        result = ActionResult(
            outputs={"r": pl.DataFrame({"a": [1]})}, artifacts=artifacts
        )
        assert [artifact.id for artifact in result.artifacts] == [
            "beth-comeaux",
            "beth-comeaux-2",
            "chateau-real",
        ]

    def test_an_id_folds_accents_but_a_filename_keeps_them(self) -> None:
        """A token is not a name (build plan 12C)."""
        assert artifact_id("Château Réal") == "chateau-real"
        assert artifact_filename("Château Réal", "xlsx") == "Château Réal.xlsx"


# ---------------------------------------------------------------------------
# 12F — the filename rule itself
# ---------------------------------------------------------------------------


class TestFilenamesAreFlatNames:
    """Build plan 12F: a filename can never create a path."""

    @pytest.mark.parametrize(
        "filename",
        [
            "../evil.xlsx",
            "..\\evil.xlsx",
            "reports/beth.xlsx",
            "reports\\beth.xlsx",
            "/etc/passwd",
            "C:\\Windows\\system.ini",
            "..",
            ".",
            ".hidden.xlsx",
            "report\n.xlsx",
            "report\x00.xlsx",
            "report .xlsx ",
            " report.xlsx",
            "report.xlsx.",
            "",
            "   ",
            "CON.xlsx",
            "lpt1.txt",
            "a" * (MAX_ARTIFACT_FILENAME_LENGTH + 1),
        ],
    )
    def test_an_unsafe_filename_is_refused(self, filename: str) -> None:
        with pytest.raises(UnsafeArtifactFilenameError):
            check_artifact_filename(filename)

        with pytest.raises(UnsafeArtifactFilenameError):
            Artifact.workbook(
                id="a", label="A", filename=filename, payload=b"x"
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "Beth Comeaux - September 2026.xlsx",
            "Château Réal — Q3.xlsx",
            "report.2026.09.xlsx",
            "a.xlsx",
            "bundle.zip",
            "no extension",
        ],
    )
    def test_an_ordinary_filename_is_accepted_as_written(
        self, filename: str
    ) -> None:
        assert check_artifact_filename(filename) == filename

    @pytest.mark.parametrize(
        "stem,expected",
        [
            ("Beth Comeaux - September 2026", "Beth Comeaux - September 2026.xlsx"),
            ("../../etc/passwd", "etc passwd.xlsx"),
            ("A/B Test", "A B Test.xlsx"),
            ("  padded  ", "padded.xlsx"),
            ("", "artifact.xlsx"),
            ("...", "artifact.xlsx"),
            ("CON", "CON file.xlsx"),
            ("Two\n\nLines", "Two Lines.xlsx"),
        ],
    )
    def test_the_builder_cleans_text_into_a_safe_name(
        self, stem: str, expected: str
    ) -> None:
        """Cleaning happens where an Action asks for it, explicitly."""
        assert artifact_filename(stem, "xlsx") == expected

    def test_the_builder_always_produces_an_accepted_name(self) -> None:
        for stem in ("../..", "\\\\server\\share", "?" * 400, "\x00\x01"):
            assert check_artifact_filename(artifact_filename(stem, "xlsx"))

    def test_the_builder_truncates_to_the_limit(self) -> None:
        name = artifact_filename("x" * 500, "xlsx")

        assert len(name) <= MAX_ARTIFACT_FILENAME_LENGTH
        assert name.endswith(".xlsx")

    def test_the_builder_normalises_equivalent_spellings(self) -> None:
        """Two Unicode spellings of one name must not become two files.

        "Café" can be written with a precomposed ``é`` or with ``e`` followed
        by a combining acute accent. They look identical, compare unequal, and
        would otherwise produce two artifacts whose filenames a reader could
        not tell apart — so the collision check in
        :class:`~app.actions.base.ActionResult` would not catch it either.
        """
        precomposed = artifact_filename("Caf\u00e9 Report", "xlsx")
        decomposed = artifact_filename("Cafe\u0301 Report", "xlsx")

        assert precomposed == decomposed == "Caf\u00e9 Report.xlsx"
        assert "\u0301" not in precomposed

    def test_an_extension_may_be_given_with_or_without_its_dot(self) -> None:
        assert artifact_filename("A", "xlsx") == artifact_filename("A", ".xlsx")


# ---------------------------------------------------------------------------
# 12C — artifacts live in the Run's memory, and die with it
# ---------------------------------------------------------------------------


class TestArtifactsLiveAndDieWithTheRun:
    """Build plan 12C: artifacts stay in runtime memory for a Run."""

    def test_a_run_writes_nothing_while_producing_files(
        self, quarantine: Path
    ) -> None:
        _execute(_ReportAction())

        assert list(quarantine.iterdir()) == []

    def test_a_stored_run_still_holds_its_artifacts(self) -> None:
        outcome = _execute(_ReportAction())
        reloaded = run_store.get_run(outcome.run.run_id)

        assert reloaded.result is not None
        assert set(reloaded.result.artifacts) == {
            "beth_comeaux",
            "kevin_wardell",
            "jennifer_jones",
        }

    def test_forgetting_the_run_releases_the_bytes(self) -> None:
        """The same rule the result frames follow (build plan 6D.8)."""
        outcome = _execute(_ReportAction())
        run_id = outcome.run.run_id
        result = run_store.get_run(run_id).result
        assert result is not None
        artifact = result.artifact("beth_comeaux")
        assert artifact is not None
        reference = weakref.ref(artifact)

        del artifact, outcome, result
        assert run_store.delete_run(run_id) is True
        gc.collect()

        assert reference() is None

    def test_a_failed_run_offers_no_artifact(self) -> None:
        """Build plan 6D.8: no partially valid result survives a failure."""

        class _FailsAfterRendering(_ReportAction):
            id = "fails_after_rendering"
            outputs = (
                ActionOutput(id="summary", label="Summary"),
                ActionOutput(id="never_produced", label="Never Produced"),
            )

        with pytest.raises(ActionExecutionError):
            _execute(_FailsAfterRendering())

        (run,) = [
            run
            for run in run_store.list_runs()
            if run.action.id == "fails_after_rendering"
        ]
        assert run.status.value == "failed"
        assert run.artifacts == ()
        assert run.result is None
        assert run.to_manifest().artifacts == ()

    def test_the_run_result_exposes_artifacts_read_only(self) -> None:
        result = RunResult.of(
            {"r": pl.DataFrame({"a": [1]})},
            {"one": _workbook("One")},
        )

        with pytest.raises(TypeError):
            result.artifacts["two"] = _workbook("Two")  # type: ignore[index]

    def test_a_result_may_be_built_with_no_artifacts_at_all(self) -> None:
        result = RunResult.of({"r": pl.DataFrame({"a": [1]})})

        assert result.artifacts == {}
        assert result.artifact("anything") is None