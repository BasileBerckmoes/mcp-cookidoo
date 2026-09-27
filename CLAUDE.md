# CLAUDE.md — working notes for this fork

This is a fork of [`danielkliem/mcp-cookidoo`](https://github.com/danielkliem/mcp-cookidoo).
Full context lives in [`docs/handover-mcp-cookidoo.md`](docs/handover-mcp-cookidoo.md)
— read that first if you're new to the project.

## Repo layout

- `server.py`, `cookidoo_service.py`, `schemas.py` — the MCP server itself.
- `scripts/apply_local_patches.py` — one-shot; already applied on `local-patches`.
- `tests/unit/` — offline, always run.
- `tests/integration/` — hits the real Cookidoo API; opt-in.
- `test_auth_refresh.py` (repo root) — upstream's `unittest` mocks; pytest picks them up.
- `test_integration.py` (repo root) — upstream's manual smoke script (not a pytest file).
- `requirements.txt` — loose bounds for upstream compatibility.
- `requirements.lock` — pinned; use this in venvs. **`cookidoo-api` is pinned to 0.18.4** because `server.py` calls the private `_is_token_expiring()` method.
- `docs/TASKS.md` — mirror of open GitHub issues, grouped by phase.

## Branches

- `main` — tracks `upstream/main` (danielkliem). Do not commit here directly.
- `local-patches` — the three handover patches + dev tooling (pytest, lockfile). This is the base for all feature work.
- feature branches — one per GitHub issue, branched from `local-patches`.

## Workflow (issue-based)

1. Pick an open issue from `docs/TASKS.md` or `gh issue list`.
2. Branch: `git switch -c feat/<slug> local-patches`.
3. Write failing tests first (unit tests for logic; integration only when the tool actually hits the API).
4. Implement, keep commits small and atomic.
5. `pytest -q` must be green; run integration locally at least once if the change touches API calls (`COOKIDOO_INTEGRATION=1 pytest tests/integration -v`).
6. Open a PR back to `local-patches` on the fork; the PR body closes the issue.
7. Merge, delete branch.

## Testing rules (real Cookidoo account)

The integration suite hits `basile.berckmoes@hotmail.com`'s real Cookidoo account. Handover section 4 lays down three rules — treat them as invariants:

- Every test upload uses the name prefix `__TEST__` and is deleted in the teardown of the same test, even on failure.
- Never modify or delete recipes not created by the test itself.
- Any action outside "create then delete an `__TEST__` recipe" needs a nod from the user before it runs.

Integration tests are gated behind `COOKIDOO_INTEGRATION=1` so no accidental account hits from CI or a stray `pytest`.

## Upstream sync

If we merge new upstream changes into `main`, then rebase `local-patches` onto `main`, the three patched hunks may conflict. Recovery:

1. Resolve non-patch conflicts normally.
2. For the three patch hunks, prefer the upstream version and re-run `scripts/apply_local_patches.py` — its `count == 1` assertions will refuse to double-apply and will fail loudly if the target strings drifted.
3. `pytest -q` — `tests/unit/test_import_and_patches.py` asserts each patch is still present.

## What NOT to do

- Don't unpin `cookidoo-api` in `requirements.lock` without also removing our dependency on `_is_token_expiring()`.
- Don't commit `.env`.
- Don't upload recipes without the `__TEST__` prefix from a test.
- Don't `git commit --amend` on `local-patches` or `main` — they're shared with the production VM at `~/agent/tools/mcp-cookidoo`.
