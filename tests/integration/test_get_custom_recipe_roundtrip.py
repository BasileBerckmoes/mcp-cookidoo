"""Integration round-trip for CookidooService.get_custom_recipe.

Creates a minimal ``__TEST__``-prefixed recipe, fetches it back, asserts
name/ingredients/instructions round-trip, then deletes it. Teardown runs in
``finally`` so a mid-test failure still cleans up.

Auto-marked ``integration`` by tests/conftest.py; run with:
    COOKIDOO_INTEGRATION=1 .venv/bin/pytest tests/integration -v
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_get_custom_recipe_roundtrip() -> None:
    if not os.getenv("COOKIDOO_EMAIL") or not os.getenv("COOKIDOO_PASSWORD"):
        pytest.skip("COOKIDOO_EMAIL / COOKIDOO_PASSWORD not set")

    from cookidoo_service import CookidooService, load_cookidoo_credentials

    email, password = load_cookidoo_credentials()
    service = CookidooService(email, password)

    name = "__TEST__ get_custom_recipe roundtrip"
    ingredients = ["100 g water", "5 g salt"]
    steps = [
        "Water and salt into the mixing bowl.",
        "3 Min./100°C/Stufe 1",
    ]

    recipe_id: str | None = None
    try:
        await service.login()
        recipe_id = await service.create_custom_recipe(
            name=name,
            ingredients=ingredients,
            steps=steps,
            servings=2,
            prep_time=1,
            total_time=3,
            hints=["hint line 1"],
            tools=["TM7"],
        )
        assert recipe_id, "create_custom_recipe returned no ID"

        got = await service.get_custom_recipe(recipe_id)

        assert got["id"] == recipe_id
        assert got["name"] == name
        assert got["ingredients"] == ingredients
        assert got["instructions"] == steps
        assert got["tools"] == ["TM7"]
        assert got["yield"] == {"value": 2, "unitText": "portion"}
        assert got["prepTime"] == 60
        assert got["totalTime"] == 180
        # Documented backend gaps — see finding
        # 2026-09-27-custom-recipe-read-schema.md
        assert got["hints"] is None
        assert got["cookTime"] is None
    finally:
        if recipe_id:
            try:
                await service.delete_custom_recipe(recipe_id)
            except Exception as e:
                pytest.fail(
                    f"Teardown failed — recipe {recipe_id} may still exist: {e}"
                )
        await service.close()
