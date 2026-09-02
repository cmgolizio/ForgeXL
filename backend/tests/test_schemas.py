"""Schema tests (build plan Phase 2.2).

The Run manifest and preview response are not reachable over HTTP until
Phase 3, so these tests prove the schemas round-trip through JSON now, while
they are cheap to correct.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    MANIFEST_SCHEMA_VERSION,
    ActionReference,
    AuditInput,
    AuditResult,
    ColumnKind,
    ColumnSchema,
    InputMetadata,
    OutputMetadata,
    PreviewResponse,
    RunAudit,
    RunError,
    RunManifest,
    RunStatus,
    ValidationIssue,
    ValidationSummary,
)

PRODUCT_COLUMNS = (
    "SKU",
    "Vintage",
    "Supplier",
    "Producer",
    "Selection",
    "Volume",
)

PRODUCT_SCHEMA = (
    ColumnSchema(name="SKU", dtype="String", kind=ColumnKind.TEXT),
    ColumnSchema(name="Vintage", dtype="Int64", kind=ColumnKind.NUMBER),
    ColumnSchema(name="Supplier", dtype="String", kind=ColumnKind.TEXT),
    ColumnSchema(name="Producer", dtype="String", kind=ColumnKind.TEXT),
    ColumnSchema(name="Selection", dtype="String", kind=ColumnKind.TEXT),
    ColumnSchema(name="Volume", dtype="String", kind=ColumnKind.TEXT),
)


def _manifest(**overrides) -> RunManifest:
    created = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
    defaults = dict(
        run_id="4f27d4bb-7464-4d04-a21b-000000000000",
        status=RunStatus.SUCCEEDED,
        action=ActionReference(
            id="product_master_builder",
            version="1.0.0",
            name="Product Master Builder",
        ),
        created_at=created,
        started_at=created,
        completed_at=created,
        duration_ms=820,
        inputs=(
            InputMetadata(
                slot_id="sales_file",
                original_filename="Q3 sales (final).xlsx",
                stored_filename="source.xlsx",
                file_size_bytes=204_800,
                extension=".xlsx",
                parser_engine="fastexcel",
                worksheet="Sheet1",
                row_count=15_842,
                column_count=6,
                columns=PRODUCT_COLUMNS,
            ),
        ),
        validation=ValidationSummary(passed=True),
        outputs=(
            OutputMetadata(
                id="product_master",
                label="Product Master",
                row_count=1_247,
                column_count=6,
                columns=PRODUCT_COLUMNS,
                formats=("csv", "xlsx"),
                column_schema=PRODUCT_SCHEMA,
                input_row_count=15_842,
                columns_added=(),
                columns_removed=("Customer",),
            ),
        ),
        metrics={"input_rows": 15_842, "output_rows": 1_247},
        audit=RunAudit(
            action=ActionReference(
                id="product_master_builder",
                version="1.0.0",
                name="Product Master Builder",
            ),
            status=RunStatus.SUCCEEDED,
            inputs=(
                AuditInput(
                    slot_id="sales_file",
                    original_filename="Q3 sales (final).xlsx",
                    row_count=15_842,
                    column_count=6,
                ),
            ),
            rows_received=15_842,
            rows_returned=1_247,
            rows_affected=14_595,
            results=(
                AuditResult(
                    output_id="product_master",
                    label="Product Master",
                    row_count=1_247,
                    column_count=6,
                ),
            ),
            primary_result_id="product_master",
            metrics={"input_rows": 15_842, "output_rows": 1_247},
            duration_ms=820,
        ),
    )
    return RunManifest(**{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------


def test_manifest_round_trips_through_json():
    manifest = _manifest()

    restored = RunManifest.model_validate(json.loads(manifest.model_dump_json()))

    assert restored == manifest


def test_manifest_stamps_the_schema_version_without_being_told():
    # Version 2 since Phase 6E: the manifest gained the required result
    # metadata and audit summary, so a version 1 manifest no longer validates.
    assert _manifest().schema_version == MANIFEST_SCHEMA_VERSION == 2


def test_manifest_records_the_original_filename_as_metadata_only():
    """The name the user uploaded is kept, but is not the name used on disk."""
    payload = json.loads(_manifest().model_dump_json())
    stored = payload["inputs"][0]

    assert stored["original_filename"] == "Q3 sales (final).xlsx"
    assert stored["stored_filename"] == "source.xlsx"


def test_manifest_records_the_parser_engine_and_worksheet():
    stored = json.loads(_manifest().model_dump_json())["inputs"][0]

    assert stored["parser_engine"] == "fastexcel"
    assert stored["worksheet"] == "Sheet1"


def test_manifest_carries_no_dataframe_rows():
    """Section 23: the manifest describes outputs; it never contains them."""
    payload = json.loads(_manifest().model_dump_json())

    assert set(payload["outputs"][0]) == {
        "id",
        "label",
        "row_count",
        "column_count",
        "columns",
        "formats",
        # Phase 6E result metadata (build plan 6E.1). Every addition describes
        # the output; none of them carries a row.
        "column_schema",
        "input_row_count",
        "columns_added",
        "columns_removed",
    }
    assert "rows" not in payload["outputs"][0]
    assert "data" not in payload["outputs"][0]


def test_failed_manifest_carries_structured_error_and_no_outputs():
    manifest = _manifest(
        status=RunStatus.FAILED,
        outputs=(),
        metrics={},
        validation=ValidationSummary(
            passed=False,
            errors=(
                ValidationIssue(
                    code="MISSING_COLUMNS",
                    message="The uploaded file is missing required columns.",
                    details={"missing_columns": ["Supplier", "Volume"]},
                    slot_id="sales_file",
                ),
            ),
        ),
        error=RunError(
            code="MISSING_COLUMNS",
            message="The uploaded file is missing required columns.",
            details={"missing_columns": ["Supplier", "Volume"]},
        ),
    )

    payload = json.loads(manifest.model_dump_json())

    assert payload["status"] == "failed"
    assert payload["outputs"] == []
    assert payload["validation"]["passed"] is False
    assert payload["validation"]["errors"][0]["details"]["missing_columns"] == [
        "Supplier",
        "Volume",
    ]
    assert payload["error"]["code"] == "MISSING_COLUMNS"


def test_successful_manifest_has_a_null_error():
    assert json.loads(_manifest().model_dump_json())["error"] is None


def test_run_status_values_are_the_documented_strings():
    assert [status.value for status in RunStatus] == [
        "running",
        "succeeded",
        "failed",
    ]


def test_manifest_rejects_an_unknown_status():
    with pytest.raises(ValidationError):
        _manifest(status="finished")


# ---------------------------------------------------------------------------
# Validation issues
# ---------------------------------------------------------------------------


def test_validation_issue_details_default_to_empty_and_are_not_shared():
    first = ValidationIssue(code="A", message="a")
    second = ValidationIssue(code="B", message="b")

    first.details["x"] = 1

    assert second.details == {}


def test_validation_summary_defaults_to_no_issues():
    summary = ValidationSummary(passed=True)

    assert summary.errors == ()
    assert summary.warnings == ()


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_response_round_trips_and_keeps_rows_positional():
    preview = PreviewResponse(
        run_id="4f27d4bb-7464-4d04-a21b-000000000000",
        output_id="product_master",
        columns=("SKU", "Vintage", "Volume"),
        rows=[["A-1", 2019, 750], ["A-2", None, 1500]],
        offset=0,
        limit=100,
        total_rows=1_247,
        column_schema=(
            ColumnSchema(name="SKU", dtype="String", kind=ColumnKind.TEXT),
            ColumnSchema(name="Vintage", dtype="Int64", kind=ColumnKind.NUMBER),
            ColumnSchema(name="Volume", dtype="Int64", kind=ColumnKind.NUMBER),
        ),
    )

    payload = json.loads(preview.model_dump_json())

    assert payload["columns"] == ["SKU", "Vintage", "Volume"]
    assert payload["rows"] == [["A-1", 2019, 750], ["A-2", None, 1500]]
    assert payload["offset"] == 0
    assert payload["limit"] == 100
    assert payload["total_rows"] == 1_247
    assert PreviewResponse.model_validate(payload) == preview