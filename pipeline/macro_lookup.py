"""
FOOD-012: USDA FoodData Central API macro lookup.

Given a confirmed dish name, queries USDA FNDDS for macros per 100g.
Results are cached locally so the same dish never makes a redundant API call.

Flow:
  1. Check cache at data/macro_cache/{slug}.json
  2. If miss: query USDA with English name
  3. If 0 results: try pinyin fallback for common Chinese dishes
  4. If still 0: return no_results (signals FOOD-013 manual entry)
  5. Cache and return top-3 candidates + selected (index 0 by default)

Public API:
    MacroResult             — dataclass with per-100g macro fields
    lookup_macros(...)      -> MacroResult
    select_candidate(...)   -> MacroResult   (user picks from top-3)
    clear_cache(dish_name)  -> None
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import requests
import yaml

# ---------------------------------------------------------------------------
# Config / constants
# ---------------------------------------------------------------------------

CACHE_DIR = Path("data/macro_cache")

# USDA FoodData Central search endpoint, filtered to FNDDS (prepared/composite dishes)
_USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# USDA nutrient IDs we care about
_NUTRIENT_IDS = {
    "calories": 1008,   # Energy, kcal
    "carbs_g":  1005,   # Carbohydrate, by difference
    "fiber_g":  1079,   # Fiber, total dietary
    "protein_g": 1003,  # Protein
    "fat_g":    1004,   # Total lipid (fat)
}

# Simple pinyin → English fallback for common Chinese dishes with poor USDA coverage.
# Extend as needed; not intended to be exhaustive.
_PINYIN_FALLBACK: dict[str, str] = {
    # Chinese dish pinyin → searchable English
    "hong shao rou":          "red braised pork belly",
    "hong shao":              "red braised pork",
    "mapo tofu":              "mapo tofu spicy",
    "kung pao chicken":       "kung pao chicken",
    "char siu":               "chinese bbq pork",
    "congee":                 "rice porridge congee",
    "chow mein":              "chow mein noodles",
    "lo mein":                "lo mein noodles",
    "braised beef noodle":    "beef noodle soup taiwanese",
    "dried tofu sticks":      "dried tofu sticks chinese",
    "dan dan noodles":        "dan dan noodles spicy",
    "wontons":                "wonton soup",
    "tang yuan":              "glutinous rice balls sesame",
    "spring rolls":           "spring rolls fried",
    "dumplings":              "dumplings steamed",
    # Short or ambiguous queries that hit USDA 400s — expand to be more specific
    "brown rice":             "rice brown cooked",
    "white rice":             "rice white cooked",
    "purple rice":            "rice purple cooked",
    "bok choy":               "bok choy cooked",
    "kimchi":                 "kimchi fermented",
    "tofu":                   "tofu cooked",
    "egg":                    "egg cooked scrambled",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MacroResult:
    dish_name: str          # original query string
    usda_name: str          # matched USDA food description
    fdc_id: Optional[int]   # USDA FDC identifier
    calories: Optional[float]
    carbs_g: Optional[float]
    fiber_g: Optional[float]
    protein_g: Optional[float]
    fat_g: Optional[float]
    source: str             # "usda_api" | "cache" | "no_results"
    fetched_at: Optional[str] = None
    candidates: list[dict] = field(default_factory=list)  # top-3 raw USDA hits


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug(dish_name: str) -> str:
    """Convert dish name to a safe filename slug."""
    slug = dish_name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "_", slug)
    return slug


def _cache_path(dish_name: str) -> Path:
    return CACHE_DIR / f"{_slug(dish_name)}.json"


def _load_cache(dish_name: str) -> Optional[MacroResult]:
    path = _cache_path(dish_name)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    # user_override entries also live here (FOOD-013); always return them
    result = MacroResult(**{k: data[k] for k in MacroResult.__dataclass_fields__ if k in data})
    result.source = "cache"
    return result


def _save_cache(result: MacroResult) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(result.dish_name)
    with open(path, "w") as f:
        json.dump(asdict(result), f, indent=2)


def _get_api_key(api_key: Optional[str] = None) -> str:
    if api_key:
        return api_key
    env_key = os.environ.get("USDA_API_KEY")
    if env_key:
        return env_key
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        key = cfg.get("usda", {}).get("api_key")
        if key and key not in ("PLACEHOLDER", "YOUR_KEY_HERE"):
            return key
    # USDA provides "DEMO_KEY" for limited unauthenticated testing
    return "DEMO_KEY"


def _extract_nutrient(food_nutrients: list[dict], nutrient_id: int) -> Optional[float]:
    for n in food_nutrients:
        nid = n.get("nutrientId") or n.get("nutrient", {}).get("id")
        if nid == nutrient_id:
            return n.get("value")
    return None


def _parse_candidate(food: dict) -> dict:
    """Extract a lean candidate dict from a USDA search hit."""
    nutrients = food.get("foodNutrients", [])
    return {
        "fdc_id":     food.get("fdcId"),
        "usda_name":  food.get("description", ""),
        "data_type":  food.get("dataType", ""),
        "calories":   _extract_nutrient(nutrients, _NUTRIENT_IDS["calories"]),
        "carbs_g":    _extract_nutrient(nutrients, _NUTRIENT_IDS["carbs_g"]),
        "fiber_g":    _extract_nutrient(nutrients, _NUTRIENT_IDS["fiber_g"]),
        "protein_g":  _extract_nutrient(nutrients, _NUTRIENT_IDS["protein_g"]),
        "fat_g":      _extract_nutrient(nutrients, _NUTRIENT_IDS["fat_g"]),
    }


class _APIError(Exception):
    """Raised on auth/network failures (bad key, timeout) — caller skips caching."""


class _QueryError(Exception):
    """Raised on 400 Bad Request — USDA rejects this query string; caller should try fallback."""


def _query_usda(query: str, api_key: str) -> list[dict]:
    """
    Hit the USDA FNDDS search endpoint and return up to 3 parsed candidates.
    Returns [] when the API returns 0 foods for the query.
    Raises _QueryError on 400 (bad query, not bad key) so caller can try fallback.
    Raises _APIError on other HTTP / network errors so callers can skip caching.
    """
    params = {
        "query":    query,
        "dataType": "Survey (FNDDS)",
        "pageSize": 5,
        "api_key":  api_key,
    }
    try:
        resp = requests.get(_USDA_SEARCH_URL, params=params, timeout=10)
        if resp.status_code == 400:
            raise _QueryError(f"USDA rejected query '{query}' (400)")
        resp.raise_for_status()
    except _QueryError:
        raise
    except requests.HTTPError as exc:
        raise _APIError(f"USDA API HTTP error: {exc}") from exc
    except requests.RequestException as exc:
        raise _APIError(f"USDA API network error: {exc}") from exc

    foods = resp.json().get("foods", [])
    return [_parse_candidate(f) for f in foods[:3]]


def _build_result(dish_name: str, candidates: list[dict], source: str) -> MacroResult:
    """Build a MacroResult from candidates list, selecting index 0 as default."""
    if not candidates:
        return MacroResult(
            dish_name=dish_name,
            usda_name="",
            fdc_id=None,
            calories=None,
            carbs_g=None,
            fiber_g=None,
            protein_g=None,
            fat_g=None,
            source="no_results",
            fetched_at=_now_iso(),
            candidates=[],
        )
    best = candidates[0]
    return MacroResult(
        dish_name=dish_name,
        usda_name=best["usda_name"],
        fdc_id=best["fdc_id"],
        calories=best["calories"],
        carbs_g=best["carbs_g"],
        fiber_g=best["fiber_g"],
        protein_g=best["protein_g"],
        fat_g=best["fat_g"],
        source=source,
        fetched_at=_now_iso(),
        candidates=candidates,
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_macros(dish_name: str, api_key: Optional[str] = None) -> MacroResult:
    """
    Return macros (per 100g) for a confirmed dish name.

    1. Returns cached result immediately if available.
    2. Queries USDA FNDDS with the dish name.
    3. Falls back to a pinyin → English translation for Chinese dishes.
    4. Returns a no_results MacroResult if no match found (triggers FOOD-013).

    Args:
        dish_name: Confirmed dish label, e.g. "braised_beef_noodle" or "brown rice".
        api_key:   USDA API key. If None, reads from USDA_API_KEY env var,
                   config.yaml, or falls back to DEMO_KEY.
    """
    # Normalise underscores → spaces for readability in queries
    query_name = dish_name.replace("_", " ").strip()

    # 1. Cache hit
    cached = _load_cache(dish_name)
    if cached is not None:
        return cached

    key = _get_api_key(api_key)

    # 2. Primary query (English); on 400 fall through to fallback immediately
    candidates: list[dict] = []
    try:
        candidates = _query_usda(query_name, key)
    except _QueryError:
        pass  # fall through to fallback below
    except _APIError as exc:
        print(f"[macro_lookup] {exc} — result not cached; provide a valid USDA API key")
        return _build_result(dish_name, [], source="no_results")

    # 3. Fallback: try pinyin/expanded query when 0 results OR query was rejected (400)
    if not candidates:
        fallback_query = _PINYIN_FALLBACK.get(query_name.lower())
        if fallback_query:
            print(f"[macro_lookup] Trying fallback query for '{query_name}': '{fallback_query}'")
            try:
                candidates = _query_usda(fallback_query, key)
            except (_QueryError, _APIError) as exc:
                print(f"[macro_lookup] Fallback also failed: {exc}")

    result = _build_result(dish_name, candidates, source="usda_api")
    # Cache hits and genuine no_results alike (dish truly absent from USDA)
    _save_cache(result)
    return result


def select_candidate(dish_name: str, candidate_index: int) -> MacroResult:
    """
    Update the cached result to use a different candidate from the top-3.

    Call this when the user picks candidate 1 or 2 from the UI instead of
    the default top result.

    Args:
        dish_name:       Must match an existing cache entry.
        candidate_index: 0-based index into MacroResult.candidates.
    """
    cached = _load_cache(dish_name)
    if cached is None:
        raise ValueError(f"No cached result for '{dish_name}'. Call lookup_macros() first.")
    if not cached.candidates:
        raise ValueError(f"No candidates stored for '{dish_name}'.")
    if candidate_index >= len(cached.candidates):
        raise IndexError(f"candidate_index {candidate_index} out of range (have {len(cached.candidates)})")

    chosen = cached.candidates[candidate_index]
    updated = MacroResult(
        dish_name=dish_name,
        usda_name=chosen["usda_name"],
        fdc_id=chosen["fdc_id"],
        calories=chosen["calories"],
        carbs_g=chosen["carbs_g"],
        fiber_g=chosen["fiber_g"],
        protein_g=chosen["protein_g"],
        fat_g=chosen["fat_g"],
        source=cached.source,
        fetched_at=cached.fetched_at,
        candidates=cached.candidates,
    )
    _save_cache(updated)
    return updated


def clear_cache(dish_name: str) -> None:
    """Delete the cache file for a dish (forces a fresh API call next lookup)."""
    path = _cache_path(dish_name)
    if path.exists():
        path.unlink()
        print(f"[macro_lookup] Cache cleared for '{dish_name}'")
    else:
        print(f"[macro_lookup] No cache found for '{dish_name}'")
