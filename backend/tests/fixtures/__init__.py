"""Controlled datasets for the Phase 4 accuracy tests.

Each module here holds one hand-built dataset together with the output its
Action must produce, written out row by row. The expected output is *defined*,
not derived: nothing in this package calls an Action or re-implements a
transformation, so a test comparing the two is comparing the implementation
against a human decision rather than against itself.

The rows are Python literals rather than committed `.csv`/`.xlsx` blobs.
`tests.helpers.csv_bytes` and `tests.helpers.xlsx_bytes` render the same rows
into either format, which is what lets one fixture prove that a CSV upload and
an XLSX upload of the same data produce the same result (build plan Phase 4D).
"""