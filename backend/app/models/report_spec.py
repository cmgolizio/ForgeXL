"""The frozen business definitions of the Monthly Sales Rep Report (13A).

Build plan 13A requires the report's rules to be written down *before* the
calculation engine exists, so there is something to check the engine against:

    Do not invent a formula merely because it appears reasonable. If the
    existing report does not establish a rule clearly, document the ambiguity
    and resolve it before implementation.

This module is that specification in code, and `docs/monthly-sales-rep-report-spec.md`
is the same specification in prose. The engine
(:mod:`app.services.monthly_report`) reads these declarations; it never spells
a rule of its own. A rule therefore appears in this repository once, exactly
the way a source column name appears in
:mod:`app.models.source_schemas` once.

**Every rule carries its confidence, and the provisional ones say so.**
:class:`Confidence` is part of the declaration rather than a comment, so the
distinction survives into the tests and into anything that reads the spec. The
precedent is Phase 10A's account-assignment schema, which is marked
``confirmed=False`` for the same reason: this repository has never been given
the finished Excel report or the Power Query behind it, so a rule that was
*derived* from it would be a fabrication. What is declared here instead is:

* **confirmed** — established by something in the repository: the confirmed
  source schemas of Phase 10A, a sentence of the build plan, or a rule this
  application already enforces elsewhere.
* **provisional** — a definition the real report must confirm. Each one states
  the reasoning behind the default chosen, and each is the smallest, most
  conservative reading available, so confirming it is an edit to one line here
  rather than a rewrite of the engine.

:data:`PROVISIONAL_RULES` is derived, not maintained by hand, and
`test_report_spec.py` asserts it is non-empty for as long as any rule is
unconfirmed. When the real report is supplied, change the rule's
:class:`Confidence` here, adjust its value if the real definition differs, and
the engine follows.

Nothing in this module reads a file, a clock or a database. It is declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.source_schemas import (
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
    SALES_SOURCE_SCHEMA,
    SAMPLES_SOURCE_SCHEMA,
    SourceSchema,
)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

#: The Action this specification defines.
REPORT_ACTION_ID = "monthly_sales_rep_report"

#: The Action's semantic version.
#:
#: Deliberately below 1.0.0, and that is a statement rather than a placeholder.
#: The two proof Actions are 1.0.0 because their behaviour is fully specified
#: by build plan sections 26 and 27 and cannot move. This Action's arithmetic
#: is specified by :data:`REPORT_RULES`, part of which is still provisional, so
#: claiming 1.0.0 would assert a stability the definitions do not yet have.
#: Raise it to 1.0.0 in the same change that clears :data:`PROVISIONAL_RULES`.
REPORT_ACTION_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class Confidence(str, Enum):
    """Whether a rule is established or still awaiting the real report."""

    #: Derived from the confirmed source schemas, from the build plan, or from
    #: a rule this application already enforces.
    CONFIRMED = "confirmed"

    #: A defensible default the finished monthly report must confirm.
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class Rule:
    """One business definition this report is built on."""

    #: Stable identifier. Used by the tests and quoted in the spec document.
    key: str

    #: What the rule says, in one sentence.
    statement: str

    #: Whether the rule is established or provisional.
    confidence: Confidence

    #: Why it says that — the evidence for a confirmed rule, the reasoning
    #: behind the default for a provisional one.
    basis: str


REPORT_RULES: tuple[Rule, ...] = (
    # -- Sources ----------------------------------------------------------
    Rule(
        key="sources",
        statement=(
            "The report is built from three Data Library datasets and nothing "
            "else: sales history, sample history, and the account-assignment "
            "snapshot for the reporting month."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13B names exactly these three. No other persistent "
            "dataset exists, and 13B permits more only if the specification "
            "proves them required."
        ),
    ),
    Rule(
        key="source_schemas",
        statement=(
            "Sales and samples carry the fifteen confirmed transaction "
            "columns; the assignment snapshot carries Customer and Sales "
            "Person. Column names are matched exactly, with no aliasing."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "docs/monthly-source-schemas.md, Phase 10A. The account-assignment "
            "schema is itself marked UNCONFIRMED there; this rule inherits "
            "that status from it rather than restating it."
        ),
    ),
    # -- Reporting period -------------------------------------------------
    Rule(
        key="report_month",
        statement=(
            "The reporting period is the greatest calendar month present in "
            "Invoice Date across the sales history the Run read, and every "
            "window is derived from it."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13C requires one explicit period resolved before any "
            "calculation. It is read from the data, never from a filename, "
            "which is the rule Phase 10B already established. The Run chooses "
            "it explicitly by bounding its history selector "
            "(history:YYYY-MM), and that bounding month must exist, so the "
            "month derived here is always the month requested."
        ),
    ),
    Rule(
        key="comparison_windows",
        statement=(
            "Five windows: the reporting month, the month before it, the same "
            "month one year earlier, year to date, and the same year-to-date "
            "window one year earlier. All five are calendar windows, "
            "inclusive of both ends."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "Build plan 13C names month-over-month, year-over-year, "
            "current-period and prior-period calculations, so these five are "
            "the smallest set that covers all four. Whether the finished "
            "report shows a rolling twelve months, a quarter or a trailing "
            "average as well is not established here."
        ),
    ),
    # -- Measures ---------------------------------------------------------
    Rule(
        key="revenue",
        statement="Revenue is the sum of Total Price.",
        confidence=Confidence.CONFIRMED,
        basis=(
            "Total Price is declared as the line total in the confirmed "
            "schema. Nothing else in the export states money at the line."
        ),
    ),
    Rule(
        key="quantity",
        statement="Quantity is the sum of Quantity.",
        confidence=Confidence.CONFIRMED,
        basis="Declared as units on the line in the confirmed schema.",
    ),
    Rule(
        key="credits",
        statement=(
            "Credits and returns are included at their signed value; nothing "
            "is filtered out by Invoice Type."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "The confirmed schema states Quantity is negative on a credit or "
            "return, which is only useful if credits are summed with sales. "
            "Excluding a row would also be the silent dropping build plan "
            "section 3.3 forbids."
        ),
    ),
    Rule(
        key="known_invoice_types",
        statement=(
            "Invoice and Credit are the expected Invoice Type values. Any "
            "other value is reported as a warning and its rows are still "
            "counted."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "The real set of Invoice Type values has not been supplied. Build "
            "plan 13H requires unexpected invoice types to be detected, so "
            "they are named and reported; they are a warning rather than a "
            "refusal because every row is counted regardless of its type, so "
            "an unfamiliar label changes no total."
        ),
    ),
    Rule(
        key="money_precision",
        statement=(
            "Aggregated money is rounded to two decimal places after "
            "summation. Source values are never altered."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Two decimals is the precision the source data itself carries. "
            "Rounding an aggregate to it removes floating-point residue that "
            "would otherwise make one total read differently in a workbook "
            "and in a CSV; it is a statement of the sum at the source's own "
            "precision, not a change to any value."
        ),
    ),
    Rule(
        key="missing_measures",
        statement=(
            "A blank or unreadable Quantity or Total Price fails the report "
            "rather than being treated as zero."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan section 3.3: never silently substitute missing data. "
            "Treating a blank as zero is a substitution that understates a "
            "total invisibly. Build plan 13H lists missing required "
            "monetary/quantity fields among the conditions to detect."
        ),
    ),
    # -- Ownership --------------------------------------------------------
    Rule(
        key="ownership",
        statement=(
            "An account belongs to the rep the assignment snapshot names for "
            "the reporting month. Sales Person on the transaction is never "
            "used to attribute revenue."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 9E is explicit that a month's report must use that "
            "month's ownership snapshot, and the confirmed sales schema "
            "records that Sales Person on a transaction is not the report's "
            "source of ownership."
        ),
    ),
    Rule(
        key="ownership_matching",
        statement=(
            "Customer is matched between the transactions and the snapshot "
            "exactly: no trimming, no case folding, no near match."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "The matching rule Phase 10A established for column names and "
            "Phase 4 established for values. Build plan section 3.3 forbids "
            "fuzzy matching outright."
        ),
    ),
    Rule(
        key="unowned_accounts",
        statement=(
            "An account with activity in any report window and no owner in "
            "the snapshot fails the report. An account named in the snapshot "
            "with no activity is not a problem."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13H lists missing account ownership and requires a "
            "failure where a condition makes the report unreliable. Revenue "
            "from an unowned account appears in the company total and in no "
            "rep's report, so every rep's share of the company would be "
            "wrong while every number still looked plausible."
        ),
    ),
    Rule(
        key="duplicate_ownership",
        statement=(
            "An account the snapshot assigns to two different reps fails the "
            "report."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13H lists duplicate account ownership. The ingestion "
            "layer already refuses such a snapshot (Phase 10E); this is the "
            "same rule re-checked where the report is built, because a "
            "version committed by another path would otherwise double-count "
            "an account."
        ),
    ),
    # -- Rep roster -------------------------------------------------------
    Rule(
        key="rep_roster",
        statement=(
            "The reps are exactly the distinct non-blank Sales Person values "
            "in the assignment snapshot for the reporting month. A rep with "
            "no activity still receives a report."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13D forbids a hard-coded roster and requires it to "
            "come from the authoritative reporting-period data, which for "
            "ownership is the snapshot. A rep who sold nothing needs to see "
            "that as much as one who sold well."
        ),
    ),
    Rule(
        key="unrecognised_reps",
        statement=(
            "A Sales Person named on a transaction but absent from the "
            "snapshot is reported as a warning and receives no report."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13H lists unrecognised sales reps. It is a warning "
            "rather than a refusal because that column attributes nothing: "
            "the revenue on the row is still attributed through its account's "
            "owner, so no total is affected."
        ),
    ),
    # -- Placements -------------------------------------------------------
    Rule(
        key="placement",
        statement=(
            "A placement is an account and product (Customer, SKU) whose "
            "first sale of positive quantity anywhere in the sales history "
            "the Run read falls inside the reporting month."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "The finished report's placement definition has not been "
            "supplied. This is the plainest reading of a new placement, it is "
            "reproducible because the Run records every history version it "
            "read, and it never counts a credit as a placement. A fixed "
            "look-back window — twelve months, say, so a product bought two "
            "years ago counts as a new placement again — is the most likely "
            "alternative and is one line here."
        ),
    ),
    Rule(
        key="placement_history",
        statement=(
            "At least twelve months of sales history should precede the "
            "reporting month; less than that is reported as a warning."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "A short history makes every product look new, so placements are "
            "overstated. Build plan 13H lists a missing historical comparison "
            "period among the conditions to detect. Twelve months is the span "
            "the year-over-year windows already require."
        ),
    ),
    # -- Samples ----------------------------------------------------------
    Rule(
        key="sample",
        statement=(
            "A sample is a line of the sample dataset. Samples are counted, "
            "valued and reported separately, and are never added to sales."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 10D requires sales and samples to remain logically "
            "distinct, and the two are separate datasets for that reason."
        ),
    ),
    Rule(
        key="sample_period",
        statement=(
            "The sample history must carry the reporting month if it carries "
            "any earlier month; a gap fails the report."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "Build plan 13H lists reporting-period mismatch. A month whose "
            "sample file was never imported would report every rep as having "
            "given no samples, which is a false statement rather than a "
            "missing one. A month in which no samples were genuinely given is "
            "different and is not a failure."
        ),
    ),
    # -- Derived figures --------------------------------------------------
    Rule(
        key="share",
        statement=(
            "A share is part divided by whole, stored as a fraction. A zero "
            "or absent whole gives no share, never zero."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13A requires the treatment of zero and null values to "
            "be stated. Zero would be a claim about a ratio that does not "
            "exist. Storing a fraction rather than text keeps the value "
            "numeric, which build plan 14F requires of the rendered workbook."
        ),
    ),
    Rule(
        key="growth",
        statement=(
            "Growth is (current - prior) / |prior|, stored as a fraction. A "
            "zero or absent prior gives no growth figure."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "The absolute value in the denominator keeps the sign of the "
            "change meaningful when a prior period was negative, which a "
            "month dominated by credits can be. Division by zero has no "
            "answer and is reported as none rather than as infinite or zero."
        ),
    ),
    Rule(
        key="comparison_index",
        statement=(
            "The company-versus-rep index is the rep's share of a supplier "
            "divided by the company's share of that supplier."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "Build plan 13F requires a company-versus-rep supplier "
            "comparison but does not define its measure. A ratio of shares is "
            "the standard form — 1.0 means the rep sells that supplier in the "
            "same proportion as the company — and the two shares it is built "
            "from are reported beside it, so the comparison is legible even "
            "if the finished report expresses it differently."
        ),
    ),
    # -- Presentation-independent ordering --------------------------------
    Rule(
        key="sorting",
        statement=(
            "Every table is sorted by its primary measure descending, then by "
            "its name columns ascending, so the order never depends on the "
            "order rows arrived in."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 13A requires sorting rules to be stated and build "
            "plan section 3.3 requires determinism. A tie broken by name "
            "rather than by input order is what makes two Runs over the same "
            "versions produce identical files."
        ),
    ),
    Rule(
        key="totals",
        statement=(
            "A displayed total is the sum of the rows shown in that table for "
            "that rep, calculated by the engine and never by the workbook."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 12D forbids business calculations in the formatting "
            "layer, and the renderer writes literal values rather than "
            "formulas, so a total must arrive already calculated."
        ),
    ),
    Rule(
        key="duplicate_source_rows",
        statement=(
            "Rows that repeat identically within the reporting month are "
            "reported as a warning; none is removed."
        ),
        confidence=Confidence.PROVISIONAL,
        basis=(
            "Build plan 13H lists duplicate monthly source data. Two "
            "identical invoice lines can be genuine, so removing one would be "
            "the silent dropping section 3.3 forbids; counting them is the "
            "signal. 'Identical across every column' is the definition this "
            "application already uses for a duplicate row (build plan "
            "section 26)."
        ),
    ),
)

#: Rules the finished monthly report must still confirm. Derived, so it cannot
#: fall out of step with the declarations above.
PROVISIONAL_RULES: tuple[Rule, ...] = tuple(
    item for item in REPORT_RULES if item.confidence is Confidence.PROVISIONAL
)

#: Whether every rule has been confirmed against the real report.
SPEC_CONFIRMED: bool = not PROVISIONAL_RULES


def rule(key: str) -> Rule:
    """Return the declared rule called `key`.

    Raises:
        KeyError: no rule has that key. Never falls back to a near match, the
            same way the Action registry and the Data Library do not.
    """
    for item in REPORT_RULES:
        if item.key == key:
            return item
    raise KeyError(f"No report rule is declared under {key!r}.")


# ---------------------------------------------------------------------------
# Column roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportColumns:
    """Which source column carries each thing the report calculates with.

    This is the one place outside :mod:`app.models.source_schemas` where a
    source column is named, and it is unavoidable: something has to say which
    of the fifteen transaction columns holds the money. The names are not
    invented — every one is copied from the confirmed schema — and
    `test_report_spec.py` asserts that each still exists there, so renaming a
    column in the schema fails a test rather than producing a report built on
    a column that is gone.
    """

    date: str
    invoice_type: str
    invoice_number: str
    customer: str
    customer_type: str
    transaction_rep: str
    sku: str
    vintage: str
    supplier: str
    producer: str
    selection: str
    volume: str
    quantity: str
    item_price: str
    revenue: str

    @property
    def measures(self) -> tuple[str, ...]:
        """The numeric columns the report sums."""
        return (self.quantity, self.revenue)

    @property
    def product_key(self) -> tuple[str, ...]:
        """What identifies a product line, in report column order."""
        return (
            self.sku,
            self.supplier,
            self.producer,
            self.selection,
            self.vintage,
            self.volume,
        )


TRANSACTION_COLUMNS = ReportColumns(
    date="Invoice Date",
    invoice_type="Invoice Type",
    invoice_number="Invoice Number",
    customer="Customer",
    customer_type="Cust Type",
    transaction_rep="Sales Person",
    sku="SKU",
    vintage="Vintage",
    supplier="Supplier",
    producer="Producer",
    selection="Selection",
    volume="Volume",
    quantity="Quantity",
    item_price="Item Price",
    revenue="Total Price",
)

#: The column the assignment snapshot identifies an account by.
ASSIGNMENTS_CUSTOMER_COLUMN = ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.customer_column or ""

#: The column the assignment snapshot names the owning rep in.
ASSIGNMENTS_REP_COLUMN = ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.rep_column or ""

#: The name the report gives the owning rep in every table it produces. Not
#: the source column's name: `Sales Person` on a transaction means the rep on
#: the document, and conflating the two in one output would be exactly the
#: silent substitution of a semantically different field that build plan
#: section 3.3 forbids.
REP_COLUMN = "Sales Rep"


# ---------------------------------------------------------------------------
# Values the engine reads
# ---------------------------------------------------------------------------

#: Invoice Type values the report expects to see (rule ``known_invoice_types``).
KNOWN_INVOICE_TYPES: tuple[str, ...] = ("Invoice", "Credit")

#: Decimal places an aggregated money figure is stated to (rule
#: ``money_precision``).
MONEY_DECIMALS = 2

#: Months of history that should precede the reporting month (rule
#: ``placement_history``).
MINIMUM_PLACEMENT_HISTORY_MONTHS = 12


class WindowKey(str, Enum):
    """The comparison windows every figure in the report is measured over."""

    CURRENT_MONTH = "current_month"
    PRIOR_MONTH = "prior_month"
    PRIOR_YEAR_MONTH = "prior_year_month"
    YEAR_TO_DATE = "year_to_date"
    PRIOR_YEAR_TO_DATE = "prior_year_to_date"


#: How each window is labelled in a report table.
WINDOW_LABELS: dict[WindowKey, str] = {
    WindowKey.CURRENT_MONTH: "Reporting Month",
    WindowKey.PRIOR_MONTH: "Prior Month",
    WindowKey.PRIOR_YEAR_MONTH: "Same Month Last Year",
    WindowKey.YEAR_TO_DATE: "Year to Date",
    WindowKey.PRIOR_YEAR_TO_DATE: "Prior Year to Date",
}


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportSection:
    """One table the report produces.

    `categories` records which of build plan 13F's listed report categories
    this table answers, so the mapping between the plan's list and the tables
    that exist is data rather than prose that can drift from it.
    """

    id: str
    label: str
    description: str
    categories: tuple[str, ...]

    #: False for the two company-wide tables, which carry no rep column
    #: because they are the same for every rep (build plan 13G).
    per_rep: bool = True


REPORT_SECTIONS: tuple[ReportSection, ...] = (
    ReportSection(
        id="rep_summary",
        label="Rep Summary",
        description=(
            "One row per rep: revenue, quantity, accounts sold, placements "
            "and samples across every window, with the growth figures."
        ),
        categories=("rep summary",),
    ),
    ReportSection(
        id="company_summary",
        label="Company Summary",
        description=(
            "The same measures for the whole company, calculated once and "
            "shared by every rep's report."
        ),
        categories=("rep summary",),
        per_rep=False,
    ),
    ReportSection(
        id="account_performance",
        label="Account Performance",
        description=(
            "One row per owned account: revenue and quantity across every "
            "window, year-over-year growth, placements and samples."
        ),
        categories=("account performance",),
    ),
    ReportSection(
        id="supplier_performance",
        label="Supplier Performance",
        description=(
            "One row per supplier the rep sold: revenue and quantity, the "
            "supplier's share of that rep's sales, and year-over-year growth."
        ),
        categories=("supplier performance", "supplier share of rep sales"),
    ),
    ReportSection(
        id="company_supplier_performance",
        label="Company Supplier Performance",
        description=(
            "One row per supplier for the whole company, calculated once."
        ),
        categories=(
            "supplier performance",
            "company-vs-rep supplier comparison",
        ),
        per_rep=False,
    ),
    ReportSection(
        id="supplier_comparison",
        label="Company vs Rep by Supplier",
        description=(
            "One row per rep and supplier: the rep's share beside the "
            "company's share, the difference between them, and the index."
        ),
        categories=("company-vs-rep supplier comparison",),
    ),
    ReportSection(
        id="product_performance",
        label="Product Performance",
        description=(
            "One row per product the rep sold, identified by SKU, supplier, "
            "producer, selection, vintage and volume."
        ),
        categories=("product performance",),
    ),
    ReportSection(
        id="placements",
        label="Placements",
        description=(
            "One row per new placement in the reporting month: the account, "
            "the product, the date it first sold and what it sold."
        ),
        categories=("placements",),
    ),
    ReportSection(
        id="placement_detail",
        label="Placement Detail",
        description=(
            "The reporting-month transaction lines behind those placements."
        ),
        categories=("placement detail",),
    ),
    ReportSection(
        id="samples",
        label="Samples",
        description=(
            "One row per account sampled in the reporting month: lines, "
            "quantity, value and distinct products."
        ),
        categories=("samples",),
    ),
    ReportSection(
        id="sample_detail",
        label="Sample Detail",
        description="The reporting-month sample lines behind those totals.",
        categories=("sample detail",),
    ),
    ReportSection(
        id="data_quality",
        label="Data Quality",
        description=(
            "One row per condition the report detected but continued past, "
            "with what it affects and how many rows it concerns."
        ),
        categories=("supporting validation/detail tables",),
        per_rep=False,
    ),
)

#: The report categories build plan 13F lists. Every one must be answered by at
#: least one section above; `test_report_spec.py` asserts it.
BUILD_PLAN_13F_CATEGORIES: tuple[str, ...] = (
    "rep summary",
    "account performance",
    "supplier performance",
    "company-vs-rep supplier comparison",
    "supplier share of rep sales",
    "product performance",
    "placements",
    "samples",
    "placement detail",
    "sample detail",
    "supporting validation/detail tables",
)


def section(section_id: str) -> ReportSection:
    """Return the declared section called `section_id`.

    Raises:
        KeyError: no section has that ID.
    """
    for item in REPORT_SECTIONS:
        if item.id == section_id:
            return item
    raise KeyError(f"No report section is declared under {section_id!r}.")


# ---------------------------------------------------------------------------
# Validation conditions (build plan 13H)
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Whether a detected condition stops the report or is reported beside it.

    Build plan 13H: "Where a condition makes the report unsafe, fail rather
    than producing a plausible-looking workbook. Warnings may be used only
    when continuing is genuinely safe."
    """

    #: The report is not produced. The Run fails with this condition among its
    #: validation errors.
    ERROR = "error"

    #: The report is produced, and the condition appears in the Data Quality
    #: table and in the Run's metrics.
    WARNING = "warning"


