# Handover: mcp-cookidoo fork

Handover for a Claude Code session on my Windows PC. Goal: fork
`danielkliem/mcp-cookidoo`, re-apply the three local patches that run on my
homelab VM, then extend the server (edit tools, Dutch/English step text,
English validator). When it works, deploy back to the VM.

---

## 1. Current state

| Item | Value |
| --- | --- |
| Upstream | https://github.com/danielkliem/mcp-cookidoo |
| Base commit | `b097330` |
| `cookidoo-api` version in use | `0.18.4` (upstream `requirements.txt` only says `>=0.8.0`) |
| Python | 3.12 |
| Account locale | country `be`, language `nl-BE` (`https://cookidoo.be/foundation/nl-BE`) |
| Other `be` locales | `en`, `fr-BE`, `de-BE` |
| Production host | Ubuntu VM `claude-vm`, user `james` |
| Production path | `/home/james/agent/tools/mcp-cookidoo` (branch `local-patches`) |
| MCP client | Claude Desktop (Linux beta), stdio transport |

Production Claude Desktop config (`~/.config/Claude/claude_desktop_config.json`, `mcpServers` block):

```json
"cookidoo": {
  "command": "/home/james/agent/tools/mcp-cookidoo/venv/bin/python",
  "args": ["/home/james/agent/tools/mcp-cookidoo/server.py"],
  "env": { "COOKIDOO_MCP_MODE": "stdio" }
}
```

`.env` keys (never commit this file; check `.gitignore`):

```
COOKIDOO_EMAIL=...
COOKIDOO_PASSWORD=...
COOKIDOO_COUNTRY=be
COOKIDOO_LANGUAGE=nl-BE
```

---

## 2. The three local patches

Apply them on a fresh clone of the fork with the script in 2.4. Each patch is
a plain string replacement; the script stops if a target is missing or not
unique.

### 2.1 Locale via environment variables (`cookidoo_service.py`)

Upstream hardcodes Switzerland, so recipes would land in the Swiss Cookidoo.

```diff
-                    await get_localization_options(country="ch", language="de-CH")
+                    await get_localization_options(country=os.getenv("COOKIDOO_COUNTRY", "ch"), language=os.getenv("COOKIDOO_LANGUAGE", "de-CH"))
```

### 2.2 Token expiry check (`server.py`)

`cookidoo-api` 0.18.x removed `Cookidoo.expires_in`, so every tool call after
the first failed with `'Cookidoo' object has no attribute 'expires_in'`. The
library now tracks `_expires_at` itself, exposes `_is_token_expiring()`, and
refreshes automatically before API calls (around line 761 of
`cookidoo_api/cookidoo.py`).

```diff
-        if _cookidoo_api.expires_in > TOKEN_REFRESH_BUFFER_SECONDS:
+        if not _cookidoo_api._is_token_expiring():
```

`_is_token_expiring` is private; that is why dependencies are pinned in
`requirements.lock`.

### 2.3 Keep the leading time in pure action steps (`server.py`, `generate_recipe_structure`)

`lstrip("0123456789.)-• \t")` removed list numbering, but also the time of a
pure action step: `"20 Sek./Stufe 5"` became `"Sek./Stufe 5"`.

```diff
-            step.strip().lstrip("0123456789.)-• \t")
+            re.sub(r"^\s*(?:\d+[.)]|[-•])\s+", "", step).strip()
```

### 2.4 Apply script

Run from the repository root with the venv Python:

```python
# apply_local_patches.py
from pathlib import Path

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

for filename, old, new in PATCHES:
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    assert count == 1, f"{filename}: expected 1 match, found {count}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"patched {filename}")
```

Then:

```powershell
.\venv\Scripts\python.exe apply_local_patches.py
.\venv\Scripts\python.exe -c "import server; print('import ok')"
git commit -am "Local patches: locale env vars, token expiry for cookidoo-api 0.18, keep leading time in action steps"
```

---

## 3. How the server works today (facts from the code)

### Tools

| Tool | Notes |
| --- | --- |
| `connect_to_cookidoo` | explicit login; other tools auto-connect |
| `get_recipe_details(recipe_id)` | official recipes only (`r59322`); fails on custom IDs; does not return step text in practice |
| `generate_recipe_structure(...)` | ingredients split on newlines, or on commas if there are no newlines |
| `validate_recipe_quality(recipe_json)` | score 0-100, bar `COOKIDOO_QUALITY_BAR` (default 70), messages in German |
| `upload_custom_recipe(recipe_json, force_upload)` | quality-gated; POST then PATCH; deletes the empty recipe if the PATCH fails |
| `list_my_custom_recipes` | GET list |
| `delete_custom_recipe(recipe_id)` | via library `remove_custom_recipe` |
| prompt `create_tm7_recipe(dish)` | German, pushes Varoma use |

### Endpoints (undocumented)

`base` = scheme + host of `localization.url` (`https://cookidoo.be`), `locale` = `localization.language` (`nl-BE`).

| Action | Request |
| --- | --- |
| Create empty | `POST {base}/created-recipes/{locale}` body `{"recipeName": name}` → `{"recipeId": ...}` |
| Fill / update | `PATCH {base}/created-recipes/{locale}/{id}` with the full payload below |
| List | `GET {base}/created-recipes/{locale}` → `items[].recipeId`, `items[].recipeContent` |
| Edit page | `{base}/created-recipes/{locale}/{id}/edit` |

Auth header: `Authorization: Bearer {auth_data.access_token}`; `_authed_request`
in `cookidoo_service.py` refreshes once on 401.

PATCH payload as built by `create_custom_recipe`:

