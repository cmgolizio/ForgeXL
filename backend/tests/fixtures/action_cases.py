"""Known Action outcomes over the synthetic fixtures (build plan 6H.3).

Build plan 6H.3 asks for fixtures shaped:

    known input  ->  known Action configuration  ->  known expected output

An :class:`ActionCase` is exactly that triple. The input is a
:class:`tests.fixtures.spreadsheets.Table`; the configuration is the Action ID
and the slot the file is submitted under; the expected output is a `Table`
built from the input's own literals by :meth:`Table.deduplicated` and
:meth:`Table.with_columns` — plain Python list and set work, never Polars and
never the Action.

That independence is the point. A test that compared the Action against a
dataframe operation would be comparing Polars with Polars; these cases compare
it against a separately-written statement of what the transformation means.

`tests/fixtures/duplicate_rows.py` and `product_rows.py` remain the Phase 4
hand-written accuracy fixtures, where every expected row is typed out
literally. These cases are the Phase 6H complement: broader scenario coverage,
with the expectation *derived* from the input so that adding a scenario does
not mean hand-copying a second table.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import PRODUCT_COLUMNS, ProductMasterBuilderAction

from tests.fixtures import spreadsheets as fx
from tests.fixtures.spreadsheets import Table

#: Rows the larger end-to-end fixture carries. Big enough that nothing about
#: the result could be a coincidence of a handful of rows, small enough that
#: generating and writing the workbook stays well inside a fast test suite.
#: Phase 7G owns the 100,000-row performance fixtures; this is a correctness
#: fixture that happens to be large.
LARGE_ROW_COUNT = 25_000


@dataclass(frozen=True)
class ActionCase:
    """One fixture, one Action configuration, one expected output."""

    name: str

    #: The Action, and the slot its file is submitted under.
    action_id: str
    slot_id: str

    #: The output the assertions are about, and the label its worksheet and
    #: download carry.
    output_id: str
    output_label: str

    #: The upload.
    source: Table

    #: What that upload must produce.
    expected: Table

    #: Rows the Action reports it affected (build plan 6E.5).
    expected_rows_affected: int

    @property
    def expected_row_count(self) -> int:
        return self.expected.row_count

    @property
    def expected_columns(self) -> tuple[str, ...]:
        return self.expected.header


def _deduplication_case(source: Table) -> ActionCase:
    """The Exact Duplicate Remover applied to `source`.

    Its whole contract is "drop rows repeated exactly across every column,
    keep the first", so the expected output is the input with exactly that
    done to it — computed here from the literals.
    """
    expected = source.deduplicated()
    return ActionCase(
        name=f"dedupe/{source.name}",
        action_id=ExactDuplicateRemoverAction.id,
        slot_id="source_file",
        output_id="deduplicated_data",
        output_label="Deduplicated Data",
        source=source,
        expected=expected,
        expected_rows_affected=source.row_count - expected.row_count,
    )


def _product_master_case(source: Table) -> ActionCase:
    """The Product Master Builder applied to `source`.

    Its contract is "keep the six required columns in the fixed order, then
    drop repeated combinations", which is `with_columns` followed by
    `deduplicated`. `rows_affected` is measured against the *upload's* row
    count, because that is what the Action reports.
    """
    expected = source.with_columns(*PRODUCT_COLUMNS).deduplicated()
    return ActionCase(
        name=f"product-master/{source.name}",
        action_id=ProductMasterBuilderAction.id,
        slot_id="sales_file",
        output_id="product_master",
        output_label="Product Master",
        source=source,
        expected=expected,
        expected_rows_affected=source.row_count - expected.row_count,
    )


#: Fixtures whose values survive both upload formats identically, so a case
#: built from them can be asserted against a CSV upload and an XLSX upload
#: alike.
#:
#: `MIXED_VALUES` is deliberately absent: a column holding both numbers and
#: text does *not* survive the two paths identically, which is a finding about
#: the XLSX engine rather than about these Actions. It has its own tests in
#: `test_end_to_end.py`.
#:
#: `HEADER_ONLY` is absent because a dataset with no rows is refused before any
#: Action runs; it belongs to the failure battery.
_DEDUPLICATION_SOURCES: tuple[Table, ...] = (
    fx.SIMPLE_TABLE,
    fx.BLANK_ROWS,
    fx.BLANK_CELLS,
    fx.DUPLICATE_ROWS,
    fx.DUPLICATE_KEYS,
    fx.DATES,
    fx.MALFORMED_DATES,
    fx.UNICODE_TEXT,
    fx.ACCENTED_TEXT,
    fx.UNUSUAL_COLUMN_NAMES,
    fx.SINGLE_ROW,
    fx.EXTRA_COLUMNS,
)

#: Fixtures carrying the Product Master Builder's six required columns.
_PRODUCT_MASTER_SOURCES: tuple[Table, ...] = (fx.EXTRA_COLUMNS,)

DEDUPLICATION_CASES: tuple[ActionCase, ...] = tuple(
    _deduplication_case(source) for source in _DEDUPLICATION_SOURCES
)

PRODUCT_MASTER_CASES: tuple[ActionCase, ...] = tuple(
    _product_master_case(source) for source in _PRODUCT_MASTER_SOURCES
)

#: Every known-outcome case, for the sweeps that do not care which Action.
CASES: tuple[ActionCase, ...] = DEDUPLICATION_CASES + PRODUCT_MASTER_CASES

CASE_IDS: list[str] = [case.name for case in CASES]


def large_deduplication_case(row_count: int = LARGE_ROW_COUNT) -> ActionCase:
    """The Exact Duplicate Remover over a generated large dataset.

    The expected row count is closed-form rather than computed by
    deduplicating anything: :func:`spreadsheets.large_table` repeats itself
    with a period of :data:`spreadsheets.PRODUCT_POOL`, so `row_count` rows
    hold `min(row_count, PRODUCT_POOL)` distinct ones, in generation order.
    """
    source = fx.large_table(row_count)
    expected = Table(
        name=f"{source.name}-expected",
        description="The first occurrence of each distinct generated row.",
        header=source.header,
        rows=source.rows[: fx.distinct_rows_in_large_table(row_count)],
    )
    return ActionCase(
        name=f"dedupe/large-{row_count}",
        action_id=ExactDuplicateRemoverAction.id,
        slot_id="source_file",
        output_id="deduplicated_data",
        output_label="Deduplicated Data",
        source=source,
        expected=expected,
        expected_rows_affected=row_count - expected.row_count,
    )


def large_product_master_case(row_count: int = LARGE_ROW_COUNT) -> ActionCase:
    """The Product Master Builder over the same generated large dataset.

    The generated table carries the six required columns plus a `Units` column
    the Action must drop, so this case proves the selection and the
    deduplication together at size.
    """
    source = fx.large_table(row_count)
    distinct = fx.distinct_rows_in_large_table(row_count)
    expected = Table(
        name=f"{source.name}-product-master-expected",
        description="The first occurrence of each distinct generated product.",
        header=PRODUCT_COLUMNS,
        rows=source.with_columns(*PRODUCT_COLUMNS).rows[:distinct],
    )
    return ActionCase(
        name=f"product-master/large-{row_count}",
        action_id=ProductMasterBuilderAction.id,
        slot_id="sales_file",
        output_id="product_master",
        output_label="Product Master",
        source=source,
        expected=expected,
        expected_rows_affected=row_count - expected.row_count,
    )
