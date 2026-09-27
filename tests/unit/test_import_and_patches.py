"""Smoke tests: the modules import and the three local patches are in place.

If any of these fail after an `upstream` merge, re-apply the patches from
docs/handover-mcp-cookidoo.md section 2 (or run scripts/apply_local_patches.py
on a fresh checkout).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_server_imports() -> None:
    import server  # noqa: F401


def test_cookidoo_service_imports() -> None:
    import cookidoo_service  # noqa: F401


def test_patch_1_locale_env_vars_applied() -> None:
    text = (REPO_ROOT / "cookidoo_service.py").read_text(encoding="utf-8")
    assert 'os.getenv("COOKIDOO_COUNTRY"' in text, "patch 1 missing: locale env vars"
    assert 'country="ch", language="de-CH"' not in text, "patch 1 reverted: hardcoded ch/de-CH is back"


def test_patch_2_is_token_expiring_applied() -> None:
    text = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert "_is_token_expiring()" in text, "patch 2 missing: _is_token_expiring check"
    assert "expires_in > TOKEN_REFRESH_BUFFER_SECONDS" not in text, (
        "patch 2 reverted: expires_in check is back (removed in cookidoo-api 0.18)"
    )


def test_patch_3_step_numbering_regex_applied() -> None:
    text = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert r're.sub(r"^\s*(?:\d+[.)]|[-•])\s+", "", step)' in text, "patch 3 missing: regex strip"
    assert r'lstrip("0123456789.)-• \t")' not in text, "patch 3 reverted: old lstrip is back"
