"""The canonical schemas of the three recurring source files (build plan 10A).

Monthly ingestion accepts exactly three files, and this module is the one place
that says what each of them must contain. Everything downstream — period
detection, row validation, the coordinated import — reads a
:class:`SourceSchema` rather than naming a column of its own, so a column name
appears in this repository once.

Build plan 10A is explicit about the standard these declarations are held to:

    Use the actual company exports that support the existing manually verified
    monthly reports. Do not guess alternative column names. Do not silently
    treat semantically similar columns as equivalent. Any required
    normalization or aliasing must be explicitly specified, deterministic, and
    tested.

So there is **no aliasing here at all**. A column is matched by its exact name,
the same way :class:`~app.actions.product_master_builder.ProductMasterBuilderAction`
matches its six: ``Sales Person`` is not ``Salesperson`` and ``Cust Type`` is
not ``Customer Type``. A mismatch is reported with both spellings rather than
resolved.

**Confirmed and provisional schemas are marked as such.** `confirmed` is part
of the declaration, not a comment, so the distinction survives into the
metadata a caller can read and into the tests. The sales and sample schemas
were supplied verbatim from the real exports. The account-assignment schema was
not, and is provisional — see :data:`ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA`.

The declarations here describe the *source file*. They are not the shape of
what gets stored: the Data Library stores the parsed frame exactly as it
arrived, extra columns included (build plan 10E, "preserve the full source
snapshot"). See `docs/monthly-source-schemas.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.library import (
    ACCOUNT_ASSIGNMENTS,
    SALES_HISTORY,
    SAMPLE_HISTORY,
)

# ---------------------------------------------------------------------------
# Date formats
# ---------------------------------------------------------------------------

#: The date spellings ingestion will read, tried in this order.
#:
#: Declared rather than inferred, and deliberately short. Every entry is a form
#: a spreadsheet export actually produces, and each is applied whole: a format
#: is accepted only if it reads *every* populated value in the column, so a
#: column is never parsed by two rules at once.
#:
#: ``%m/%d/%Y`` and ``%d/%m/%Y`` are both here, and they disagree about
#: ``03/04/2026``. That is not resolved by preferring one — a wrong guess moves
#: a row into the wrong month, which is precisely the failure build plan 10B
#: exists to prevent. When both read a column and they disagree anywhere, the
#: column is reported as ambiguous and an explicit choice is required
#: (build plan 10B, "require explicit user selection rather than guessing").
#:
#: Two-digit years are absent on purpose: they add ambiguity and no export in
#: evidence produces one.
ISO_DATE = "%Y-%m-%d"
ISO_DATETIME = "%Y-%m-%d %H:%M:%S"
US_DATE = "%m/%d/%Y"
INTERNATIONAL_DATE = "%d/%m/%Y"

DEFAULT_DATE_FORMATS: tuple[str, ...] = (
    ISO_DATE,
    ISO_DATETIME,
    US_DATE,
    INTERNATIONAL_DATE,
)

#: Formats that carry a time of day and so must be read as a datetime before
#: the date is taken. Everything else is read straight to a date.
DATETIME_FORMATS: frozenset[str] = frozenset({ISO_DATETIME})


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


class SourceColumnKind(str, Enum):
    """What a source column is expected to hold.

    Deliberately coarse, and it drives exactly two behaviours and nothing else:

    * :attr:`DATE` marks the column a reporting period is derived from.
    * :attr:`NUMBER` marks a column whose arrival as text is worth reporting —
      a ``Total Price`` that parsed as text usually means the export wrote
      ``$1,234.56`` or ``(45.00)``, which a report cannot add up.

    It is never used to *convert* anything. Ingestion stores the frame as the
    parser produced it; a kind that does not match is reported, not repaired
    (build plan section 3.3).
    """

    TEXT = "text"
    DATE = "date"
    NUMBER = "number"


@dataclass(frozen=True)
class SourceColumn:
    """One column a source file must contain."""

    name: str
    kind: SourceColumnKind
    description: str


@dataclass(frozen=True)
class SourceSchema:
    """The canonical schema of one recurring source file (build plan 10A).

    `columns` are all required and are matched by exact name. Columns the file
    carries beyond them are **kept** — the stored version is the full source
    snapshot — and reported as a warning, never as a refusal. The reasoning is
    in `docs/monthly-source-schemas.md`: a missing required column makes the
    month unusable, while an added column is a source-schema change worth
    surfacing (build plan 13H) and safe to carry.

    Column *order* is not required. The canonical order is recorded here
    because it is the order the export produces, but a file that reorders its
    columns has lost nothing, and refusing it would be a rule about
    presentation rather than about data.
    """

    dataset_id: str
    label: str
    columns: tuple[SourceColumn, ...]

    #: Whether these column names came from the real company export. False
    #: means provisional: the shape is right and the names are not confirmed.
    confirmed: bool

    #: The column a reporting period is derived from (build plan 10B). ``None``
    #: for a dataset whose period cannot be read from its rows — an account
    #: snapshot states current ownership and carries no date, so its period is
    #: supplied explicitly instead.
    period_column: str | None = None

    #: The account identity, for the ownership checks of build plan 10E.
    customer_column: str | None = None

    #: The owning sales rep, for the same checks.
    rep_column: str | None = None

    date_formats: tuple[str, ...] = DEFAULT_DATE_FORMATS

    @property
    def column_names(self) -> tuple[str, ...]:
        """The required column names, in canonical order."""
        return tuple(column.name for column in self.columns)

    @property
    def number_columns(self) -> tuple[str, ...]:
        """Required columns expected to hold numbers."""
        return tuple(
            column.name
            for column in self.columns
            if column.kind is SourceColumnKind.NUMBER
        )

    def column(self, name: str) -> SourceColumn | None:
        """Return the declared column called `name`, or None."""
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def missing_from(self, present: tuple[str, ...]) -> tuple[str, ...]:
        """Required columns `present` does not contain, in canonical order.

        Compared exactly. No case folding, no whitespace trimming and no near
        match: the point of build plan 10A is that a differently spelled column
        is reported rather than accepted.
        """
        known = set(present)
        return tuple(name for name in self.column_names if name not in known)

    def unexpected_in(self, present: tuple[str, ...]) -> tuple[str, ...]:
        """Columns `present` carries that this schema does not declare.

        Reported as a warning and kept in the stored version; see the class
        docstring.
        """
        declared = set(self.column_names)
        return tuple(name for name in present if name not in declared)


# ---------------------------------------------------------------------------
# Sales and Samples
#
# CONFIRMED. Supplied verbatim from the real monthly exports, in the order the
# exports produce. Sales and samples are exported by the same system and their
# header rows are identical.
# ---------------------------------------------------------------------------

#: The shared column list of the two transaction exports.
#:
#: Written once because the two files genuinely have the same header, and
#: **used to build two separate schemas** rather than shared as one. Build plan
#: 10D is explicit that sales and samples stay logically distinct "even if
#: their source schemas overlap"; one schema object used for both datasets is
#: how that distinction quietly stops being real.
_TRANSACTION_COLUMNS: tuple[SourceColumn, ...] = (
    SourceColumn(
        name="Invoice Date",
        kind=SourceColumnKind.DATE,
        description=(
            "The date of the invoice. This is the column the reporting period "
            "is derived from (build plan 10B); a row with no readable date "
            "cannot be placed in a month."
        ),
    ),
    SourceColumn(
        name="Invoice Type",
        kind=SourceColumnKind.TEXT,
        description="The kind of document — invoice, credit, and so on.",
    ),
    SourceColumn(
        name="Invoice Number",
        kind=SourceColumnKind.TEXT,
        description=(
            "The document's identifier. Read as text rather than as a number: "
            "an identifier that leads with a zero or carries a prefix must "
            "keep it."
        ),
    ),
    SourceColumn(
        name="Customer",
        kind=SourceColumnKind.TEXT,
        description="The account the transaction belongs to.",
    ),
    SourceColumn(
        name="Cust Type",
        kind=SourceColumnKind.TEXT,
        description="The account's classification.",
    ),
    SourceColumn(
        name="Sales Person",
        kind=SourceColumnKind.TEXT,
        description=(
            "The rep recorded on the transaction itself. Ownership for a "
            "report comes from the account-assignment snapshot for the month, "
            "not from this column."
        ),
    ),
    SourceColumn(
        name="SKU",
        kind=SourceColumnKind.TEXT,
        description="The product code.",
    ),
    SourceColumn(
        name="Vintage",
        kind=SourceColumnKind.TEXT,
        description=(
            "The product's vintage. Text rather than a number because it is "
            "routinely blank or non-numeric, and nothing here invents one."
        ),
    ),
    SourceColumn(
        name="Supplier",
        kind=SourceColumnKind.TEXT,
        description="The supplier the product is bought from.",
    ),
    SourceColumn(
        name="Producer",
        kind=SourceColumnKind.TEXT,
        description=(
            "The producer. Accented names are kept exactly as exported; "
            "nothing strips or folds them."
        ),
    ),
    SourceColumn(
        name="Selection",
        kind=SourceColumnKind.TEXT,
        description="The selection the product belongs to.",
    ),
    SourceColumn(
        name="Volume",
        kind=SourceColumnKind.TEXT,
        description="The bottle or pack size, as exported, e.g. '750ml'.",
    ),
    SourceColumn(
        name="Quantity",
        kind=SourceColumnKind.NUMBER,
        description="Units on the line. Negative on a credit or return.",
    ),
    SourceColumn(
        name="Item Price",
        kind=SourceColumnKind.NUMBER,
        description="Price per unit.",
    ),
    SourceColumn(
        name="Total Price",
        kind=SourceColumnKind.NUMBER,
        description="Line total.",
    ),
)


SALES_SOURCE_SCHEMA = SourceSchema(
    dataset_id=SALES_HISTORY.id,
    label="Monthly Sales",
    columns=_TRANSACTION_COLUMNS,
    confirmed=True,
    period_column="Invoice Date",
    customer_column="Customer",
    rep_column="Sales Person",
)


SAMPLES_SOURCE_SCHEMA = SourceSchema(
    dataset_id=SAMPLE_HISTORY.id,
    label="Monthly Samples",
    columns=_TRANSACTION_COLUMNS,
    confirmed=True,
    period_column="Invoice Date",
    customer_column="Customer",
    rep_column="Sales Person",
)


# ---------------------------------------------------------------------------
# Account Assignments
#
# UNCONFIRMED — PROVISIONAL.
# ---------------------------------------------------------------------------

#: **This schema is provisional and has not been confirmed against the real
#: account-assignment export.** `confirmed=False` records that in code, a test
#: asserts it, and `docs/monthly-source-schemas.md` explains what to do about
#: it. Every other schema in this module was supplied verbatim; this one was
#: not, and pretending otherwise would be exactly the guess build plan 10A
#: forbids.
#:
#: Two decisions make the provisional version as harmless as a provisional
#: version can be:
#:
#: * **The two column names are not invented.** ``Customer`` and
#:   ``Sales Person`` are taken verbatim from the confirmed transaction schema
#:   above, which is the only evidence in the repository about how this company
#:   spells those two things. A newly made-up spelling would be a guess; reusing
#:   the confirmed one is at least a consistent guess, and it is the likeliest
#:   to be right.
#: * **It declares the minimum rather than the whole file.** Extra columns are
#:   kept and warned about, never refused, so a real export carrying ten more
#:   columns still imports and the full snapshot is still stored. The narrower
#:   this declaration is, the smaller the chance that the provisional part
#:   blocks a real file.
#:
#: To confirm it: replace the columns below with the real header row, set
#: `confirmed=True`, and update the table in `docs/monthly-source-schemas.md`.
#: Nothing else has to change — no service names a column of its own.
ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA = SourceSchema(
    dataset_id=ACCOUNT_ASSIGNMENTS.id,
    label="Account Assignments",
    columns=(
        SourceColumn(
            name="Customer",
            kind=SourceColumnKind.TEXT,
            description=(
                "The account. Must be present on every row and must not name "
                "two different reps within one snapshot (build plan 10E)."
            ),
        ),
        SourceColumn(
            name="Sales Person",
            kind=SourceColumnKind.TEXT,
            description=(
                "The rep who owns the account for this reporting month. Must "
                "be present on every row: a blank owner is the question the "
                "snapshot exists to answer."
            ),
        ),
    ),
    confirmed=False,
    # A snapshot carries no date of its own — it states ownership as it stands.
    # Its reporting month is supplied explicitly by the caller instead, which
    # is the "explicit user selection" build plan 10B prefers to a guess.
    period_column=None,
    customer_column="Customer",
    rep_column="Sales Person",
)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

#: Every recurring source file, in the order a monthly cycle supplies them.
SOURCE_SCHEMAS: tuple[SourceSchema, ...] = (
    SALES_SOURCE_SCHEMA,
    SAMPLES_SOURCE_SCHEMA,
    ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA,
)


def schema_for(dataset_id: str) -> SourceSchema | None:
    """Return the source schema for `dataset_id`, or None if it has none.

    Unknown IDs never fall back to a near match, the same rule the Action
    registry and the Data Library follow.
    """
    for schema in SOURCE_SCHEMAS:
        if schema.dataset_id == dataset_id:
            return schema
    return None
