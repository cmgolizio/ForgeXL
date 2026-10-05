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

Sales and samples remain separate datasets. Monthly report performance uses
invoice `Sales Person`. Rep/customer context comes from transaction activity;
account assignment files are not an input or an upload option.

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

## Monthly report upload behavior

The monthly reporting form accepts a single month or several years in either
source slot. The reporting month is an explicit selection. Parsed rows are
partitioned by `Invoice Date` with no transformations to source fields.
Every month must contain its complete source data; the app never appends a
partial export to an already saved month.

Required-column, malformed-date, future-date, numeric and invoice-type checks
still apply. Ambiguous data worksheets and unsupported extensions are refused.
Exactly unchanged stored months are reused without duplication. A conflict in
any uploaded partition blocks the entire review before any new source writes.
Corrections contain just the selected month and explicitly name the current
version with a reason; the superseded version remains available.

All source partitions validate before saving. A later disk or receipt failure
reports every successful month/version and preserves those valid commits.
Revalidating the same files safely reuses completed partitions and saves the
remaining ones. Report receipts record only the sales/sample versions through
the selected month. Existing low-level snapshot storage remains for historical
compatibility; it is not exposed by the report catalog, form or Action.

See [monthly-sales-rep-report-spec.md](monthly-sales-rep-report-spec.md) for
report calculations and [architecture.md](architecture.md) for ingestion,
resolution, versioning and artifact delivery.
