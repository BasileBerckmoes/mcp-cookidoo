"""Unit tests for the shared PATCH payload builder.

`_build_recipe_payload` is the pure function extracted from
`create_custom_recipe`. Both `create` and `update_custom_recipe` delegate to
it, so a behavior-preserving extraction is the acceptance for issue #3's
refactor step. Tests pin the output shape (12 keys, seconds-native times,
schema.org-shaped ingredients/instructions, hints joined) so a later drift is
caught without needing an API round-trip.
"""
from __future__ import annotations

from cookidoo_service import _build_recipe_payload, build_instruction, normalize_action_step


def _sample_payload(**overrides):
    """Minimal representative input; overrides let each test focus on one field."""
    base = dict(
        name="Test Recipe",
        ingredients=["100 g water", "5 g salt"],
        steps=[
            "Water and salt into the mixing bowl.",
            "Kochen 3 Min./100°C/Stufe 1.",
        ],
        servings=2,
        prep_time_seconds=60,
        total_time_seconds=180,
    )
    base.update(overrides)
    return _build_recipe_payload(**base)


def test_payload_has_all_twelve_keys() -> None:
    """Payload matches the exact key set that `create_custom_recipe` used to
    build inline. New keys should be a deliberate change here, not a drift."""
    p = _sample_payload()
    assert set(p) == {
        "name",
        "image",
        "isImageOwnedByUser",
        "tools",
        "yield",
        "prepTime",
        "cookTime",
        "totalTime",
        "ingredients",
        "instructions",
        "hints",
        "workStatus",
        "recipeMetadata",
    }


def test_times_pass_through_as_seconds() -> None:
    p = _sample_payload(prep_time_seconds=90, total_time_seconds=240)
    assert p["prepTime"] == 90
    assert p["totalTime"] == 240
    assert p["cookTime"] == 0


def test_cook_time_seconds_override() -> None:
    p = _sample_payload(cook_time_seconds=45)
    assert p["cookTime"] == 45


def test_yield_shape() -> None:
    p = _sample_payload(servings=6)
    assert p["yield"] == {"value": 6, "unitText": "portion"}


def test_ingredients_wrapped_as_schema_objects() -> None:
    p = _sample_payload(ingredients=["a", "b"])
    assert p["ingredients"] == [
        {"type": "INGREDIENT", "text": "a"},
        {"type": "INGREDIENT", "text": "b"},
    ]


def test_instructions_use_build_instruction_after_normalize() -> None:
    """Steps go through `normalize_action_step` then `build_instruction`, so
    action-shaped steps carry TTS annotations. Guards against a future refactor
    that skips the annotation builder for the update path."""
    steps = ["Kochen 3 Min./100°C/Stufe 1."]
    p = _sample_payload(steps=steps, ingredients=[])
    expected = [build_instruction(normalize_action_step(steps[0]), [])]
    assert p["instructions"] == expected
    # Sanity check that the annotation actually landed.
    assert any(a["type"] == "TTS" for a in p["instructions"][0]["annotations"])


def test_hints_none_becomes_empty_string() -> None:
    p = _sample_payload(hints=None)
    assert p["hints"] == ""


def test_hints_list_joined_with_newline() -> None:
    p = _sample_payload(hints=["one", "two"])
    assert p["hints"] == "one\ntwo"


def test_hints_string_pass_through() -> None:
    p = _sample_payload(hints="single hint")
    assert p["hints"] == "single hint"


def test_tools_default_when_none() -> None:
    p = _sample_payload(tools=None)
    assert p["tools"] == ["TM7", "TM6", "TM5"]


def test_tools_pass_through() -> None:
    p = _sample_payload(tools=["TM7"])
    assert p["tools"] == ["TM7"]


def test_image_defaults_to_none_and_not_owned_by_user() -> None:
    p = _sample_payload()
    assert p["image"] is None
    assert p["isImageOwnedByUser"] is False


def test_image_propagates_and_sets_ownership_flag() -> None:
    """When the caller supplies an image URL (e.g. from a read-modify-write),
    the payload should preserve it and mark it user-owned. Prevents the update
    path from silently dropping an image the recipe already had."""
    p = _sample_payload(image="https://example/img.svg")
    assert p["image"] == "https://example/img.svg"
    assert p["isImageOwnedByUser"] is True


def test_work_status_and_metadata_pinned() -> None:
    p = _sample_payload()
    assert p["workStatus"] == "PRIVATE"
    assert p["recipeMetadata"] == {"requiresAnnotationsCheck": False}
