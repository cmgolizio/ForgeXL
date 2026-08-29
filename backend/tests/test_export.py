"""Export service tests (build plan 3.10 and section 28).

Proves the exports are genuinely readable files, not merely files with the
right extension.
"""

from __future__ import annotations

import zipfile

import polars as pl
import pytest

from app.services import export, parser, storage

FRAME = pl.DataFrame(
    {
        "SKU": ["A1", "A2", "A3"],
        "Producer": ["Château Margaux", "Bodegas Muñoz", None],
        "Volume": [750, 1500, 375],
    }
)


def test_write_output_creates_all_three_artifacts(
    run_paths: storage.RunPaths,
) -> None:
    written = export.write_output(run_paths, "product_master", FRAME)

    assert run_paths.working_artifact("product_master").is_file()
    assert run_paths.export_artifact("product_master", "csv").is_file()
    assert run_paths.export_artifact("product_master", "xlsx").is_file()
    assert written.formats == ("csv", "xlsx")


def test_write_output_reports_the_frame_shape(run_paths: storage.RunPaths) -> None:
    written = export.write_output(run_paths, "product_master", FRAME)

    assert written.output_id == "product_master"
    assert written.row_count == 3
    assert written.column_count == 3
    assert written.columns == ("SKU", "Producer", "Volume")


def test_the_parquet_round_trips_with_its_schema_intact(
    run_paths: storage.RunPaths,
) -> None:
    export.write_output(run_paths, "product_master", FRAME)

    reloaded = pl.read_parquet(run_paths.working_artifact("product_master"))

    assert reloaded.columns == FRAME.columns
    assert reloaded.dtypes == FRAME.dtypes
    assert reloaded.rows() == FRAME.rows()


def test_the_csv_export_round_trips(run_paths: storage.RunPaths) -> None:
    export.write_output(run_paths, "product_master", FRAME)

    reloaded = pl.read_csv(run_paths.export_artifact("product_master", "csv"))

    assert reloaded.columns == FRAME.columns
    assert reloaded.height == 3
    assert reloaded["Producer"].to_list() == ["Château Margaux", "Bodegas Muñoz", None]


def test_the_xlsx_export_is_a_real_workbook_that_round_trips(
    run_paths: storage.RunPaths,
) -> None:
    export.write_output(run_paths, "product_master", FRAME)
    path = run_paths.export_artifact("product_master", "xlsx")

    with zipfile.ZipFile(path) as archive:
        assert "xl/workbook.xml" in archive.namelist()

    # Read back through the application's own reader: the export must be
    # usable by the same parser that ingests uploads.
    reloaded = parser.parse_tabular_bytes(path.read_bytes(), ".xlsx").frame
    assert reloaded.columns == FRAME.columns
    assert reloaded.height == 3
    assert reloaded["SKU"].to_list() == ["A1", "A2", "A3"]
    assert reloaded["Volume"].to_list() == [750, 1500, 375]


def test_accented_values_survive_the_excel_round_trip(
    run_paths: storage.RunPaths,
) -> None:
    export.write_output(run_paths, "product_master", FRAME)

    reloaded = parser.parse_tabular_bytes(
        run_paths.export_artifact("product_master", "xlsx").read_bytes(), ".xlsx"
    ).frame

    assert reloaded["Producer"].to_list()[:2] == ["Château Margaux", "Bodegas Muñoz"]


def test_an_output_with_no_rows_still_writes_every_artifact(
    run_paths: storage.RunPaths,
) -> None:
    empty = FRAME.head(0)

    written = export.write_output(run_paths, "product_master", empty)

    assert written.row_count == 0
    assert run_paths.working_artifact("product_master").is_file()
    assert run_paths.export_artifact("product_master", "csv").is_file()
    assert run_paths.export_artifact("product_master", "xlsx").is_file()
    assert pl.read_parquet(run_paths.working_artifact("product_master")).height == 0


def test_two_outputs_are_written_side_by_side(run_paths: storage.RunPaths) -> None:
    export.write_output(run_paths, "first_output", FRAME)
    export.write_output(run_paths, "second_output", FRAME.head(1))

    assert pl.read_parquet(run_paths.working_artifact("first_output")).height == 3
    assert pl.read_parquet(run_paths.working_artifact("second_output")).height == 1


@pytest.mark.parametrize("unsafe", ["../escape", "a/b", "a.b"])
def test_an_unsafe_output_id_is_refused(
    run_paths: storage.RunPaths, unsafe: str
) -> None:
    with pytest.raises(ValueError):
        export.write_output(run_paths, unsafe, FRAME)