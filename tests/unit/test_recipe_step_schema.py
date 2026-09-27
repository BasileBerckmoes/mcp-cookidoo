"""Schema tests for RecipeStep / RecipeAction (Option A, issue #4).

Verifies that:
- Plain string steps normalize to RecipeStep(text=..., action=None) — backwards
  compat with everything that today passes list[str].
- Structured steps round-trip through the discriminated union.
- Invalid actions raise on validation.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import BrowningAction, CustomRecipe, RecipeStep, SteamingAction, TTSAction


def _base_recipe(**overrides):
    base = dict(
        name="R",
        ingredients=["a"],
        steps=["only step"],
    )
    base.update(overrides)
    return CustomRecipe(**base)


def test_string_step_normalizes_to_recipe_step_without_action() -> None:
    r = _base_recipe(steps=["chop the onion", "mix well"])
    assert all(isinstance(s, RecipeStep) for s in r.steps)
    assert [s.text for s in r.steps] == ["chop the onion", "mix well"]
    assert all(s.action is None for s in r.steps)


def test_structured_tts_step_roundtrips() -> None:
    r = _base_recipe(steps=[{"text": "Meng 5 sec op stand 5.", "action": {"kind": "tts", "time": 5, "speed": "5"}}])
    step = r.steps[0]
    assert step.text == "Meng 5 sec op stand 5."
    assert isinstance(step.action, TTSAction)
    assert step.action.time == 5
    assert step.action.speed == "5"
    assert step.action.temperature is None
    assert step.action.direction is None


def test_structured_tts_with_temperature_and_direction() -> None:
    r = _base_recipe(steps=[{
        "text": "Cook 3 min at 100°C reverse speed 1.",
        "action": {"kind": "tts", "time": 180, "speed": "1", "temperature": 100, "direction": "reverse"},
    }])
    a = r.steps[0].action
    assert a.temperature == 100
    assert a.direction == "reverse"


def test_structured_steaming_step_roundtrips() -> None:
    r = _base_recipe(steps=[{"text": "Stoom 15 min op stand 2.", "action": {"kind": "steaming", "time": 900, "speed": "2"}}])
    a = r.steps[0].action
    assert isinstance(a, SteamingAction)
    assert a.time == 900
    assert a.speed == "2"


def test_structured_browning_step_roundtrips() -> None:
    r = _base_recipe(steps=[{
        "text": "Aanbraden 7 min op 160°C intensief.",
        "action": {"kind": "browning", "time": 420, "temperature": 160, "power": "Intense"},
    }])
    a = r.steps[0].action
    assert isinstance(a, BrowningAction)
    assert a.time == 420
    assert a.temperature == 160
    assert a.power == "Intense"


def test_mixed_string_and_structured_steps_coexist() -> None:
    r = _base_recipe(steps=[
        "prose only",
        {"text": "5 sec/stand 5", "action": {"kind": "tts", "time": 5, "speed": "5"}},
    ])
    assert r.steps[0].action is None
    assert r.steps[0].text == "prose only"
    assert isinstance(r.steps[1].action, TTSAction)


def test_unknown_action_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{"text": "x", "action": {"kind": "warp_speed", "time": 1, "speed": "1"}}])


def test_tts_action_missing_required_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{"text": "x", "action": {"kind": "tts", "time": 5}}])  # missing speed


def test_browning_temperature_out_of_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{
            "text": "x",
            "action": {"kind": "browning", "time": 300, "temperature": 200, "power": "Intense"},
        }])


def test_browning_power_out_of_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{
            "text": "x",
            "action": {"kind": "browning", "time": 300, "temperature": 160, "power": "Wild"},
        }])


def test_browning_time_capped_at_1800_seconds() -> None:
    # 30 min max.
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{
            "text": "x",
            "action": {"kind": "browning", "time": 1801, "temperature": 160, "power": "Gentle"},
        }])


def test_direction_only_reverse_or_none() -> None:
    with pytest.raises(ValidationError):
        _base_recipe(steps=[{
            "text": "x",
            "action": {"kind": "tts", "time": 5, "speed": "5", "direction": "sideways"},
        }])
