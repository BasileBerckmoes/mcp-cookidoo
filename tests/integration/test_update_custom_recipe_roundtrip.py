"""Integration round-trip for CookidooService.update_custom_recipe.

Creates a `__TEST__` recipe, then updates it with a different name, ingredients,
steps, and times, and asserts the changes round-trip through a subsequent GET.
Uses the same `__TEST__`-prefix + `try/finally` teardown pattern as the other
integration tests.

Auto-marked ``integration`` by tests/conftest.py; run with:
    COOKIDOO_INTEGRATION=1 .venv/bin/pytest tests/integration -v
"""
from __future__ import annotations

import os

import pytest


async def test_update_round_trips_through_get() -> None:
    if not os.getenv("COOKIDOO_EMAIL") or not os.getenv("COOKIDOO_PASSWORD"):
        pytest.skip("COOKIDOO_EMAIL / COOKIDOO_PASSWORD not set")

    from cookidoo_service import CookidooService, load_cookidoo_credentials
    from schemas import CustomRecipe

    email, password = load_cookidoo_credentials()
    service = CookidooService(email, password)

    recipe_id: str | None = None
    try:
        await service.login()
        recipe_id = await service.create_custom_recipe(
            name="__TEST__ update before",
            ingredients=["100 g water"],
            steps=["Water into the mixing bowl."],
            servings=2,
            prep_time=1,
            total_time=3,
            tools=["TM7"],
        )
        assert recipe_id

        # Snapshot pre-update state to smoke-check image survives the update.
        # NOTE: this is a weak signal because the backend normalizes stored
        # `image: null` to the placeholder URL on GET, so a placeholder→
        # placeholder round-trip is indistinguishable from a wiped image. The
        # real regression guard is the unit-test omission set in
        # tests/unit/test_update_custom_recipe.py. Verified once locally by
        # reverse-running the test pre-fix: it also passed. For a real user-
        # uploaded image (which we can't seed programmatically because the
        # backend's image field only accepts its own CDN paths from the upload
        # endpoint), the pre-fix behavior would wipe the URL — that path must
        # be checked manually on the VM.
        before = await service.get_custom_recipe(recipe_id)
        assert before["image"], "expected the placeholder image URL on create"

        updated = CustomRecipe(
            name="__TEST__ update after",
            ingredients=["200 g water", "5 g salt"],
            steps=[
                "Water and salt into the mixing bowl.",
                "Kochen 3 Min./100°C/Stufe 1.",
            ],
            servings=4,
            prep_time=2,
            total_time=5,
            hints=["fresh hint"],
            tools=["TM7", "TM6"],
        )
        await service.update_custom_recipe(recipe_id, updated)

        after = await service.get_custom_recipe(recipe_id)

        assert after["name"] == "__TEST__ update after"
        assert after["ingredients"] == ["200 g water", "5 g salt"]
        assert after["instructions"] == [
            "Water and salt into the mixing bowl.",
            # normalize_action_step strips the "Kochen " verb prefix and the
            # trailing period, same as it does in create_custom_recipe.
            "3 Min./100°C/Stufe 1",
        ]
        assert after["yield"] == {"value": 4, "unitText": "portion"}
        assert after["prepTime"] == 120
        assert after["totalTime"] == 300
        assert after["tools"] == ["TM7", "TM6"]
        # Image URL must survive the update — the whole point of the partial-
        # PATCH omission for `image` / `isImageOwnedByUser`.
        assert after["image"] == before["image"]
    finally:
        if recipe_id:
            try:
                await service.delete_custom_recipe(recipe_id)
            except Exception as e:
                pytest.fail(
                    f"Teardown failed — recipe {recipe_id} may still exist: {e}"
                )
        await service.close()
