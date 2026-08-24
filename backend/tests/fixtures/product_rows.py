"""Controlled dataset for the Product Master Builder (build plan Phase 4B).

The upload is a sales extract, so it deliberately does *not* look like the
Product Master: it carries two extra columns (`Order Id`, `Quantity`) that must
be dropped, and it lists the six required columns in a different order from the
one the output must use.

Contains, deliberately:

- repeated identical products     rows 2 and 9 repeat row 1's six values
- different vintages              row 3 is row 1 at a different Vintage
- different volumes               row 4 is row 1 in 1.5L
- different selections            row 5 is row 1 under a different Selection
- blank values                    rows 7 and 8 have no Vintage; rows 10 and 11
                                  have no Selection
- accented producer/selection     "Château Margaux", "Côtes du Rhône",
                                  "Réserve", "Domaine Père et Fils"

Rows 10 and 11 are the accent trap: same SKU, same everything, except that one
producer is spelled "Domaine Père et Fils" and the other "Domaine Pere et
Fils". Stripping accents would collapse them into one product and quietly lose
a row of company data. They must both survive.
"""

from __future__ import annotations

from typing import Any

#: Column names as uploaded — six required plus two the Action must ignore,
#: in an order that does not match the required output order.
HEADER: tuple[str, ...] = (
    "Order Id",
    "SKU",
    "Producer",
    "Supplier",
    "Selection",
    "Vintage",
    "Volume",
    "Quantity",
)

#: The upload, in order. `None` is a blank cell.
ROWS: tuple[tuple[Any, ...], ...] = (
    (1001, "CM-750-19", "Château Margaux", "Wine Imports Co", "Côtes du Rhône", 2019, "750ml", 6),
    # Same product; only Order Id and Quantity differ, and neither is part of
    # the Product Master.
    (1002, "CM-750-19", "Château Margaux", "Wine Imports Co", "Côtes du Rhône", 2019, "750ml", 12),
    # Different vintage.
    (1003, "CM-750-18", "Château Margaux", "Wine Imports Co", "Côtes du Rhône", 2018, "750ml", 3),
    # Different volume.
    (1004, "CM-150-19", "Château Margaux", "Wine Imports Co", "Côtes du Rhône", 2019, "1.5L", 1),
    # Different selection.
    (1005, "CM-750-19", "Château Margaux", "Wine Imports Co", "Réserve", 2019, "750ml", 2),
    (1006, "BCZ-750-20", "Bodega Catena Zapata", "Andes Selections", "Reserva", 2020, "750ml", 24),
    # Blank vintage.
    (1007, "BCZ-750-NV", "Bodega Catena Zapata", "Andes Selections", "Reserva", None, "750ml", 5),
    # Exact repeat of row 7, blank vintage included.
    (1008, "BCZ-750-NV", "Bodega Catena Zapata", "Andes Selections", "Reserva", None, "750ml", 5),
    # Third occurrence of row 1's product.
    (1009, "CM-750-19", "Château Margaux", "Wine Imports Co", "Côtes du Rhône", 2019, "750ml", 1),
    # Blank selection.
    (1010, "DOM-750-21", "Domaine Père et Fils", "Loire Distributors", None, 2021, "750ml", 8),
    # Same SKU as row 10 but an unaccented producer: a different product.
    (1011, "DOM-750-21", "Domaine Pere et Fils", "Loire Distributors", None, 2021, "750ml", 8),
)

#: The exact output column order build plan section 27 requires.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "SKU",
    "Vintage",
    "Supplier",
    "Producer",
    "Selection",
    "Volume",
)

#: The Product Master output, defined by hand: one row per distinct
#: combination of the six columns, in the order each first appeared, with the
#: two sales-only columns gone and accents intact.
EXPECTED_ROWS: tuple[tuple[Any, ...], ...] = (
    ("CM-750-19", 2019, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "750ml"),
    ("CM-750-18", 2018, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "750ml"),
    ("CM-150-19", 2019, "Wine Imports Co", "Château Margaux", "Côtes du Rhône", "1.5L"),
    ("CM-750-19", 2019, "Wine Imports Co", "Château Margaux", "Réserve", "750ml"),
    ("BCZ-750-20", 2020, "Andes Selections", "Bodega Catena Zapata", "Reserva", "750ml"),
    ("BCZ-750-NV", None, "Andes Selections", "Bodega Catena Zapata", "Reserva", "750ml"),
    ("DOM-750-21", 2021, "Loire Distributors", "Domaine Père et Fils", None, "750ml"),
    ("DOM-750-21", 2021, "Loire Distributors", "Domaine Pere et Fils", None, "750ml"),
)

#: Metrics the Action must report for this dataset.
EXPECTED_INPUT_ROWS = 11
EXPECTED_OUTPUT_ROWS = 8
EXPECTED_DUPLICATE_PRODUCT_ROWS_REMOVED = 3