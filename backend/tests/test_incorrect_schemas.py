"""Column names are matched exactly, never approximately (build plan 7C).

Build plan 7C names two specific near-misses:

    SKU       vs  Sku
    Supplier  vs  Supplier Name

and states the rule the whole module exists to hold: **accuracy beats
convenience**. A file whose column is called `Sku` is a file ForgeXL cannot
process, and saying so is the correct behaviour — not resolving it to `SKU`,
not matching case-insensitively, and not trimming a stray space until
something fits.

Every near-miss below would be resolvable by a system willing to guess.
ForgeXL is not: build plan section 3.3 forbids fuzzy matching, guessing what a
column represents and silently choosing a semantically different field, and
each of those is exactly what accepting one of these would be.

The Product Master Builder is the Action under test because it is the one with
a schema. Its six required columns are declared in Python
(`PRODUCT_COLUMNS`), the runner compares them by string equality, and the
refusal names precisely which are missing so the user can fix the file.
"""

from __future__ import annotations

import pytest

from app.actions.product_master_builder import (
    PRODUCT_COLUMNS,
    ProductMasterBuilderAction,
)

from tests.fixtures import spreadsheets as fx
from tests.helpers import upload_file

PRODUCT_MASTER = ProductMasterBuilderAction.id
PRODUCT_MASTER_OUTPUT = "product_master"

#: One valid row, so a refusal below is always about the header and never
#: about the data underneath it.
_ROW = ("SKU-1", 2019, "Acme Imports", "Château Margaux", "Réserve", "750ml")


def _sales_csv(header: tuple[str, ...]) -> bytes:
    return fx.Table(
        name="sales",
        description="A one-row sales extract under a stated header.",
        header=header,
        rows=(_ROW,),
    ).as_csv()


def _replacing(original: str, replacement: str) -> tuple[str, ...]:
    """`PRODUCT_COLUMNS` with one name swapped for a near-miss."""
    return tuple(
        replacement if name == original else name for name in PRODUCT_COLUMNS
    )


def _run(client, header: tuple[str, ...]):
    return client.post(
        "/api/runs",
        data={"action_id": PRODUCT_MASTER},
        files={"sales_file": upload_file("sales.csv", _sales_csv(header))},
    )


# ---------------------------------------------------------------------------
# The two the build plan names
# ---------------------------------------------------------------------------


