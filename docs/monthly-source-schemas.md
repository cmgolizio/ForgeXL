# Monthly Source Schemas

**The authoritative description of the three recurring source files ForgeXL
ingests.** Build plan 10A requires these to be documented exactly, from the
real company exports, with no guessed column names and no silent equivalence
between similar ones.

The declarations live in
[`backend/app/models/source_schemas.py`](../backend/app/models/source_schemas.py).
This document explains them; that module is what the code reads. A column name
appears in the repository **once**, in that module — no service, route or test
spells one of its own.

| Source file         | Dataset ID            | Kind       | Status                        |
| ------------------- | --------------------- | ---------- | ----------------------------- |
| Monthly Sales       | `sales_history`       | `history`  | **Confirmed**                 |
| Monthly Samples     | `sample_history`      | `history`  | **Confirmed**                 |
| Account Assignments | `account_assignments` | `snapshot` | **UNCONFIRMED — provisional** |

---

## The matching rules

These apply to all three files and are the whole of build plan 10A's
"do not guess, do not silently treat semantically similar columns as
equivalent".

**Column names are matched exactly.** No case folding, no whitespace trimming,
no punctuation normalising, no near match. `Sales Person` is not `Salesperson`,
not `sales person`, and not `Sales  Person`. A file spelling it differently is
reported as _missing_ `Sales Person` and as _carrying_ the other name, with
both spellings in the message, so the difference is visible rather than
guessed at.

**There is no aliasing mechanism at all.** Build plan 10A permits normalisation
only if it is "explicitly specified, deterministic, and tested"; the simplest
way to satisfy that is to have none. A test asserts the absence.

**A missing required column fails the import.** The month cannot be used
without it.

**An unexpected extra column is a warning, and the column is kept.** The
reasoning, since this is a judgement rather than a sentence in the build plan:

- Refusing would block a month over a column nothing reads. Exports gain
  columns; a report that does not read them is unaffected.
- Ignoring silently would hide a source-schema change, which build plan 13H
  lists among the conditions that can make a report unreliable.
- So it is reported (`UNEXPECTED_SOURCE_COLUMNS`) and the column is stored with
  the rest of the file. Build plan 10E requires the full source snapshot to be
  preserved, and a column that was dropped could not be recovered later.

If an export has genuinely changed, update the declaration in
`source_schemas.py` — the warning is the prompt to do that.

**Column order is not required.** The canonical order below is the order the
export produces and is worth recording, but a reordered file has lost nothing.
Requiring position would be a rule about presentation rather than about data.

**Nothing is stored except what was uploaded.** The parsed frame goes into the
Data Library exactly as the parser produced it — same columns, same order, same
types, same values, accents and blanks included. The reporting month, the
earliest and latest dates, the parser engine and the source hash are recorded
as _metadata on the version_, never written into the rows.

---

## Sales and Samples — **confirmed**

Both exports are produced by the same system and have **identical header
rows**. They are nonetheless declared as two separate schemas targeting two
separate datasets, because build plan 10D requires sales and samples to stay
logically distinct "even if their source schemas overlap". Folding them into
one is a one-line change that should not exist.

Fifteen columns, in the order the export produces them:

| #   | Column           | Kind   | Notes                                                                 |
| --- | ---------------- | ------ | --------------------------------------------------------------------- |
| 1   | `Invoice Date`   | date   | **The reporting period comes from this column** (build plan 10B).     |
| 2   | `Invoice Type`   | text   | Invoice, credit, and so on.                                           |
| 3   | `Invoice Number` | text   | Text, not a number: a leading zero or a prefix must survive.          |
| 4   | `Customer`       | text   | The account.                                                          |
| 5   | `Cust Type`      | text   | The account's classification.                                         |
| 6   | `Sales Person`   | text   | The rep on the transaction. **Not** the report's source of ownership. |
| 7   | `SKU`            | text   | Product code.                                                         |
| 8   | `Vintage`        | text   | Text: routinely blank or non-numeric, and nothing invents one.        |
| 9   | `Supplier`       | text   |                                                                       |
| 10  | `Producer`       | text   | Accents preserved exactly.                                            |
| 11  | `Selection`      | text   | Accents preserved exactly.                                            |
| 12  | `Volume`         | text   | As exported, e.g. `750ml`.                                            |
| 13  | `Quantity`       | number | Negative on a credit or return.                                       |
| 14  | `Item Price`     | number |                                                                       |
| 15  | `Total Price`    | number |                                                                       |

### What "kind" does and does not do

`kind` drives exactly two behaviours and never converts anything:

- **date** marks the column a reporting period is read from.
- **number** marks a column whose arrival as _text_ is worth reporting
  (`NON_NUMERIC_SOURCE_COLUMN`). A `Total Price` that parsed as text usually
  means the export wrote `$1,234.56` or `(45.00)`. The value is stored exactly
  as it arrived — a parser that turned `(45.00)` into `-45` would have decided
  what the file meant.

Ownership for a report comes from the account-assignment snapshot for the
month, **not** from `Sales Person` on the invoice. That is the point of
build plan 9E: an account owned by one rep in September and another in November
must have September's report built from September's ownership.

---

## Account Assignments — **UNCONFIRMED, provisional**

> **This schema has not been confirmed against the real export.** It is
> declared with `confirmed=False`, a test asserts that, and this section is the
> record of why.

The sales and sample headers above were supplied verbatim. The
account-assignment header was not, and inventing one would be exactly the guess
build plan 10A forbids. Two decisions make the provisional version as harmless
as a provisional version can be:

**The two column names are not invented.** `Customer` and `Sales Person` are
taken verbatim from the confirmed transaction schema — the only evidence
available about how this company spells those two things. A newly made-up
spelling would be a guess; reusing the confirmed one is at least a consistent
guess, and the likeliest to be right.

**It declares a minimum, not a whole file.** Only two columns are required.
Because extra columns are kept and warned about rather than refused, a real
export carrying ten more columns still imports, and the full snapshot is still
stored. The narrower the provisional declaration, the smaller the chance that
the unconfirmed part blocks a real file.

| Column         | Kind | Required | Notes                                          |
| -------------- | ---- | -------- | ---------------------------------------------- |
| `Customer`     | text | yes      | The account. The ownership checks key on this. |
| `Sales Person` | text | yes      | The rep who owns the account for the month.    |

### To confirm it

1. Replace the columns in `ACCOUNT_ASSIGNMENTS_SOURCE_SCHEMA` with the real
   header row, verbatim and in order.
2. Set `confirmed=True`.
3. Update `customer_column` / `rep_column` if the real export names those two
   things differently.
4. Update the table above and remove the UNCONFIRMED marks.
5. Update `test_the_account_assignment_schema_is_marked_unconfirmed` in
   `backend/tests/test_source_schemas.py`, which exists so this status cannot
   be lost by being forgotten.

Nothing else changes. No service names a column of its own.

---

## Reporting period detection

The month a file belongs to is read **from the data**, never from the filename
(build plan 10B: "Do not rely solely on filenames such as `September Sales.csv`").

For sales and samples it comes from `Invoice Date`. For account assignments
there is no date column — the export states ownership as it stands — so the
month **must be supplied by the caller**, which is build plan 10B's "require
explicit user selection" applied to a question the file genuinely cannot
answer.

### Accepted date formats

Tried in this order. A format is accepted only if it reads **every** populated
value in the column; a format that reads most of them has not understood it.

| Format              | Example               |
| ------------------- | --------------------- |
| `%Y-%m-%d`          | `2026-09-04`          |
| `%Y-%m-%d %H:%M:%S` | `2026-09-04 14:30:00` |
| `%m/%d/%Y`          | `09/04/2026`          |
| `%d/%m/%Y`          | `04/09/2026`          |

A column that already carries real dates — which is what an `.xlsx` date cell
produces — is used as it stands and no format is applied.

Two-digit years are deliberately absent: they add ambiguity and no export in
evidence produces one. Values are not trimmed, so ` 2026-09-01` with a leading
space is reported as unreadable rather than quietly repaired.

### Ambiguity is refused, not resolved

`%m/%d/%Y` and `%d/%m/%Y` disagree about `03/04/2026` — 4 March or 3 April.
Both are declared **so that the disagreement is visible**. When two formats
both read a column and produce different dates for any row, the file is
refused as `AMBIGUOUS_DATE_FORMAT` and an explicit format must be stated.
Picking one by preference would move rows into the wrong month, which is the
failure this whole layer exists to prevent.

In practice a month of invoices almost always contains a day past the 12th,
which rules out one reading on its own.

---

## What is checked, and whether it refuses or warns

The split follows build plan 13H's rule: fail where a condition would make a
report unreliable, warn only where continuing is genuinely safe.

### Refusals — nothing is written