@dataclass(frozen=True)
class Condition:
    """One thing the report checks for before it is generated."""

    code: str
    severity: Severity
    summary: str

    #: Why it fails or why continuing is safe.
    basis: str


CONDITIONS: tuple[Condition, ...] = (
    Condition(
        code="EMPTY_SALES_HISTORY",
        severity=Severity.ERROR,
        summary="The sales history the Run read has no rows.",
        basis="There is no reporting month to derive and nothing to report.",
    ),
    Condition(
        code="MALFORMED_INVOICE_DATE",
        severity=Severity.ERROR,
        summary="An Invoice Date is blank or cannot be read as a date.",
        basis=(
            "A row with no readable date belongs to no window, so every "
            "window would silently omit it."
        ),
    ),
    Condition(
        code="NON_NUMERIC_MEASURE",
        severity=Severity.ERROR,
        summary="A Quantity or Total Price value is not a number.",
        basis=(
            "Reading it as zero would understate a total invisibly; guessing "
            "what '$1,234.56' or '(45.00)' meant would be a substitution."
        ),
    ),
    Condition(
        code="MISSING_MEASURE",
        severity=Severity.ERROR,
        summary="A Quantity or Total Price value is blank.",
        basis="Build plan section 3.3: never silently substitute missing data.",
    ),
    Condition(
        code="MISSING_ACCOUNT_OWNERSHIP",
        severity=Severity.ERROR,
        summary=(
            "An account with activity in a report window has no owner in the "
            "snapshot."
        ),
        basis=(
            "Its revenue would be in the company total and in no rep's "
            "report, so every rep's share of the company would be wrong."
        ),
    ),
    Condition(
        code="DUPLICATE_ACCOUNT_OWNERSHIP",
        severity=Severity.ERROR,
        summary="The snapshot assigns one account to two different reps.",
        basis=(
            "The account's revenue would be counted twice, once in each rep's "
            "report, and the rep totals would not add up to the company's."
        ),
    ),
    Condition(
        code="NO_SALES_REPS",
        severity=Severity.ERROR,
        summary="The assignment snapshot names no rep.",
        basis="There is nobody to produce a report for.",
    ),
    Condition(
        code="SAMPLE_PERIOD_MISMATCH",
        severity=Severity.ERROR,
        summary=(
            "The sample history carries earlier months but not the reporting "
            "month."
        ),
        basis=(
            "Every rep would be reported as having given no samples, which "
            "states something false rather than omitting something."
        ),
    ),
    Condition(
        code="UNRECOGNISED_SALES_REP",
        severity=Severity.WARNING,
        summary=(
            "A Sales Person on a transaction is not named in the snapshot."
        ),
        basis=(
            "That column attributes nothing: revenue follows the account's "
            "owner, so no total is affected."
        ),
    ),
    Condition(
        code="UNEXPECTED_INVOICE_TYPE",
        severity=Severity.WARNING,
        summary="An Invoice Type value is not one the specification expects.",
        basis=(
            "Every row is counted whatever its type, so an unfamiliar label "
            "changes no figure. It is reported because build plan 13H asks "
            "for it and because it may mean the export changed."
        ),
    ),
    Condition(
        code="UNEXPECTED_SOURCE_COLUMNS",
        severity=Severity.WARNING,
        summary="A source carries columns the schema does not declare.",
        basis=(
            "The report reads only the declared columns, so an added one "
            "changes nothing it calculates. Build plan 13H asks for an "
            "unexplained source-schema change to be surfaced."
        ),
    ),
    Condition(
        code="DUPLICATE_SOURCE_ROWS",
        severity=Severity.WARNING,
        summary=(
            "Rows repeat identically within the reporting month."
        ),
        basis=(
            "Two identical invoice lines can be genuine, so none is removed; "
            "the count is the signal that a month may have been imported "
            "twice."
        ),
    ),
    Condition(
        code="MISSING_COMPARISON_PERIOD",
        severity=Severity.WARNING,
        summary=(
            "A comparison window contains no rows, so its growth figures "
            "cannot be calculated."
        ),
        basis=(
            "The reporting month's own figures are unaffected, and a growth "
            "column with no answer reports none rather than zero."
        ),
    ),
    Condition(
        code="SHORT_PLACEMENT_HISTORY",
        severity=Severity.WARNING,
        summary=(
            "Fewer months of history precede the reporting month than the "
            "placement rule expects."
        ),
        basis=(
            "Placements are overstated, because a product bought before the "
            "history begins looks new. Every other figure is unaffected."
        ),
    ),
    Condition(
        code="PROVISIONAL_REPORT_RULES",
        severity=Severity.WARNING,
        summary=(
            "Some business definitions have not been confirmed against the "
            "finished monthly report."
        ),
        basis=(
            "Reported on every Run for as long as PROVISIONAL_RULES is "
            "non-empty, so a reader is never left to assume the arithmetic "
            "has been signed off. It disappears by itself when the last rule "
            "is confirmed."
        ),
    ),
)


