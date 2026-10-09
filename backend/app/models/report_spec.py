"""Software contract for monthly report calculations and workbook views.

Rules declare exact source roles, performance attribution, calendar windows,
measures and validation behavior. Confidence records whether a configured rule
is fixed or remains a provisional supplementary definition. Production-source
provenance and acceptance findings are retained in private review material;
this module contains policy declarations and no business records.

Nothing here reads files, clocks, source workbooks or databases. Calculation
services read these declarations; presentation services receive their results.
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
REPORT_ACTION_VERSION = "0.4.0"


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
            "The report is built from sales history and sample history. "
            "Account assignment files are not report inputs."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "User direction on 2026-10-02 removes assignment inputs and "
            "requires invoice-based reports from sales and samples."
        ),
    ),
    Rule(
        key="source_schemas",
        statement=(
            "Sales and samples carry the fifteen confirmed transaction "
            "columns. Column names are matched exactly, with no aliasing."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            'The canonical transaction schemas define required identities and measures. Account context is derived from those transactions.'
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
        key='comparison_windows',
        statement='The reporting month, prior month, same month last year, current and prior YTD, current R12 and prior R12 are calendar windows inclusive of both ends. Missing interior months are exposed as incomplete history.',
        confidence=Confidence.CONFIRMED,
        basis='Calendar windows support monthly, YTD and rolling-year views. Explicit month coverage prevents an incomplete rolling period from being presented as a complete annual total.',
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
        key='credits',
        statement='Sales credits and returns, and sample credits, are summed at their signed source values in their respective datasets.',
        confidence=Confidence.CONFIRMED,
        basis='Net activity is the sum of signed values. Sales and sample credits remain in their own datasets, and credit signs are preserved rather than negated twice.',
    ),
    Rule(
        key='known_invoice_types',
        statement='Sales contain Invoice and Credit Invoice; samples contain Sample Invoice and Sample Credit Invoice. An unfamiliar, blank or wrong-dataset type fails rather than changing totals silently.',
        confidence=Confidence.CONFIRMED,
        basis='Each dataset accepts an explicit pair of document types. Refusing other labels prevents an unknown document kind from silently changing sales or sample calculations.',
    ),
    Rule(
        key="money_precision",
        statement=(
            "Aggregated money is rounded to two decimal places after "
            "summation. Source values are never altered."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            'Raw currency precision is preserved. Aggregated reporting money is rounded to two decimals and footer money sums displayed groups, making presentation and stored result values agree.'
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
        key='ownership',
        statement='Revenue and sample activity follow Sales Person on each transaction. Account context comes from distinct customer/rep pairs in current R12 activity.',
        confidence=Confidence.CONFIRMED,
        basis='User direction keeps transaction attribution and removes external ownership context.',
    ),
    Rule(
        key="ownership_matching",
        statement=(
            "Customer identities are grouped exactly as supplied on transactions: "
            "no trimming, case folding or near match."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "The matching rule Phase 10A established for column names and "
            "Phase 4 established for values. Build plan section 3.3 forbids "
            "fuzzy matching outright."
        ),
    ),
    Rule(
        key='unowned_accounts',
        statement='Sales need a nonblank Customer and Sales Person. Samples need Sales Person; sample Customer can be blank. Missing current ownership never erases invoice performance.',
        confidence=Confidence.CONFIRMED,
        basis='Performance attribution comes from the transaction identity. Sales require an account, while product-based sample views can retain samples with no known account.',
    ),
    Rule(
        key='duplicate_ownership',
        statement='Distinct customer/rep context pairs are counted once. Repeated transaction rows remain present and explicitly warned about.',
        confidence=Confidence.CONFIRMED,
        basis='Account context is derived from transactions and never joined to an external ownership map.',
    ),
    # -- Rep roster -------------------------------------------------------
    Rule(
        key='rep_roster',
        statement='Every nonblank rep with sales/sample activity in current R12 receives a workbook. A separate assignment list is never consulted.',
        confidence=Confidence.CONFIRMED,
        basis='The roster is dynamically derived from current-R12 invoice activity without an external assignment list or hard-coded names.',
    ),
    Rule(
        key='unrecognised_reps',
        statement='Every nonblank transaction rep is accepted as supplied; reps active in current R12 are included without verification against an assignment list.',
        confidence=Confidence.CONFIRMED,
        basis='Invoice identity defines performance and the roster; no external assignment input is available.',
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
            "Placement sections were not among the six accepted worksheets. "
            "This supplementary preview is the plainest reading of a new placement; it is "
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
        key='comparison_index',
        statement="The accepted supplier comparison is rep net supplier sales divided by company net supplier sales; rep supplier share is divided by that rep's total monthly net sales.",
        confidence=Confidence.CONFIRMED,
        basis='The two supplier ratios measure different relationships: supplier mix within a rep and a rep share within company supplier activity. Their denominators are explicitly separate.',
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
            "Footer amounts sum the rows shown for that rep. Footer "
            "percentages are calculated from the summed amounts, never by "
            "adding percentages. Every footer is calculated before rendering."
        ),
        confidence=Confidence.CONFIRMED,
        basis=(
            "Build plan 12D forbids business calculations in the formatting "
            "layer, and the renderer writes literal values rather than "
            "formulas, so a total must arrive already calculated."
        ),
    ),
    Rule(
        key='duplicate_source_rows',
        statement='Identical source rows are reported and preserved. Overlapping files must be reconciled before import; neither source rows nor correction files are deduplicated automatically.',
        confidence=Confidence.CONFIRMED,
        basis='Equality alone cannot distinguish a legitimate repeated line from an overlapping input. Reporting repetitions preserves source values while prompting explicit reconciliation.',
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
SALES_INVOICE_TYPES: tuple[str, ...] = ("Invoice", "Credit Invoice")
SAMPLE_INVOICE_TYPES: tuple[str, ...] = ("Sample Invoice", "Sample Credit Invoice")
KNOWN_INVOICE_TYPES: tuple[str, ...] = (*SALES_INVOICE_TYPES, *SAMPLE_INVOICE_TYPES)

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
    ROLLING_YEAR = "rolling_year"
    PRIOR_ROLLING_YEAR = "prior_rolling_year"


#: How each window is labelled in a report table.
WINDOW_LABELS: dict[WindowKey, str] = {
    WindowKey.CURRENT_MONTH: "Reporting Month",
    WindowKey.PRIOR_MONTH: "Prior Month",
    WindowKey.PRIOR_YEAR_MONTH: "Same Month Last Year",
    WindowKey.YEAR_TO_DATE: "Year to Date",
    WindowKey.PRIOR_YEAR_TO_DATE: "Prior Year to Date",
    WindowKey.ROLLING_YEAR: "Current R12",
    WindowKey.PRIOR_ROLLING_YEAR: "Prior R12",
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


# Accepted workbook views, derived from the supplied six report sections.
REPORT_SECTIONS += (
    ReportSection("monthly_samples", "Monthly Samples by Product", "Net sample bottles by supplier and producer/selection.", ("samples",)),
    ReportSection("rolling_samples", "Samples R12", "Chronological monthly net sample bottles over current R12.", ("samples",)),
    ReportSection("rolling_account_sales", "Sales R12 by Account", "Invoice-attributed current R12 net sales by account.", ("account performance",)),
    ReportSection("monthly_supplier_sales", "Monthly Supplier Sales", "Monthly net sales, rep mix and share of company supplier sales.", ("company-vs-rep supplier comparison",)),
    ReportSection("rolling_product_accounts", "Product and Account R12", "Current R12 net bottles by producer/selection and customer.", ("product performance",)),
    ReportSection("rolling_account_comparison", "Account R12 Comparison", "Current versus prior R12 net sales, change and status.", ("account performance",)),
    ReportSection("workbook_totals", "Workbook Totals", "Precalculated numeric totals for the six accepted workbook sections.", ("rep summary",)),
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
        code="MISSING_TRANSACTION_REP",
        severity=Severity.ERROR,
        summary="A transaction in a reporting window has no invoice salesperson.",
        basis="The performance attribution cannot be established without it.",
    ),
    Condition(
        code="MISSING_TRANSACTION_ACCOUNT",
        severity=Severity.ERROR,
        summary="A sales transaction in a reporting window has no Customer.",
        basis="An account-level performance report cannot place that transaction.",
    ),
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
        severity=Severity.WARNING,
        summary=(
            "An account with activity in a report window has no owner in the "
            "snapshot."
        ),
        basis=(
            "Retained as a legacy condition identifier. Invoice attribution "
            "does not require a current owner to retain the transaction."
        ),
    ),
    Condition(
        code="DUPLICATE_ACCOUNT_OWNERSHIP",
        severity=Severity.WARNING,
        summary="The snapshot assigns one account to two different reps.",
        basis=(
            "The engine never joins performance through the owner map. "
            "A conflict qualifies assignment context without multiplying sales; "
            "library ingestion still refuses a conflicting snapshot."
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
            "Invoice attribution retains this rep's performance. Active R12 "
            "reps also receive a workbook even when absent from the snapshot."
        ),
    ),
    Condition(
        code="UNEXPECTED_INVOICE_TYPE",
        severity=Severity.ERROR,
        summary="An Invoice Type value is not one the specification expects.",
        basis=(
            "Only the two declared types for each dataset are accepted. "
            "An unfamiliar or wrong-dataset type cannot be safely interpreted "
            "as sales or samples and fails before any artifact is produced."
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
            "Source rows repeat identically anywhere in selected sales/sample history."
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
            "A comparison window lacks at least one calendar month."
        ),
        basis=(
            "Missing imports cannot be distinguished from a true zero month. "
            "Accepted R12 views suppress unavailable totals and growth; the "
            "reporting month's own figures remain usable."
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
