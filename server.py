"""
Cookidoo MCP Server

MCP tools for reading Cookidoo recipes and creating TM7-optimized custom recipes
with automatic quality validation. Runs as stateless HTTP server (token auth) or
over stdio for local Claude Desktop use.
"""

import json
import os
import re
from typing import Optional

from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from cookidoo_service import (
    CookidooService,
    build_step_annotations,
    load_cookidoo_credentials,
)
from schemas import CustomRecipe

VERSION = "0.2.0"
API_TOKEN = os.environ.get("COOKIDOO_API_TOKEN", "")
PORT = int(os.environ.get("COOKIDOO_MCP_PORT", "8001"))
QUALITY_BAR = int(os.environ.get("COOKIDOO_QUALITY_BAR", "70"))
TOKEN_REFRESH_BUFFER_SECONDS = 60

mcp = FastMCP("cookidoo-mcp-server")

_cookidoo_service: Optional[CookidooService] = None
_cookidoo_api = None


async def _ensure_connected() -> tuple[Optional[str], Optional[CookidooService]]:
    """Lazy-login with proactive token refresh.

    The server process is long-lived; Cookidoo access tokens are not. Without
    refresh, every authenticated call after the first hour returns 401. This
    function uses the OAuth refresh-grant via ``Cookidoo.refresh_token()`` so
    the email+password login only runs once per process (or after the refresh
    token itself dies).

    Returns ``(error_message, service)``. ``error_message`` is None on success.
    """
    global _cookidoo_service, _cookidoo_api
    if _cookidoo_service and _cookidoo_api:
        if not _cookidoo_api._is_token_expiring():
            return None, _cookidoo_service
        try:
            await _cookidoo_api.refresh_token()
            return None, _cookidoo_service
        except Exception:
            try:
                await _cookidoo_service.close()
            except Exception:
                pass
            _cookidoo_service = None
            _cookidoo_api = None
    try:
        email, password = load_cookidoo_credentials()
        _cookidoo_service = CookidooService(email, password)
        _cookidoo_api = await _cookidoo_service.login()
        return None, _cookidoo_service
    except ValueError as e:
        return f"Configuration error: {e}. Ensure COOKIDOO_EMAIL and COOKIDOO_PASSWORD are set.", None
    except Exception as e:
        return f"Login failed: {e}", None


# -----------------------------------------------------------------------------
# Recipe quality validation (TM7-aware)
# -----------------------------------------------------------------------------

_ACCESSORY_RE = re.compile(
    r"(schmetterling|spatel|varoma|gareinsatz|messeinsatz|mixtopf|butterfly|simmering basket|mixing bowl)",
    re.IGNORECASE,
)
_PARALLEL_RE = re.compile(
    r"(gleichzeitig|parallel|während.*kocht|varoma.*aufsatz|varoma.*einsatz|während.*gart|meanwhile|while.*cook)",
    re.IGNORECASE,
)


