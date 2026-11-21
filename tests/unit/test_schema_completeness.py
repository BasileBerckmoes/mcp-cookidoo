"""Schema-completeness tests: speed/temperature enums, Varoma temperature,
steaming accessories, extra modes, UTF-16 positions, and read-back mapping.
"""
from __future__ import annotations

import pytest
from cookidoo_api.types import (
    CookidooIngredientAnnotation,
    CookidooInstruction,
    CookidooModeAnnotation,
    CookidooTemperatureSetting,
    CookidooTTSAnnotation,
)
from pydantic import ValidationError

from cookidoo_service import (
    _annotations_from_action,
    _step_to_instruction,
    action_from_annotation,
    build_instruction,
    step_from_instruction,
)
from schemas import (
    BrowningAction,
    ModeAction,
    RecipeStep,
    SteamingAction,
    TTSAction,
)

# --- speed / temperature validation -----------------------------------------


@pytest.mark.parametrize("speed,expected", [("soft", "soft"), ("0.5", "0.5"), ("4.0", "4"), (10, "10"), ("SOFT", "soft")])
def test_speed_accepts_device_values(speed, expected) -> None:
    assert TTSAction(time=5, speed=speed).speed == expected


@pytest.mark.parametrize("speed", ["0", "11", "3.3", "turbo", ""])
def test_speed_rejects_non_device_values(speed) -> None:
    with pytest.raises(ValidationError):
        TTSAction(time=5, speed=speed)


@pytest.mark.parametrize("temp", [37, 98, 120, "100"])
def test_temperature_accepts_device_values(temp) -> None:
    assert TTSAction(time=5, speed="1", temperature=temp).temperature == int(temp)


@pytest.mark.parametrize("temp", [36, 62, 121, 160, "hot", True])
def test_temperature_rejects_non_device_values(temp) -> None:
    with pytest.raises(ValidationError):
        TTSAction(time=5, speed="1", temperature=temp)


def test_varoma_temperature_serializes_without_unit() -> None:
    a = TTSAction(time=600, speed="1", temperature="Varoma")
    assert a.temperature == "varoma"
    data = _annotations_from_action("Cook 10 min at Varoma on speed 1.", a, [])[0]["data"]
    assert data["temperature"] == {"value": "varoma"}


# --- steaming ----------------------------------------------------------------


def test_steaming_defaults_to_varoma_clockwise() -> None:
    data = _annotations_from_action("Steam.", SteamingAction(time=900, speed="2"), [])[0]["data"]
    assert data == {"time": 900, "speed": "2", "direction": "CW", "accessory": "Varoma"}


@pytest.mark.parametrize("acc", ["SimmeringBasket", "VaromaAndSimmeringBasket"])
def test_steaming_accessory_options(acc) -> None:
    a = SteamingAction(time=900, speed="1", accessory=acc, direction="reverse")
    data = _annotations_from_action("Steam.", a, [])[0]["data"]
    assert data["accessory"] == acc
    assert data["direction"] == "CCW"


def test_steaming_rejects_unknown_accessory() -> None:
    with pytest.raises(ValidationError):
        SteamingAction(time=900, speed="1", accessory="Basket")


# --- extra modes -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["dough", "turbo", "blend", "warm_up", "rice_cooker"])
def test_extra_modes_parse_via_recipe_step(kind) -> None:
    step = RecipeStep(text="Run mode.", action={"kind": kind, "time": 60})
    assert isinstance(step.action, ModeAction)
    ann = _annotations_from_action(step.text, step.action, [])[0]
    assert ann["type"] == "MODE"
    assert ann["name"] == kind.upper()
    assert ann["data"] == {"time": 60}


def test_warm_up_mode_carries_temperature_and_speed() -> None:
    a = ModeAction(kind="warm_up", time=1800, temperature=37, speed="soft")
    data = _annotations_from_action("Warm.", a, [])[0]["data"]
    assert data == {"time": 1800, "temperature": {"value": "37", "unit": "C"}, "speed": "soft"}


def test_extra_mode_validates_speed_and_temperature() -> None:
    with pytest.raises(ValidationError):
        ModeAction(kind="warm_up", temperature=62)
    with pytest.raises(ValidationError):
        ModeAction(kind="blend", speed="12")


# --- UTF-16 positions ----------------------------------------------------------


