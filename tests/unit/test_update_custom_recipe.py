"""Unit tests for CookidooService.update_custom_recipe.

Monkey-patches `_authed_request` so we can assert (a) it PATCHes the
`/created-recipes/{locale}/{id}` URL, (b) the body is exactly what
`_build_recipe_payload` produces for the same input, and (c) 4xx responses
raise. Integration coverage lives in tests/integration/.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cookidoo_service import CookidooService, _build_recipe_payload
from schemas import CustomRecipe


def _service_with_fake_client() -> CookidooService:
    """A CookidooService with a client that has just enough localization/session
    surface for update_custom_recipe to build the URL. The `_authed_request`
    method itself is monkey-patched per-test."""
    svc = CookidooService("e@example.com", "pw")
    fake_localization = SimpleNamespace(
        url="https://cookidoo.be/foo/bar",
        language="nl-BE",
    )
    svc._api_client = SimpleNamespace(localization=fake_localization)  # type: ignore[assignment]
    svc._session = SimpleNamespace()  # type: ignore[assignment]
    return svc


def _sample_recipe() -> CustomRecipe:
    return CustomRecipe(
        name="Updated Recipe",
        ingredients=["100 g water", "5 g salt"],
        steps=[
            "Water and salt into the mixing bowl.",
            "Kochen 3 Min./100°C/Stufe 1.",
        ],
        servings=2,
        prep_time=1,
        total_time=3,
        hints=["one hint"],
        tools=["TM7"],
    )


async def test_patches_correct_url_and_body() -> None:
    captured: dict[str, Any] = {}

    async def fake_authed_request(self, method, url, *, json_body=None, accept="application/json"):
        captured["method"] = method
        captured["url"] = url
        captured["json_body"] = json_body
        return 200, "{}"

    svc = _service_with_fake_client()
    svc._authed_request = fake_authed_request.__get__(svc, CookidooService)  # type: ignore[assignment]

    recipe = _sample_recipe()
    await svc.update_custom_recipe("01ABC", recipe)

    assert captured["method"] == "PATCH"
    assert captured["url"] == "https://cookidoo.be/created-recipes/nl-BE/01ABC"
    # Body equals what _build_recipe_payload produces for the same inputs,
    # with the CustomRecipe's minutes converted to seconds by the service.
    expected = _build_recipe_payload(
        name=recipe.name,
        ingredients=recipe.ingredients,
        steps=recipe.steps,
        servings=recipe.servings,
        prep_time_seconds=recipe.prep_time * 60,
        total_time_seconds=recipe.total_time * 60,
        hints=recipe.hints,
        tools=recipe.tools,
    )
    assert captured["json_body"] == expected


async def test_accepts_200_and_204() -> None:
    for ok_status in (200, 204):

        async def fake(self, method, url, *, json_body=None, accept="application/json", _s=ok_status):
            return _s, ""

        svc = _service_with_fake_client()
        svc._authed_request = fake.__get__(svc, CookidooService)  # type: ignore[assignment]

        # No exception on 200 / 204
        await svc.update_custom_recipe("01ABC", _sample_recipe())


async def test_raises_on_non_success_status() -> None:
    async def fake(self, method, url, *, json_body=None, accept="application/json"):
        return 400, '{"error":"bad"}'

    svc = _service_with_fake_client()
    svc._authed_request = fake.__get__(svc, CookidooService)  # type: ignore[assignment]

    with pytest.raises(Exception, match="Failed to update recipe"):
        await svc.update_custom_recipe("01ABC", _sample_recipe())


async def test_raises_when_not_authenticated() -> None:
    svc = CookidooService("e@example.com", "pw")
    with pytest.raises(Exception, match="Not authenticated"):
        await svc.update_custom_recipe("01ABC", _sample_recipe())


async def test_does_not_sleep() -> None:
    """Regression guard: create's 5-second sleep is a POST-then-PATCH race
    workaround and must NOT be inherited by update, which PATCHes an already-
    materialized recipe. If someone adds a sleep to update, this test hangs on
    a slow test runner but the intent is documented."""
    import asyncio
    import time

    async def fake(self, method, url, *, json_body=None, accept="application/json"):
        return 200, "{}"

    svc = _service_with_fake_client()
    svc._authed_request = fake.__get__(svc, CookidooService)  # type: ignore[assignment]

    start = time.monotonic()
    await asyncio.wait_for(
        svc.update_custom_recipe("01ABC", _sample_recipe()),
        timeout=2.0,
    )
    elapsed = time.monotonic() - start
    # Way below the 5s create-path sleep; any regression that reintroduces it
    # will blow this out of the water.
    assert elapsed < 1.0, f"update_custom_recipe took {elapsed:.2f}s"
