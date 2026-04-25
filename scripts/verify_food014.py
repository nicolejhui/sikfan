"""
FOOD-014 verification: Portion size estimation from crop dimensions.

Tests:
  1. Bucket mapping from config thresholds (small/medium/large)
  2. Category → grams (grain, protein, vegetable, default)
  3. Mixed-bowl weighted fractions (base=2x, protein/veg=1x)
  4. Multiplier apply / persist / read-back
  5. None / no_results graceful handling (no crash, needs_macro_entry=True)
  6. macros_scaled arithmetic correctness
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline.nutrition as nutrition
import pipeline.portion as portion_module
from pipeline.portion import (
    apply_multiplier,
    estimate_portion,
    estimate_portions_mixed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ok(msg: str) -> None:
    print(f"  PASS  {msg}")

def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)

def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Config fixture — keeps tests independent of real config.yaml values
# ---------------------------------------------------------------------------

_TEST_CFG = {
    "bucket_thresholds": {"small_max": 0.15, "medium_max": 0.35},
    "bowl_total":        {"small": 300, "medium": 500, "large": 700},
    "role_weights":      {"base": 2.0, "protein": 1.0, "vegetable": 1.0, "default": 1.0},
    "grain":             {"small": 100, "medium": 180, "large": 280},
    "protein":           {"small":  80, "medium": 150, "large": 220},
    "vegetable":         {"small":  60, "medium": 120, "large": 180},
    "default":           {"small":  90, "medium": 160, "large": 240},
}

def _patch_dirs(tmp: str):
    overrides = Path(tmp) / "overrides"
    cache     = Path(tmp) / "macro_cache"
    overrides.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    nutrition.OVERRIDES_DIR      = overrides
    portion_module.OVERRIDES_DIR = overrides
    nutrition.CACHE_DIR          = cache
    portion_module.CACHE_DIR     = cache
    return overrides, cache


def _crop(mask_pixels: int, image_pixels: int = 10_000) -> dict:
    """Minimal crop_result dict for single-dish tests."""
    return {"mask_pixels": mask_pixels, "image_pixels": image_pixels}


def _mixed_crop(mask_pixels: int, image_pixels: int, components: list[dict]) -> dict:
    return {
        "crop_type":    "mixed_bowl",
        "mask_pixels":  mask_pixels,
        "image_pixels": image_pixels,
        "components":   components,
    }


# ---------------------------------------------------------------------------
# Test 1 — Bucket mapping from config thresholds
# ---------------------------------------------------------------------------

section("Test 1 — Bucket mapping from config thresholds")

with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
     patch("pipeline.portion.get_macros", return_value=None):

    # ratio = 0.10 → small (< 0.15)
    r = estimate_portion(_crop(1000, 10_000), "kimchi")
    assert r["portion_bucket"] == "small", f"Expected small, got {r['portion_bucket']}"
    ok("ratio=0.10 → small")

    # ratio = 0.20 → medium (0.15–0.35)
    r = estimate_portion(_crop(2000, 10_000), "kimchi")
    assert r["portion_bucket"] == "medium", f"Expected medium, got {r['portion_bucket']}"
    ok("ratio=0.20 → medium")

    # ratio = 0.50 → large (>= 0.35)
    r = estimate_portion(_crop(5000, 10_000), "kimchi")
    assert r["portion_bucket"] == "large", f"Expected large, got {r['portion_bucket']}"
    ok("ratio=0.50 → large")

    # Verify thresholds are config-driven: change thresholds and see different bucket
    alt_cfg = dict(_TEST_CFG, bucket_thresholds={"small_max": 0.05, "medium_max": 0.15})
    with patch.object(portion_module, "_load_portion_cfg", return_value=alt_cfg):
        r = estimate_portion(_crop(1000, 10_000), "kimchi")
        assert r["portion_bucket"] == "medium", (
            f"With small_max=0.05, ratio=0.10 should be medium, got {r['portion_bucket']}"
        )
        ok("alt thresholds: ratio=0.10 with small_max=0.05 → medium (config-driven)")


# ---------------------------------------------------------------------------
# Test 2 — Category → grams
# ---------------------------------------------------------------------------

section("Test 2 — Category → grams (grain / protein / vegetable / default)")

with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
     patch("pipeline.portion.get_macros", return_value=None):

    # grain: "white_rice" → grain → medium=180
    r = estimate_portion(_crop(2000, 10_000), "white_rice")
    assert r["portion_g"] == 180.0, f"Expected 180g for white_rice medium, got {r['portion_g']}"
    ok("white_rice medium → 180g (grain category)")

    # protein: "braised_beef" → protein → medium=150
    r = estimate_portion(_crop(2000, 10_000), "braised_beef")
    assert r["portion_g"] == 150.0, f"Expected 150g for braised_beef medium, got {r['portion_g']}"
    ok("braised_beef medium → 150g (protein category)")

    # vegetable: "bok_choy" → vegetable → medium=120
    r = estimate_portion(_crop(2000, 10_000), "bok_choy")
    assert r["portion_g"] == 120.0, f"Expected 120g for bok_choy medium, got {r['portion_g']}"
    ok("bok_choy medium → 120g (vegetable category)")

    # default: "hong_shao_rou" → no keyword match → default → medium=160
    r = estimate_portion(_crop(2000, 10_000), "hong_shao_rou")
    assert r["portion_g"] == 160.0, f"Expected 160g for hong_shao_rou medium, got {r['portion_g']}"
    ok("hong_shao_rou medium → 160g (default category)")

    # role arg overrides keyword scan: dish_name="chicken_xyz", role="base" → grain large
    r = estimate_portion(_crop(5000, 10_000), "chicken_xyz", role="base")
    assert r["portion_g"] == 280.0, (
        f"role='base' should override keyword and use grain large=280, got {r['portion_g']}"
    )
    ok("role='base' overrides keyword scan → grain large=280g")


# ---------------------------------------------------------------------------
# Test 3 — Mixed-bowl weighted fractions
# ---------------------------------------------------------------------------

section("Test 3 — Mixed-bowl weighted fractions")

with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
     patch("pipeline.portion.get_macros", return_value=None):

    # 2-component bowl: base(2) + protein(1) → fractions 0.6667 / 0.3333
    # bucket: ratio=0.50 → large → bowl_total=700g
    components_2 = [
        {"role": "base",    "dish_name": "purple_rice"},
        {"role": "protein", "dish_name": "braised_beef"},
    ]
    out_2 = estimate_portions_mixed(_mixed_crop(5000, 10_000, components_2))
    results = out_2["components"]
    assert len(results) == 2
    assert not out_2["warnings"], f"Expected no warnings for 2-component bowl, got {out_2['warnings']}"
    assert abs(results[0]["portion_fraction"] - 2/3) < 0.001, (
        f"base fraction expected ~0.667, got {results[0]['portion_fraction']}"
    )
    assert abs(results[1]["portion_fraction"] - 1/3) < 0.001, (
        f"protein fraction expected ~0.333, got {results[1]['portion_fraction']}"
    )
    assert results[0]["portion_g"] == round(700 * 2/3, 1)
    assert results[1]["portion_g"] == round(700 * 1/3, 1)
    ok("base+protein (2+1=3): fractions 0.667/0.333, grams from bowl_total=700")

    # 3-component bowl: base(2) + protein(1) + vegetable(1) → 0.50/0.25/0.25
    # bucket: ratio=0.20 → medium → bowl_total=500g
    components_3 = [
        {"role": "base",      "dish_name": "white_rice"},
        {"role": "protein",   "dish_name": "braised_beef"},
        {"role": "vegetable", "dish_name": "bok_choy"},
    ]
    out_3 = estimate_portions_mixed(_mixed_crop(2000, 10_000, components_3))
    results = out_3["components"]
    assert abs(results[0]["portion_fraction"] - 0.50) < 0.001
    assert abs(results[1]["portion_fraction"] - 0.25) < 0.001
    assert abs(results[2]["portion_fraction"] - 0.25) < 0.001
    assert results[0]["portion_g"] == 250.0   # 500 * 0.50
    assert results[1]["portion_g"] == 125.0   # 500 * 0.25
    assert results[2]["portion_g"] == 125.0   # 500 * 0.25
    ok("base+protein+vegetable (2+1+1=4): fractions 0.50/0.25/0.25, grams from bowl_total=500")


# ---------------------------------------------------------------------------
# Test 4 — Multiplier apply / persist / read-back
# ---------------------------------------------------------------------------

section("Test 4 — apply_multiplier persists and estimate_portion reads it back")

with tempfile.TemporaryDirectory() as tmp:
    _patch_dirs(tmp)

    with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
         patch("pipeline.portion.get_macros", return_value=None):

        # Apply 1.5x to kimchi — no prior file, creates multiplier-only override
        record = apply_multiplier("kimchi", 1.5)
        assert record["portion_multiplier"] == 1.5
        ok("apply_multiplier('kimchi', 1.5) returns record with portion_multiplier=1.5")

        override_file = nutrition.OVERRIDES_DIR / "kimchi.json"
        assert override_file.exists(), "Multiplier-only override file not created"
        saved = json.loads(override_file.read_text())
        assert saved["portion_multiplier"] == 1.5
        assert saved["source"] == "user_override"
        assert "calories" not in saved, "Multiplier-only entry must not include macro fields"
        ok("override file has portion_multiplier=1.5, source=user_override, no macro fields")

        # estimate_portion reads back the 1.5x multiplier
        # default bucket = medium (ratio=0.20), category = default → 160g base
        r = estimate_portion(_crop(2000, 10_000), "kimchi")
        assert r["multiplier"] == 1.5
        assert r["portion_g"] == round(160 * 1.5, 1), (
            f"Expected 240.0g (160 * 1.5), got {r['portion_g']}"
        )
        ok("estimate_portion reads back 1.5x → portion_g=240.0 (160 * 1.5)")

    # Invalid multiplier raises ValueError
    raised = False
    try:
        apply_multiplier("kimchi", 1.2)
    except ValueError as e:
        raised = True
        ok(f"invalid multiplier 1.2 rejected: {e}")
    assert raised, "Expected ValueError for multiplier=1.2"

    # Updating an existing file with a new multiplier
    with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
         patch("pipeline.portion.get_macros", return_value=None):

        apply_multiplier("kimchi", 2.0)
        saved2 = json.loads(override_file.read_text())
        assert saved2["portion_multiplier"] == 2.0
        ok("apply_multiplier updates existing override file in place")


# ---------------------------------------------------------------------------
# Test 5 — None / no_results graceful handling
# ---------------------------------------------------------------------------

section("Test 5 — None / no_results graceful handling")

with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG):

    # get_macros returns None
    with patch("pipeline.portion.get_macros", return_value=None):
        r = estimate_portion(_crop(2000, 10_000), "mystery_dish")
        assert r["needs_macro_entry"] is True, "Expected needs_macro_entry=True for None"
        assert r["macros_scaled"] is None, "Expected macros_scaled=None for None"
        assert r["portion_g"] > 0, "portion_g should still be estimated"
        ok("get_macros()=None → needs_macro_entry=True, macros_scaled=None, portion_g set")

    # get_macros returns no_results record
    no_results = {"source": "no_results", "calories": None, "carbs_g": None,
                  "fiber_g": None, "protein_g": None, "fat_g": None, "reference_weight_g": 100.0}
    with patch("pipeline.portion.get_macros", return_value=no_results):
        r = estimate_portion(_crop(2000, 10_000), "mystery_dish")
        assert r["needs_macro_entry"] is True, "Expected needs_macro_entry=True for no_results"
        assert r["macros_scaled"] is None
        ok("get_macros()=no_results → needs_macro_entry=True, macros_scaled=None")

    # Mixed bowl: no crash when all components have missing macros
    with patch("pipeline.portion.get_macros", return_value=None):
        components = [
            {"role": "base",    "dish_name": "unknown_grain"},
            {"role": "protein", "dish_name": "unknown_protein"},
        ]
        out = estimate_portions_mixed(_mixed_crop(5000, 10_000, components))
        results = out["components"]
        assert all(c["needs_macro_entry"] for c in results)
        assert all(c["macros_scaled"] is None for c in results)
        ok("mixed_bowl: all missing macros → needs_macro_entry=True for each component, no crash")


# ---------------------------------------------------------------------------
# Test 6 — macros_scaled arithmetic
# ---------------------------------------------------------------------------

section("Test 6 — macros_scaled arithmetic correctness")

with tempfile.TemporaryDirectory() as tmp:
    _patch_dirs(tmp)

    # Plant a known override: 100 cal per 100g reference_weight_g
    known_macros = {
        "dish_name":          "test_dish",
        "source":             "user_override",
        "reference_weight_g": 100.0,
        "calories":           100.0,
        "carbs_g":            20.0,
        "fiber_g":            2.0,
        "protein_g":          5.0,
        "fat_g":              3.0,
        "saved_at":           "2026-01-01T00:00:00Z",
    }
    (nutrition.OVERRIDES_DIR / "test_dish.json").write_text(json.dumps(known_macros))

    with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG):
        # ratio=0.50 → large → default category → 240g
        r = estimate_portion(_crop(5000, 10_000), "test_dish")
        assert r["portion_g"] == 240.0, f"Expected 240g, got {r['portion_g']}"
        # scaled = 240 / 100 * macros
        expected_cal = round(100.0 * 240 / 100, 2)
        assert r["macros_scaled"]["calories"] == expected_cal, (
            f"Expected calories={expected_cal}, got {r['macros_scaled']['calories']}"
        )
        assert r["macros_scaled"]["carbs_g"] == round(20.0 * 2.4, 2)
        assert r["macros_scaled"]["fiber_g"] == round(2.0 * 2.4, 2)
        assert r["needs_macro_entry"] is False
        ok(f"240g portion of 100cal/100g dish → scaled calories={expected_cal}")

    # Non-100g reference_weight_g: 150g reference, 300g portion → scale=2.0
    alt_macros = dict(known_macros, reference_weight_g=150.0, calories=200.0)
    (nutrition.OVERRIDES_DIR / "test_dish.json").write_text(json.dumps(alt_macros))

    with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG):
        r = estimate_portion(_crop(5000, 10_000), "test_dish")
        # large → default → 240g; scale = 240/150 = 1.6
        expected_cal = round(200.0 * 240 / 150, 2)
        assert r["macros_scaled"]["calories"] == expected_cal, (
            f"Expected {expected_cal} cal, got {r['macros_scaled']['calories']}"
        )
        ok(f"non-100g reference (150g): 240g portion → scaled calories={expected_cal}")


# ---------------------------------------------------------------------------
# Test 7 — Partial detection guard (single base-only component)
# ---------------------------------------------------------------------------

section("Test 7 — Partial detection guard (single base-only component)")

with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
     patch("pipeline.portion.get_macros", return_value=None):

    # ratio = 0.25 → medium bucket; grain medium = 180g; bowl_total medium = 500g
    components_base_only = [
        {"role": "base", "dish_name": "brown_rice",
         "confidence": 0.25, "status": "UNCERTAIN", "confirmed": False}
    ]
    out = estimate_portions_mixed(_mixed_crop(2500, 10_000, components_base_only))

    assert out["components"][0]["partial_detection"] is True, (
        f"Expected partial_detection=True, got {out['components'][0].get('partial_detection')}"
    )
    ok(f"partial_detection=True for single base-only component")

    assert out["warnings"][0]["reason"] == "only_base_detected", (
        f"Expected reason='only_base_detected', got {out['warnings'][0]['reason']}"
    )
    ok(f"warnings[0].reason='only_base_detected'")

    grain_medium = _TEST_CFG["grain"]["medium"]   # 180
    bowl_medium  = _TEST_CFG["bowl_total"]["medium"]  # 500
    assert grain_medium != bowl_medium, "Test values must differ to be meaningful"
    actual_g = out["components"][0]["portion_g"]
    assert actual_g == float(grain_medium), (
        f"Guard must use grain[medium]={grain_medium}, not bowl_total[medium]={bowl_medium}; "
        f"got portion_g={actual_g}"
    )
    ok(f"portion_g={actual_g} comes from cfg['grain']['medium']={grain_medium}, not bowl_total={bowl_medium}")

    assert len(out["warnings"]) == 1, (
        f"Expected 1 warning, got {len(out['warnings'])}"
    )
    ok(f"exactly 1 warning emitted")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

section("All FOOD-014 tests passed")
print("  Module:         pipeline/portion.py")
print("  Config:         portion_defaults section in config.yaml")
print("  Storage:        data/overrides/ (portion_multiplier field)")
print("  Bucket logic:   mask_pixels / image_pixels vs small_max / medium_max")
print("  Mixed fractions: role_weights (base=2.0, others=1.0)")
print("  Bowl total:     bowl_total.{bucket} (300 / 500 / 700g)")
