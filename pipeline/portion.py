"""
FOOD-014: Portion size estimation from crop dimensions.

Layered estimation — single dish vs. mixed bowl:
  single_dish: mask_pixels / image_pixels → small/medium/large bucket → grams
  mixed_bowl:  same bucket against bowl_total → split by role weights per component

All thresholds and gram defaults live in config.yaml under `portion_defaults`.
Multiplier (0.5x/1x/1.5x/2x) is persisted per dish in data/overrides/ or
data/macro_cache/ as an optional `portion_multiplier` field.

FOOD-020 adds a durable per-dish portion prior in data/portion_priors/,
read as the new highest-priority layer ahead of overrides/macro_cache — see
plans/FOOD-020-plan.md.

Public API:
    estimate_portion(crop_result, dish_name, role=None)   -> dict
    estimate_portions_mixed(crop_result)                  -> dict
    apply_multiplier(dish_name, multiplier)                -> dict
    save_portion_prior(dish_name, factor, reason, direction) -> dict
    touch_portion_prior(dish_name)                          -> None
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
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

# FOOD-020: durable per-dish portion priors — see plans/FOOD-020-plan.md.
PRIORS_DIR = Path("data/portion_priors")

# PERSISTED_REASONS describe the DISH ("this dish's real portion is
# different from the pixel estimate") and teach a prior. COUNTED_REASONS
# describe the MEAL in front of the user right now ("I didn't finish this
# one plate") and must never influence a future scan of the same dish —
# enforced here, not by the caller (D4).
PERSISTED_REASONS = {"portion", "broth", "hidden"}
COUNTED_REASONS = {"leftover"}

_MULTIPLIER_MIN, _MULTIPLIER_MAX = 0.5, 2.0
_ACTIVATION_THRESHOLD = 2
_PRIOR_STALE_DAYS = 180

# too_low factors are the reciprocals of too_high so that a correction and
# its opposite return the multiplier to exactly 1.0 (D6) — the design's
# symmetric 15%/40% (results.jsx:612) is NOT invertible and was rejected.
_FACTORS = {
    ("too_high", "little"): 0.85,
    ("too_low", "little"): 1 / 0.85,
    ("too_high", "lot"): 0.60,
    ("too_low", "lot"): 1 / 0.60,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_stale(record: dict) -> bool:
    """A prior stops being applied once its dish hasn't been scanned in
    _PRIOR_STALE_DAYS (D7) — measured from last_scanned, not last_updated,
    so a prior that's still correct (and so never corrected) never expires."""
    last_scanned = record.get("last_scanned")
    if not last_scanned:
        return False
    try:
        scanned_at = datetime.strptime(last_scanned, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - scanned_at).days > _PRIOR_STALE_DAYS


def _clamp_multiplier(value: float) -> float:
    return round(max(_MULTIPLIER_MIN, min(_MULTIPLIER_MAX, value)), 2)


def _prior_path(dish_name: str) -> Path:
    return PRIORS_DIR / f"{_slug(dish_name)}.json"


