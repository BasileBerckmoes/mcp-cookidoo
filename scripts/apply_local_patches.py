"""One-shot: apply the three local patches from docs/handover-mcp-cookidoo.md.

Run once on a fresh clone (before installing deps is fine — this uses stdlib
only). The script asserts each target string appears exactly once, so running
it a second time on an already-patched tree fails loudly.

Patches (see docs/handover-mcp-cookidoo.md section 2):
    1. locale via env vars      cookidoo_service.py
    2. token expiry check       server.py    (cookidoo-api >= 0.18)
    3. keep leading time        server.py    (numbering strip regex)
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PATCHES = [
    (
        "cookidoo_service.py",
        'await get_localization_options(country="ch", language="de-CH")',
        'await get_localization_options(country=os.getenv("COOKIDOO_COUNTRY", "ch"), language=os.getenv("COOKIDOO_LANGUAGE", "de-CH"))',
    ),
    (
        "server.py",
        "if _cookidoo_api.expires_in > TOKEN_REFRESH_BUFFER_SECONDS:",
        "if not _cookidoo_api._is_token_expiring():",
    ),
    (
        "server.py",
        r'step.strip().lstrip("0123456789.)-• \t")',
        r're.sub(r"^\s*(?:\d+[.)]|[-•])\s+", "", step).strip()',
    ),
]


def main() -> None:
    for filename, old, new in PATCHES:
        path = REPO_ROOT / filename
        text = path.read_text(encoding="utf-8")
        count = text.count(old)
        assert count == 1, f"{filename}: expected 1 match, found {count}: {old!r}"
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"patched {filename}")


if __name__ == "__main__":
    main()
