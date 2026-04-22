"""
FOOD-013: Manual macro entry and override flow.

Layered storage — lookup priority (highest to lowest):
  1. data/overrides/{slug}.json    source: "user_override"
  2. data/macro_cache/{slug}.json  source: "usda_api" | "estimated" | "no_results"
  3. None                          → caller must call lookup_macros() or prompt manual entry

All macros are stored per `reference_weight_g` (default 100 g) so FOOD-014 can
scale to estimated portion size without re-fetching.

Atomic writes use write-to-temp-then-rename so a crash mid-write never leaves a
half-written JSON file.

Normalization is imported from pipeline.feedback.normalize_dish_name — the same
function used by the feedback loop — so "Beef Stew" and "beef_stew" always resolve
to the same file: beef_stew.json.

Public API:
    set_manual_override(dish_name, macros, reference_weight_g=100) -> dict
    get_macros(dish_name) -> dict | None
    reset_override(dish_name) -> bool
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from pipeline.feedback import normalize_dish_name

# ---------------------------------------------------------------------------
# Storage roots
# ---------------------------------------------------------------------------

OVERRIDES_DIR = Path("data/overrides")
CACHE_DIR = Path("data/macro_cache")

# Required macro fields — fiber_g is mandatory for net-carb tracking (T1D safety)
_MACRO_FIELDS = ("calories", "carbs_g", "fiber_g", "protein_g", "fat_g")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug(dish_name: str) -> str:
    return normalize_dish_name(dish_name)


def _override_path(dish_name: str) -> Path:
    return OVERRIDES_DIR / f"{_slug(dish_name)}.json"


def _cache_path(dish_name: str) -> Path:
    return CACHE_DIR / f"{_slug(dish_name)}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically: write to .tmp sibling then os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _validate_macros(macros: dict) -> None:
    """
    Raise ValueError if any required macro field is missing, non-numeric, or negative.
    """
    for field in _MACRO_FIELDS:
        if field not in macros:
            raise ValueError(f"Missing required macro field: '{field}'")
        val = macros[field]
        if not isinstance(val, (int, float)):
            raise ValueError(
                f"Macro field '{field}' must be numeric, got {type(val).__name__}: {val!r}"
            )
        if val < 0:
            raise ValueError(
                f"Macro field '{field}' must be >= 0, got {val}"
            )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path: Path) -> Optional[dict]:
    """Return parsed JSON from path, or None if the file doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_manual_override(
    dish_name: str,
    macros: dict,
    reference_weight_g: float = 100.0,
) -> dict:
    """
    Save a manual macro entry for a dish to data/overrides/.

    Args:
        dish_name:          Any casing/spacing — normalized before saving.
        macros:             Dict with keys: calories, carbs_g, fiber_g, protein_g, fat_g.
                            All values must be numeric and >= 0.
        reference_weight_g: Gram weight the macros are relative to (default 100 g).
                            Must be > 0. FOOD-014 uses this to scale to portion size.

    Returns:
        The saved override dict (same schema written to disk).

    Raises:
        ValueError: if any macro field is missing, non-numeric, negative,
                    or if reference_weight_g <= 0.
    """
    if not isinstance(reference_weight_g, (int, float)) or reference_weight_g <= 0:
        raise ValueError(
            f"reference_weight_g must be a positive number, got {reference_weight_g!r}"
        )

    _validate_macros(macros)

    slug = _slug(dish_name)
    record = {
        "dish_name":          slug,
        "source":             "user_override",
        "reference_weight_g": reference_weight_g,
        "calories":           macros["calories"],
        "carbs_g":            macros["carbs_g"],
        "fiber_g":            macros["fiber_g"],
        "protein_g":          macros["protein_g"],
        "fat_g":              macros["fat_g"],
        "saved_at":           _now_iso(),
    }

    _atomic_write(_override_path(dish_name), record)
    return record


def get_macros(dish_name: str) -> Optional[dict]:
    """
    Return the macro dict for a dish using layered priority:
      1. data/overrides/{slug}.json   (user_override — highest priority)
      2. data/macro_cache/{slug}.json (usda_api / estimated)
      3. None                         (no data — call lookup_macros or set_manual_override)

    The returned dict always contains: dish_name, source, calories, carbs_g,
    fiber_g, protein_g, fat_g, and reference_weight_g (100 g for API cache entries
    that predate FOOD-013, since USDA returns per-100g values).

    Args:
        dish_name: Any casing/spacing — normalized before lookup.
    """
    # Layer 1: user override
    override = _load_json(_override_path(dish_name))
    if override is not None:
        return override

    # Layer 2: API cache
    cached = _load_json(_cache_path(dish_name))
    if cached is not None:
        # USDA cache files written before FOOD-013 don't have reference_weight_g —
        # inject the default since USDA always returns per-100g values.
        cached.setdefault("reference_weight_g", 100.0)
        return cached

    # Layer 3: no data
    return None


def reset_override(dish_name: str) -> bool:
    """
    Delete the override file for a dish, reverting get_macros() to the API cache.

    Args:
        dish_name: Any casing/spacing — normalized before lookup.

    Returns:
        True if an override existed and was deleted, False if nothing was found.
    """
    path = _override_path(dish_name)
    if path.exists():
        path.unlink()
        return True
    return False