def save_portion_prior(
    dish_name: str,
    factor: float,
    reason: Optional[str],
    direction: str,
) -> dict:
    """
    Persist one correction (or confirmation) as evidence about a dish's
    portion size, and — once there's enough evidence — activate a learned
    multiplier. See plans/FOOD-020-plan.md D4/D5/D6/D8 for the full decision
    log behind every branch here.

    Args:
        dish_name: Any casing/spacing — normalized before saving.
        factor:    The multiplier this single correction represents (e.g.
                   one of _FACTORS' values), or 1.0 for direction="looks_right".
        reason:    One of PERSISTED_REASONS | COUNTED_REASONS for a
                   correction; must be None for "looks_right".
        direction: "too_high" | "too_low" | "looks_right".

    Returns:
        The written record dict, with a "prior_state" key describing what
        just happened: "counted" | "pending" | "activated" | "compounded" |
        "cleared" | "confirmed".

    Raises:
        ValueError: reason/direction combination isn't recognized, or
                    "looks_right" is given a reason or a non-1.0 factor.
    """
    if direction not in ("too_high", "too_low", "looks_right"):
        raise ValueError(f"unknown direction {direction!r}")

    if direction == "looks_right":
        if reason is not None:
            raise ValueError("looks_right must not carry a reason")
        if factor != 1.0:
            raise ValueError("looks_right must carry factor=1.0")
    elif reason not in PERSISTED_REASONS | COUNTED_REASONS:
        raise ValueError(
            f"unknown reason {reason!r} for direction {direction!r}; "
            f"must be one of {sorted(PERSISTED_REASONS | COUNTED_REASONS)}"
        )

    slug = _slug(dish_name)
    path = _prior_path(dish_name)
    record = _load_json(path)
    if record is None:
        record = {"dish_name": slug, "active": False, "n_corrections": 0, "reasons": {}}

    now = _now_iso()
    record["last_updated"] = now
    record.setdefault("last_scanned", now)

    if direction == "looks_right":
        if record.get("pending_direction") is not None:
            record.pop("pending_direction", None)
            record.pop("pending_factor", None)
            record["prior_state"] = "cleared"
        else:
            record["prior_state"] = "confirmed"
        # An active prior is left untouched — "looks right" about the
        # CORRECTED number confirms the prior is working, not that it
        # should be discarded (D8).
        _atomic_write(path, record)
        return record

    # too_high / too_low: evidence always accumulates, even for a counted
    # (leftover) reason — only whether it ever becomes a multiplier differs.
    record["n_corrections"] = record.get("n_corrections", 0) + 1
    record["reasons"][reason] = record["reasons"].get(reason, 0) + 1

    if reason in COUNTED_REASONS:
        record["prior_state"] = "counted"
        _atomic_write(path, record)
        return record

    active = record.get("active", False)
    pending_direction = record.get("pending_direction")

    if active:
        current = record.get("portion_multiplier", 1.0)
        record["portion_multiplier"] = _clamp_multiplier(current * factor)
        record["prior_state"] = "compounded"
    elif pending_direction is None:
        record["pending_direction"] = direction
        record["pending_factor"] = factor
        record["prior_state"] = "pending"
    elif pending_direction == direction:
        record["portion_multiplier"] = _clamp_multiplier(1.0 * factor)
        record["active"] = True
        record.pop("pending_direction", None)
        record.pop("pending_factor", None)
        record["prior_state"] = "activated"
    else:
        # Conflicting direction: the user changed their mind. Two
        # contradictory corrections are not two pieces of evidence, so
        # replace the pending evidence and reset the tally (D5).
        record["pending_direction"] = direction
        record["pending_factor"] = factor
        record["n_corrections"] = 1
        record["reasons"] = {reason: 1}
        record["prior_state"] = "pending"

    _atomic_write(path, record)
    return record


def touch_portion_prior(dish_name: str) -> None:
    """Update last_scanned on an existing prior record (D7). No-op if the
    dish has no prior yet. Deliberately not called from _read_multiplier(),
    which stays a pure read — call this from the scan-completion path."""
    path = _prior_path(dish_name)
    record = _load_json(path)
    if record is None:
        return
    record["last_scanned"] = _now_iso()
    _atomic_write(path, record)


def _read_multiplier(dish_name: str) -> float:
    """Read persisted portion_multiplier for a dish (default 1.0).

    Layer order: priors > overrides > macro_cache (highest priority first).
    Every layer falls through when the file is present but lacks a
    portion_multiplier key (fixes the early-return bug where a macro-only
    override — e.g. a real data/overrides/mapo_tofu.json with no multiplier
    key — used to short-circuit to 1.0 instead of falling through to a
    multiplier stored underneath it). A stale prior (D7) is skipped, not
    deleted — it remains as evidence for save_portion_prior()."""
    slug = _slug(dish_name)
    for directory in (PRIORS_DIR, OVERRIDES_DIR, CACHE_DIR):
        record = _load_json(directory / f"{slug}.json")
        if record is None or "portion_multiplier" not in record:
            continue
        if directory is PRIORS_DIR and _is_stale(record):
            continue
        return float(record["portion_multiplier"])
    return 1.0


# ---------------------------------------------------------------------------
# Macro scaling
# ---------------------------------------------------------------------------

def scale_macros(macros: dict, portion_g: float) -> Optional[dict]:
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
    macros_scaled = None if needs_macro_entry else scale_macros(macros, portion_g)

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
        macros_scaled = None if needs_macro_entry else scale_macros(macros, portion_g)

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
        macros_scaled = None if needs_macro_entry else scale_macros(macros, portion_g_adjusted)

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
