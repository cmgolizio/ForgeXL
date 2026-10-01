# Monthly Sales Rep Report — Specification

**The authoritative definition of the Monthly Sales Rep Report** (build plan
13A). The calculation engine is checked against this document, not the other
way round.

The declarations live in
[`backend/app/models/report_spec.py`](../backend/app/models/report_spec.py).
This document explains them; that module is what the code reads. A business
rule appears in this repository **once**, in that module — the engine
(`backend/app/services/monthly_report.py`) spells none of its own.

| | |
| --- | --- |
| Action ID | `monthly_sales_rep_report` |
| Action version | `0.1.0` — see [Version](#version) |
| Specification status | **PARTLY PROVISIONAL** — see [Status](#status) |
| Sources | `sales_history`, `sample_history`, `account_assignments` |

---

## Status

> **This specification has not been confirmed against the finished monthly
> report.** Build plan 13A asks for it to be derived from "the current
> verified Excel monthly report", "the existing Power Query logic", "accepted
> business definitions" and "manually verified results from a completed
> month". None of those four has been supplied to this repository: there is no
> workbook, no query, no worked example and no written definition anywhere in
> the tree or in the session that produced this document.

Rather than invent the missing rules and present them as the business's own —
which is exactly what 13A forbids — every rule below carries its confidence,
in code rather than in a comment:

- **Confirmed** — established by evidence in this repository: the confirmed
  source schemas of Phase 10A, a sentence of the build plan, or a rule this
  application already enforces somewhere else.
- **Provisional** — a defensible default the finished report must confirm.
  Each states the reasoning behind the choice and names the most likely
  alternative.

This is the same treatment Phase 10A gave the account-assignment schema, which
is marked `confirmed=False` for the same reason and is still marked that way.

The provisional rules today:

| Rule | What is unknown |
| --- | --- |
| `comparison_windows` | Whether the report also shows a rolling twelve months, a quarter or a trailing average. |
| `known_invoice_types` | The real set of `Invoice Type` values. |
| `placement` | The finished report's placement definition, in particular its look-back. |
| `placement_history` | How much history the placement rule assumes. |
| `sample_period` | Whether a month with no imported sample file should fail or warn. |
| `comparison_index` | How the finished report expresses the company-versus-rep comparison. |
| `duplicate_source_rows` | What the business counts as a duplicated monthly import. |

Every Run reports `PROVISIONAL_REPORT_RULES` as a warning in its Data Quality
table for as long as that list is non-empty, so nobody reading a generated
report is left to assume the arithmetic has been signed off. The warning
disappears by itself when the last rule is confirmed.

### To confirm a rule

1. Change its `Confidence` in `REPORT_RULES` to `Confidence.CONFIRMED`.
2. If the real definition differs from the default, change the value it drives
   — `KNOWN_INVOICE_TYPES`, `MINIMUM_PLACEMENT_HISTORY_MONTHS`, the
   `WindowKey` members, the `placement` rule's implementation in
   `app/services/monthly_report.py`, or the `SAMPLE_PERIOD_MISMATCH`
   severity — and update its tests.
3. Update the table above.
4. When the last one is confirmed, `SPEC_CONFIRMED` becomes true, the
   `PROVISIONAL_REPORT_RULES` warning stops appearing, and
   `REPORT_ACTION_VERSION` should be raised to `1.0.0`.

`test_report_spec.py` asserts each of those couplings, so none of them can be
half-done.

### Version

`0.1.0`, and deliberately so. The two proof Actions are `1.0.0` because build
plan sections 26 and 27 specify their behaviour completely. This Action's
arithmetic is specified by this document, part of which is provisional, so
`1.0.0` would assert a stability the definitions do not have. Every Run records
the Action version in its manifest, so a report generated today is
distinguishable from one generated after the definitions are confirmed.

---

## Source datasets

The report reads three Data Library datasets and nothing else (rule
`sources`). Each is an **exact committed version**, resolved before the Action
runs and recorded in the Run manifest (build plan 11C), so a report can be
regenerated later against the same source state.

| Slot | Dataset | Selector | What it supplies |
| --- | --- | --- | --- |
| `sales_history` | `sales_history` | `history:YYYY-MM` | Every live month up to and including the reporting month. |
| `sample_history` | `sample_history` | `history:YYYY-MM` | The same span of sample months. |
| `account_assignments` | `account_assignments` | `period:YYYY-MM` | Ownership as of the reporting month. |

The two history slots read **many** versions. Build plan 13B requires the
Action's inputs to "include the historical information required by the report
specification", and the year-over-year and year-to-date windows of build plan
13C need up to two years of months. Phase 11 resolved one version per slot; the
`history` selector added in Phase 13 resolves a set, concatenates them in
period order and records every version it read. See
[`docs/architecture.md`](architecture.md) §5c.

Accepted schemas are the ones Phase 10A froze — see
[`docs/monthly-source-schemas.md`](monthly-source-schemas.md). Column names are
matched exactly, with no aliasing (rule `source_schemas`). The
account-assignment schema is itself provisional there; this specification
inherits that status rather than restating it.

### Column roles

Something has to say which of the fifteen transaction columns holds the money.
`ReportColumns` in `report_spec.py` is the one place outside
`source_schemas.py` where a source column is named, and every name in it is
copied from the confirmed schema rather than invented. `test_report_spec.py`
asserts each still exists there, so renaming a column in the schema fails a
test rather than producing a report built on a column that is gone.

The owning rep is called **`Sales Rep`** in every table the report produces,
never `Sales Person`. They are different facts: `Sales Person` is the rep on
the document, and the report attributes revenue by the account's owner. Using
one name for both would be the silent substitution of a semantically different
field that build plan section 3.3 forbids.

---

## Reporting period

**One period, resolved once, before any calculation** (build plan 13C). No
section decides for itself what "this month" means.

The reporting period is the **greatest calendar month present in `Invoice
Date` across the sales history the Run read** (rule `report_month`). It comes
from the data, never from a filename — the rule Phase 10B already established
for ingestion.

The Run chooses it explicitly by bounding its selector: `history:2026-09` reads
every live month through September 2026. **That bounding month must exist**, or
the Run fails with `UNKNOWN_DATASET_VERSION` before the Action starts, so the
month derived from the data is always the month that was requested.

### Windows

Five, all inclusive of both ends (rule `comparison_windows`):

| Window | Definition for a reporting month of `2026-09` |
| --- | --- |
| Reporting Month | 2026-09-01 … 2026-09-30 |
| Prior Month | 2026-08-01 … 2026-08-31 |
| Same Month Last Year | 2025-09-01 … 2025-09-30 |
| Year to Date | 2026-01-01 … 2026-09-30 |
| Prior Year to Date | 2025-01-01 … 2025-09-30 |

A window that contains no rows is reported as `MISSING_COMPARISON_PERIOD`
(warning) and its growth figures are reported as absent, never as zero.

---

## Measures

| Measure | Definition |
| --- | --- |
| Revenue | Sum of `Total Price` (rule `revenue`). |
| Quantity | Sum of `Quantity` (rule `quantity`). |
| Lines | Count of transaction rows. |
| Accounts Sold | Distinct `Customer` values with at least one transaction row in the window. |
| Placements | See [Placements](#placements). |
| Samples | See [Samples](#samples). |

### Credits and returns

Included, at their signed value, and **nothing is filtered out by `Invoice
Type`** (rule `credits`). The confirmed schema states that `Quantity` is
negative on a credit or return, which is only useful if credits are summed
together with sales. Dropping a row because of its type would also be the
silent dropping build plan section 3.3 forbids.

`Invoice` and `Credit` are the expected `Invoice Type` values (rule
`known_invoice_types`, **provisional**). Any other value raises
`UNEXPECTED_INVOICE_TYPE` as a warning and its rows are still counted, because
an unfamiliar label changes no total.

### Precision

Aggregated money is rounded to **two decimal places after summation** (rule
`money_precision`). That is the precision the source data itself carries;
rounding to it removes floating-point residue that would otherwise make one
total read `1550.75` in a workbook and `1550.7500000000002` in a CSV. No source
value is altered, and quantities and percentages are not rounded at all.

### Zero, blank and unreadable values

| Situation | Treatment |
| --- | --- |
| `Quantity` or `Total Price` blank | `MISSING_MEASURE` — **fails the report**. |
| `Quantity` or `Total Price` not a number (`$1,234.56`, `(45.00)`) | `NON_NUMERIC_MEASURE` — **fails the report**. |
| `Quantity` or `Total Price` exactly zero | A number. Counted as zero. |
| `Invoice Date` blank or unreadable | `MALFORMED_INVOICE_DATE` — **fails the report**. |
| A share whose whole is zero or absent | No share. Never `0`. |
| A growth figure whose prior period is zero or absent | No growth figure. Never `0`. |
| A text field blank | Left blank. Nothing is invented and nothing is folded. |

A blank measure read as zero is a substitution that understates a total
invisibly, which is why it fails rather than warns (rule `missing_measures`).

---

## Sales-rep ownership

**An account belongs to the rep the assignment snapshot names for the
reporting month** (rule `ownership`). `Sales Person` on the transaction is
never used to attribute revenue. This is build plan 9E's whole reason for
storing ownership by month: an account owned by one rep in September and
another in November must have September's report built from September's
snapshot.

`Customer` is matched between the transactions and the snapshot **exactly** —
no trimming, no case folding, no near match (rule `ownership_matching`).

| Situation | Treatment |
| --- | --- |
| Account has activity in a report window and no owner in the snapshot | `MISSING_ACCOUNT_OWNERSHIP` — **fails the report**. |
| Account is named in the snapshot and has no activity | Not a problem. The rep's report shows it with zeroes. |
| Snapshot assigns one account to two different reps | `DUPLICATE_ACCOUNT_OWNERSHIP` — **fails the report**. |
| Snapshot lists one account twice with the same rep | Not a problem. It says one true thing twice. |
| `Sales Person` on a transaction is not in the snapshot | `UNRECOGNISED_SALES_REP` — warning. |

The two failures are failures because of what they would do to the numbers
while every number still looked plausible. Revenue from an unowned account sits
in the company total and in no rep's report, so every rep's share of the
company would be wrong. An account owned twice is counted twice, so the rep
totals would not add up to the company's.

---

## Rep roster

**The reps are the distinct non-blank `Sales Person` values in the assignment
snapshot for the reporting month** (rule `rep_roster`). No roster is hard-coded
anywhere (build plan 13D): a rep who appears in a new snapshot appears in the
next report, and a rep who leaves stops appearing as soon as their accounts are
reassigned.

A rep with **no activity still receives a report**. Someone who sold nothing
needs to see that as much as someone who sold well, and omitting them would
make a rep's absence from the bundle ambiguous.

---

## Placements

**A placement is an account and product — a (`Customer`, `SKU`) pair — whose
first sale of positive quantity anywhere in the sales history the Run read
falls inside the reporting month** (rule `placement`, **provisional**).

- "Positive quantity" means a credit is never a placement.
- "Anywhere in the history the Run read" makes the answer reproducible: the
  Run records every history version it read, so re-running against those same
  versions gives the same placements.
- The most likely alternative is a fixed look-back — twelve months, say, so a
  product last bought two years ago counts as new again. That is one line in
  `app/services/monthly_report.py` and one rule change here.

A placement is attributed to the rep who owns the account **in the reporting
month**, like every other figure.

`SHORT_PLACEMENT_HISTORY` (warning) is raised when fewer than
`MINIMUM_PLACEMENT_HISTORY_MONTHS` (12) months precede the reporting month,
because a short history makes every product look new and overstates placements
(rule `placement_history`, **provisional**).

---

## Samples

**A sample is a line of the sample dataset** (rule `sample`). Samples are
counted, valued and reported separately, and are **never added to sales** —
build plan 10D requires the two to stay logically distinct, and they are
separate datasets for that reason.

| Measure | Definition |
| --- | --- |
| Sample Lines | Count of sample rows. |
| Sample Quantity | Sum of `Quantity` in the sample dataset. |
| Sample Value | Sum of `Total Price` in the sample dataset. |
| Sample Products | Distinct `SKU` values sampled. |

Samples are attributed by account ownership, exactly like sales.

`SAMPLE_PERIOD_MISMATCH` (**fails the report**, rule `sample_period`,
**provisional**) is raised when the sample history carries earlier months but
none for the reporting month. That means the month's sample file was never
imported, and the report would state that every rep gave no samples — a false
statement rather than a missing one. A month in which no samples were genuinely
given is a different thing and is not a failure.

---

## Company and rep calculations

Company-level figures are **calculated once** and shared by every rep's report
(build plan 13G). A rep's report is then that rep's figures beside the shared
company ones. This matters for accuracy as much as for speed: one company total
cannot disagree with another.

| Derived figure | Definition |
| --- | --- |
| Share | part ÷ whole, stored as a fraction (rule `share`). |
| Growth | (current − prior) ÷ \|prior\|, stored as a fraction (rule `growth`). |
| Company vs rep index | rep's share of a supplier ÷ company's share of that supplier (rule `comparison_index`, **provisional**). |

The absolute value in the growth denominator keeps the sign of the change
meaningful when a prior period was negative, which a month dominated by credits
can be.

Shares and growth figures are stored as **numbers**, not as formatted text:
`0.153`, not `"15.3%"`. Build plan 14F requires percentages in the rendered
workbook to remain numeric, and formatting is Phase 14's job.

An index of `1.0` means the rep sells that supplier in the same proportion as
the company does. Both shares are reported beside the index, so the comparison
stays legible even if the finished report expresses it differently.

---

## Report sections

Twelve tables. Build plan 13F's list of report categories maps onto them as
follows, and the mapping is data (`ReportSection.categories`) rather than prose
that could drift from it — `test_report_spec.py` asserts every 13F category is
answered by at least one section.

| Section | Per rep | Answers |
| --- | --- | --- |
| `rep_summary` | ✓ | rep summary |
| `company_summary` | — | rep summary |
| `account_performance` | ✓ | account performance |
| `supplier_performance` | ✓ | supplier performance, supplier share of rep sales |
| `company_supplier_performance` | — | supplier performance, company-vs-rep supplier comparison |
| `supplier_comparison` | ✓ | company-vs-rep supplier comparison |
| `product_performance` | ✓ | product performance |
| `placements` | ✓ | placements |
| `placement_detail` | ✓ | placement detail |
| `samples` | ✓ | samples |
| `sample_detail` | ✓ | sample detail |
| `data_quality` | — | supporting validation/detail tables |

Every per-rep table carries a `Sales Rep` column and holds **every rep's rows
in one frame**. That is deliberate: an Action declares a fixed set of outputs
and the roster is dynamic (build plan 13D), so a table per rep could not be
declared. Phase 14 slices these frames by `Sales Rep` into one workbook each.

### Sorting

Every table is sorted by its primary measure descending, then by its name
columns ascending (rule `sorting`). A tie broken by name rather than by input
order is what makes two Runs over the same versions produce identical files.

| Section | Sort |
| --- | --- |
| `rep_summary` | Revenue ↓, Sales Rep ↑ |
| `company_summary` | One row. |
| `account_performance` | Sales Rep ↑, Revenue ↓, Customer ↑ |
| `supplier_performance` | Sales Rep ↑, Revenue ↓, Supplier ↑ |
| `company_supplier_performance` | Revenue ↓, Supplier ↑ |
| `supplier_comparison` | Sales Rep ↑, Rep Revenue ↓, Supplier ↑ |
| `product_performance` | Sales Rep ↑, Revenue ↓, SKU ↑ |
| `placements` | Sales Rep ↑, Revenue ↓, Customer ↑, SKU ↑ |
| `placement_detail` | Sales Rep ↑, Invoice Date ↑, Customer ↑, SKU ↑ |
| `samples` | Sales Rep ↑, Sample Value ↓, Customer ↑ |
| `sample_detail` | Sales Rep ↑, Invoice Date ↑, Customer ↑, SKU ↑ |
| `data_quality` | Severity ↑, Code ↑ |

### Totals

A displayed total is the sum of the rows shown in that table for that rep, and
it is calculated by the engine (rule `totals`). Build plan 12D forbids business
calculations in the formatting layer, and the renderer writes literal values
rather than formulas, so a total must arrive already calculated. Phase 14 reads
the totals; it does not compute them.

---

## Validation before generation

Build plan 13H: "Where a condition makes the report unsafe, fail rather than
producing a plausible-looking workbook. Warnings may be used only when
continuing is genuinely safe."

**Errors** are returned from the Action's `validate()` hook, so the Run fails
with a structured `422` **before** any table is calculated and no report is
produced at all.

**Warnings** travel in the `data_quality` result table and in the Run's
metrics, because an Action has no warning channel of its own — everything
`validate()` returns fails the Run. Putting them in a table is also where a
reader needs them: they arrive with the report they qualify.

### Errors — no report is produced

| Code | Condition |
| --- | --- |
| `EMPTY_SALES_HISTORY` | The sales history the Run read has no rows. |
| `MALFORMED_INVOICE_DATE` | An `Invoice Date` is blank or unreadable. |
| `NON_NUMERIC_MEASURE` | A `Quantity` or `Total Price` is not a number. |
| `MISSING_MEASURE` | A `Quantity` or `Total Price` is blank. |
| `MISSING_ACCOUNT_OWNERSHIP` | An account with activity has no owner. |
| `DUPLICATE_ACCOUNT_OWNERSHIP` | One account, two reps, one snapshot. |
| `NO_SALES_REPS` | The snapshot names no rep. |
| `SAMPLE_PERIOD_MISMATCH` | Sample history skips the reporting month. |

A missing required column, an unsupported file, an unknown dataset and an
unresolvable version are refused earlier still, by the runner and the input
resolver, with the codes Phases 3, 10 and 11 established. The report never
re-implements those checks.

### Warnings — the report is produced

| Code | Condition |
| --- | --- |
| `UNRECOGNISED_SALES_REP` | A transaction names a rep the snapshot does not. |
| `UNEXPECTED_INVOICE_TYPE` | An `Invoice Type` value is not an expected one. |
| `UNEXPECTED_SOURCE_COLUMNS` | A source carries undeclared columns. |
| `DUPLICATE_SOURCE_ROWS` | Rows repeat identically within the month. |
| `MISSING_COMPARISON_PERIOD` | A comparison window has no rows. |
| `SHORT_PLACEMENT_HISTORY` | Less history than the placement rule expects. |
| `PROVISIONAL_REPORT_RULES` | Some definitions are not yet confirmed. |

---

## What this phase deliberately does not do

Build plan Phase 13's exit criterion is "The Action produces correct report
DataFrames for every applicable rep, and automated tests prove the business
calculations **before any attention is paid to workbook appearance**."

So Phase 13 produces **tables only**. The Action returns no artifact, renders
no workbook and builds no ZIP. Worksheet structure, formatting, filenames and
the batch bundle are build plan Phase 14, which reads these tables through
`app.services.workbook` — already built and tested in Phase 12.

Nor is there a frontend for it. Choosing a reporting period in the browser is
build plan 15A, with the monthly workflow it belongs to. Until then the Action
is driven in-process or by naming the dataset selectors in the `POST /api/runs`
form, exactly as Phase 11 left it.

---

## Where this is used

```text
Data Library versions
    → app/services/input_resolution.py   history: → many versions → one frame
    → app/models/report_spec.py          this document                  (13A)
    → app/services/monthly_report.py     period, prepared frames,
                                         company + rep tables      (13C–13H)
    → app/actions/monthly_sales_rep_report.py   the registered Action   (13B)
    → Run result tables
    → [Phase 14] app/services/workbook.py → one XLSX per rep
```

See also [`docs/architecture.md`](architecture.md) §5e.
