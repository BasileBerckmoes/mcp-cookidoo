"""Shared pytest configuration.

Anything under `tests/integration/` is auto-marked as `integration` and
skipped unless `COOKIDOO_INTEGRATION=1` is set in the environment. This
keeps the default `pytest` run fully offline; integration tests are opt-in.

Any test that would mutate the real Cookidoo account MUST use the
`__TEST__` name prefix and clean up after itself. See
docs/handover-mcp-cookidoo.md section 4 "Rules for testing".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


INTEGRATION_DIR = Path(__file__).parent / "integration"

# Load .env once so tests that gate on COOKIDOO_EMAIL/PASSWORD see them without
# having to export the vars separately. No-op when .env is absent (CI).
load_dotenv(INTEGRATION_DIR.parent.parent / ".env")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    integration_enabled = os.getenv("COOKIDOO_INTEGRATION") == "1"
    skip_marker = pytest.mark.skip(reason="set COOKIDOO_INTEGRATION=1 to run integration tests")
    for item in items:
        if INTEGRATION_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)
            if not integration_enabled:
                item.add_marker(skip_marker)
