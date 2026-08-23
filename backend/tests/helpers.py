"""Shared test helpers for the backend suite."""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from app.actions.base import Action, ActionResult
from app.models.schemas import ActionInput, ActionOutput


def make_action(
    action_id: str,
    *,
    version: str = "1.0.0",
    name: str | None = None,
    required_columns: tuple[str, ...] = (),
) -> Action:
    """Build a throwaway Action for registry and API tests.

    Deliberately not one of the application's real Actions: these tests are
    about the contract and the registry, not about any transformation.
    """
    # Bound to locals because a class body cannot read the enclosing
    # function's names for attributes of the same name.
    declared_version = version
    declared_name = name or action_id.replace("_", " ").title()
    declared_inputs = (
        ActionInput(
            id="source_file",
            label="Source File",
            accepted_extensions=(".csv", ".xlsx"),
            required_columns=required_columns,
        ),
    )

    class _TestAction(Action):
        id = action_id
        version = declared_version
        name = declared_name
        description = f"Test Action {action_id}."
        inputs = declared_inputs
        outputs = (ActionOutput(id="result", label="Result"),)

        def run(self, inputs: Mapping[str, pl.DataFrame]) -> ActionResult:
            return ActionResult(outputs={"result": inputs["source_file"]})

    return _TestAction()