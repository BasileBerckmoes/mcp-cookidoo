"""Integration round-trip for CookidooService.rename_custom_recipe.

Creates a `__TEST__` recipe with a TTS action step (so the backend has
annotations to preserve), snapshots the schema.org projection before and
after `rename_custom_recipe`, and asserts the two snapshots differ only in
`name`. That's the best available proof that annotations survive a rename,
since GET strips annotation data from `recipeInstructions`.

Auto-marked ``integration`` by tests/conftest.py; run with:
    COOKIDOO_INTEGRATION=1 .venv/bin/pytest tests/integration -v
"""
from __future__ import annotations

import os

import pytest


async def test_rename_preserves_everything_but_name() -> None:
    if not os.getenv("COOKIDOO_EMAIL") or not os.getenv("COOKIDOO_PASSWORD"):
        pytest.skip("COOKIDOO_EMAIL / COOKIDOO_PASSWORD not set")

    from cookidoo_service import CookidooService, load_cookidoo_credentials

    email, password = load_cookidoo_credentials()
    service = CookidooService(email, password)

    original_name = "__TEST__ rename before"
    renamed = "__TEST__ rename after"
    ingredients = ["100 g water", "5 g salt"]
    steps = [
        "Water and salt into the mixing bowl.",
        "Kochen 3 Min./100°C/Stufe 1.",
    ]

    recipe_id: str | None = None
    try:
        await service.login()
        recipe_id = await service.create_custom_recipe(
            name=original_name,
            ingredients=ingredients,
            steps=steps,
            servings=2,
            prep_time=1,
            total_time=3,
            hints=["one hint"],
            tools=["TM7"],
        )
        assert recipe_id

        before = await service.get_custom_recipe(recipe_id)
        assert before["name"] == original_name

        await service.rename_custom_recipe(recipe_id, renamed)

        after = await service.get_custom_recipe(recipe_id)
        assert after["name"] == renamed

        # Everything except `name` must survive the rename byte-for-byte.
        # Any drift here — image URL, times, ingredients, instructions, tools,
        # yield — would indicate the backend touched fields we didn't send,
        # invalidating the partial-PATCH design.
        for key in before:
            if key == "name":
                continue
            assert after[key] == before[key], (
                f"field {key!r} changed after rename: {before[key]!r} -> {after[key]!r}"
            )
    finally:
        if recipe_id:
            try:
                await service.delete_custom_recipe(recipe_id)
            except Exception as e:
                pytest.fail(
                    f"Teardown failed — recipe {recipe_id} may still exist: {e}"
                )
        await service.close()
