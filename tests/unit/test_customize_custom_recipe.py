"""Surgical edits on an official-recipe copy (customize_custom_recipe)."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from cookidoo_service import CookidooService, RecipeEditError, apply_recipe_edits
from schemas import RecipeEdits

# A copy of an official recipe as the full+json read returns it: mixed
# prose+action steps with mid-text spans, an annotation type we don't model,
# and a Varoma step for the chicken.
RAW = {
    "recipeId": "01COPY",
    "recipeContent": {
        "name": "Kip met rijst",
        "ingredients": [
            {"type": "INGREDIENT", "text": "1 ui"},
            {"type": "INGREDIENT", "text": "250 g rijst"},
            {"type": "INGREDIENT", "text": "600 g kipfilet"},
        ],
        "instructions": [
            {"type": "STEP", "text": "1 ui in de mengkom doen en 5 sec/stand 5 fijnhakken.",
             "annotations": [
                 {"type": "INGREDIENT", "data": {"description": "1 ui"}, "position": {"offset": 0, "length": 4}},
                 {"type": "TTS", "data": {"time": 5, "speed": "5"}, "position": {"offset": 25, "length": 14}},
             ]},
            {"type": "STEP", "text": "600 g kipfilet in de Varoma leggen.",
             "annotations": [
                 {"type": "INGREDIENT", "data": {"description": "600 g kipfilet"}, "position": {"offset": 0, "length": 14}},
             ]},
            {"type": "STEP", "text": "250 g rijst toevoegen en 20 min/Varoma/stand 1 stomen.",
             "annotations": [
                 {"type": "INGREDIENT", "data": {"description": "250 g rijst"}, "position": {"offset": 0, "length": 11}},
                 {"type": "MODE", "name": "STEAMING", "data": {"time": 1200, "speed": "1", "direction": "CW", "accessory": "Varoma"},
                  "position": {"offset": 24, "length": 22}},
                 {"type": "FUTURE_THING", "data": {"x": 1}, "position": {"offset": 0, "length": 1}},
             ]},
            {"type": "STEP", "text": "Serveren."},
        ],
        "hints": "Lekker met citroen.",
        "yield": {"value": 4, "unitText": "portion"},
        "prepTime": 900,
        "totalTime": 2400,
        "image": "keep-me.jpg",
    },
}


def edits(*ops):
    return RecipeEdits(edits=list(ops)).edits


EGG_SWAP = edits(
    {"op": "replace_step", "step": 2, "with": [
        {"text": "Big Green Egg: 600 g kipfilet direct grillen op 230°C, 4-5 min per kant, tot 74°C kerntemperatuur."},
    ]},
    {"op": "replace_step", "step": 3, "with": [
        {"text": "250 g rijst toevoegen."},
        {"text": "20 min koken op 100°C, tegendraads, stand 1.",
         "action": {"kind": "tts", "time": 1200, "speed": "1", "temperature": 100, "direction": "reverse"}},
    ]},
)


def test_egg_swap_keeps_untouched_steps_byte_for_byte() -> None:
    raw = copy.deepcopy(RAW)
    patch, summary = apply_recipe_edits(raw, EGG_SWAP)
    steps = patch["instructions"]
    assert steps[0] == RAW["recipeContent"]["instructions"][0]   # mixed step, mid-text TTS span kept
    assert steps[-1] == RAW["recipeContent"]["instructions"][3]
    assert [s["origin"] for s in summary["steps"]] == ["kept 1", "replaces 2", "replaces 3", "replaces 3", "kept 4"]


def test_egg_swap_patches_only_instructions() -> None:
    patch, _ = apply_recipe_edits(copy.deepcopy(RAW), EGG_SWAP)
    assert set(patch) == {"instructions"}  # image, hints, times untouched by partial PATCH


def test_new_steps_get_links_and_actions() -> None:
    patch, summary = apply_recipe_edits(copy.deepcopy(RAW), EGG_SWAP)
    egg = patch["instructions"][1]
    assert any(a["type"] == "INGREDIENT" and a["data"]["description"] == "600 g kipfilet" for a in egg["annotations"])
    cook = patch["instructions"][3]
    assert cook["annotations"][0]["data"]["direction"] == "CCW"
    assert [s["play_button"] for s in summary["steps"]] == [True, False, False, True, False]


def test_unknown_annotation_types_survive_on_kept_steps() -> None:
    patch, _ = apply_recipe_edits(copy.deepcopy(RAW), edits({"op": "delete_steps", "steps": [4]}))
    assert any(a["type"] == "FUTURE_THING" for a in patch["instructions"][2]["annotations"])


def test_step_numbers_refer_to_original_recipe() -> None:
    patch, summary = apply_recipe_edits(copy.deepcopy(RAW), edits(
        {"op": "insert_steps", "after": 0, "steps": [{"text": "Egg aansteken."}]},
        {"op": "delete_steps", "steps": [1]},
        {"op": "insert_steps", "after": 4, "steps": [{"text": "Smakelijk."}]},
    ))
    assert [s["text"] for s in patch["instructions"]] == [
        "Egg aansteken.",
        "600 g kipfilet in de Varoma leggen.",
        "250 g rijst toevoegen en 20 min/Varoma/stand 1 stomen.",
        "Serveren.",
        "Smakelijk.",
    ]


def test_removing_a_linked_ingredient_is_refused() -> None:
    with pytest.raises(RecipeEditError, match="600 g kipfilet"):
        apply_recipe_edits(copy.deepcopy(RAW), edits({"op": "remove_ingredient", "ingredient": "600 g kipfilet"}))


def test_replacing_ingredient_with_its_steps_is_allowed() -> None:
    patch, _ = apply_recipe_edits(copy.deepcopy(RAW), edits(
        {"op": "replace_ingredient", "old": "600 g kipfilet", "new": "800 g kippendijen"},
        {"op": "replace_step", "step": 2, "with": [{"text": "Big Green Egg: 800 g kippendijen grillen."}]},
    ))
    assert patch["ingredients"][2] == {"type": "INGREDIENT", "text": "800 g kippendijen"}
    assert patch["instructions"][1]["annotations"][0]["data"]["description"] == "800 g kippendijen"


@pytest.mark.parametrize("ops,msg", [
    ([{"op": "replace_step", "step": 9, "with": [{"text": "x"}]}], "does not exist"),
    ([{"op": "delete_steps", "steps": [2]}, {"op": "replace_step", "step": 2, "with": [{"text": "x"}]}], "more than one"),
    ([{"op": "delete_steps", "steps": [1, 2, 3, 4]}], "without steps"),
    ([{"op": "replace_ingredient", "old": "1 ajuin", "new": "x"}], "not found"),
    ([{"op": "set_meta", "prep_time": 60, "total_time": 30}], "exceed"),
])
def test_unsafe_edits_are_refused(ops, msg) -> None:
    with pytest.raises(RecipeEditError, match=msg):
        apply_recipe_edits(copy.deepcopy(RAW), edits(*ops))


def test_meta_hints_and_rename() -> None:
    patch, _ = apply_recipe_edits(copy.deepcopy(RAW), edits(
        {"op": "set_hints", "hints": ["Frigo: 4 dagen.", "Diepvries: max 2 porties."]},
        {"op": "rename", "name": "[Egg] Kip met rijst"},
        {"op": "set_meta", "servings": 6, "total_time": 50},
    ))
    assert patch == {
        "hints": "Frigo: 4 dagen.\nDiepvries: max 2 porties.",
        "name": "[Egg] Kip met rijst",
        "yield": {"value": 6, "unitText": "portion"},
        "totalTime": 3000,
    }


@pytest.mark.parametrize("raw", [
    {},
    {"recipeContent": {"recipeInstructions": [{"@type": "HowToStep", "text": "x"}]}},
    {"recipeContent": {"instructions": ["plain string step"]}},
])
def test_refuses_when_backend_returns_no_full_recipe(raw) -> None:
    with pytest.raises(RecipeEditError, match="refusing"):
        apply_recipe_edits(raw, edits({"op": "rename", "name": "x"}))


# --- service level: request shape -----------------------------------------------


def _svc(calls: list):
    svc = CookidooService("e@example.com", "pw")
    svc._api_client = SimpleNamespace(localization=SimpleNamespace(url="https://cookidoo.be/foo/bar", language="nl-BE"))  # type: ignore[assignment]
    svc._session = SimpleNamespace()  # type: ignore[assignment]

    async def fake(method, url, *, json_body=None, accept="application/json"):
        calls.append((method, url, json_body, accept))
        return (200, json.dumps(RAW)) if method == "GET" else (204, "")

    svc._authed_request = fake  # type: ignore[method-assign]
    return svc


async def test_dry_run_reads_full_json_and_sends_nothing() -> None:
    calls: list = []
    summary = await _svc(calls).customize_custom_recipe("01COPY", EGG_SWAP, dry_run=True)
    assert calls == [("GET", "https://cookidoo.be/created-recipes/nl-BE/01COPY", None,
                      "application/vnd.vorwerk.customer-recipe.full+json")]
    assert summary["dry_run"] is True


async def test_apply_patches_only_changed_fields() -> None:
    calls: list = []
    await _svc(calls).customize_custom_recipe("01COPY", EGG_SWAP, dry_run=False)
    method, url, body, _ = calls[1]
    assert method == "PATCH" and url.endswith("/created-recipes/nl-BE/01COPY")
    assert set(body) == {"instructions"}
