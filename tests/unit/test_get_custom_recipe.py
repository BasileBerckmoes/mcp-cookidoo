"""Unit tests for CookidooService.get_custom_recipe.

Monkey-patches the underlying ``cookidoo_api.Cookidoo.get_custom_recipe`` to
return a fake ``CookidooCustomRecipe`` so we can pin the returned dict shape
without hitting the network. Integration tests in ``tests/integration/`` do
the actual round-trip.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cookidoo_api.exceptions import CookidooAuthException

from cookidoo_service import CookidooService


def _fake_recipe(**overrides: Any) -> SimpleNamespace:
    """Minimal stand-in for ``cookidoo_api.types.CookidooCustomRecipe``.

    The real dataclass exposes: id, name, ingredients, instructions, tools,
    serving_size, active_time, total_time, thumbnail, image, url. We use a
    SimpleNamespace so we don't couple the test to the library's dataclass
    definition (which could grow fields).
    """
    defaults = dict(
        id="01ABC",
        name="my recipe",
        ingredients=["100 g water", "5 g salt"],
        instructions=["Water and salt into the mixing bowl.", "3 Min./100°C/Stufe 1"],
        tools=["TM7"],
        serving_size=2,
        active_time=60,
        total_time=180,
        thumbnail="https://example/thumb.svg",
        image="https://example/img.svg",
        url="https://cookidoo.be/created-recipes/nl-BE/01ABC",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _service_with_fake_api(fake_api: object) -> CookidooService:
    svc = CookidooService("e@example.com", "pw")
    svc._api_client = fake_api  # type: ignore[assignment]
    return svc


async def test_returns_full_dict_shape() -> None:
    fake = _fake_recipe()

    class FakeApi:
        async def get_custom_recipe(self, recipe_id: str) -> Any:
            assert recipe_id == "01ABC"
            return fake

    result = await _service_with_fake_api(FakeApi()).get_custom_recipe("01ABC")

    assert result == {
        "id": "01ABC",
        "name": "my recipe",
        "ingredients": ["100 g water", "5 g salt"],
        "instructions": ["Water and salt into the mixing bowl.", "3 Min./100°C/Stufe 1"],
        "yield": {"value": 2, "unitText": "portion"},
        "prepTime": 60,
        "cookTime": None,
        "totalTime": 180,
        "tools": ["TM7"],
        "hints": None,
        "image": "https://example/img.svg",
        "url": "https://cookidoo.be/created-recipes/nl-BE/01ABC",
    }


async def test_hints_and_cook_time_are_none() -> None:
    """Guard: even if the library grows a `hints`/`cook_time` field later, our
    service explicitly returns them as None until we probe an endpoint that
    exposes them. See finding 2026-09-27-custom-recipe-read-schema.md."""
    fake = _fake_recipe()

    class FakeApi:
        async def get_custom_recipe(self, recipe_id: str) -> Any:
            return fake

    result = await _service_with_fake_api(FakeApi()).get_custom_recipe("01ABC")
    assert result["hints"] is None
    assert result["cookTime"] is None


async def test_retries_once_on_auth_exception() -> None:
    """Mirrors delete_custom_recipe's refresh-once-on-401 idiom."""
    calls: list[str] = []
    fake = _fake_recipe()

    class FakeApi:
        async def get_custom_recipe(self, recipe_id: str) -> Any:
            calls.append("get")
            if calls.count("get") == 1:
                raise CookidooAuthException("token expired")
            return fake

        async def refresh_token(self) -> None:
            calls.append("refresh")

    result = await _service_with_fake_api(FakeApi()).get_custom_recipe("01ABC")
    assert calls == ["get", "refresh", "get"]
    assert result["id"] == "01ABC"


async def test_raises_when_not_authenticated() -> None:
    svc = CookidooService("e@example.com", "pw")
    with pytest.raises(Exception, match="Not authenticated"):
        await svc.get_custom_recipe("01ABC")
