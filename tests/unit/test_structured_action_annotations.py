"""Annotation-emission tests for structured RecipeStep actions (Option A).

Locks the mapping from a `RecipeAction` to the Cookidoo annotation dict that
`_build_recipe_payload` emits, and asserts the German-text parser fallback
still runs when a step has no structured action (backwards compat).
"""
from __future__ import annotations

from cookidoo_service import (
    _annotations_from_action,
    _build_recipe_payload,
    _step_to_instruction,
    build_instruction,
    normalize_action_step,
)
from schemas import BrowningAction, RecipeStep, SteamingAction, TTSAction


def test_tts_action_emits_tts_annotation_spanning_full_text() -> None:
    text = "Meng 5 sec op stand 5."
    action = TTSAction(time=5, speed="5")
    anns = _annotations_from_action(text, action, ingredients=[])
    assert anns == [
        {
            "type": "TTS",
            "data": {"speed": "5", "time": 5},
            "position": {"offset": 0, "length": len(text)},
        }
    ]


def test_tts_action_with_temperature_emits_temperature_dict() -> None:
    text = "Cook 3 min at 100°C speed 1."
    action = TTSAction(time=180, speed="1", temperature=100)
    anns = _annotations_from_action(text, action, ingredients=[])
    assert anns[0]["data"]["temperature"] == {"value": "100", "unit": "C"}


def test_steaming_action_emits_mode_steaming_with_varoma_accessory() -> None:
    text = "Stoom 15 min op stand 2."
    action = SteamingAction(time=900, speed="2")
    anns = _annotations_from_action(text, action, ingredients=[])
    assert anns == [
        {
            "type": "MODE",
            "name": "STEAMING",
            "data": {
                "time": 900,
                "speed": "2",
                "direction": "CW",
                "accessory": "Varoma",
            },
            "position": {"offset": 0, "length": len(text)},
        }
    ]


def test_browning_action_emits_mode_browning() -> None:
    text = "Aanbraden 7 min op 160°C intensief."
    action = BrowningAction(time=420, temperature=160, power="Intense")
    anns = _annotations_from_action(text, action, ingredients=[])
    assert anns == [
        {
            "type": "MODE",
            "name": "BROWNING",
            "data": {
                "time": 420,
                "temperature": {"value": "160", "unit": "C"},
                "power": "Intense",
            },
            "position": {"offset": 0, "length": len(text)},
        }
    ]


def test_step_to_instruction_string_uses_parser_fallback() -> None:
    # Legacy path: plain string still goes through normalize + parser.
    step = "Kochen 3 Min./100°C/Stufe 1."
    inst = _step_to_instruction(step, ingredients=[])
    assert inst == build_instruction(normalize_action_step(step), [])
    assert any(a["type"] == "TTS" for a in inst["annotations"])


def test_step_to_instruction_no_action_uses_parser_fallback() -> None:
    # RecipeStep without an action still parses text (backwards compat).
    step = RecipeStep(text="Kochen 3 Min./100°C/Stufe 1.")
    inst = _step_to_instruction(step, ingredients=[])
    assert inst == build_instruction(normalize_action_step(step.text), [])


def test_step_to_instruction_with_action_skips_parser() -> None:
    # Dutch text with a structured action — parser would miss (no 'Stufe'),
    # but the annotation should still land.
    step = RecipeStep(
        text="Meng 5 sec op stand 5.",
        action=TTSAction(time=5, speed="5"),
    )
    inst = _step_to_instruction(step, ingredients=[])
    assert inst["text"] == "Meng 5 sec op stand 5."
    assert inst["annotations"] == [
        {
            "type": "TTS",
            "data": {"speed": "5", "time": 5},
            "position": {"offset": 0, "length": len(step.text)},
        }
    ]


def test_build_recipe_payload_mixed_string_and_structured_steps() -> None:
    # End-to-end: `_build_recipe_payload` accepts a mixed list of strings and
    # RecipeSteps, dispatching per element.
    steps = [
        "Water and salt into the mixing bowl.",
        RecipeStep(text="Meng 5 sec op stand 5.", action=TTSAction(time=5, speed="5")),
        "Kochen 3 Min./100°C/Stufe 1.",  # German string still parses
    ]
    p = _build_recipe_payload(
        name="R",
        ingredients=[],
        steps=steps,
        servings=1,
        prep_time_seconds=60,
        total_time_seconds=120,
    )
    instructions = p["instructions"]
    assert len(instructions) == 3
    # Step 0: prose, no annotations from action
    assert instructions[0]["text"] == "Water and salt into the mixing bowl."
    assert all(a["type"] == "INGREDIENT" for a in instructions[0]["annotations"]) or instructions[0]["annotations"] == []
    # Step 1: Dutch with structured action → TTS annotation
    assert instructions[1]["text"] == "Meng 5 sec op stand 5."
    assert instructions[1]["annotations"] == [
        {"type": "TTS", "data": {"speed": "5", "time": 5}, "position": {"offset": 0, "length": len("Meng 5 sec op stand 5.")}}
    ]
    # Step 2: German string → parser fallback emits TTS
    assert any(a["type"] == "TTS" for a in instructions[2]["annotations"])


def test_structured_action_annotation_language_neutral() -> None:
    """The same TTSAction produces byte-identical annotations regardless of the
    surrounding text language."""
    action = TTSAction(time=5, speed="5")
    for text in (
        "Meng 5 sec op stand 5.",  # Dutch
        "Blend 5 sec at speed 5.",  # English
        "Zerkleinern 5 Sek. Stufe 5.",  # German
        "5秒 スピード5",  # Japanese
    ):
        anns = _annotations_from_action(text, action, ingredients=[])
        assert anns[0]["type"] == "TTS"
        assert anns[0]["data"] == {"speed": "5", "time": 5}
        assert anns[0]["position"] == {"offset": 0, "length": len(text)}
