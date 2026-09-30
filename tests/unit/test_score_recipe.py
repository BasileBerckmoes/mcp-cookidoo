"""Scoring tests for `_score_recipe` (issue #5).

Locks:
- Validator messages are English (no German prose returned to the LLM).
- Parallel-work bonus is configurable via `COOKIDOO_PARALLEL_BONUS`:
  * Default (unset): phrase-based +10 only. Varoma alone gives 0.
  * `varoma`: additionally credits +10 for a `SteamingAction` or "varoma" text.
- `ondertussen` (nl-BE) is recognised as a phrase-based parallel marker.
"""
from __future__ import annotations

import pytest

from schemas import CustomRecipe, RecipeStep, SteamingAction, TTSAction


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _recipe(steps, ingredients=None, name="__TEST__ recipe") -> CustomRecipe:
    """Minimal CustomRecipe for scoring tests. Steps can be strings or RecipeSteps."""
    return CustomRecipe(
        name=name,
        ingredients=ingredients or ["100 g water", "5 g zout"],
        steps=steps,
    )


def _score(recipe: CustomRecipe) -> dict:
    """Import inside the call so monkeypatched env vars are picked up per test."""
    from server import _score_recipe

    return _score_recipe(recipe)


# --------------------------------------------------------------------------- #
# Parallel-work bonus: default (unset)
# --------------------------------------------------------------------------- #


def test_varoma_alone_default_gets_zero_parallel_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        "100 g water in de mengbeker doen.",
        RecipeStep(
            text="Stomen 15 min op stand 2.",
            action=SteamingAction(time=900, speed="2"),
        ),
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 0


def test_phrase_ondertussen_default_earns_phrase_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(
            text="Water koken 5 min op stand 1.",
            action=TTSAction(time=300, speed="1"),
        ),
        "Ondertussen de ui fijnsnijden.",
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


def test_phrase_meanwhile_english_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(
            text="Boil water 5 min at speed 1.",
            action=TTSAction(time=300, speed="1"),
        ),
        "Meanwhile chop the onion finely.",
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


def test_phrase_gleichzeitig_german_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(
            text="Wasser 5 Min./Stufe 1 kochen.",
            action=TTSAction(time=300, speed="1"),
        ),
        "Gleichzeitig die Zwiebel klein schneiden.",
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


# --------------------------------------------------------------------------- #
# Parallel-work bonus: COOKIDOO_PARALLEL_BONUS=varoma
# --------------------------------------------------------------------------- #


