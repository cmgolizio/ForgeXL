"""Data edge cases, end to end through the real API (build plan 7B).

Build plan 7B names the list this module works through:

    empty CSV, headers only, one-row dataset, duplicate rows, all-null column,
    Unicode, accents, apostrophes, commas inside quoted CSV cells, multiline
    CSV text, dates, negative numbers, zero values, blank values, large text
    cells

and states the property to check of each: **verify logical integrity**. That
is the whole point of the module, and it is a stronger claim than "the request
returned 200". For every case below the assertion is about the *values*: what
was uploaded is what the Action received, what the Action produced is what the
preview shows, and what the export contains is what was uploaded. A case that
ForgeXL deliberately refuses asserts the refusal instead, with its code.

Where an existing module already proves one of these at the unit level this one
does not repeat it; it exercises the case through `POST /api/runs`, the
preview and both downloads, because build plan 7A's concern is the whole path
rather than any single service. Phase 7 added five fixtures to
`tests/fixtures/spreadsheets.py` for the cases the 6H catalogue did not cover:
all-null column, multiline text, numeric extremes, large text cells and
duplicate column names.

Both upload formats are exercised for every case a workbook can express, so a
value is never proved to survive CSV and quietly lost in XLSX.
"""

from __future__ import annotations

import io

import polars as pl
import pytest

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.services import export, parser

from tests.fixtures import spreadsheets as fx
from tests.helpers import normalise_rows, upload_file

DEDUPE = ExactDuplicateRemoverAction.id
DEDUPE_OUTPUT = "deduplicated_data"


def _run(client, payload: bytes, filename: str):
    """Upload `payload` to the Exact Duplicate Remover and return the response.

    That Action requires no columns and only ever removes exactly-repeated
    rows, so it is the one that keeps a dataset closest to what was uploaded:
    every assertion below is about the data surviving, not about a
    transformation.
    """
    return client.post(
        "/api/runs",
        data={"action_id": DEDUPE},
        files={"source_file": upload_file(filename, payload)},
    )


def _run_table(client, table: fx.Table, extension: str):
    return _run(client, table.payload(extension), table.filename(extension))


def _all_rows(client, run_id: str) -> list[tuple[object, ...]]:
    """Every row of the Run's result, paged through the preview API.

    Paged rather than fetched at once because that is the only way the API
    offers: build plan section 21 caps a page at 500 rows, and a test that
    wants the whole result has to ask for it the way a client would.
    """
    rows: list[tuple[object, ...]] = []
    offset = 0
    while True:
        page = client.get(
            f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/preview",
            params={"offset": offset, "limit": 500},
        )
        assert page.status_code == 200, page.text
        body = page.json()
        rows.extend(tuple(row) for row in body["rows"])
        offset += len(body["rows"])
        if offset >= body["total_rows"] or not body["rows"]:
            return rows


# ---------------------------------------------------------------------------
# Empty and near-empty datasets
# ---------------------------------------------------------------------------