def _score_recipe(recipe: CustomRecipe) -> dict:
    """
    Score a recipe for TM7 quality.

    Core criterion: how many action steps are parseable as TTS annotations, i.e.
    will render as tappable action chips with a play button on the TM7 device.
    Plus bonuses for ingredient annotation coverage, accessories, and TM7
    parallelization (Varoma / Gareinsatz).
    """
    steps = recipe.steps
    step_texts = [s.text for s in steps]
    ingredients = recipe.ingredients
    n = len(steps)
    if n == 0:
        return {
            "score": 0, "meets_bar": False,
            "issues": ["Recipe has no steps."],
            "suggestions": [],
            "breakdown": {},
        }

    issues: list[str] = []
    suggestions: list[str] = []

    # Count action hits: a structured action counts directly; otherwise fall
    # through to the German-text parser (backwards-compatible).
    tts_step_count = 0
    ingredient_hit_steps = 0
    for step in steps:
        if step.action is not None:
            tts_step_count += 1
            if any(ing and ing in step.text for ing in ingredients):
                ingredient_hit_steps += 1
        else:
            anns = build_step_annotations(step.text, ingredients)
            if any(a["type"] in ("TTS", "MODE") for a in anns):
                tts_step_count += 1
            if any(a["type"] == "INGREDIENT" for a in anns):
                ingredient_hit_steps += 1

    # Estimate how many action-steps the recipe SHOULD have: roughly half the
    # steps should be actions (alternating prose/action pattern).
    expected_actions = max(1, n // 2)

    has_accessory = any(_ACCESSORY_RE.search(s) for s in step_texts)
    has_parallel = any(_PARALLEL_RE.search(s) for s in step_texts)
    has_varoma = any(re.search(r"varoma", s, re.IGNORECASE) for s in step_texts) or any(
        s.action is not None and s.action.kind == "steaming" for s in steps
    )
    has_butterfly = any(
        re.search(r"schmetterling|butterfly", s, re.IGNORECASE) for s in step_texts
    )

    # Points 1: TTS-parseable action steps (50 pts — the big one)
    tts_ratio = min(1.0, tts_step_count / expected_actions)
    tts_points = round(tts_ratio * 50)
    if tts_step_count == 0:
        issues.append(
            "Kein Schritt hat eine parseable TTS-Aktion. Formuliere Maschinen-Aktionen "
            "in einem eigenen Schritt, z.B. 'Zerkleinern 5 Sek./Stufe 5.' oder "
            "'Kochen 18 Min./100°C/Linkslauf/Stufe 1.' — nur diese werden mit "
            "Play-Button am TM7 als guided step angezeigt."
        )
    elif tts_ratio < 0.5:
        issues.append(
            f"Nur {tts_step_count} von ~{expected_actions} erwarteten Aktionsschritten "
            "sind TTS-parseable. Weitere Aktionen im Format 'Zeit/[Temp°C/][Linkslauf/]Stufe X' "
            "in eigenen Schritten ergänzen."
        )

    # Points 2: ingredient references resolved via annotations (20 pts)
    ing_points = 20 if ingredient_hit_steps >= 1 else 0
    if ingredient_hit_steps == 0 and ingredients:
        issues.append(
            "Keine Zutat wird im Schritt-Text exakt referenziert. Verwende die "
            "Zutaten-Einträge 1:1 im Text (z.B. '200 g Langkornreis in den Mixtopf geben.')"
            " damit sie als INGREDIENT-Annotation verlinkt werden."
        )

    # Points 3: accessories mentioned (10 pts)
    accessory_points = 10 if has_accessory else 0
    if not has_accessory:
        suggestions.append(
            "Erwäge Zubehör zu nennen: Schmetterling, Spatel, Varoma-Aufsatz, Gareinsatz."
        )

    # Points 4: TM7 parallelization (20 pts — Varoma/Gareinsatz)
    parallel_points = 0
    if has_parallel:
        parallel_points += 10
    if has_varoma:
        parallel_points += 10
    parallel_points = min(20, parallel_points)
    if parallel_points < 20:
        suggestions.append(
            "TM7-Parallelisierung: prüfe ob Varoma-Aufsatz (Dämpfen oben) oder "
            "Gareinsatz (Pasta/Reis im Mixtopf) parallel zum Haupt-Kochen genutzt werden "
            "kann — spart Zeit und ist TM7 best practice."
        )

    score = tts_points + ing_points + accessory_points + parallel_points

    text_all = " ".join(step_texts).lower()
    if ("sahne" in text_all or "eiweiß" in text_all or "eischnee" in text_all or "cream" in text_all) and not has_butterfly:
        suggestions.append("Bei Sahne/Eischnee: Schmetterling einsetzen.")
    if ("teig" in text_all or "dough" in text_all) and not re.search(
        r"teigknetstufe|kneading", text_all, re.IGNORECASE
    ):
        suggestions.append("Bei Teig: Teigknetstufe (🌾) verwenden.")

    meets_bar = score >= QUALITY_BAR

    return {
        "score": score,
        "meets_bar": meets_bar,
        "issues": issues,
        "suggestions": suggestions,
        "breakdown": {
            "tts_points": tts_points,
            "ingredient_points": ing_points,
            "accessory_points": accessory_points,
            "parallel_points": parallel_points,
            "tts_step_count": tts_step_count,
            "ingredient_hit_steps": ingredient_hit_steps,
            "expected_actions": expected_actions,
            "quality_bar": QUALITY_BAR,
        },
    }


# -----------------------------------------------------------------------------
# Tools
# -----------------------------------------------------------------------------


@mcp.tool()
async def connect_to_cookidoo() -> str:
    """
    Authenticate with Cookidoo. Usually not needed — other tools auto-connect.
    Only call this explicitly if you want to verify credentials up front.
    """
    error, _ = await _ensure_connected()
    if error:
        return error
    email, _ = load_cookidoo_credentials()
    return f"Connected to Cookidoo as {email}"


@mcp.tool()
async def get_recipe_details(recipe_id: str) -> str:
    """
    Fetch a Cookidoo recipe by ID (e.g. 'r59322', 'r907015').

    Use this to study existing recipes for structure, times, temperatures, and
    speed settings before drafting your own. Auto-connects if needed.
    """
    error, _ = await _ensure_connected()
    if error:
        return error
    try:
        recipe = await _cookidoo_api.get_recipe_details(recipe_id)
    except Exception as e:
        return f"Failed to get recipe details: {e}"

    result = f"Recipe Details:\n\nName: {getattr(recipe, 'name', '?')}\nID: {getattr(recipe, 'id', recipe_id)}\n\n"
    if hasattr(recipe, "serving_size"):
        result += f"Servings: {recipe.serving_size}\n"
    if hasattr(recipe, "total_time"):
        result += f"Total Time: {recipe.total_time} minutes\n"
    if hasattr(recipe, "difficulty"):
        result += f"Difficulty: {recipe.difficulty}\n"
    result += "\n"

    if getattr(recipe, "ingredients", None):
        result += "Ingredients:\n"
        for ing in recipe.ingredients:
            name = getattr(ing, "name", str(ing))
            qty = getattr(ing, "quantity", None)
            result += f"  • {name}" + (f" — {qty}" if qty else "") + "\n"
        result += "\n"

    if getattr(recipe, "steps", None):
        result += "Steps:\n"
        for i, step in enumerate(recipe.steps, 1):
            desc = getattr(step, "description", str(step))
            result += f"{i}. {desc}\n"
        result += "\n"

    if getattr(recipe, "url", None):
        result += f"URL: {recipe.url}\n"
    return result


@mcp.tool()
async def get_custom_recipe(recipe_id: str) -> str:
    """
    Fetch one of the user's own custom recipes by ID.

    Complements get_recipe_details (which only works for official Cookidoo
    content) by reading recipes created via upload_custom_recipe.

    Note: the backend's read endpoint returns a schema.org projection and
    does not expose `hints`, `cookTime`, or step annotations, even though
    they are accepted on write. Those show as N/A here.
    """
    error, service = await _ensure_connected()
    if error:
        return error
    try:
        recipe = await service.get_custom_recipe(recipe_id)
    except Exception as e:
        return f"Failed to get custom recipe: {e}"

    y = recipe.get("yield") or {}
    lines = [
        "Custom Recipe:",
        "",
        f"Name: {recipe.get('name', '?')}",
        f"ID: {recipe.get('id', recipe_id)}",
        f"Servings: {y.get('value', '?')} {y.get('unitText', '')}".rstrip(),
        f"Prep Time: {recipe.get('prepTime', 0) // 60} min",
        f"Total Time: {recipe.get('totalTime', 0) // 60} min",
        f"Tools: {', '.join(recipe.get('tools') or []) or '?'}",
        "",
    ]
    if recipe.get("ingredients"):
        lines.append("Ingredients:")
        for ing in recipe["ingredients"]:
            lines.append(f"  • {ing}")
        lines.append("")
    if recipe.get("instructions"):
        lines.append("Steps:")
        for i, step in enumerate(recipe["instructions"], 1):
            lines.append(f"{i}. {step}")
        lines.append("")
    lines.append("Hints: N/A (not exposed by read endpoint)")
    lines.append("Cook Time: N/A (not exposed by read endpoint)")
    if recipe.get("url"):
        lines.append(f"URL: {recipe['url']}")
    return "\n".join(lines)


@mcp.tool()
async def generate_recipe_structure(
    name: str,
    ingredients: str,
    steps: str,
    servings: int = 4,
    prep_time: int = 30,
    total_time: int = 60,
    hints: str = "",
) -> str:
    """
    Validate recipe data and return a JSON structure ready for quality check + upload.

    Steps should use Thermomix vocabulary (time/temperature/speed) and ideally
    leverage TM7 parallelization (Varoma, Gareinsatz). Ingredients and steps can
    be newline- or comma-separated.
    """
    try:
        ingredients_list = [
            ing.strip()
            for ing in (ingredients.split("\n") if "\n" in ingredients else ingredients.split(","))
            if ing.strip()
        ]
        steps_list = [
            re.sub(r"^\s*(?:\d+[.)]|[-•])\s+", "", step).strip()
            for step in steps.split("\n")
            if step.strip()
        ]
        hints_list = None
        if hints:
            hints_list = [
                h.strip()
                for h in (hints.split("\n") if "\n" in hints else hints.split(","))
                if h.strip()
            ]
        recipe = CustomRecipe(
            name=name,
            ingredients=ingredients_list,
            steps=steps_list,
            servings=servings,
            prep_time=prep_time,
            total_time=total_time,
            hints=hints_list,
        )
        return (
            "Recipe structure validated.\n\n"
            + recipe.model_dump_json(indent=2)
            + "\n\nNext: call validate_recipe_quality(recipe_json) to check TM7 quality, "
            "then upload_custom_recipe(recipe_json) to publish."
        )
    except Exception as e:
        return f"Validation failed: {e}"


@mcp.tool()
async def validate_recipe_quality(recipe_json: str) -> str:
    """
    Check a recipe against TM7 quality criteria.

    Returns a score 0-100, whether it meets the quality bar, specific issues
    (missing time/temp/speed), and suggestions for TM7 parallelization and
    best-practice cooking. Use this before upload_custom_recipe. If score is
    below the bar, revise the steps and re-validate.
    """
    try:
        data = json.loads(recipe_json)
        recipe = CustomRecipe(**data)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    except Exception as e:
        return f"Invalid recipe data: {e}"

    result = _score_recipe(recipe)
    bd = result["breakdown"]
    lines = [
        f"TM7 Quality Score: {result['score']}/100 (bar: {QUALITY_BAR})",
        f"Meets quality bar: {'YES ✓' if result['meets_bar'] else 'NO — revise before upload'}",
        "",
        "Breakdown:",
        f"  TTS action steps (play button): {bd['tts_points']}/50  "
        f"({bd['tts_step_count']} parseable / ~{bd['expected_actions']} expected)",
        f"  Ingredient annotations:         {bd['ingredient_points']}/20  "
        f"({bd['ingredient_hit_steps']} step(s) with linked ingredients)",
        f"  Accessories mentioned:          {bd['accessory_points']}/10",
        f"  TM7 parallelization:            {bd['parallel_points']}/20",
    ]
    if result["issues"]:
        lines.append("")
        lines.append("Issues (fix these to meet the bar):")
        for issue in result["issues"]:
            lines.append(f"  ✗ {issue}")
    if result["suggestions"]:
        lines.append("")
        lines.append("Suggestions (optional improvements):")
        for s in result["suggestions"]:
            lines.append(f"  • {s}")
    return "\n".join(lines)


@mcp.tool()
async def list_my_custom_recipes() -> str:
    """
    List all custom recipes in the user's Cookidoo account.

    Use this to find recipes by name (e.g. before deleting or when looking up
    a prior upload). Returns name, recipe_id, creation date, total_time, and
    servings for each entry.
    """
    error, service = await _ensure_connected()
    if error:
        return error
    try:
        items = await service.list_custom_recipes()
    except Exception as e:
        return f"Failed to list recipes: {e}"
    if not items:
        return "No custom recipes in account."
    lines = [f"{len(items)} custom recipe(s) in account:\n"]
    for it in items:
        lines.append(
            f"  {it['recipe_id']}  {it['name']}  "
            f"(created {it.get('created_at', '?')}, "
            f"{it.get('servings', '?')} portions, {it.get('total_time', '?')})"
        )
    return "\n".join(lines)


@mcp.tool()
async def delete_custom_recipe(recipe_id: str) -> str:
    """
    Permanently delete a custom recipe from the user's Cookidoo account.

    Use this to clean up failed/zombie uploads or old test recipes. Only custom
    (user-created) recipes can be deleted — official Cookidoo content cannot.
    Irreversible; ask the user for confirmation before calling.
    """
    error, service = await _ensure_connected()
    if error:
        return error
    try:
        await service.delete_custom_recipe(recipe_id)
        return f"Deleted custom recipe {recipe_id}."
    except Exception as e:
        return f"Failed to delete recipe: {e}"


@mcp.tool()
async def update_custom_recipe(
    recipe_id: str, recipe_json: str, force_upload: bool = False
) -> str:
    """
    Replace an existing custom recipe with a full new recipe body.

    Same recipe schema as upload_custom_recipe. Runs the same TM7 quality gate;
    set force_upload=True to override a sub-bar score. For a name-only change,
    use rename_custom_recipe instead — it's a single partial PATCH.

    Preserves fields that CustomRecipe can't carry: any uploaded image and
    cookTime survive the update untouched. `hints` are also left alone when
    omitted from recipe_json — pass `"hints": []` to explicitly clear them,
    or a populated list to overwrite.
    """
    error, service = await _ensure_connected()
    if error:
        return error

    try:
        data = json.loads(recipe_json)
        recipe = CustomRecipe(**data)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    except Exception as e:
        return f"Invalid recipe data: {e}"

    quality = _score_recipe(recipe)
    if not quality["meets_bar"] and not force_upload:
        lines = [
            f"Update blocked — quality score {quality['score']}/100 below bar {QUALITY_BAR}.",
            "",
            "Issues:",
        ]
        for issue in quality["issues"]:
            lines.append(f"  ✗ {issue}")
        if quality["suggestions"]:
            lines.append("")
            lines.append("Suggestions:")
            for s in quality["suggestions"]:
                lines.append(f"  • {s}")
        lines.append("")
        lines.append(
            "Revise the steps with Thermomix vocabulary and TM7 parallelization, "
            "then call validate_recipe_quality again. To override, pass force_upload=true."
        )
        return "\n".join(lines)

    try:
        await service.update_custom_recipe(recipe_id, recipe)
    except Exception as e:
        return f"Update failed: {e}"

    return (
        f"Recipe {recipe_id} updated to '{recipe.name}' "
        f"(quality {quality['score']}/100)."
    )


@mcp.tool()
async def rename_custom_recipe(recipe_id: str, new_name: str) -> str:
    """
    Rename a custom recipe. Sends a partial PATCH with just the new name; every
    other field on the recipe (ingredients, steps + their annotations, image,
    times, tools) is preserved untouched by the backend. No quality gate —
    a name change can't affect TM7 play-button rendering.

    Use this for the common "just fix the title" case; use update_custom_recipe
    when other fields also need to change.
    """
    error, service = await _ensure_connected()
    if error:
        return error
    try:
        await service.rename_custom_recipe(recipe_id, new_name)
    except ValueError as e:
        return f"Invalid name: {e}"
    except Exception as e:
        return f"Rename failed: {e}"
    return f"Recipe {recipe_id} renamed to '{new_name}'."


@mcp.tool()
async def upload_custom_recipe(recipe_json: str, force_upload: bool = False) -> str:
    """
    Upload a recipe to the user's Cookidoo account.

    Automatically validates TM7 quality first and refuses upload if the recipe
    does not meet the quality bar. Set force_upload=True only if the user has
    explicitly accepted a lower-quality upload. Auto-connects if needed.
    """
    error, service = await _ensure_connected()
    if error:
        return error

    try:
        data = json.loads(recipe_json)
        recipe = CustomRecipe(**data)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    except Exception as e:
        return f"Invalid recipe data: {e}"

    quality = _score_recipe(recipe)
    if not quality["meets_bar"] and not force_upload:
        lines = [
            f"Upload blocked — quality score {quality['score']}/100 below bar {QUALITY_BAR}.",
            "",
            "Issues:",
        ]
        for issue in quality["issues"]:
            lines.append(f"  ✗ {issue}")
        if quality["suggestions"]:
            lines.append("")
            lines.append("Suggestions:")
            for s in quality["suggestions"]:
                lines.append(f"  • {s}")
        lines.append("")
        lines.append(
            "Revise the steps with Thermomix vocabulary and TM7 parallelization, "
            "then call validate_recipe_quality again. To override, pass force_upload=true."
        )
        return "\n".join(lines)

    try:
        recipe_id = await service.create_custom_recipe(
            name=recipe.name,
            ingredients=recipe.ingredients,
            steps=recipe.steps,
            servings=recipe.servings,
            prep_time=recipe.prep_time,
            total_time=recipe.total_time,
            hints=recipe.hints,
            tools=recipe.tools,
        )
        from urllib.parse import urlparse
        localization = _cookidoo_api.localization
        parsed = urlparse(localization.url)
        recipe_url = (
            f"{parsed.scheme}://{parsed.netloc}/created-recipes/"
            f"{localization.language}/{recipe_id}/edit"
        )
        return (
            f"Recipe '{recipe.name}' created (quality {quality['score']}/100).\n\n"
            f"Recipe ID: {recipe_id}\nURL: {recipe_url}"
        )
    except Exception as e:
        return f"Upload failed: {e}"


# -----------------------------------------------------------------------------
# Prompt — autonomous TM7 recipe creation workflow
# -----------------------------------------------------------------------------


@mcp.prompt()
def create_tm7_recipe(dish: str) -> str:
    """Autonomous workflow to create and upload a TM7-optimized custom recipe with guided-cooking annotations."""
    return f"""You are an experienced Thermomix cook creating a TM7-optimized recipe for: **{dish}**

Work through this workflow autonomously; only ask the user for the final confirmation.

## 1. Inspiration (optional)
If a reference recipe is known, fetch it with `get_recipe_details` and study its structure, times and temperatures.

## 2. Step design (CRITICAL — otherwise no play button on TM7)

Cookidoo renders a **play button** on a step only when the step carries an *action annotation*. This project supports two ways to attach an annotation:

- **Structured action (preferred, language-independent).** Give each machine-action step as a `RecipeStep` with an `action` object. The step's `text` can be in any language — Dutch, English, French, German, whatever fits the user's locale.
- **German string fallback (legacy).** A step given as a plain string is parsed by a German-grammar regex (`X Sek./Stufe Y`, `X Min./Varoma/Stufe Y`, `X Min./T°C/Intensiv`, …). Kept for backward compatibility. Prefer structured actions.

### Supported action kinds

Each `RecipeStep.action` is one of these three shapes. Times are always in **seconds**.

**1. Standard cooking / mixing (`tts`)** — play button.
```json
{{"kind": "tts",
  "time": <seconds>,
  "speed": "<label, e.g. '5' or '0.5'>",
  "temperature": <°C, optional>,
  "direction": "reverse" (optional; omit for the default clockwise direction)}}
```
Only these temperatures are accepted by the backend: `37, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 98, 100, 105, 110, 115, 120`. Other values are rejected and the upload fails.

**2. Varoma steaming (`steaming`)** — play button.
```json
{{"kind": "steaming",
  "time": <seconds>,
  "speed": "<label, e.g. '2'>"}}
```
Varoma is a *steam mode*, not a temperature. Use this when the ingredient sits in the Varoma tray on the lid.

**3. Browning / searing (`browning`)** — play button (Modus Anbraten).
```json
{{"kind": "browning",
  "time": <seconds, max 1800 = 30 min>,
  "temperature": <one of 140, 145, 150, 155, 160>,
  "power": "Intense" or "Gentle"}}
```

### Not-yet-supported modes (write as prose, no action)

The TM7 also has these modes; the server has no structured shape for them yet, so they cannot render a play button. Write them as prose steps and let the user set them on the device by hand:

- Fermenting / proofing (WARM_UP, 37–90°C)
- Rice Cooker
- Turbo / vigorous chopping
- Dough kneading (Teigknetstufe 🌾)

### The three hard rules

**Rule 1: One machine action per step, no surrounding prose in the same step.** The TM7 renders a play button only for pure-action steps. When action text is mixed with prose, the step falls back to a "mark as done" checkbox.

- ✗ WRONG (mixed):
  ```json
  {{"text": "Add onion and garlic and blend 5 sec at speed 5.",
   "action": {{"kind": "tts", "time": 5, "speed": "5"}}}}
  ```
- ✓ RIGHT (split into two steps):
  ```json
  {{"text": "Add onion and garlic to the mixing bowl."}}
  {{"text": "Blend 5 sec at speed 5.",
   "action": {{"kind": "tts", "time": 5, "speed": "5"}}}}
  ```

**Rule 2: Ingredients in the previous step, action in the next.**
- Prose step: all ingredients you are adding, with exact quantities.
- Action step: the machine action alone, with a structured `action`.

**Rule 3: Ingredient references must match the ingredient list byte-for-byte.** Ingredient annotations use exact substring matching. If the ingredient list has `"200 g rice"`, the step text must contain exactly `"200 g rice"` — not `"the rice"`, not `"200g rice"` (spacing matters), not a translation.

### Language of step text

Write step text in whichever language is natural for the user (their Cookidoo locale is the safest default). The play button comes from the structured `action`, not from a specific vocabulary in the text — so Dutch, English, French, German all work equally well.

## 3. TM7 parallelization
- **Varoma lid**: steam vegetables, fish or dumplings up top while the mixing bowl cooks/stirs below.
- **Simmering basket (Gareinsatz)**: cook pasta/rice/potatoes inside the bowl in parallel with the main process.
- **Mise en place** during heat-up phases.

## 4. Best-practice cooking technique
- Chop onion/garlic/herbs first (speed 5–7, 3–5 sec), then set aside or continue in the bowl.
- Reverse direction + low speed (1–2) for delicate ingredients you do not want cut.
- Butterfly whisk for cream, egg whites, whipped butter.
- Dough-kneading mode (🌾) for any dough.
- Weigh mise en place directly in the mixing bowl (saves washing).

## 5. Workflow

You will build the recipe JSON directly (the `CustomRecipe` shape), then validate and upload.

1. **Construct the recipe JSON.** Include:
   - `name`, `ingredients` (list of strings with quantities), `servings`, `prep_time`, `total_time`, optional `hints`.
   - `steps`: a list where each entry is either a plain string (parsed as German fallback) or a `RecipeStep` object `{{"text": "...", "action": {{...}}}}` for language-independent machine actions.
2. `validate_recipe_quality(recipe_json)` — confirm score ≥ {QUALITY_BAR}. `tts_step_count` must be > 0 (otherwise no play buttons will render).
3. If score too low or `tts_step_count = 0`: restructure per Rules 1+2, add structured actions, revalidate.
4. Show the final JSON + score to the user and ask **once**: *"Upload?"*
5. On approval: `upload_custom_recipe(recipe_json)` → return the URL.

### Example — structured recipe (Dutch text, structured actions)

```json
{{
  "name": "Ui-tomaten saus",
  "ingredients": ["1 ui", "2 tenen knoflook", "400 g gepelde tomaten", "1 el olijfolie", "zout, peper"],
  "servings": 4,
  "prep_time": 5,
  "total_time": 20,
  "steps": [
    "Snijd de ui en knoflook grof en doe ze in de mengkom.",
    {{"text": "Fijnhakken 5 sec op stand 5.",
     "action": {{"kind": "tts", "time": 5, "speed": "5"}}}},
    "Voeg 1 el olijfolie toe.",
    {{"text": "Aanbraden 3 min op 160°C.",
     "action": {{"kind": "browning", "time": 180, "temperature": 160, "power": "Gentle"}}}},
    "Voeg 400 g gepelde tomaten, zout en peper toe.",
    {{"text": "Koken 12 min op 100°C tegendraads op stand 1.",
     "action": {{"kind": "tts", "time": 720, "speed": "1", "temperature": 100, "direction": "reverse"}}}}
  ]
}}
```
"""


# -----------------------------------------------------------------------------
# HTTP transport (token auth + health) — used when run as a server process
# -----------------------------------------------------------------------------


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path == "/health":
            return await call_next(request)
        if API_TOKEN:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {API_TOKEN}":
                return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


async def health_endpoint(request):
    return JSONResponse(
        {
            "status": "ok",
            "service": "cookidoo-mcp-server",
            "version": VERSION,
            "quality_bar": QUALITY_BAR,
            "auth_configured": bool(API_TOKEN),
        }
    )


def build_http_app():
    app = mcp.http_app(transport="streamable-http", stateless_http=True, json_response=True)
    app.add_middleware(TokenAuthMiddleware)
    app.routes.append(Route("/health", health_endpoint))
    return app


app = None
if os.environ.get("COOKIDOO_MCP_MODE", "").lower() == "http":
    app = build_http_app()


if __name__ == "__main__":
    mode = os.environ.get("COOKIDOO_MCP_MODE", "http").lower()
    if mode == "stdio":
        mcp.run()
    else:
        import uvicorn

        if app is None:
            app = build_http_app()
        uvicorn.run(app, host="0.0.0.0", port=PORT)
