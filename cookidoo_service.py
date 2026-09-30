"""
Cookidoo Service

Module to encapsulate all cookidoo-api logic for interacting with the Cookidoo platform.
"""

import json
import os
import re
from typing import Optional
from dotenv import load_dotenv
from aiohttp import ClientSession
from cookidoo_api import Cookidoo, CookidooConfig
from cookidoo_api.exceptions import CookidooAuthException
from cookidoo_api.helpers import (
    get_localization_options,
)
import aiohttp
import asyncio


# --- TTS parsing / annotation building ---

_TTS_SPAN_RE = re.compile(
    r"(\d+\s*(?:Sek\.?|sec|Min\.?|min)"                    # time (required)
    r"(?:\s*/\s*(?:\d+\s*°\s*C|Varoma))?"                   # optional temp
    r"(?:\s*/\s*(?:Linkslauf|sens inverse|reverse))?"       # optional direction
    r"\s*/\s*Stufe\s*\d+(?:[.,]\d+)?)",                    # speed (required)
    re.IGNORECASE,
)
# Browning (Modus Anbraten) — time + temperature + power. The distinctive
# power word (Leicht/Intensiv/Gentle/Intense) lets us detect browning without
# relying on an "Anbraten" verb prefix (which the normalizer may strip).
_BROWNING_SPAN_RE = re.compile(
    r"(\d+\s*Min\.?"                                       # time
    r"\s*/\s*\d+\s*°\s*C"                                  # temperature
    r"\s*/\s*(?:Leicht|Intensiv|Gentle|Intense))",         # power
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(\d+)\s*(Sek\.?|sec|Min\.?|min)", re.IGNORECASE)
_SPEED_RE = re.compile(r"Stufe\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_TEMP_RE = re.compile(r"(\d+)\s*°\s*C|([Vv]aroma)")
_POWER_RE = re.compile(r"(Leicht|Intensiv|Gentle|Intense)", re.IGNORECASE)
# BROWNING temperature enum (Celsius): only these discrete values are accepted.
_BROWNING_TEMPS = {"140", "145", "150", "155", "160"}

# A step is "near-pure action" if it contains exactly one action span and the
# surrounding text is just a single verb prefix + optional trailing punctuation.
# On the TM7 device, such steps must be stored as pure action text (no prose)
# or the guided step gets a "mark as done" checkbox instead of a play button.
_PURE_TTS_STEP_RE = re.compile(
    r"^\s*(?:[A-ZÄÖÜ][a-zäöüß]+\s+)?"
    r"(\d+\s*(?:Sek\.?|sec|Min\.?|min)"
    r"(?:\s*/\s*(?:\d+\s*°\s*C|Varoma))?"
    r"(?:\s*/\s*(?:Linkslauf|sens inverse|reverse))?"
    r"\s*/\s*Stufe\s*\d+(?:[.,]\d+)?)"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_PURE_BROWNING_STEP_RE = re.compile(
    r"^\s*(?:(?:Modus\s+)?Anbraten\s+)?"
    r"(\d+\s*Min\.?\s*/\s*\d+\s*°\s*C\s*/\s*(?:Leicht|Intensiv|Gentle|Intense))"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def normalize_action_step(text: str) -> str:
    """
    Strip verb prefix and trailing punctuation from near-pure action steps.

    ``"Mahlen 30 Sek./Stufe 10."`` → ``"30 Sek./Stufe 10"``
    ``"Dämpfen 15 Min./Varoma/Stufe 2."`` → ``"15 Min./Varoma/Stufe 2"``
    ``"Anbraten 7 Min./160°C/Intensiv."`` → ``"7 Min./160°C/Intensiv"``

    Steps with meaningful surrounding prose (more than a single verb word) are
    left untouched so their ingredients/context still show up.
    """
    m = _PURE_TTS_STEP_RE.match(text)
    if m:
        return m.group(1)
    m = _PURE_BROWNING_STEP_RE.match(text)
    if m:
        return m.group(1)
    return text


def _parse_action(text: str) -> Optional[tuple[str, Optional[str], dict]]:
    """
    Parse a TM action span into (annotation_type, annotation_name, data).

    - Standard cooking ('5 Sek./Stufe 5', '3 Min./120°C/Linkslauf/Stufe 1')
      → ("TTS", None, {time, speed, temperature?})
    - Varoma steaming ('15 Min./Varoma/Stufe 2')
      → ("MODE", "STEAMING", {time, speed, direction, accessory})
    """
    m_time = _TIME_RE.search(text)
    if not m_time:
        return None
    n = int(m_time.group(1))
    unit = m_time.group(2).lower()
    if unit.startswith("sek") or unit.startswith("sec"):
        seconds = n
    elif unit.startswith("min"):
        seconds = n * 60
    else:
        return None

    m_speed = _SPEED_RE.search(text)
    if not m_speed:
        return None
    speed = m_speed.group(1).replace(",", ".")

    m_temp = _TEMP_RE.search(text)
    is_varoma = bool(m_temp and m_temp.group(2))

    if is_varoma:
        return (
            "MODE",
            "STEAMING",
            {
                "time": seconds,
                "speed": speed,
                "direction": "CW",
                "accessory": "Varoma",
            },
        )

    data: dict = {"speed": speed, "time": seconds}
    if m_temp:
        data["temperature"] = {"value": m_temp.group(1), "unit": "C"}
    return ("TTS", None, data)


def _parse_browning(span_text: str) -> Optional[dict]:
    """Parse a browning action like '7 Min./160°C/Intensiv' into BROWNING mode data."""
    m_time = _TIME_RE.search(span_text)
    if not m_time:
        return None
    n = int(m_time.group(1))
    unit = m_time.group(2).lower()
    seconds = n * 60 if unit.startswith("min") else n
    if not (1 <= seconds <= 1800):
        return None

    m_temp = _TEMP_RE.search(span_text)
    if not m_temp or not m_temp.group(1):
        return None
    temp_str = m_temp.group(1)
    if temp_str not in _BROWNING_TEMPS:
        return None

    m_power = _POWER_RE.search(span_text)
    if not m_power:
        return None
    raw = m_power.group(1).lower()
    power = "Intense" if raw in ("intensiv", "intense") else "Gentle"

    return {
        "time": seconds,
        "temperature": {"value": temp_str, "unit": "C"},
        "power": power,
    }


def build_step_annotations(text: str, ingredients: list[str]) -> list[dict]:
    """
    Build annotations for a single step text.

    Detects three action pattern families and matches ingredient references
    from the given ingredients list using exact-substring matching. Overlapping
    matches are avoided; longer matches win first.

    Action patterns:
    - Standard cook ``X Sek./Stufe Y`` / ``X Min./T°C/[Linkslauf/]Stufe Y``
      → type=TTS (renders as ``<cr-tts>``)
    - Varoma steam ``X Min./Varoma/Stufe Y``
      → type=MODE, name=STEAMING (renders as ``<cr-mode name="steaming">``)
    - Browning ``Anbraten X Min./T°C/Intensiv`` (T in {140,145,150,155,160})
      → type=MODE, name=BROWNING (renders as ``<cr-mode name="browning">``)
    """
    annotations: list[dict] = []
    used: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(not (end <= a or start >= b) for a, b in used)

    for m in _BROWNING_SPAN_RE.finditer(text):
        start, end = m.span(1)
        data = _parse_browning(m.group(1))
        if data is None or overlaps(start, end):
            continue
        annotations.append(
            {
                "type": "MODE",
                "name": "BROWNING",
                "data": data,
                "position": {"offset": start, "length": end - start},
            }
        )
        used.append((start, end))

    for m in _TTS_SPAN_RE.finditer(text):
        start, end = m.span(1)
        if overlaps(start, end):
            continue
        parsed = _parse_action(m.group(1))
        if parsed is None:
            continue
        atype, aname, data = parsed
        annotation: dict = {
            "type": atype,
            "data": data,
            "position": {"offset": start, "length": end - start},
        }
        if aname is not None:
            annotation["name"] = aname
        annotations.append(annotation)
        used.append((start, end))

    for ing in sorted((i for i in ingredients if i), key=len, reverse=True):
        search_from = 0
        while True:
            idx = text.find(ing, search_from)
            if idx < 0:
                break
            end = idx + len(ing)
            if overlaps(idx, end):
                search_from = idx + 1
                continue
            annotations.append(
                {
                    "type": "INGREDIENT",
                    "data": {"description": ing},
                    "position": {"offset": idx, "length": len(ing)},
                }
            )
            used.append((idx, end))
            search_from = end

    annotations.sort(key=lambda a: a["position"]["offset"])
    return annotations


def build_instruction(text: str, ingredients: list[str]) -> dict:
    return {
        "type": "STEP",
        "text": text,
        "annotations": build_step_annotations(text, ingredients),
    }


def _annotations_from_action(text: str, action, ingredients: list[str]) -> list[dict]:
    """Build Cookidoo annotations from a structured action.

    The action carries the numeric/enum values directly, so no text parsing is
    needed — the step text can be in any language. The annotation spans the
    full step text (offset=0, length=len(text)); callers keep step text short
    and action-only, per the "one action per step" rule.

    ``action`` is duck-typed as anything with the fields on
    :class:`schemas.TTSAction` / :class:`schemas.SteamingAction` /
    :class:`schemas.BrowningAction`, discriminated by ``action.kind``.
    """
    span = {"offset": 0, "length": len(text)}
    if action.kind == "tts":
        data: dict = {"speed": action.speed, "time": action.time}
        if getattr(action, "temperature", None) is not None:
            data["temperature"] = {"value": str(action.temperature), "unit": "C"}
        return [{"type": "TTS", "data": data, "position": dict(span)}]
    if action.kind == "steaming":
        return [
            {
                "type": "MODE",
                "name": "STEAMING",
                "data": {
                    "time": action.time,
                    "speed": action.speed,
                    "direction": "CW",
                    "accessory": "Varoma",
                },
                "position": dict(span),
            }
        ]
    if action.kind == "browning":
        return [
            {
                "type": "MODE",
                "name": "BROWNING",
                "data": {
                    "time": action.time,
                    "temperature": {"value": str(action.temperature), "unit": "C"},
                    "power": action.power,
                },
                "position": dict(span),
            }
        ]
    raise ValueError(f"Unknown action kind: {action.kind!r}")


def _step_to_instruction(step, ingredients: list[str]) -> dict:
    """Convert one step into a Cookidoo instruction dict.

    Accepts either a plain string (legacy — parses German text via
    :func:`build_step_annotations`) or a :class:`schemas.RecipeStep`-like
    object with ``.text`` and ``.action``. When ``action`` is present the
    annotation is built directly from it (language-independent); when absent
    or when the step is a bare string, the parser fallback runs.
    """
    if isinstance(step, str):
        return build_instruction(normalize_action_step(step), ingredients)
    if getattr(step, "action", None) is not None:
        return {
            "type": "STEP",
            "text": step.text,
            "annotations": _annotations_from_action(step.text, step.action, ingredients),
        }
    return build_instruction(normalize_action_step(step.text), ingredients)


def _build_recipe_payload(
    *,
    name: str,
    ingredients: list[str],
    steps: list[str],
    servings: int,
    prep_time_seconds: int,
    total_time_seconds: int,
    hints: Optional[list[str] | str] = None,
    tools: Optional[list[str]] = None,
    image: Optional[str] = None,
    cook_time_seconds: int = 0,
) -> dict:
    """Build the PATCH body for /created-recipes/{locale}/{id}.

    Extracted from :meth:`CookidooService.create_custom_recipe` so
    :meth:`CookidooService.update_custom_recipe` can reuse the same shape.
    Times are seconds — payload-native — to avoid hidden minute↔second
    conversions inside the helper; callers convert at their boundary.
    Steps go through :func:`normalize_action_step` and :func:`build_instruction`
    so TTS/MODE/INGREDIENT annotations are rebuilt on every write (the read
    endpoint strips them, so read-modify-write flows re-derive them here).
    """
    return {
        "name": name,
        "image": image,
        "isImageOwnedByUser": image is not None,
        "tools": tools if tools else ["TM7", "TM6", "TM5"],
        "yield": {"value": servings, "unitText": "portion"},
        "prepTime": prep_time_seconds,
        "cookTime": cook_time_seconds,
        "totalTime": total_time_seconds,
        "ingredients": [{"type": "INGREDIENT", "text": ing} for ing in ingredients],
        "instructions": [_step_to_instruction(step, ingredients) for step in steps],
        "hints": "\n".join(hints) if hints and isinstance(hints, list) else (hints if hints else ""),
        "workStatus": "PRIVATE",
        "recipeMetadata": {"requiresAnnotationsCheck": False},
    }


def load_cookidoo_credentials() -> tuple[str, str]:
    """
    Load Cookidoo credentials from .env file.
    
    Returns:
        tuple[str, str]: Email and password
        
    Raises:
        ValueError: If credentials are not found in environment variables
    """
    load_dotenv()
    
    email = os.getenv("COOKIDOO_EMAIL")
    password = os.getenv("COOKIDOO_PASSWORD")
    
    if not email or not password:
        raise ValueError(
            "Missing Cookidoo credentials. Please set COOKIDOO_EMAIL and "
            "COOKIDOO_PASSWORD in your .env file"
        )
    
    return email, password


class CookidooService:
    """Service class for managing Cookidoo API interactions."""
    
    def __init__(self, email: str, password: str):
        """
        Initialize the Cookidoo service with credentials.
        
        Args:
            email: Cookidoo account email
            password: Cookidoo account password
        """
        self.email = email
        self.password = password
        self._api_client: Optional[Cookidoo] = None
        self._session: Optional[ClientSession] = None
    
    async def login(self) -> Cookidoo:
        """
        Authenticate with Cookidoo and return the API client.
        
        Returns:
            Cookidoo: Authenticated Cookidoo API client
            
        Raises:
            Exception: If authentication fails
        """
        try:
            # TLS verification on by default; COOKIDOO_INSECURE_SSL=1 disables it
            # for debugging behind intercepting proxies.
            insecure = os.getenv("COOKIDOO_INSECURE_SSL", "").lower() in ("1", "true", "yes")
            connector = aiohttp.TCPConnector(ssl=False) if insecure else aiohttp.TCPConnector()
            self._session = ClientSession(connector=connector)
            

            # Create CookidooConfig with credentials
            config = CookidooConfig(
                email=self.email,
                password=self.password,
                localization=(
                    await get_localization_options(country=os.getenv("COOKIDOO_COUNTRY", "ch"), language=os.getenv("COOKIDOO_LANGUAGE", "de-CH"))
                )[0],
            )
            
            # Create Cookidoo API client with session and config
            self._api_client = Cookidoo(session=self._session, cfg=config)
            
            # Perform login (no parameters needed - uses config)
            await self._api_client.login()
            
            return self._api_client
            
        except Exception as e:
            # Clean up session if login fails
            if self._session:
                await self._session.close()
            raise Exception(f"Failed to authenticate with Cookidoo: {str(e)}") from e
    
    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()

    async def _authed_request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[dict] = None,
        accept: str = "application/json",
    ) -> tuple[int, str]:
        """Make an authenticated HTTP request, refreshing the token once on 401.

        Returns ``(status, body_text)``. The custom-recipe endpoints are not
        wrapped by the cookidoo-api library, so the library's auth-retry logic
        doesn't apply — we have to do the refresh-on-401 here ourselves.
        """
        if not self._api_client:
            raise Exception("Not authenticated. Please call login() first.")

        async def _send() -> tuple[int, str]:
            headers = {
                "Accept": accept,
                "Authorization": f"Bearer {self._api_client.auth_data.access_token}",
            }
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            async with self._api_client._session.request(
                method, url, json=json_body, headers=headers
            ) as r:
                return r.status, await r.text()

        status, text = await _send()
        if status == 401:
            await self._api_client.refresh_token()
            status, text = await _send()
        return status, text

    async def create_custom_recipe(
        self,
        name: str,
        ingredients: list[str],
        steps: list[str],
        servings: int = 4,
        prep_time: int = 30,
        total_time: int = 60,
        hints: Optional[list[str]] = None,
        tools: Optional[list[str]] = None,
    ) -> str:
        """
        Create a completely new custom recipe from scratch using the undocumented API.
        
        Args:
            name: Recipe name
            ingredients: List of ingredient descriptions
            steps: List of cooking step descriptions
            servings: Number of servings (default: 4)
            prep_time: Preparation time in minutes (default: 30)
            total_time: Total cooking time in minutes (default: 60)
            hints: Optional list of hints/tips for the recipe
            
        Returns:
            str: The created recipe ID
            
        Raises:
            Exception: If recipe creation fails
        """
        if not self._api_client or not self._session:
            raise Exception("Not authenticated. Please call login() first.")

        try:
            localization = self._api_client.localization
            url_parts = localization.url.split("/")
            base_url = f"{url_parts[0]}//{url_parts[2]}"
            locale = localization.language

            create_url = f"{base_url}/created-recipes/{locale}"
            status, text = await self._authed_request(
                "POST", create_url, json_body={"recipeName": name}
            )
            if status != 200:
                raise Exception(
                    f"Failed to create recipe. Status: {status}, Error: {text}"
                )
            result = json.loads(text)
            recipe_id = result.get("recipeId")
            if not recipe_id:
                raise Exception("No recipe ID returned from creation")

            update_url = f"{base_url}/created-recipes/{locale}/{recipe_id}"
            update_data = _build_recipe_payload(
                name=name,
                ingredients=ingredients,
                steps=steps,
                servings=servings,
                prep_time_seconds=prep_time * 60,
                total_time_seconds=total_time * 60,
                hints=hints,
                tools=tools,
            )

            # Give the backend time to materialize the recipe created by the POST
            # above — PATCHing immediately after creation is unreliable.
            await asyncio.sleep(5)

            try:
                status, text = await self._authed_request(
                    "PATCH", update_url, json_body=update_data
                )
                if status not in (200, 204):
                    raise Exception(
                        f"Failed to update recipe. Status: {status}, Error: {text}"
                    )
            except Exception:
                # Rollback: the POST created an empty recipe; delete it so no zombie remains.
                try:
                    await self._api_client.remove_custom_recipe(recipe_id)
                except Exception:
                    pass
                raise

            return recipe_id

        except Exception as e:
            raise Exception(f"Failed to create custom recipe: {str(e)}") from e

    async def get_custom_recipe(self, recipe_id: str) -> dict:
        """Read one of the user's custom recipes by ID.

        Uses ``cookidoo-api``'s ``Cookidoo.get_custom_recipe`` and projects
        the returned ``CookidooCustomRecipe`` dataclass into a dict whose
        keys mirror the PATCH payload built by :meth:`create_custom_recipe`,
        so callers can read → mutate → write symmetrically.

        The backend read endpoint returns a schema.org projection that omits
        two fields we send on PATCH: ``hints`` and ``cookTime``. It also
        strips step annotations from ``instructions``. Both are documented
        limitations — a read-modify-write flow must rebuild annotations from
        the raw step text via :func:`build_instruction`. See
        ``.claude/docs/findings/2026-09-27-custom-recipe-read-schema.md``.

        Retries once after ``refresh_token()`` on ``CookidooAuthException``,
        matching :meth:`delete_custom_recipe`.
        """
        if not self._api_client:
            raise Exception("Not authenticated. Please call login() first.")
        try:
            recipe = await self._api_client.get_custom_recipe(recipe_id)
        except CookidooAuthException:
            await self._api_client.refresh_token()
            recipe = await self._api_client.get_custom_recipe(recipe_id)

        return {
            "id": recipe.id,
            "name": recipe.name,
            "ingredients": list(recipe.ingredients),
            "instructions": list(recipe.instructions),
            "yield": {"value": recipe.serving_size, "unitText": "portion"},
            "prepTime": recipe.active_time,
            "cookTime": None,
            "totalTime": recipe.total_time,
            "tools": list(recipe.tools),
            "hints": None,
            "image": recipe.image,
            "url": recipe.url,
        }

    async def update_custom_recipe(self, recipe_id: str, recipe) -> None:
        """PATCH an existing custom recipe with a full recipe body.

        ``recipe`` is a :class:`schemas.CustomRecipe`. The PATCH body is built
        via :func:`_build_recipe_payload` and then trimmed of the fields
        :class:`schemas.CustomRecipe` can't safely source from the caller:

        - ``image`` / ``isImageOwnedByUser`` — not in :class:`CustomRecipe`;
          sending ``image: None`` would wipe any user-uploaded image.
        - ``cookTime`` — not in :class:`CustomRecipe`; sending ``0`` would
          wipe any non-zero cook time the recipe had.
        - ``hints`` — omitted when ``recipe.hints is None``. The read
          endpoint doesn't return hints (see finding
          ``2026-09-27-custom-recipe-read-schema.md``), so callers can't
          preserve them via read-modify-write. ``hints=None`` therefore means
          "leave alone"; ``hints=[]`` explicitly clears; a populated list
          writes.

        Partial-PATCH semantics (finding
        ``2026-09-27-custom-recipe-partial-patch.md``) mean the backend
        leaves any field we don't send untouched, which is what makes this
        safe. No POST, no 5-second sleep — those are create-time race
        workarounds. Refresh-on-401 is handled by :meth:`_authed_request`.
        """
        if not self._api_client or not self._session:
            raise Exception("Not authenticated. Please call login() first.")

        localization = self._api_client.localization
        url_parts = localization.url.split("/")
        base_url = f"{url_parts[0]}//{url_parts[2]}"
        locale = localization.language
        url = f"{base_url}/created-recipes/{locale}/{recipe_id}"

        body = _build_recipe_payload(
            name=recipe.name,
            ingredients=recipe.ingredients,
            steps=recipe.steps,
            servings=recipe.servings,
            prep_time_seconds=recipe.prep_time * 60,
            total_time_seconds=recipe.total_time * 60,
            hints=recipe.hints,
            tools=recipe.tools,
        )
        # Fields the caller can't safely control via CustomRecipe; omitting
        # them keeps whatever the backend already has.
        for key in ("image", "isImageOwnedByUser", "cookTime"):
            body.pop(key, None)
        if recipe.hints is None:
            body.pop("hints", None)

        status, text = await self._authed_request("PATCH", url, json_body=body)
        if status not in (200, 204):
            raise Exception(
                f"Failed to update recipe. Status: {status}, Error: {text}"
            )

    async def rename_custom_recipe(self, recipe_id: str, new_name: str) -> None:
        """Rename a custom recipe with a single partial PATCH.

        The backend accepts partial PATCH bodies for this endpoint and leaves
        every other field on the recipe untouched — probed against the real
        account (see ``.claude/docs/findings/2026-09-27-custom-recipe-partial-patch.md``).
        A full-body read-then-write would risk annotation drift, since the
        schema.org GET strips annotations and we'd have to rebuild them from
        raw step text; partial PATCH avoids that entirely.
        """
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("new_name must be a non-empty string")
        if not self._api_client or not self._session:
            raise Exception("Not authenticated. Please call login() first.")

        localization = self._api_client.localization
        url_parts = localization.url.split("/")
        base_url = f"{url_parts[0]}//{url_parts[2]}"
        locale = localization.language
        url = f"{base_url}/created-recipes/{locale}/{recipe_id}"

        status, text = await self._authed_request(
            "PATCH", url, json_body={"name": new_name}
        )
        if status not in (200, 204):
            raise Exception(
                f"Failed to rename recipe. Status: {status}, Error: {text}"
            )

    async def delete_custom_recipe(self, recipe_id: str) -> None:
        """Delete one of the user's custom recipes by ID. Refreshes the access
        token once if the library reports an auth failure (expired token)."""
        if not self._api_client:
            raise Exception("Not authenticated. Please call login() first.")
        try:
            await self._api_client.remove_custom_recipe(recipe_id)
        except CookidooAuthException:
            await self._api_client.refresh_token()
            await self._api_client.remove_custom_recipe(recipe_id)

    async def list_custom_recipes(self) -> list[dict]:
        """List the user's custom recipes. Returns a list of {recipe_id, name, created_at, total_time}."""
        if not self._api_client or not self._session:
            raise Exception("Not authenticated. Please call login() first.")
        localization = self._api_client.localization
        url_parts = localization.url.split("/")
        base_url = f"{url_parts[0]}//{url_parts[2]}"
        url = f"{base_url}/created-recipes/{localization.language}"

        status, text = await self._authed_request("GET", url)
        if status != 200:
            raise Exception(f"Failed to list recipes. Status: {status}")
        data = json.loads(text)

        items = []
        for item in data.get("items", []):
            content = item.get("recipeContent", {})
            items.append(
                {
                    "recipe_id": item.get("recipeId"),
                    "name": content.get("name"),
                    "created_at": item.get("createdAt"),
                    "total_time": content.get("totalTime"),
                    "servings": content.get("recipeYield", {}).get("value"),
                }
            )
        return items
    
    @property
    def api_client(self) -> Optional[Cookidoo]:
        """Get the current API client instance."""
        return self._api_client
