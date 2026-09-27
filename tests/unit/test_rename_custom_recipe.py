"""Unit tests for CookidooService.rename_custom_recipe.

Rename is deliberately a single partial PATCH — no GET, no full-body
reconstruction. See finding
`2026-09-27-custom-recipe-partial-patch.md`: the backend accepts
`{"name": "..."}` alone and leaves every other field on the recipe untouched.
These tests pin that design so a future 'safer' refactor doesn't quietly
reintroduce a full-body write (which would risk annotation drift, since the
schema.org GET drops annotations).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cookidoo_service import CookidooService


def _service_with_fake_client() -> CookidooService:
    svc = CookidooService("e@example.com", "pw")
    fake_localization = SimpleNamespace(
        url="https://cookidoo.be/foo/bar",
        language="nl-BE",
    )
    svc._api_client = SimpleNamespace(localization=fake_localization)  # type: ignore[assignment]
    svc._session = SimpleNamespace()  # type: ignore[assignment]
    return svc


async def test_patches_only_name() -> None:
    captured: dict[str, Any] = {}

    async def fake_authed_request(self, method, url, *, json_body=None, accept="application/json"):
        captured["method"] = method
        captured["url"] = url
        captured["json_body"] = json_body
        return 200, "{}"

    svc = _service_with_fake_client()
    svc._authed_request = fake_authed_request.__get__(svc, CookidooService)  # type: ignore[assignment]

    await svc.rename_custom_recipe("01ABC", "New Name")

    assert captured["method"] == "PATCH"
    assert captured["url"] == "https://cookidoo.be/created-recipes/nl-BE/01ABC"
    # Exactly one key: name. No leaked full payload.
    assert captured["json_body"] == {"name": "New Name"}


async def test_does_not_call_get_first() -> None:
    """Rename does NOT read the recipe before writing. If a future refactor
    reintroduces a pre-GET (e.g. to preserve fields the backend already
    preserves on its own), it would show up as an extra library call here."""
    get_calls: list[str] = []

    class TrackingApi:
        localization = SimpleNamespace(url="https://cookidoo.be/foo/bar", language="nl-BE")

        async def get_custom_recipe(self, recipe_id: str) -> Any:  # pragma: no cover
            get_calls.append(recipe_id)
            raise AssertionError("rename must not GET the recipe")

    async def fake_authed(self, method, url, *, json_body=None, accept="application/json"):
        return 200, "{}"

    svc = CookidooService("e@example.com", "pw")
    svc._api_client = TrackingApi()  # type: ignore[assignment]
    svc._session = SimpleNamespace()  # type: ignore[assignment]
    svc._authed_request = fake_authed.__get__(svc, CookidooService)  # type: ignore[assignment]

    await svc.rename_custom_recipe("01ABC", "New Name")
    assert get_calls == []


async def test_raises_on_non_success_status() -> None:
    async def fake(self, method, url, *, json_body=None, accept="application/json"):
        return 404, '{"error":"not found"}'

    svc = _service_with_fake_client()
    svc._authed_request = fake.__get__(svc, CookidooService)  # type: ignore[assignment]

    with pytest.raises(Exception, match="Failed to rename recipe"):
        await svc.rename_custom_recipe("01ABC", "New Name")


async def test_raises_when_not_authenticated() -> None:
    svc = CookidooService("e@example.com", "pw")
    with pytest.raises(Exception, match="Not authenticated"):
        await svc.rename_custom_recipe("01ABC", "New Name")


async def test_empty_new_name_rejected() -> None:
    """The MCP tool docstring promises 'no quality gate', but an empty string
    is not a valid name and would either be rejected by the backend or silently
    accepted — both bad. Reject it locally so callers get a clear error."""
    svc = _service_with_fake_client()

    with pytest.raises(ValueError, match="new_name must be a non-empty string"):
        await svc.rename_custom_recipe("01ABC", "")
