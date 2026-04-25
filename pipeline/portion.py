"""
FOOD-014: Portion size estimation from crop dimensions.

Layered estimation — single dish vs. mixed bowl:
  single_dish: mask_pixels / image_pixels → small/medium/large bucket → grams
  mixed_bowl:  same bucket against bowl_total → split by role weights per component

All thresholds and gram defaults live in config.yaml under `portion_defaults`.
Multiplier (0.5x/1x/1.5x/2x) is persisted per dish in data/overrides/ or
data/macro_cache/ as an optional `portion_multiplier` field.

Public API:
    estimate_portion(crop_result, dish_name, role=None)   -> dict
    estimate_portions_mixed(crop_result)                  -> dict
    apply_multiplier(dish_name, multiplier)               -> dict
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

# Imported from nutrition.py so we reuse the same storage helpers and paths
from pipeline.nutrition import (
    CACHE_DIR,
    OVERRIDES_DIR,
    _atomic_write,  # noqa: F401 — private but stable; reuse to avoid duplication
    _load_json,
    _slug,
)
from pipeline.nutrition import get_macros


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_portion_cfg(config_path: str = "config.yaml") -> dict:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["portion_defaults"]


# ---------------------------------------------------------------------------
# Bucket helpers
# ---------------------------------------------------------------------------

def _pixel_bucket(mask_pixels: int, image_pixels: int, cfg: dict) -> str:
    """Map mask_pixels / image_pixels ratio to 'small' | 'medium' | 'large'."""
    ratio = mask_pixels / image_pixels
    thresholds = cfg["bucket_thresholds"]
    if ratio < thresholds["small_max"]:
        return "small"
    if ratio < thresholds["medium_max"]:
        return "medium"
    return "large"


# ---------------------------------------------------------------------------
# Category resolver
# ---------------------------------------------------------------------------

_GRAIN_KEYWORDS    = {"rice", "noodle", "noodles", "congee", "grain", "bread", "pasta"}
_PROTEIN_KEYWORDS  = {"beef", "pork", "chicken", "tofu", "fish", "egg", "shrimp", "lamb", "duck"}
_VEG_KEYWORDS      = {"bok_choy", "bok", "choy", "spinach", "broccoli", "vegetable",
                       "vegetables", "greens", "cabbage", "carrot"}


def _dish_category(dish_name: Optional[str], role: Optional[str]) -> str:
    """
    Resolve dish category for gram-table lookup.

    Priority:
      1. role arg (from mixed_bowl component): base→grain, protein/vegetable passed through
      2. keyword scan of dish_name
      3. fallback: 'default'
    """
    if role is not None:
        if role == "base":
            return "grain"
        if role in ("protein", "vegetable"):
            return role

    if dish_name:
        tokens = set(dish_name.lower().replace("-", "_").split("_"))
        if tokens & _GRAIN_KEYWORDS:
            return "grain"
        if tokens & _PROTEIN_KEYWORDS:
            return "protein"
        if tokens & _VEG_KEYWORDS:
            return "vegetable"

    return "default"


# ---------------------------------------------------------------------------
# Multiplier helpers
# ---------------------------------------------------------------------------

_VALID_MULTIPLIERS = {0.5, 1.0, 1.5, 2.0}


def _read_multiplier(dish_name: str) -> float:
    """Read persisted portion_multiplier for a dish (default 1.0)."""
    override = _load_json(OVERRIDES_DIR / f"{_slug(dish_name)}.json")
    if override is not None:
        return float(override.get("portion_multiplier", 1.0))
    cached = _load_json(CACHE_DIR / f"{_slug(dish_name)}.json")
    if cached is not None:
        return float(cached.get("portion_multiplier", 1.0))
    return 1.0


# ---------------------------------------------------------------------------
# Macro scaling
# ---------------------------------------------------------------------------

def _scale_macros(macros: dict, portion_g: float) -> Optional[dict]:
    """
    Scale per-reference_weight_g macros to portion_g.

    Returns None if any required macro field is missing or None in the source.
    """
    ref_g = macros.get("reference_weight_g", 100.0)
    if not ref_g:
        return None
    scale = portion_g / ref_g
    fields = ("calories", "carbs_g", "fiber_g", "protein_g", "fat_g")
    result = {}
    for f in fields:
        val = macros.get(f)
        if val is None:
            return None
        result[f] = round(val * scale, 2)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_portion(
    crop_result: dict,
    dish_name: str,
    role: Optional[str] = None,
    config_path: str = "config.yaml",
) -> dict:
    """
    Estimate portion size for a single-dish crop.

    Args:
        crop_result:  Output from segment_meal() or classify_components():
                      must contain 'mask_pixels' and 'image_pixels'.
        dish_name:    Normalized dish name (used for macro lookup and category).
        role:         Optional role hint from mixed_bowl component
                      ('base' | 'protein' | 'vegetable'). Overrides keyword scan.
        config_path:  Path to config.yaml (injectable for tests).

    Returns:
        {
            "dish_name": str,
            "portion_bucket": "small" | "medium" | "large",
            "portion_g": float,
            "multiplier": float,
            "macros_scaled": dict | None,
            "needs_macro_entry": bool,
        }
    """
    cfg = _load_portion_cfg(config_path)
    bucket = _pixel_bucket(crop_result["mask_pixels"], crop_result["image_pixels"], cfg)
    category = _dish_category(dish_name, role)
    base_g = cfg[category][bucket]

    multiplier = _read_multiplier(dish_name)
    portion_g = round(base_g * multiplier, 1)

    macros = get_macros(dish_name)
    needs_macro_entry = (
        macros is None or macros.get("source") == "no_results"
    )
    macros_scaled = None if needs_macro_entry else _scale_macros(macros, portion_g)

    return {
        "dish_name":        dish_name,
        "portion_bucket":   bucket,
        "portion_g":        portion_g,
        "multiplier":       multiplier,
        "macros_scaled":    macros_scaled,
        "needs_macro_entry": needs_macro_entry,
    }


def estimate_portions_mixed(
    crop_result: dict,
    config_path: str = "config.yaml",
) -> list[dict]:
    """
    Estimate portion size for each component in a mixed-bowl crop.

    Uses bowl_total gram values (not per-category defaults) as the total base,
    then splits by role weights to compute per-component portion_fraction and
    portion_g.

    Args:
        crop_result:  Output from classify_components() with crop_type='mixed_bowl'.
                      Must contain 'mask_pixels', 'image_pixels', and 'components'.
        config_path:  Path to config.yaml (injectable for tests).

    Returns:
        {
            "components": list[dict],   # one dict per component (same schema as estimate_portion
                                        # return, plus role and portion_fraction; partial_detection
                                        # added only in guard path)
            "warnings": list[dict],     # empty list when 2+ roles detected; one entry when guard
                                        # fires (reason="only_base_detected")
        }
    """
    cfg = _load_portion_cfg(config_path)
    components = crop_result.get("components", [])

    # ------------------------------------------------------------------
    # Partial detection guard — FOOD-005b failure mode where CLIP
    # sentinels win for protein/vegetable, leaving only base detected.
    # Use single-dish grain gram table (not bowl_total) so the lone base
    # component is not inflated to a full-bowl weight.
    # ------------------------------------------------------------------
    if len(components) == 1 and components[0]["role"] == "base":
        bucket = _pixel_bucket(crop_result["mask_pixels"], crop_result["image_pixels"], cfg)
        dish_name = components[0]["dish_name"]
        category = _dish_category(dish_name, role="base")
        base_g = cfg[category][bucket]
        multiplier = _read_multiplier(dish_name)
        portion_g = round(base_g * multiplier, 1)

        macros = get_macros(dish_name)
        needs_macro_entry = macros is None or macros.get("source") == "no_results"
        macros_scaled = None if needs_macro_entry else _scale_macros(macros, portion_g)

        component_dict = {
            "dish_name":         dish_name,
            "role":              "base",
            "portion_fraction":  1.0,
            "portion_bucket":    bucket,
            "portion_g":         portion_g,
            "multiplier":        multiplier,
            "macros_scaled":     macros_scaled,
            "needs_macro_entry": needs_macro_entry,
            "partial_detection": True,
        }
        warnings = [
            {
                "reason":        "only_base_detected",
                "message":       "Protein and vegetable roles not detected. User confirmation required.",
                "affected_dish": dish_name,
            }
        ]
        return {"components": [component_dict], "warnings": warnings}

    # Bowl total grams from bowl_total config entry
    bucket = _pixel_bucket(crop_result["mask_pixels"], crop_result["image_pixels"], cfg)
    bowl_total_g = cfg["bowl_total"][bucket]

    # Weighted fractions
    role_weights = cfg["role_weights"]
    weights = [role_weights.get(c["role"], role_weights["default"]) for c in components]
    total_weight = sum(weights)

    results = []
    for component, weight in zip(components, weights):
        fraction = weight / total_weight
        portion_g = round(bowl_total_g * fraction, 1)
        dish_name = component["dish_name"]
        role = component["role"]

        multiplier = _read_multiplier(dish_name)
        portion_g_adjusted = round(portion_g * multiplier, 1)

        macros = get_macros(dish_name)
        needs_macro_entry = macros is None or macros.get("source") == "no_results"
        macros_scaled = None if needs_macro_entry else _scale_macros(macros, portion_g_adjusted)

        results.append({
            "dish_name":         dish_name,
            "role":              role,
            "portion_fraction":  round(fraction, 4),
            "portion_bucket":    bucket,
            "portion_g":         portion_g_adjusted,
            "multiplier":        multiplier,
            "macros_scaled":     macros_scaled,
            "needs_macro_entry": needs_macro_entry,
        })

    return {"components": results, "warnings": []}


def apply_multiplier(
    dish_name: str,
    multiplier: float,
    config_path: str = "config.yaml",
) -> dict:
    """
    Persist a portion multiplier for a dish.

    Updates the existing override or cache file in place. If neither exists,
    creates a multiplier-only override entry (no macro validation — macro
    fields are set separately via set_manual_override()).

    Args:
        dish_name:   Any casing/spacing — normalized before saving.
        multiplier:  Must be one of {0.5, 1.0, 1.5, 2.0}.
        config_path: Unused; kept for API symmetry with other functions.

    Returns:
        The written record dict.

    Raises:
        ValueError: if multiplier is not in {0.5, 1.0, 1.5, 2.0}.
    """
    if multiplier not in _VALID_MULTIPLIERS:
        raise ValueError(
            f"multiplier must be one of {sorted(_VALID_MULTIPLIERS)}, got {multiplier!r}"
        )

    slug = _slug(dish_name)
    override_path = OVERRIDES_DIR / f"{slug}.json"
    cache_path    = CACHE_DIR    / f"{slug}.json"

    # Prefer updating an existing file so we don't lose macro data
    if override_path.exists():
        record = json.loads(override_path.read_text())
        record["portion_multiplier"] = multiplier
        _atomic_write(override_path, record)
        return record

    if cache_path.exists():
        record = json.loads(cache_path.read_text())
        record["portion_multiplier"] = multiplier
        _atomic_write(cache_path, record)
        return record

    # No existing file — create a multiplier-only override entry.
    # Bypass _validate_macros() intentionally: this is NOT a macro override.
    record = {
        "dish_name":          slug,
        "source":             "user_override",
        "portion_multiplier": multiplier,
    }
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(override_path, record)
    return record