def condition(code: str) -> Condition:
    """Return the declared condition with `code`.

    Raises:
        KeyError: no condition has that code.
    """
    for item in CONDITIONS:
        if item.code == code:
            return item
    raise KeyError(f"No report condition is declared under {code!r}.")


def severity_of(code: str) -> Severity:
    """Return the declared severity of condition `code`."""
    return condition(code).severity


#: Every condition that stops the report.
ERROR_CODES: tuple[str, ...] = tuple(
    item.code for item in CONDITIONS if item.severity is Severity.ERROR
)

#: Every condition the report continues past.
WARNING_CODES: tuple[str, ...] = tuple(
    item.code for item in CONDITIONS if item.severity is Severity.WARNING
)


# ---------------------------------------------------------------------------
# The datasets this report reads
# ---------------------------------------------------------------------------

#: The three schemas, in the order the Action declares its slots. Exposed here
#: so the Action does not have to import :mod:`app.models.source_schemas`
#: itself, keeping its imports to this module and the calculation engine.
SALES_SCHEMA: SourceSchema = SALES_SOURCE_SCHEMA
SAMPLES_SCHEMA: SourceSchema = SAMPLES_SOURCE_SCHEMA
ASSIGNMENTS_SCHEMA: SourceSchema = ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA

#: The Data Library datasets the three slots read.
SALES_DATASET_ID: str = SALES_SOURCE_SCHEMA.dataset_id
SAMPLES_DATASET_ID: str = SAMPLES_SOURCE_SCHEMA.dataset_id
ASSIGNMENTS_DATASET_ID: str = ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA.dataset_id
