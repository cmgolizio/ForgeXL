"""Action 3 — Monthly Sales Rep Report (build plan Phases 13 and 14).

The first Action that reads the persistent Data Library. It declares three
library-backed input slots (build plan 11A), receives their rows as DataFrames
like every other Action, and returns calculation tables and every rep workbook.

It is a thin module on purpose. The Action contract lives here — the ID, the
version, the slots, the outputs and the validation hook — and the arithmetic
lives in :mod:`app.services.monthly_report`, checked against the frozen
definitions in :mod:`app.models.report_spec`. That is the same split
:mod:`app.services.workbook` already has with the Actions that render reports:
one file per concern, and an Action that can be read in a minute.

Phase 14 renders the six accepted views with the shared Phase 12 renderer.
Artifacts remain distinct from table exports and use the generic individual
and ZIP download routes. No spreadsheet calculations live in the renderer.

**Every table holds every rep's rows**, keyed by `Sales Rep`. An Action
declares a fixed set of outputs and the rep roster is dynamic (build plan
13D), so one output per rep could not be declared — and should not be: build
plan 13G wants the company's figures calculated once rather than rebuilt per
rep, which is what one frame per section gives. Rendering slices them per rep.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.actions.base import Action, ActionResult
from app.models.artifact import artifact_filename
from app.models.report_spec import (
    ASSIGNMENTS_DATASET_ID,
    ASSIGNMENTS_SCHEMA,
    REPORT_ACTION_ID,
    REPORT_ACTION_VERSION,
    REPORT_SECTIONS,
    SALES_DATASET_ID,
    SALES_SCHEMA,
    SAMPLES_DATASET_ID,
    SAMPLES_SCHEMA,
)
from app.models.schemas import (
    ActionInput,
    ActionInputSource,
    ActionOutput,
    ValidationIssue,
)
from app.services.monthly_report import (
    PreparedReport,
    build_tables,
    prepare,
    report_metrics,
)
from app.services.report_workbooks import render_rep_workbooks

#: The three input slots, named for the datasets they read.
SALES_SLOT = "sales_history"
SAMPLES_SLOT = "sample_history"
ASSIGNMENTS_SLOT = "account_assignments"


def _library_slot(
    slot_id: str,
    label: str,
    dataset_id: str,
    columns: tuple[str, ...],
    description: str,
    period_matches: str | None = None,
    interpret_dates: bool = False,
) -> ActionInput:
    """Declare one library-backed slot, with its required columns.

    The columns come from the source schema rather than being retyped, so a
    column name is still spelled once in this repository (build plan 10A). The
    runner checks them against the resolved rows exactly as it checks an
    uploaded file's, so a stored version earns no trust for being stored.
    """
    return ActionInput(
        id=slot_id,
        label=label,
        description=description,
        required=True,
        source=ActionInputSource.LIBRARY,
        dataset_id=dataset_id,
        required_columns=columns,
        period_matches=period_matches,
        interpret_dates=interpret_dates,
    )


class MonthlySalesRepReportAction(Action):
    """Calculate the monthly sales-rep report from stored company data."""

    id = REPORT_ACTION_ID
    version = REPORT_ACTION_VERSION
    name = "Monthly Sales Rep Report"
    description = (
        "Calculate the monthly sales-rep report from the Data Library: one "
        "set of tables per rep covering accounts, suppliers, products, "
        "samples and R12 comparisons, with one finished workbook per rep and "
        "a monthly ZIP download. Performance follows the invoice salesperson; "
        "the month is read from the committed sales data."
    )
    inputs = (
        _library_slot(
            SALES_SLOT,
            "Sales History",
            SALES_DATASET_ID,
            SALES_SCHEMA.column_names,
            interpret_dates=True,
            description=(
                "The committed sales months the report covers. Name them "
                "with 'history:YYYY-MM' to report on that month, which reads "
                "every stored month up to and including it."
            ),
        ),
        _library_slot(
            SAMPLES_SLOT,
            "Sample History",
            SAMPLES_DATASET_ID,
            SAMPLES_SCHEMA.column_names,
            interpret_dates=True,
            period_matches=SALES_SLOT,
            description=(
                "The committed sample months, over the same span as the "
                "sales history. Samples are counted separately and are never "
                "added to sales."
            ),
        ),
        _library_slot(
            ASSIGNMENTS_SLOT,
            "Account Assignments",
            ASSIGNMENTS_DATASET_ID,
            ASSIGNMENTS_SCHEMA.column_names,
            period_matches=SALES_SLOT,
            description=(
                "The account-ownership snapshot for the reporting month, "
                "named with 'period:YYYY-MM'. Supplies roster and account-list "
                "context. Performance stays attributed to the invoice salesperson."
            ),
        ),
    )
    outputs = tuple(
        ActionOutput(
            id=section.id, label=section.label, description=section.description
        )
        for section in REPORT_SECTIONS
    )

    def validate(
        self, inputs: Mapping[str, pl.DataFrame]
    ) -> list[ValidationIssue]:
        """Refuse a report the data cannot support (build plan 13H).

        Everything returned here fails the Run before :meth:`run` is called,
        with a structured 422 naming each condition — which is build plan
        13H's "fail rather than producing a plausible-looking workbook",
        enforced at the only point where failing costs nothing.

        Warnings are not returned: an Action's validation hook has no warning
        channel, and everything it returns fails the Run. They travel in the
        Data Quality result table instead, which is also where a reader wants
        them — beside the report they qualify.
        """
        return list(self._prepare(inputs).errors)

    def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
        prepared = self._prepare(inputs)
        tables = build_tables(prepared)

        return ActionResult(
            outputs={section.id: tables[section.id] for section in REPORT_SECTIONS},
            metrics=report_metrics(prepared),
            # The report transforms no rows into other rows — it measures
            # them — so it states no affected-row count rather than inventing
            # one from two totals that mean different things (build plan 6E.5).
            rows_affected=None,
            artifacts=render_rep_workbooks(prepared, tables),
            artifact_bundle_filename=artifact_filename(
                f"{prepared.require_period().label} Sales Rep Reports", "zip"
            ),
        )

    @staticmethod
    def _prepare(
        inputs: Mapping[str, pl.DataFrame],
    ) -> PreparedReport:
        """Build the shared prepared model (build plan 13E).

        Called by both :meth:`validate` and :meth:`run`, and deliberately not
        cached between them: an Action instance is registered once and reused
        for every Run, so it must hold no per-Run state (build plan section
        24). Preparation is deterministic and side-effect free, so doing it
        twice gives the same answer twice.
        """
        return prepare(
            inputs[SALES_SLOT],
            inputs[SAMPLES_SLOT],
            inputs[ASSIGNMENTS_SLOT],
            sales_slot=SALES_SLOT,
            samples_slot=SAMPLES_SLOT,
            assignments_slot=ASSIGNMENTS_SLOT,
        )