```json
{
  "name": "...",
  "image": null,
  "isImageOwnedByUser": false,
  "tools": ["TM7", "TM6", "TM5"],
  "yield": {"value": 4, "unitText": "portion"},
  "prepTime": 1800,
  "cookTime": 0,
  "totalTime": 3600,
  "ingredients": [{"type": "INGREDIENT", "text": "200 g ui"}],
  "instructions": [{"type": "STEP", "text": "...", "annotations": []}],
  "hints": "line 1\nline 2",
  "workStatus": "PRIVATE",
  "recipeMetadata": {"requiresAnnotationsCheck": false}
}
```

The create code sleeps 5 s between POST and PATCH; an immediate PATCH is unreliable.

### Annotations built from step text

```json
{"type": "TTS", "data": {"speed": "5", "time": 20, "temperature": {"value": "100", "unit": "C"}},
 "position": {"offset": 0, "length": 22}}
{"type": "MODE", "name": "STEAMING", "data": {"time": 900, "speed": "2", "direction": "CW", "accessory": "Varoma"},
 "position": {"offset": 0, "length": 21}}
{"type": "MODE", "name": "BROWNING", "data": {"time": 420, "temperature": {"value": "160", "unit": "C"}, "power": "Intense"},
 "position": {"offset": 0, "length": 20}}
{"type": "INGREDIENT", "data": {"description": "200 g ui"}, "position": {"offset": 10, "length": 8}}
```

`time` is in seconds. Ingredient matching is exact substring, longest first,
no overlaps.

### Step grammar the parser accepts (German only)

| Kind | Pattern | Constraints |
| --- | --- | --- |
| Standard | `X Sek./Stufe Y`, `X Min./T°C/[Linkslauf/]Stufe Y` | T in 37, 40, 45, ..., 95, 98, 100, 105, 110, 115, 120 |
| Varoma | `X Min./Varoma/Stufe Y` | becomes `MODE/STEAMING` |
| Browning | `X Min./T°C/Intensiv` or `/Leicht` | T in 140, 145, 150, 155, 160; max 30 min |
| Not supported | fermenting, rice cooker, turbo, dough kneading | write as prose |

A step gets a play button on the TM7 only if it is a pure action: no prose
around the action span. `normalize_action_step` strips a single verb prefix.

### Quality score

TTS/MODE steps 50 (vs about half the steps expected), linked ingredients 20,
accessory words 10 (German plus butterfly/simmering basket/mixing bowl),
parallel work 20 (10 for "gleichzeitig/meanwhile/while ... cook" phrases,
10 for any Varoma step). I avoid the Varoma, so the Varoma bonus pushes the
agent in the wrong direction.

---

## 4. Work to do in the fork

Separate commit per item, pytest tests for parser changes, existing tools stay
backward compatible.

1. **`get_custom_recipe(recipe_id)`**: return a custom recipe in full (name,
   ingredients, step text, hints, yield, times, tools). Use the library's
   `get_custom_recipe()` or `GET {base}/created-recipes/{locale}/{id}`.
2. **`update_custom_recipe(recipe_id, recipe_json)`**: PATCH in place, same
   ID. Move the payload builder out of `create_custom_recipe` so create and
   update share it. Add `rename_custom_recipe(recipe_id, new_name)` as get +
   patch that only changes the name, keeping everything else (image,
   annotations).
3. **Dutch and English step text**: recognise Dutch and English action text
   next to German, so a recipe can be fully Dutch or English. Build the
   annotations from the parsed values, independent of the text language.
   Reference steps from official nl-BE recipes (copied from cookidoo.be):

   ```
   <PASTE REAL nl-BE ACTION STEPS HERE: plain, with temperature,
   reverse direction, Varoma, browning>
   ```

   Open question to settle with one `__TEST__` upload: does the TM7 show a
   play button when the annotated span is in Dutch?
4. **Validator**: English messages; parallel-work bonus configurable, default
   without the Varoma bonus, recognising "meanwhile" and "ondertussen".
5. **Housekeeping**: pin dependencies in `requirements.lock`, document new
   tools and env vars in the README.
6. **Later**: `search_recipes` using the search backend from my previous
   agent (Alf), then an upstream PR for the locale env vars and search.

### Rules for testing against my real account

- Every test upload uses the name prefix `__TEST__` and is deleted afterwards.
- Never modify or delete my other recipes.
- Ask me before any other action on the account.

---

## 5. Local test setup (Windows)

```powershell
git clone https://github.com/<me>/mcp-cookidoo.git
cd mcp-cookidoo
py -3.12 -m venv venv
.\venv\Scripts\pip install -r requirements.txt
# create .env (section 1), then apply the patches (section 2.4)
```

Use Claude Code itself as the MCP client:

```powershell
claude mcp add cookidoo-dev --env COOKIDOO_MCP_MODE=stdio -- .\venv\Scripts\python.exe server.py
```

---

## 6. Deploy back to the VM

```bash
cd ~/agent/tools/mcp-cookidoo
git remote add mine https://github.com/<me>/mcp-cookidoo.git
git fetch mine
git switch -c fork mine/main        # or the branch you worked on
./venv/bin/pip install -r requirements.lock
./venv/bin/python -c "import server; print('import ok')"
pkill -f -i claude-desktop          # then restart the app (VNC) or reboot
```

Then update the Cookidoo project instructions:

- Remove "You cannot read my custom recipes yet" and the manual-rename rule.
- Allow `update_custom_recipe` and `rename_custom_recipe` (in place, after my approval).
- Replace the German step grammar with the new Dutch/English grammar.
- Remove the note that validator messages are German.
