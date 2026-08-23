"""Action contract and registry tests (build plan Phase 2.8)."""

from __future__ import annotations

import polars as pl
import pytest

from app.actions import registry as registry_module
from app.actions.base import Action, ActionResult
from app.actions.registry import ActionRegistry, DuplicateActionIdError
from app.models.schemas import ActionDefinition, ActionInput, ActionOutput

from tests.helpers import make_action


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_action_can_register():
    registry = ActionRegistry()
    action = make_action("alpha")

    assert registry.register(action) is action
    assert "alpha" in registry
    assert len(registry) == 1


def test_registry_accepts_actions_at_construction():
    registry = ActionRegistry([make_action("alpha"), make_action("beta")])

    assert len(registry) == 2


def test_register_rejects_blank_action_id():
    registry = ActionRegistry()

    with pytest.raises(ValueError, match="non-empty Action id"):
        registry.register(make_action(""))

    assert len(registry) == 0


# ---------------------------------------------------------------------------
# list_actions
# ---------------------------------------------------------------------------


def test_list_actions_returns_registered_actions_in_registration_order():
    alpha, beta, gamma = make_action("alpha"), make_action("beta"), make_action("gamma")
    registry = ActionRegistry([gamma, alpha, beta])

    assert [action.id for action in registry.list_actions()] == [
        "gamma",
        "alpha",
        "beta",
    ]


def test_list_actions_is_empty_for_an_empty_registry():
    assert ActionRegistry().list_actions() == []


def test_list_actions_returns_a_copy_that_cannot_mutate_the_registry():
    registry = ActionRegistry([make_action("alpha")])

    registry.list_actions().clear()

    assert len(registry) == 1


# ---------------------------------------------------------------------------
# get_action
# ---------------------------------------------------------------------------


def test_get_action_returns_the_matching_action():
    alpha, beta = make_action("alpha"), make_action("beta")
    registry = ActionRegistry([alpha, beta])

    assert registry.get_action("alpha") is alpha
    assert registry.get_action("beta") is beta


def test_get_action_returns_none_for_an_unknown_id():
    registry = ActionRegistry([make_action("alpha")])

    assert registry.get_action("does_not_exist") is None


@pytest.mark.parametrize(
    "action_id",
    ["", "ALPHA", " alpha", "alpha ", "alph", "alpha_extra", "../alpha"],
)
def test_get_action_never_guesses_a_near_match(action_id):
    """An almost-right ID must miss rather than resolve to something else."""
    registry = ActionRegistry([make_action("alpha")])

    assert registry.get_action(action_id) is None


# ---------------------------------------------------------------------------
# Duplicate IDs
# ---------------------------------------------------------------------------


def test_duplicate_action_id_is_rejected_not_silently_overwritten():
    first = make_action("alpha", version="1.0.0")
    second = make_action("alpha", version="2.0.0")
    registry = ActionRegistry([first])

    with pytest.raises(DuplicateActionIdError, match="already registered"):
        registry.register(second)

    # The original Action survives; the second never took its place.
    survivor = registry.get_action("alpha")
    assert survivor is first
    assert survivor is not None and survivor.version == "1.0.0"
    assert len(registry) == 1


def test_duplicate_action_id_is_rejected_at_construction():
    with pytest.raises(DuplicateActionIdError):
        ActionRegistry([make_action("alpha"), make_action("alpha")])


def test_duplicate_action_id_error_is_a_value_error():
    assert issubclass(DuplicateActionIdError, ValueError)


# ---------------------------------------------------------------------------
# Registry isolation
# ---------------------------------------------------------------------------


def test_registries_are_independent():
    one = ActionRegistry([make_action("alpha")])
    two = ActionRegistry([make_action("beta")])

    assert one.get_action("beta") is None
    assert two.get_action("alpha") is None


def test_module_level_helpers_read_the_application_registry():
    expected = registry_module.ACTION_REGISTRY.list_actions()

    assert [a.id for a in registry_module.list_actions()] == [a.id for a in expected]
    for action in expected:
        assert registry_module.get_action(action.id) is action
    assert registry_module.get_action("does_not_exist") is None


def test_application_registry_has_at_least_one_action_with_unique_ids():
    actions = registry_module.list_actions()
    ids = [action.id for action in actions]

    assert actions, "the application must register at least one Action"
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# The Action contract itself
# ---------------------------------------------------------------------------


def test_definition_reports_the_declared_metadata():
    action = make_action("alpha", version="2.3.4", name="Alpha Action")

    definition = action.definition()

    assert isinstance(definition, ActionDefinition)
    assert definition.id == "alpha"
    assert definition.version == "2.3.4"
    assert definition.name == "Alpha Action"
    assert definition.description == "Test Action alpha."
    assert [i.id for i in definition.inputs] == ["source_file"]
    assert definition.inputs[0].accepted_extensions == (".csv", ".xlsx")
    assert definition.inputs[0].required is True
    assert [o.id for o in definition.outputs] == ["result"]
    assert definition.outputs[0].formats == ("csv", "xlsx")


def test_definition_is_immutable():
    definition = make_action("alpha").definition()

    with pytest.raises(Exception):
        definition.id = "beta"


def test_validate_reports_no_issues_by_default():
    action = make_action("alpha")

    assert action.validate({"source_file": pl.DataFrame({"a": [1]})}) == []


def test_run_receives_inputs_keyed_by_slot_id():
    action = make_action("alpha")
    frame = pl.DataFrame({"a": [1, 2, 3]})

    result = action.run({"source_file": frame})

    assert isinstance(result, ActionResult)
    assert result.outputs["result"].equals(frame)
    assert result.metrics == {}


def test_an_action_cannot_be_instantiated_without_run():
    class Incomplete(Action):
        id = "incomplete"
        version = "1.0.0"
        name = "Incomplete"
        description = "Declares metadata but never implements run()."
        inputs = (ActionInput(id="source_file", label="S", accepted_extensions=(".csv",)),)
        outputs = (ActionOutput(id="result", label="Result"),)

    with pytest.raises(TypeError):
        Incomplete()  # pyright: ignore[reportAbstractUsage] — the error is the assertion