| Code                                                                                                             | Condition                                                      |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `SOURCE_SCHEMA_MISMATCH`                                                                                         | A required column is absent (matched exactly).                 |
| `UNREADABLE_REPORTING_DATE`                                                                                      | The date column cannot be read by any declared format.         |
| `AMBIGUOUS_DATE_FORMAT`                                                                                          | Two formats read it and disagree.                              |
| `UNKNOWN_DATE_FORMAT`                                                                                            | A stated format is not one the schema declares.                |
| `EMPTY_REPORTING_PERIOD`                                                                                         | No row carries a readable date.                                |
| `MISSING_REPORTING_DATE`                                                                                         | Some rows have no date, so they belong to no month.            |
| `MULTIPLE_REPORTING_PERIODS`                                                                                     | A monthly file spans more than one month.                      |
| `UNEXPECTED_REPORTING_PERIOD`                                                                                    | The file's month is not the month being imported.              |
| `FUTURE_DATED_ROWS`                                                                                              | A date is later than today.                                    |
| `REPORTING_PERIOD_REQUIRED`                                                                                      | A snapshot was committed without stating its month.            |
| `DUPLICATE_SOURCE_FILE`                                                                                          | These exact bytes are already committed for this month.        |
| `PERIOD_ALREADY_COMMITTED`                                                                                       | The dataset already holds a live version of this month.        |
| `AMBIGUOUS_ACCOUNT_OWNERSHIP`                                                                                    | One account is assigned to two different reps in one snapshot. |
| `MISSING_ACCOUNT_ASSIGNMENT_FIELD`                                                                               | A snapshot row has no account, or no rep.                      |
| `MISMATCHED_REPORTING_PERIODS`                                                                                   | The three monthly files are not for the same month.            |
| `SAME_FILE_FOR_SEVERAL_DATASETS`                                                                                 | One file was supplied for two of the three slots.              |
| `EMPTY_DATASET`, `EMPTY_FILE`, `PARSE_ERROR`, `UNSUPPORTED_EXTENSION`, `AMBIGUOUS_WORKBOOK`, `DUPLICATE_COLUMNS` | The existing parser's own refusals, unchanged.                 |

`FUTURE_DATED_ROWS` is a refusal rather than a warning because a reporting
month is imported after it has happened, and a mistyped future date silently
decides which month a row lands in.

### Warnings — the import proceeds

| Code                        | Condition                                                 |
| --------------------------- | --------------------------------------------------------- |
| `UNEXPECTED_SOURCE_COLUMNS` | The file carries columns the schema does not declare.     |
| `NON_NUMERIC_SOURCE_COLUMN` | A column declared numeric arrived as text.                |
| `BLANK_SOURCE_IDENTIFIER`   | A transaction row has no `Customer` or no `Sales Person`. |

A blank rep on a _transaction_ is not a defect — ownership comes from the
snapshot. A blank rep in a _snapshot_ is a refusal, because it leaves the one
question the snapshot exists to answer unanswered.

---

## Duplicate detection

Build plan 10C.5: an accidental repeat upload must not record a month twice.
Matched on the SHA-256 of the uploaded bytes **and** the reporting period,
across every version the dataset holds — superseded ones included, since a file
that was imported and then corrected has still been imported.

The period is part of the match because identical bytes mean different things
for the two dataset kinds:

- **History**: the bytes decide the month, so identical bytes are always the
  same month. The period adds nothing and a re-upload is caught either way.
- **Snapshot**: the month is supplied by the caller, and identical bytes for a
  _different_ month are normal — account ownership often does not change, so
  October's export is byte-for-byte September's. Refusing that would force the
  user to perturb a correct file to record a true fact. Only the same snapshot
  for the same month is a duplicate.

A month that is already committed is refused as `PERIOD_ALREADY_COMMITTED`
**during validation**, not by the Data Library at commit time. The distinction
matters for the three-file cycle: a rule enforced only at commit could let the
first two inputs land and the third fail, which is the partial state build plan
10F exists to prevent. When the same _file_ is re-uploaded both rules apply, and
the more specific `DUPLICATE_SOURCE_FILE` is the one reported.

Correcting a month that is already committed is deliberate and separate: commit
a replacement naming the version it supersedes and why (build plan 9D). The old
version stays readable, so the report built from it can still be reproduced.

---

## Where this is used

```text
uploaded bytes
    → app/services/parser.py            the Run pipeline's parser, unchanged
    → Polars DataFrame
    → app/models/source_schemas.py      this document                (10A)
    → app/services/reporting_period.py  which month, from the data   (10B)
    → app/services/ingestion.py         row checks, duplicates, commit (10C–10G)
    → app/services/data_library.py      versioned Parquet            (9C)
```

Ingestion uses the **same parser as the Run pipeline**, deliberately: the
extension rules, the worksheet-ambiguity refusal and the duplicate-column
refusal then apply identically to an ingested file and an uploaded one, and
cannot drift apart.

See also [`docs/architecture.md`](architecture.md) §5a and §5b.
