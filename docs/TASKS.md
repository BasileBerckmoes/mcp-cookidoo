# Tasks

Human-readable mirror of the GitHub Issues on this fork. **The Issues are the source of truth** — this file is a quick index. When an issue is opened/closed/relabeled, update the row here.

Live list: `gh issue list --repo BasileBerckmoes/mcp-cookidoo`

## Phase 0 — done (on `local-patches`)

| # | Title | Status | Notes |
| --- | --- | --- | --- |
| [#1](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/1) | Track: three local patches applied on `local-patches` branch | open, tracking | Patches themselves are landed; issue stays open until they're upstreamed. |

## Phase 1 — features

Order below is a suggested build order; nothing is strictly blocked on anything else, but earlier items make later ones easier.

| # | Title | Status |
| --- | --- | --- |
| [#2](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/2) | `get_custom_recipe(recipe_id)` — return a custom recipe in full | open |
| [#3](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/3) | `update_custom_recipe` + `rename_custom_recipe` + shared payload builder | open |
| [#4](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/4) | Recognize Dutch and English action-step text | open |
| [#5](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/5) | English validator messages, configurable parallel-work bonus | open |

## Phase 2 — housekeeping

| # | Title | Status |
| --- | --- | --- |
| [#6](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/6) | Update README for new tools, env vars, and `requirements.lock` | open |

## Phase 3 — later

| # | Title | Status |
| --- | --- | --- |
| [#7](https://github.com/BasileBerckmoes/mcp-cookidoo/issues/7) | `search_recipes` tool, then upstream PR for locale env vars + search | open |

## How this stays in sync

- One PR closes one issue. Include `Closes #N` in the PR body.
- On close, tick the row here in the same PR that closes the issue.
- New issues get a new row in the same phase they belong to.
