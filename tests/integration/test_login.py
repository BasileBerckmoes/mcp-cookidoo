"""Integration smoke test: log in to Cookidoo with the configured creds.

Auto-marked `integration` by tests/conftest.py; run with:
    COOKIDOO_INTEGRATION=1 .venv/bin/pytest tests/integration -v

This test does NOT create, modify, or delete any recipes. It only exercises
the login path and confirms the api_client is populated. Any test that
touches recipes MUST use the `__TEST__` name prefix and clean up after
itself (see docs/handover-mcp-cookidoo.md section 4).
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_login_populates_api_client() -> None:
    if not os.getenv("COOKIDOO_EMAIL") or not os.getenv("COOKIDOO_PASSWORD"):
        pytest.skip("COOKIDOO_EMAIL / COOKIDOO_PASSWORD not set")

    from cookidoo_service import CookidooService, load_cookidoo_credentials

    email, password = load_cookidoo_credentials()
    service = CookidooService(email, password)
    try:
        await service.login()
        assert service.api_client is not None, "login() returned but api_client is None"
    finally:
        await service.close()