def test_an_empty_csv_is_refused_as_an_empty_file(client) -> None:
    """Zero bytes is an empty *file*, which is a different fact from no rows."""
    response = _run(client, b"", "empty.csv")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_a_csv_holding_only_a_newline_is_refused_as_a_parse_failure(
    client,
) -> None:
    """Bytes arrived but hold no table. Reported, never guessed at."""
    response = _run(client, b"\n", "blank.csv")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PARSE_ERROR"


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_header_only_file_is_refused_as_an_empty_dataset(
    client, extension: str
) -> None:
    """Columns and no rows: a legitimate file the Action has nothing to do with.

    Distinct from `EMPTY_FILE` above, and the distinction is the point — the
    two need different things from the user.
    """
    response = _run_table(client, fx.HEADER_ONLY, extension)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_DATASET"


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_one_row_dataset_survives_whole(client, extension: str) -> None:
    """The smallest dataset that holds data still round-trips exactly."""
    response = _run_table(client, fx.SINGLE_ROW, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["outputs"][0]["row_count"] == 1
    assert tuple(manifest["outputs"][0]["columns"]) == fx.SINGLE_ROW.header
    assert _all_rows(client, manifest["run_id"]) == normalise_rows(
        fx.SINGLE_ROW.rows
    )


# ---------------------------------------------------------------------------
# Rows and columns that look removable and are not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_exact_duplicates_go_and_near_misses_stay(client, extension: str) -> None:
    """The Action's whole contract, checked against the fixture's own literals.

    `DUPLICATE_ROWS` carries three exact repeats and two near-misses that
    differ in one column. Removing a near-miss would be the fuzzy matching
    build plan section 3.3 forbids.
    """
    response = _run_table(client, fx.DUPLICATE_ROWS, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()
    expected = fx.DUPLICATE_ROWS.deduplicated()

    assert manifest["outputs"][0]["row_count"] == expected.row_count
    assert manifest["metrics"]["duplicates_removed"] == (
        fx.DUPLICATE_ROWS.row_count - expected.row_count
    )
    assert _all_rows(client, manifest["run_id"]) == normalise_rows(expected.rows)


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_a_repeated_key_is_not_a_repeated_row(client, extension: str) -> None:
    """`DUPLICATE_KEYS` repeats one SKU across rows that differ elsewhere."""
    response = _run_table(client, fx.DUPLICATE_KEYS, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert manifest["outputs"][0]["row_count"] == fx.DUPLICATE_KEYS.row_count
    assert manifest["metrics"]["duplicates_removed"] == 0


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_an_all_null_column_survives_as_a_column(client, extension: str) -> None:
    """Build plan 7B: a column blank in every row is still a column.

    Dropping it would narrow the user's dataset without saying so, and the
    result's column list is what the preview and both exports are built from.
    """
    response = _run_table(client, fx.ALL_NULL_COLUMN, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()
    output = manifest["outputs"][0]

    assert tuple(output["columns"]) == fx.ALL_NULL_COLUMN.header
    assert output["column_count"] == fx.ALL_NULL_COLUMN.column_count
    rows = _all_rows(client, manifest["run_id"])
    assert rows == normalise_rows(fx.ALL_NULL_COLUMN.rows)
    # Every value in the blank column is null, not an empty string: the two are
    # different values and only one of them was uploaded.
    assert [row[1] for row in rows] == [None, None, None]


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_rows_that_are_blank_in_every_column_are_kept(
    client, extension: str
) -> None:
    """A blank row is a row of nulls, and two of them are exact duplicates.

    So the Action removes the second one — because it is an exact duplicate,
    which is its stated contract — and keeps the first. Nothing is dropped for
    being blank.
    """
    response = _run_table(client, fx.BLANK_ROWS, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()
    expected = fx.BLANK_ROWS.deduplicated()

    rows = _all_rows(client, manifest["run_id"])
    assert rows == normalise_rows(expected.rows)
    assert (None, None, None) in rows


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_blanks_inside_complete_rows_stay_blank(client, extension: str) -> None:
    """No blank is filled in, substituted or turned into an empty string."""
    response = _run_table(client, fx.BLANK_CELLS, extension)

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])

    assert rows == normalise_rows(fx.BLANK_CELLS.rows)


# ---------------------------------------------------------------------------
# Text: Unicode, accents, apostrophes, commas, newlines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_unicode_apostrophes_and_embedded_commas_survive(
    client, extension: str
) -> None:
    """The comma is the CSV trap and the apostrophe the Excel one (7B)."""
    response = _run_table(client, fx.UNICODE_TEXT, extension)

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])

    assert rows == normalise_rows(fx.UNICODE_TEXT.rows)
    assert ("O'Brien & Sons, Ltd.", "Dublin") in rows


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_names_that_differ_only_by_an_accent_are_two_different_names(
    client, extension: str
) -> None:
    """Folding accents would collapse two real products into one (7B)."""
    response = _run_table(client, fx.ACCENTED_TEXT, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()

    # Every row is distinct, so none is removed.
    assert manifest["outputs"][0]["row_count"] == fx.ACCENTED_TEXT.row_count
    assert manifest["metrics"]["duplicates_removed"] == 0
    assert _all_rows(client, manifest["run_id"]) == normalise_rows(
        fx.ACCENTED_TEXT.rows
    )


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_multiline_text_stays_one_value_in_one_row(
    client, extension: str
) -> None:
    """Build plan 7B: multiline CSV text, if the parser supports it correctly.

    It does. A newline inside a quoted CSV field is part of the value, not a
    row separator — a reader that split on it would turn five rows into nine
    and change every one of them.
    """
    response = _run_table(client, fx.MULTILINE_TEXT, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert manifest["outputs"][0]["row_count"] == fx.MULTILINE_TEXT.row_count
    rows = _all_rows(client, manifest["run_id"])
    assert [row[0] for row in rows] == ["A", "B", "C", "D", "E"]
    assert rows[0][1] == "line one\nline two"
    assert rows[3][1] == "line one\nline two\nline three"


def test_a_multiline_value_survives_the_csv_export_and_a_second_upload(
    client,
) -> None:
    """The strongest form of the claim: export it, upload the export, compare.

    A quoting bug that survives one direction rarely survives both.
    """
    first = _run(
        client, fx.MULTILINE_TEXT.as_csv(), fx.MULTILINE_TEXT.filename(".csv")
    )
    assert first.status_code == 200, first.text
    run_id = first.json()["run_id"]

    exported = client.get(
        f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/csv"
    )
    assert exported.status_code == 200

    second = _run(client, exported.content, "round-trip.csv")
    assert second.status_code == 200, second.text

    assert _all_rows(client, second.json()["run_id"]) == _all_rows(client, run_id)


# ---------------------------------------------------------------------------
# Numbers and dates
# ---------------------------------------------------------------------------


def test_dates_are_read_as_the_text_they_are(client) -> None:
    """Build plan 7B and section 3.3: nothing retypes a date-shaped string."""
    response = _run(client, fx.DATES.as_csv(), fx.DATES.filename(".csv"))

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert _all_rows(client, manifest["run_id"]) == normalise_rows(fx.DATES.rows)
    kinds = {
        entry["name"]: entry["kind"]
        for entry in client.get(
            f"/api/runs/{manifest['run_id']}/outputs/{DEDUPE_OUTPUT}/preview"
        ).json()["column_schema"]
    }
    assert kinds["Received"] == "text"


def test_date_shaped_text_that_is_not_a_date_is_not_repaired(client) -> None:
    """A 30th of February reaches the result as written, or not at all (7B)."""
    response = _run(
        client, fx.MALFORMED_DATES.as_csv(), fx.MALFORMED_DATES.filename(".csv")
    )

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])

    assert rows == normalise_rows(fx.MALFORMED_DATES.rows)
    assert ("ORD-1", "2026-02-30") in rows


