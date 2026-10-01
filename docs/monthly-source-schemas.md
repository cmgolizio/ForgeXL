# Monthly Input Schemas

This document describes the **software input contract** declared in
`backend/app/models/source_schemas.py`. It contains canonical headers and
validation behavior. Source-specific evidence and production-data findings are
retained in private review material.

## Dataset roles

| Dataset | Kind | Reporting-period policy |
| --- | --- | --- |
| `sales_history` | History | Read `Invoice Date`; commit one month per version. |
| `sample_history` | History | Separate sample data with the same canonical transaction columns. |
| `account_assignments` | Snapshot | Caller explicitly supplies the effective reporting month. |

Sales and samples remain separate datasets. Monthly report performance uses
invoice `Sales Person`. Snapshot ownership supplies current context and never
reattributes historical transactions.

## Exact transaction headers

Both transaction datasets require these columns. Order may vary; spelling,
case and whitespace must match exactly. No alias or fuzzy matching is applied.

| Column | Kind | Role |
| --- | --- | --- |
| `Invoice Date` | Date | Transaction period. |
| `Invoice Type` | Text | Document type checked by the report contract. |
| `Invoice Number` | Text | Invoice identifier. |
| `Customer` | Text | Account identifier. |
| `Cust Type` | Text | Account classification. |
| `Sales Person` | Text | Invoice performance identity. |
| `SKU` | Text | Product identifier. |
| `Vintage` | Text | Product context. |
| `Supplier` | Text | Supplier dimension. |
| `Producer` | Text | Product dimension; accents are preserved. |
| `Selection` | Text | Product dimension; accents are preserved. |
| `Volume` | Text | Product context. |
| `Quantity` | Number | Signed units. |
| `Item Price` | Number | Unit price context. |
| `Total Price` | Number | Signed line value used for net-sales aggregation. |

The report accepts `Invoice` and `Credit Invoice` in sales, and `Sample Invoice`
and `Sample Credit Invoice` in samples. It refuses blank, unfamiliar or
wrong-dataset types. Mixed document exports must be separated explicitly.
Signed credits are summed as supplied, without a second negation.

Sales in report windows require account and invoice rep identities. Samples
require the invoice rep but can have no known account. Ingestion preserves
blank transaction identifiers with a warning; report validation enforces the
stricter performance requirements before artifacts are generated.

## Assignment headers

| Column | Required | Role |
| --- | --- | --- |
| `Customer` | Yes | Account identity. |
| `Sales Person` | Yes | Current snapshot owner. |
| `Prior R12` | No | Optional contextual total. |
| `Current R12` | No | Optional contextual total. |
| `$ Change` | No | Optional contextual change. |
| `% Change` | No | Optional contextual ratio. |

Only the two identity columns are required. The four optional names are
recognized without an unexpected-column warning. These fields are retained in
the snapshot but never reused as invoice-performance calculations. Other extra
columns remain stored and produce `UNEXPECTED_SOURCE_COLUMNS`.

Assignment ingestion refuses blank identities or conflicting owners for one
account (`MISSING_ACCOUNT_ASSIGNMENT_FIELD`, `AMBIGUOUS_ACCOUNT_OWNERSHIP`).
An identical repeated assignment to the same owner is not a conflict. Direct
report-engine calls can warn on conflicting context without multiplying sales;
this does not weaken the ingestion contract or select an owner automatically.

## Parsing and preservation

CSV text identifiers are declared before inference so leading zeroes survive.
Spreadsheet cells retain their parsed types. Numeric columns arriving as text
produce a warning rather than being repaired. The monthly report refuses blank,
unreadable, non-finite and unsafe monetary/quantity values it needs to calculate.

The library stores the parsed frame unchanged: values, extra columns, order,
accents and blanks remain part of the immutable version. Period, source hash,
parser engine and chosen date interpretation are version metadata, not added
columns. Reports apply date interpretation in a working copy.

Supported text date formats are `%Y-%m-%d`, `%Y-%m-%d %H:%M:%S`, `%m/%d/%Y`
and `%d/%m/%Y`. A format must read every populated value. Conflicting valid
interpretations fail with `AMBIGUOUS_DATE_FORMAT`; the caller must select an
explicit format. Existing date cells are used directly. Dates are not trimmed
or guessed from filenames.

## Monthly import behavior

Missing required headers, unreadable/missing dates, several calendar months in
a monthly input, unexpected selected periods, future dates and missing snapshot
periods fail validation before writes. The parser also refuses duplicate headers,
unsupported extensions and ambiguous data worksheets.

Duplicate-file checks use SHA-256 plus reporting period across immutable
versions. An already committed month requires an explicit replacement naming
the old version and a reason. Snapshot bytes may legitimately repeat for another
month. An identical-byte replacement is allowed only to correct a different or
missing date interpretation; unchanged interpretations remain duplicates.

A reporting-cycle import validates all files first. If a later storage failure
occurs, its structured result identifies earlier successful commits and remaining
inputs. History bootstrap partitions validated transaction rows by month;
snapshots remain explicitly period-specific. Exact repeated transaction lines
are preserved and reported; overlap reconciliation is a separate explicit step.

See [monthly-sales-rep-report-spec.md](monthly-sales-rep-report-spec.md) for
report calculations and [architecture.md](architecture.md) for ingestion,
resolution, versioning and artifact delivery.