def _slice_utf16(text: str, pos: dict) -> str:
    enc = text.encode("utf-16-le")
    return enc[pos["offset"] * 2:(pos["offset"] + pos["length"]) * 2].decode("utf-16-le")


def test_ingredient_position_is_utf16_after_emoji() -> None:
    text = "🍦 Voeg 200 g suiker toe."
    ann = build_instruction(text, ["200 g suiker"])["annotations"][0]
    assert _slice_utf16(text, ann["position"]) == "200 g suiker"


def test_action_span_length_is_utf16_with_emoji() -> None:
    text = "Mix 5 sec 🌀 speed 5."
    step = RecipeStep(text=text, action=TTSAction(time=5, speed="5"))
    pos = _step_to_instruction(step, [])["annotations"][0]["position"]
    assert pos == {"offset": 0, "length": len(text.encode("utf-16-le")) // 2}


def test_bmp_only_positions_unchanged() -> None:
    text = "Voeg 200 g suiker toe (°C)."
    ann = build_instruction(text, ["200 g suiker"])["annotations"][0]
    assert ann["position"] == {"offset": 5, "length": 12}


# --- read-back mapping (cookidoo-api 0.19 dataclasses) -------------------------


def test_tts_annotation_maps_back_to_action() -> None:
    ann = CookidooTTSAnnotation(
        slot="x", time=720, temperature=CookidooTemperatureSetting("100", "C"),
        speed="1", direction="CCW",
    )
    assert action_from_annotation(ann) == {
        "kind": "tts", "time": 720, "speed": "1", "temperature": 100, "direction": "reverse",
    }


def test_varoma_tts_annotation_maps_back() -> None:
    ann = CookidooTTSAnnotation(slot="x", time=60, temperature=CookidooTemperatureSetting("varoma"), speed="1")
    assert action_from_annotation(ann)["temperature"] == "varoma"


@pytest.mark.parametrize(
    "mode,kwargs,expected",
    [
        ("steaming", dict(time=900, speed="2", direction="CW", accessory="SimmeringBasket"),
         {"kind": "steaming", "time": 900, "speed": "2", "accessory": "SimmeringBasket"}),
        ("browning", dict(time=420, temperature=CookidooTemperatureSetting("160", "C"), power="Intense"),
         {"kind": "browning", "time": 420, "temperature": 160, "power": "Intense"}),
        ("dough", dict(time=120), {"kind": "dough", "time": 120}),
        ("STEAMING", dict(time=60, speed="1"),
         {"kind": "steaming", "time": 60, "speed": "1", "accessory": "Varoma"}),
    ],
)
def test_mode_annotations_map_back(mode, kwargs, expected) -> None:
    assert action_from_annotation(CookidooModeAnnotation(slot="x", mode=mode, **kwargs)) == expected


def test_ingredient_annotation_maps_to_none() -> None:
    assert action_from_annotation(CookidooIngredientAnnotation("flour", "flour")) is None


@pytest.mark.parametrize(
    "action",
    [
        TTSAction(time=720, speed="1", temperature=100, direction="reverse"),
        TTSAction(time=60, speed="soft", temperature="varoma"),
        SteamingAction(time=900, speed="2", accessory="VaromaAndSimmeringBasket"),
        BrowningAction(time=420, temperature=160, power="Gentle"),
        ModeAction(kind="dough", time=120),
    ],
)
def test_write_then_read_round_trip(action) -> None:
    """Annotation we emit → library parser → our mapper gives the same action."""
    from cookidoo_api.helpers import _parse_custom_recipe_annotation

    text = "Run the action."
    ann = _annotations_from_action(text, action, [])[0]
    parsed = _parse_custom_recipe_annotation(ann, text)
    back = RecipeStep(text=text, action=action_from_annotation(parsed)).action
    assert back == action


def test_step_from_instruction_handles_plain_strings_and_instructions() -> None:
    assert step_from_instruction("Add flour.") == {"text": "Add flour.", "action": None}
    instr = CookidooInstruction(
        text="Mix 5 sec speed 5.",
        annotations=[CookidooTTSAnnotation(slot="x", time=5, speed="5")],
    )
    assert step_from_instruction(instr) == {
        "text": "Mix 5 sec speed 5.",
        "action": {"kind": "tts", "time": 5, "speed": "5"},
    }
