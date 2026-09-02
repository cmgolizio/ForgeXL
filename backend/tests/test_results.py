"""Result metadata tests (build plan 6E.1, 6E.4).

Phase 6E describes what an Action produced. These tests pin the two things
that description must be: *measured* — every count and column list comes from
the real frames — and *honest about what it does not know*, so a column the
Action never touched is never reported as one it added.
"""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from app.models.schemas import ColumnKind
from app.services import results

# ---------------------------------------------------------------------------
# Column kinds (build plan 6E.4)
# ---------------------------------------------------------------------------


def test_numbers_are_classified_as_numbers() -> None:
    frame = pl.DataFrame(
        {"i": [1], "f": [1.5]},
        schema={"i": pl.Int64, "f": pl.Float64},
    )

    kinds = {column.name: column.kind for column in results.column_schema(frame)}

    assert kinds == {"i": ColumnKind.NUMBER, "f": ColumnKind.NUMBER}


def test_text_is_classified_as_text() -> None:
    (column,) = results.column_schema(pl.DataFrame({"name": ["Château"]}))

    assert column.kind is ColumnKind.TEXT
    assert column.dtype == "String"


def test_a_boolean_is_not_reported_as_a_number() -> None:
    """A checkbox column must not be right-aligned as though it were a count."""
    (column,) = results.column_schema(pl.DataFrame({"ok": [True, False]}))

    assert column.kind is ColumnKind.BOOLEAN


def test_dates_and_datetimes_are_temporal() -> None:
    frame = pl.DataFrame(
        {"d": [date(2026, 8, 30)], "t": [datetime(2026, 8, 30, 12, 0)]}
    )

    kinds = [column.kind for column in results.column_schema(frame)]

    assert kinds == [ColumnKind.TEMPORAL, ColumnKind.TEMPORAL]


def test_an_unclassified_type_falls_back_to_other_rather_than_guessing() -> None:
    # An all-null column has type Null: neither numeric, temporal nor text.
    (column,) = results.column_schema(pl.DataFrame({"blank": [None, None]}))

    assert column.kind is ColumnKind.OTHER
    assert column.dtype == "Null"


def test_the_polars_type_name_is_reported_verbatim() -> None:
    frame = pl.DataFrame({"n": [1]}, schema={"n": pl.Int32})

    (column,) = results.column_schema(frame)

    assert column.dtype == "Int32"


def test_the_schema_follows_column_order() -> None:
    frame = pl.DataFrame({"z": [1], "a": ["x"], "m": [2.5]})

    assert [column.name for column in results.column_schema(frame)] == [
        "z",
        "a",
        "m",
    ]


# ---------------------------------------------------------------------------
# Comparing a result against what was uploaded (build plan 6E.1)
# ---------------------------------------------------------------------------


def test_received_columns_keep_first_appearance_order() -> None:
    received = results.input_columns([("B", "A"), ("A", "C")])

    assert received == ("B", "A", "C")


def test_a_column_shared_by_two_inputs_is_counted_once() -> None:
    assert results.input_columns([("SKU",), ("SKU",)]) == ("SKU",)


def test_no_inputs_means_no_received_columns() -> None:
    assert results.input_columns([]) == ()


def test_a_column_the_action_created_is_reported_as_added() -> None:
    added = results.columns_added(("SKU", "Margin"), ("SKU", "Cost"))

    assert added == ("Margin",)


def test_a_column_the_result_dropped_is_reported_as_removed() -> None:
    removed = results.columns_removed(("SKU", "Margin"), ("SKU", "Cost"))

    assert removed == ("Cost",)


def test_an_unchanged_column_set_reports_neither() -> None:
    columns = ("SKU", "Volume")

    assert results.columns_added(columns, columns) == ()
    assert results.columns_removed(columns, columns) == ()


def test_reordering_columns_is_not_reported_as_adding_or_removing() -> None:
    """Only membership matters: a reordered column was not added or removed."""
    assert results.columns_added(("B", "A"), ("A", "B")) == ()
    assert results.columns_removed(("B", "A"), ("A", "B")) == ()


# ---------------------------------------------------------------------------
# describe_output
# ---------------------------------------------------------------------------


def test_describe_output_measures_the_frame_it_is_given() -> None:
    frame = pl.DataFrame({"SKU": ["A1", "A2"], "Volume": [750, 1500]})

    metadata = results.describe_output(
        output_id="product_master",
        label="Product Master",
        formats=("csv", "xlsx"),
        frame=frame,
        received_columns=("SKU", "Volume", "Customer"),
        received_rows=9,
    )

    assert metadata.id == "product_master"
    assert metadata.label == "Product Master"
    assert metadata.row_count == 2
    assert metadata.column_count == 2
    assert metadata.columns == ("SKU", "Volume")
    assert metadata.formats == ("csv", "xlsx")
    assert metadata.input_row_count == 9
    assert metadata.columns_added == ()
    assert metadata.columns_removed == ("Customer",)
    assert [column.name for column in metadata.column_schema] == ["SKU", "Volume"]
    assert [column.kind for column in metadata.column_schema] == [
        ColumnKind.TEXT,
        ColumnKind.NUMBER,
    ]


def test_describe_output_carries_no_row_of_the_result() -> None:
    """Build plan section 23: metadata describes a result, never contains it."""
    frame = pl.DataFrame({"SKU": ["SECRET-1", "SECRET-2"]})

    payload = results.describe_output(
        output_id="result",
        label="Result",
        formats=("csv",),
        frame=frame,
        received_columns=("SKU",),
        received_rows=2,
    ).model_dump_json()

    assert "SECRET-1" not in payload
    assert "SECRET-2" not in payload


def test_an_empty_result_is_described_without_error() -> None:
    frame = pl.DataFrame({"SKU": []}, schema={"SKU": pl.String})

    metadata = results.describe_output(
        output_id="result",
        label="Result",
        formats=("csv",),
        frame=frame,
        received_columns=("SKU",),
        received_rows=0,
    )

    assert metadata.row_count == 0
    assert metadata.column_count == 1
    assert metadata.input_row_count == 0