def test_negative_numbers_zeros_and_precision_survive(client) -> None:
    """Build plan 7B: negative numbers, zero values, and nothing rounded.

    Uploaded as CSV so the values are stated in text and cannot be blamed on a
    workbook's binary encoding.
    """
    response = _run(
        client,
        fx.NUMERIC_EXTREMES.as_csv(),
        fx.NUMERIC_EXTREMES.filename(".csv"),
    )

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])
    amounts = {row[0]: row[1] for row in rows}
    counts = {row[0]: row[2] for row in rows}

    assert amounts["negative integer"] == -42
    assert amounts["negative decimal"] == -0.5
    assert amounts["zero"] == 0
    # -0.0 and 0.0 are the same number; what matters is that it is not a null
    # and not a positive value with the sign dropped somewhere else.
    assert amounts["negative zero"] == 0
    assert amounts["small decimal"] == 0.000123
    # An integer column holds an integer exactly, however large: this one is
    # 2**53 + 1, past what a float represents.
    assert counts["large count"] == 9_007_199_254_740_993


def test_an_integer_column_is_exact_at_any_size(client) -> None:
    """A column of whole numbers is read as integers, not as floats.

    The distinction is the whole of the precision story below: Polars widens an
    integer column as far as it needs to — past 64 bits if the file asks for it
    — and every value in it is the value that was uploaded.
    """
    payload = b"n\n9007199254740993\n9223372036854775807\n1\n"

    response = _run(client, payload, "integers.csv")

    assert response.status_code == 200, response.text
    assert [row[0] for row in _all_rows(client, response.json()["run_id"])] == [
        9_007_199_254_740_993,
        9_223_372_036_854_775_807,
        1,
    ]


def test_a_mixed_numeric_column_carries_floating_point_precision(
    client,
) -> None:
    """A documented limitation, pinned so it cannot change unnoticed.

    One Polars column holds one type. A column containing both a decimal and
    an integer larger than 2**53 must therefore be `Float64`, and 2**53 + 1 is
    not a value a float64 can hold — it reads back as 2**53. That is IEEE-754,
    not a choice ForgeXL makes: it is the same representation Excel uses, so
    the same file shows the same number there.

    It is recorded here rather than repaired because repairing it means
    changing how numeric columns are inferred, which is an architecture
    decision outside this phase (build plan section 14). The conditions are
    narrow and this test states them exactly: a *mixed* column, and a value
    past 2**53. An integer column is exact at any size — the test above — and
    a column holding any text stays text, so neither loses a digit.
    """
    payload = b"n\n9007199254740993\n0.5\n"

    response = _run(client, payload, "mixed.csv")

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])

    assert rows[0][0] == 9_007_199_254_740_992.0
    assert rows[0][0] != 9_007_199_254_740_993

    # The same file with the decimal removed keeps every digit, which is what
    # makes this a property of the column's type rather than of the value.
    integers_only = _run(client, b"n\n9007199254740993\n1\n", "ints.csv")
    assert _all_rows(client, integers_only.json()["run_id"])[0][0] == (
        9_007_199_254_740_993
    )


