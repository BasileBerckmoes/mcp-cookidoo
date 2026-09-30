"""Integration round-trip: Dutch recipe with a structured TTS action.

Uploads a `__TEST__` recipe whose action step is in Dutch (which the German
regex parser would miss) but carries a structured `TTSAction` — so the play
button annotation lands via Option A, not via parsing. Reads the recipe back
and asserts the Dutch step text round-trips. Deletes in `try/finally`.

The read endpoint strips annotations from `recipeInstructions`, so this test
cannot assert the annotation shape directly. The debug-recipe gate (per
CLAUDE.md § Session debug recipe) covers the TM7 device visual check.

Auto-marked ``integration`` by tests/conftest.py; run with:
    COOKIDOO_INTEGRATION=1 .venv/bin/pytest tests/integration -v
"""
from __future__ import annotations

import os

import pytest


async def test_dutch_structured_action_round_trip() -> None:
    if not os.getenv("COOKIDOO_EMAIL") or not os.getenv("COOKIDOO_PASSWORD"):
        pytest.skip("COOKIDOO_EMAIL / COOKIDOO_PASSWORD not set")

    from cookidoo_service import CookidooService, load_cookidoo_credentials
    from schemas import RecipeStep, TTSAction

    email, password = load_cookidoo_credentials()
    service = CookidooService(email, password)

    ingredients = ["100 g water", "5 g zout"]
    # Mixed: prose step in Dutch (no action) + Dutch action step with
    # structured TTSAction. The German parser would MISS "5 sec op stand 1"
    # (no `Stufe`, no `Sek.`), so if the play-button annotation survives, it
    # can only be from the structured action path.
    steps = [
        "Water en zout in de mengkom doen.",
        RecipeStep(
            text="Roeren 5 sec op stand 1.",
            action=TTSAction(time=5, speed="1"),
        ),
    ]

    recipe_id: str | None = None
    try:
        await service.login()
        recipe_id = await service.create_custom_recipe(
            name="__TEST__ structured Dutch action",
            ingredients=ingredients,
            steps=steps,
            servings=1,
            prep_time=1,
            total_time=2,
            hints=None,
            tools=["TM7"],
        )
        assert recipe_id

        recipe = await service.get_custom_recipe(recipe_id)
        assert recipe["name"] == "__TEST__ structured Dutch action"
        # Text round-trips (the schema.org GET returns instructions as plain
        # strings; that's the best assertion we get without device inspection).
        assert "Water en zout in de mengkom doen." in recipe["instructions"]
        assert "Roeren 5 sec op stand 1." in recipe["instructions"]
        # Ingredients survive too.
        assert set(recipe["ingredients"]) == set(ingredients)
    finally:
        if recipe_id:
            try:
                await service.delete_custom_recipe(recipe_id)
            except Exception as e:
                pytest.fail(
                    f"Teardown failed — recipe {recipe_id} may still exist: {e}"
                )
        await service.close()