def test_sku_spelled_Sku_is_refused(client) -> None:
    """Build plan 7C, first case. `Sku` is not `SKU`."""
    response = _run(client, _replacing("SKU", "Sku"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == ["SKU"]
    # The user is told what was actually in the file, so they can see the
    # difference rather than being left to guess at it.
    assert "Sku" in error["details"]["found_columns"]


def test_supplier_spelled_supplier_name_is_refused(client) -> None:
    """Build plan 7C, second case.

    The trap here is that `Supplier Name` is plainly the same *concept*. That
    is precisely why it must be refused: recognising it would mean ForgeXL had
    decided what a column represents, which build plan section 3.3 forbids.
    """
    response = _run(client, _replacing("Supplier", "Supplier Name"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == ["Supplier"]
    assert "Supplier Name" in error["details"]["found_columns"]


# ---------------------------------------------------------------------------
# The same rule, across the ways a name can nearly match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "near_miss"),
    [
        ("SKU", "sku"),
        ("SKU", "Sku"),
        ("SKU", "SKU "),
        ("SKU", " SKU"),
        ("SKU", "SKU_"),
        ("SKU", "S K U"),
        ("Vintage", "VINTAGE"),
        ("Vintage", "Vintage Year"),
        ("Supplier", "supplier"),
        ("Supplier", "Supplier Name"),
        ("Producer", "Producer/Winery"),
        ("Selection", "Selections"),
        ("Volume", "Volume (ml)"),
        ("Volume", "Vol"),
    ],
)
def test_a_near_miss_column_name_is_refused_by_name(
    client, original: str, near_miss: str
) -> None:
    """Every one of these is resolvable by guessing, and none is resolved."""
    response = _run(client, _replacing(original, near_miss))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "MISSING_COLUMNS"
    assert error["details"]["missing_columns"] == [original]


def test_several_wrong_names_are_all_reported_at_once(client) -> None:
    """One request reports every missing column, not the first one found.

    A user fixing a file one error per upload is a worse experience than the
    accuracy rule requires, and the runner already collects the whole list.
    """
    header = ("Order Id", "Sku", "Supplier Name", "Producer", "Selection", "Vol")

    response = _run(client, header)

    assert response.status_code == 422
    assert response.json()["error"]["details"]["missing_columns"] == [
        "SKU",
        "Vintage",
        "Supplier",
        "Volume",
    ]


def test_the_6h_missing_columns_fixture_is_refused(client) -> None:
    """The catalogue's own near-miss fixture, through the API."""
    response = client.post(
        "/api/runs",
        data={"action_id": PRODUCT_MASTER},
        files={
            "sales_file": upload_file(
                fx.MISSING_REQUIRED_COLUMNS.filename(".csv"),
                fx.MISSING_REQUIRED_COLUMNS.as_csv(),
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MISSING_COLUMNS"


# ---------------------------------------------------------------------------
# The other half of the rule: what a correct schema is allowed to look like
# ---------------------------------------------------------------------------


def test_the_exact_schema_is_accepted(client) -> None:
    """The control. Without this, every test above could pass by refusing all."""
    response = _run(client, PRODUCT_COLUMNS)

    assert response.status_code == 200, response.text
    assert tuple(response.json()["outputs"][0]["columns"]) == PRODUCT_COLUMNS


def test_the_required_columns_may_appear_in_any_order(client) -> None:
    """Order in the *file* is free; order in the *output* is fixed.

    Build plan section 27 fixes the Product Master's column order, and it is
    the Action that fixes it — the upload is not required to be arranged that
    way, only to contain the right names.
    """
    response = _run(client, tuple(reversed(PRODUCT_COLUMNS)))

    assert response.status_code == 200, response.text
    assert tuple(response.json()["outputs"][0]["columns"]) == PRODUCT_COLUMNS


def test_columns_the_action_does_not_use_are_dropped_without_complaint(
    client,
) -> None:
    """A sales extract is wider than a product master, and that is normal."""
    header = (*PRODUCT_COLUMNS, "Quantity", "Order Id")

    response = client.post(
        "/api/runs",
        data={"action_id": PRODUCT_MASTER},
        files={
            "sales_file": upload_file(
                "sales.csv",
                fx.Table(
                    name="wide-sales",
                    description="The six required columns plus two others.",
                    header=header,
                    rows=((*_ROW, 6, "ORD-1"),),
                ).as_csv(),
            )
        },
    )

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert tuple(manifest["inputs"][0]["columns"]) == header
    assert tuple(manifest["outputs"][0]["columns"]) == PRODUCT_COLUMNS
    assert manifest["validation"]["warnings"] == []


def test_a_refused_run_is_recorded_with_its_evidence(client) -> None:
    """Build plan 3.9: a failed Run keeps what was uploaded and why it failed.

    The refusal is not only an HTTP response — it is a Run in the store, with
    the file's real columns recorded, so the failure can be looked at
    afterwards rather than only read once.
    """
    response = _run(client, _replacing("Supplier", "Supplier Name"))
    assert response.status_code == 422

    # The Run exists and remembers everything about the attempt.
    listed = client.get("/api/actions")
    assert listed.status_code == 200

    from app.services import run_store

    runs = run_store.list_runs()
    assert len(runs) == 1
    failed = runs[0].to_manifest()

    assert failed.status.value == "failed"
    assert failed.error is not None
    assert failed.error.code == "MISSING_COLUMNS"
    assert failed.validation.passed is False
    assert [issue.code for issue in failed.validation.errors] == ["MISSING_COLUMNS"]
    assert "Supplier Name" in failed.inputs[0].columns
    assert failed.outputs == ()