def test_zero_is_a_value_and_not_a_blank(client) -> None:
    """A zero that became a null would be a silently substituted value."""
    response = _run(client, b"Label,Amount\nzero,0\nblank,\n", "zeros.csv")

    assert response.status_code == 200, response.text
    rows = _all_rows(client, response.json()["run_id"])

    assert rows == [("zero", 0), ("blank", None)]


# ---------------------------------------------------------------------------
# Large text cells (build plan 7B)
# ---------------------------------------------------------------------------


def test_a_large_text_cell_survives_csv_upload_preview_and_export(
    client,
) -> None:
    """40,000 characters in one cell, through the whole path, unshortened.

    CSV has no cell-length limit, so nothing here may shorten the value: not
    the parser, not the preview, and not the CSV export. The XLSX export
    cannot hold it and refuses rather than truncating — that is
    `test_export_capacity` in `test_export.py` and the download route's own
    test, not this one.
    """
    table = fx.LARGE_TEXT_CELLS
    response = _run(client, table.as_csv(), table.filename(".csv"))

    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]

    rows = _all_rows(client, run_id)
    note = rows[0][1]
    assert isinstance(note, str)
    assert len(note) == fx.LARGE_TEXT_LENGTH
    assert note == table.rows[0][1]

    exported = client.get(f"/api/runs/{run_id}/outputs/{DEDUPE_OUTPUT}/download/csv")
    assert exported.status_code == 200
    read_back = parser.parse_tabular_bytes(exported.content, ".csv")
    assert read_back.frame["Note"][0] == table.rows[0][1]


def test_a_cell_at_the_excel_limit_still_exports_as_a_workbook() -> None:
    """The boundary the guard is drawn at, from the side that must still work.

    32,767 characters is exactly what an Excel cell holds, so this one is
    written whole. One character more is the refusal case next door.
    """
    at_limit = "x" * export.MAX_CELL_CHARACTERS
    payload = export.to_xlsx_bytes(pl.DataFrame({"Note": [at_limit]}), worksheet="R")

    read_back = parser.parse_tabular_bytes(payload, ".xlsx")

    assert read_back.frame["Note"][0] == at_limit


# ---------------------------------------------------------------------------
# Column names are identifiers, not text to tidy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_unusual_column_names_arrive_exactly_as_uploaded(
    client, extension: str
) -> None:
    """Leading spaces, punctuation, an accent and a leading '=' all survive."""
    response = _run_table(client, fx.UNUSUAL_COLUMN_NAMES, extension)

    assert response.status_code == 200, response.text
    manifest = response.json()

    assert tuple(manifest["inputs"][0]["columns"]) == fx.UNUSUAL_COLUMN_NAMES.header
    assert tuple(manifest["outputs"][0]["columns"]) == fx.UNUSUAL_COLUMN_NAMES.header


@pytest.mark.parametrize("extension", fx.UPLOAD_EXTENSIONS)
def test_one_name_used_for_two_columns_is_refused(
    client, extension: str
) -> None:
    """Build plan section 3.3: never silently rename a column.

    Both engines resolve the clash by renaming the second column — Polars to
    ``SKU_duplicated_0``, fastexcel to ``SKU_1`` — and then carry on, so
    before Phase 7 this Run succeeded and the second column's values were
    dropped from the result with nothing said. ForgeXL cannot know which
    column was meant, so it refuses and names the repeat.
    """
    response = _run_table(client, fx.DUPLICATE_COLUMN_NAMES, extension)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "DUPLICATE_COLUMNS"
    assert error["details"]["duplicate_columns"] == ["SKU"]
    assert "SKU" in error["message"]


def test_the_refusal_names_every_repeated_column_once(client) -> None:
    """Three columns called the same thing is still one repeated name."""
    payload = b"A,B,A,B,A\n1,2,3,4,5\n"

    response = _run(client, payload, "repeats.csv")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["duplicate_columns"] == ["A", "B"]


def test_names_differing_only_in_case_are_not_duplicates(client) -> None:
    """Compared exactly, the same way required columns are (build plan 3.7)."""
    response = _run(client, b"sku,SKU,Sku\n1,2,3\n", "cases.csv")

    assert response.status_code == 200, response.text
    assert tuple(response.json()["outputs"][0]["columns"]) == ("sku", "SKU", "Sku")


def test_two_blank_header_cells_are_not_a_duplicate_name() -> None:
    """Unnamed columns get distinct generated names; nothing is renamed."""
    parsed = parser.parse_tabular_bytes(
        io.BytesIO(
            fx.Table(
                name="blank-headers",
                description="Two header cells left empty.",
                header=("Region", "", ""),
                rows=(("North", 1, 2),),
            ).as_xlsx()
        ).getvalue(),
        ".xlsx",
    )

    assert len(set(parsed.columns)) == len(parsed.columns)