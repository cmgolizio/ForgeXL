"""Controlled dataset for the Exact Duplicate Remover (build plan Phase 4A).

Contains, deliberately:

- unique rows                 rows 4 and 6 differ from every other row
- exact duplicates            rows 2, 5, 8, 10 and 11 repeat an earlier row
- null values                 blank `Note`, and a blank `Region`
- repeated text               "Widget", "Gadget", "first" recur across rows
- repeated numbers            10 and 25 recur across rows

The two blank-`Region` rows (9 and 10) are identical including their blanks:
they are exact duplicates, because a null is a value like any other here.

Rows 4 and 6 are the trap. Row 4 repeats every value of row 1 except `Region`,
and row 6 repeats every value except `Units`. Neither may be removed.
"""

from __future__ import annotations

from typing import Any

#: Column names, in the order they appear in the upload.
HEADER: tuple[str, ...] = ("Region", "Product", "Units", "Note")

#: The upload, in order. `None` is a blank cell.
ROWS: tuple[tuple[Any, ...], ...] = (
    ("North", "Widget", 10, "first"),
    ("North", "Widget", 10, "first"),  # exact duplicate of row 1
    ("South", "Gadget", 25, None),
    ("East", "Widget", 10, "first"),  # unique: Region differs from row 1
    ("South", "Gadget", 25, None),  # exact duplicate of row 3, blanks included
    ("North", "Widget", 12, "first"),  # unique: Units differs from row 1
    ("West", "Doohickey", 25, "bulk"),
    ("North", "Widget", 10, "first"),  # third occurrence of row 1
    (None, "Gadget", 25, None),  # unique: blank Region
    (None, "Gadget", 25, None),  # exact duplicate of row 9, blanks included
    ("West", "Doohickey", 25, "bulk"),  # exact duplicate of row 7
)

#: The Deduplicated Data output, defined by hand: the first occurrence of each
#: distinct row, in the order those rows first appeared, with the upload's
#: column order unchanged.
EXPECTED_ROWS: tuple[tuple[Any, ...], ...] = (
    ("North", "Widget", 10, "first"),
    ("South", "Gadget", 25, None),
    ("East", "Widget", 10, "first"),
    ("North", "Widget", 12, "first"),
    ("West", "Doohickey", 25, "bulk"),
    (None, "Gadget", 25, None),
)

#: Metrics the Action must report for this dataset.
EXPECTED_INPUT_ROWS = 11
EXPECTED_OUTPUT_ROWS = 6
EXPECTED_DUPLICATES_REMOVED = 5