"""The single place Actions are registered and looked up (build plan section 25).

The API asks the registry what exists; it never contains a chain of
``if action_id == ...`` branches. Adding an Action means writing its module,
importing it here and adding it to :data:`ACTION_REGISTRY` — no other file in
the backend and no file in the frontend has to change.

Module-level :func:`list_actions` and :func:`get_action` read the application's
default registry. :class:`ActionRegistry` is instantiable so tests can build
isolated registries instead of mutating global state.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.actions.base import Action
from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
from app.actions.product_master_builder import ProductMasterBuilderAction


class DuplicateActionIdError(ValueError):
    """Raised when two Actions claim the same ID.

    Overwriting silently would mean a Run could execute logic other than the
    one whose version is recorded in its manifest, so registration fails loudly
    instead (build plan Phase 2.8).
    """


class ActionRegistry:
    """An ordered collection of Actions keyed by ID."""

    def __init__(self, actions: Iterable[Action] = ()) -> None:
        self._actions: dict[str, Action] = {}
        for action in actions:
            self.register(action)

    def register(self, action: Action) -> Action:
        """Add `action`, returning it so callers can register inline.

        Raises:
            ValueError: the Action has a blank ID.
            DuplicateActionIdError: the ID is already registered.
        """
        action_id = getattr(action, "id", "")
        if not action_id:
            raise ValueError(
                f"{type(action).__name__} must declare a non-empty Action id."
            )
        if action_id in self._actions:
            existing = type(self._actions[action_id]).__name__
            raise DuplicateActionIdError(
                f"Action id {action_id!r} is already registered by {existing}; "
                f"{type(action).__name__} cannot reuse it."
            )
        self._actions[action_id] = action
        return action

    def list_actions(self) -> list[Action]:
        """Return every registered Action in registration order."""
        return list(self._actions.values())

    def get_action(self, action_id: str) -> Action | None:
        """Return the Action with `action_id`, or None if it is not registered.

        Unknown IDs never fall back to a default or a near match: the caller
        decides how to report the miss (build plan Phase 2.4).
        """
        return self._actions.get(action_id)

    def __len__(self) -> int:
        return len(self._actions)

    def __contains__(self, action_id: object) -> bool:
        return action_id in self._actions


#: The application's Actions, in the order the Action selector shows them.
#: Register a new Action by importing it above and adding it here.
ACTION_REGISTRY = ActionRegistry(
    (
        ExactDuplicateRemoverAction(),
        ProductMasterBuilderAction(),
    )
)


def list_actions() -> list[Action]:
    """Return every Action registered in the application registry."""
    return ACTION_REGISTRY.list_actions()


def get_action(action_id: str) -> Action | None:
    """Return the application's Action with `action_id`, or None."""
    return ACTION_REGISTRY.get_action(action_id)