def test_varoma_alone_with_varoma_bonus_gets_ten_points(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COOKIDOO_PARALLEL_BONUS", "varoma")
    recipe = _recipe([
        "100 g water in de mengbeker doen.",
        RecipeStep(
            text="Stomen 15 min op stand 2.",
            action=SteamingAction(time=900, speed="2"),
        ),
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


def test_phrase_plus_varoma_with_bonus_caps_at_twenty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COOKIDOO_PARALLEL_BONUS", "varoma")
    recipe = _recipe([
        RecipeStep(
            text="Stomen 15 min op stand 2.",
            action=SteamingAction(time=900, speed="2"),
        ),
        "Ondertussen de pasta koken op het fornuis.",
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 20


def test_varoma_text_prose_also_credited_with_bonus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compat: `varoma` in prose is credited when the bonus is on, no structured action needed."""
    monkeypatch.setenv("COOKIDOO_PARALLEL_BONUS", "varoma")
    recipe = _recipe([
        "Groenten in de Varoma-aufsatz plaatsen.",
        RecipeStep(
            text="Meng 5 sec op stand 5.",
            action=TTSAction(time=5, speed="5"),
        ),
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


def test_bonus_env_value_is_case_and_whitespace_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COOKIDOO_PARALLEL_BONUS", "  Varoma ")
    recipe = _recipe([
        "100 g water in de mengbeker doen.",
        RecipeStep(
            text="Stomen 15 min op stand 2.",
            action=SteamingAction(time=900, speed="2"),
        ),
    ])
    assert _score(recipe)["breakdown"]["parallel_points"] == 10


# --------------------------------------------------------------------------- #
# Issue #5 headline scenario: 90 → 80 → 90
# --------------------------------------------------------------------------- #


def test_issue5_headline_recipe_drops_ten_points_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recipe with all four criteria maxed (TTS, ingredient, accessory, Varoma) scored 100 pre-#5.
    With the Varoma-alone bonus off by default, the Varoma-only recipe loses 10 pts."""
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe(
        [
            "100 g water in de Varoma-aufsatz doen.",
            RecipeStep(
                text="Stomen 15 min op stand 2.",
                action=SteamingAction(time=900, speed="2"),
            ),
        ],
        ingredients=["100 g water"],
    )
    default_score = _score(recipe)["score"]

    monkeypatch.setenv("COOKIDOO_PARALLEL_BONUS", "varoma")
    with_bonus_score = _score(recipe)["score"]

    assert with_bonus_score - default_score == 10


# --------------------------------------------------------------------------- #
# English validator messages
# --------------------------------------------------------------------------- #


def test_no_action_issue_message_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe(["Just some prose.", "More prose without any action."])
    issues = _score(recipe)["issues"]
    assert issues, "expected at least one issue when tts_step_count == 0"
    joined = " ".join(issues).lower()
    # English tokens
    assert "play button" in joined or "play-button" in joined or "parseable" in joined
    assert "structured" in joined or "action" in joined
    # No German prose
    assert "kein schritt" not in joined
    assert "aktionen" not in joined
    assert "linkslauf" not in joined


def test_low_action_ratio_message_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """One action step out of nine — ratio 1/4 < 0.5 triggers the 'few actions' issue."""
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        "Step 1 prose.",
        "Step 2 prose.",
        "Step 3 prose.",
        "Step 4 prose.",
        "Step 5 prose.",
        "Step 6 prose.",
        "Step 7 prose.",
        "Step 8 prose.",
        RecipeStep(text="Meng 5 sec op stand 5.", action=TTSAction(time=5, speed="5")),
    ])
    issues = _score(recipe)["issues"]
    joined = " ".join(issues).lower()
    assert "action" in joined
    assert "aktion" not in joined  # de.
    assert "erwarteten" not in joined


def test_no_ingredient_reference_message_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe(
        [
            RecipeStep(text="Meng grondig.", action=TTSAction(time=5, speed="5")),
        ],
        ingredients=["100 g suiker"],
    )
    issues = _score(recipe)["issues"]
    joined = " ".join(issues).lower()
    assert "ingredient" in joined
    assert "zutat" not in joined


def test_accessory_suggestion_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(text="100 g suiker toevoegen.", action=TTSAction(time=5, speed="5")),
    ], ingredients=["100 g suiker"])
    suggestions = _score(recipe)["suggestions"]
    joined = " ".join(suggestions).lower()
    assert "accessor" in joined or "spatula" in joined or "basket" in joined or "butterfly" in joined
    assert "zubehör" not in joined
    assert "erwäge" not in joined


def test_parallel_suggestion_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(text="Meng 5 sec op stand 5.", action=TTSAction(time=5, speed="5")),
    ], ingredients=["100 g suiker"])
    # bump ingredient hit for a clean recipe: still no parallel phrasing
    suggestions = _score(recipe)["suggestions"]
    joined = " ".join(suggestions).lower()
    assert "parallel" in joined or "meanwhile" in joined
    assert "parallelisierung" not in joined
    assert "prüfe" not in joined


def test_cream_suggestion_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(text="Cream and 2 egg whites in bowl.", action=TTSAction(time=5, speed="5")),
    ], ingredients=["200 ml cream"])
    suggestions = _score(recipe)["suggestions"]
    joined = " ".join(suggestions).lower()
    assert "butterfly" in joined or "whip" in joined
    assert "einsetzen" not in joined


def test_dough_suggestion_is_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COOKIDOO_PARALLEL_BONUS", raising=False)
    recipe = _recipe([
        RecipeStep(text="Make the dough.", action=TTSAction(time=5, speed="5")),
    ], ingredients=["500 g flour"])
    suggestions = _score(recipe)["suggestions"]
    joined = " ".join(suggestions).lower()
    assert "knead" in joined or "dough" in joined
    assert "teigknetstufe" not in joined
    assert "verwenden" not in joined
