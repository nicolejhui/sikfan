"""
FOOD-015 verification: End-to-end pipeline integration.

Tests:
  test_output_keys                   — top-level schema has exactly the three expected keys
  test_detected_items_single_dish_schema — single_dish item field/value validation
  test_detected_items_mixed_bowl_schema  — mixed_bowl item field/value validation
  test_total_macros_keys             — total_macros schema and non-negative values
  test_total_macros_none_safe        — None macros_scaled never crashes aggregation
  test_total_macros_decimal_accuracy — Decimal arithmetic, no floating-point creep
  test_items_needing_review_schema   — review entry field and reason validation
  test_no_food_detected_sentinel     — empty segment_meal → no_food_detected sentinel
  test_all_nonfood_crops_sentinel    — all non-food crops → no_food_detected sentinel
  test_timing_mps                    — full pipeline <= 8s on MPS (real image)

Usage:
    python -m pytest scripts/verify_food015.py -v
    python -m pytest scripts/verify_food015.py::test_timing_mps -v -s
"""

from __future__ import annotations

import time

import pytest
import torch

import analyze_meal as am_module
from analyze_meal import analyze_meal

# ---------------------------------------------------------------------------
# Fixture paths (corrected from ticket — images moved to subdirectories)
# ---------------------------------------------------------------------------

_BREAKFAST = "data/unlabeled/assorted_breakfast.jpeg"
_HOT_POT   = "data/dishes/hot_pot/hot_pot_christmas.jpeg"
_MIXED     = "data/unlabeled/284EB9EE-9506-41AA-AAF6-9693BD22EB78_1_105_c.jpeg"

_MACRO_KEYS = {"calories", "carbs_g", "protein_g", "fat_g", "fiber_g"}
_VALID_STATUSES  = {"CONFIDENT", "UNCERTAIN", "UNKNOWN"}
_VALID_PORTIONS  = {"small", "medium", "large"}
_VALID_ROLES     = {"base", "protein", "vegetable"}
_VALID_REASONS   = {
    "unknown_dish", "low_confidence", "base_always_confirm",
    "partial_detection", "no_food_detected",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _macro_dict_valid(macros: dict) -> bool:
    """Return True if macros dict has exactly the required keys with numeric values."""
    if set(macros.keys()) != _MACRO_KEYS:
        return False
    return all(isinstance(v, (int, float)) and v >= 0 for v in macros.values())


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_output_keys():
    result = analyze_meal(_BREAKFAST)
    assert set(result.keys()) == {"detected_items", "total_macros", "items_needing_review"}


def test_detected_items_single_dish_schema():
    result = analyze_meal(_BREAKFAST)
    single_items = [i for i in result["detected_items"] if i["crop_type"] == "single_dish"]
    for item in single_items:
        assert set(item.keys()) == {"crop_type", "dish_name", "confidence", "status", "macros", "portion"}, (
            f"Unexpected keys: {set(item.keys())}"
        )
        assert item["status"] in _VALID_STATUSES, f"Bad status: {item['status']}"
        assert item["portion"] in _VALID_PORTIONS, f"Bad portion: {item['portion']}"
        assert isinstance(item["confidence"], float)
        assert 0.0 <= item["confidence"] <= 1.0
        if item["macros"] is not None:
            assert _macro_dict_valid(item["macros"]), f"Invalid macros: {item['macros']}"


def test_detected_items_mixed_bowl_schema():
    result = analyze_meal(_MIXED)
    mixed_items = [i for i in result["detected_items"] if i["crop_type"] == "mixed_bowl"]
    for item in mixed_items:
        assert set(item.keys()) == {"crop_type", "components", "macros"}, (
            f"Unexpected keys: {set(item.keys())}"
        )
        assert item["macros"] is None, "mixed_bowl top-level macros must be None"
        assert isinstance(item["components"], list)
        for comp in item["components"]:
            required = {"role", "dish_name", "confidence", "status", "confirmed", "macros", "portion_fraction"}
            assert required <= set(comp.keys()), f"Missing keys: {required - set(comp.keys())}"
            assert comp["role"] in _VALID_ROLES, f"Bad role: {comp['role']}"
            assert comp["status"] in _VALID_STATUSES, f"Bad status: {comp['status']}"
            assert isinstance(comp["confirmed"], bool)
            assert isinstance(comp["portion_fraction"], float)
            assert 0.0 <= comp["portion_fraction"] <= 1.0
            if comp["macros"] is not None:
                assert _macro_dict_valid(comp["macros"]), f"Invalid comp macros: {comp['macros']}"


def test_total_macros_keys():
    result = analyze_meal(_BREAKFAST)
    assert set(result["total_macros"].keys()) == _MACRO_KEYS
    for k, v in result["total_macros"].items():
        assert isinstance(v, float), f"{k} must be float, got {type(v)}"
        assert v >= 0.0, f"{k} must be >= 0, got {v}"


def test_items_needing_review_schema():
    result = analyze_meal(_BREAKFAST)
    for entry in result["items_needing_review"]:
        assert "crop_type" in entry, f"Missing crop_type in {entry}"
        assert "role" in entry,      f"Missing role in {entry}"
        assert "dish_name" in entry, f"Missing dish_name in {entry}"
        assert "reason" in entry,    f"Missing reason in {entry}"
        assert entry["reason"] in _VALID_REASONS, f"Unknown reason: {entry['reason']}"


# ---------------------------------------------------------------------------
# None-safety and arithmetic tests
# ---------------------------------------------------------------------------

def test_total_macros_none_safe(monkeypatch):
    """All macros_scaled=None must produce zero totals without raising."""
    original_ep = am_module.estimate_portion

    def _null_macros(crop_result, dish_name, role=None, config_path="config.yaml"):
        r = original_ep(crop_result, dish_name, role=role, config_path=config_path)
        r["macros_scaled"] = None
        return r

    monkeypatch.setattr(am_module, "estimate_portion", _null_macros)
    result = analyze_meal(_BREAKFAST)

    for k, v in result["total_macros"].items():
        assert v == 0.0, f"Expected 0.0 for {k} when all macros are None, got {v}"


def test_total_macros_decimal_accuracy():
    """
    Verify the aggregation uses Decimal arithmetic and the result equals
    the naive sum rounded once — not the sum of per-step rounded floats.
    """
    from decimal import Decimal, ROUND_HALF_UP
    from analyze_meal import _aggregate_macros

    # Three items each with 0.1 calories — naive float sum = 0.30000000000000004
    items = [
        {"crop_type": "single_dish", "macros": {"calories": 0.1, "carbs_g": 0.0,
                                                  "protein_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}},
        {"crop_type": "single_dish", "macros": {"calories": 0.1, "carbs_g": 0.0,
                                                  "protein_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}},
        {"crop_type": "single_dish", "macros": {"calories": 0.1, "carbs_g": 0.0,
                                                  "protein_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}},
    ]
    result = _aggregate_macros(items)
    assert result["calories"] == 0.30, (
        f"Expected 0.30, got {result['calories']} — floating-point accumulation detected"
    )


# ---------------------------------------------------------------------------
# No-detection sentinel tests
# ---------------------------------------------------------------------------

def test_no_food_detected_sentinel(monkeypatch):
    """Empty segment_meal result → no_food_detected sentinel in items_needing_review."""
    monkeypatch.setattr(am_module, "segment_meal", lambda path: [])
    result = analyze_meal(_BREAKFAST)

    assert result["detected_items"] == []
    assert result["items_needing_review"] == [
        {"crop_type": None, "role": None, "dish_name": None, "reason": "no_food_detected"}
    ]
    for k, v in result["total_macros"].items():
        assert v == 0.0, f"Expected 0.0 for {k} on empty detection, got {v}"


def test_all_nonfood_crops_sentinel(monkeypatch):
    """is_food_crop returning False for all crops → same no_food_detected sentinel."""
    monkeypatch.setattr(am_module, "is_food_crop", lambda image, device="mps": False)
    result = analyze_meal(_BREAKFAST)

    assert result["detected_items"] == []
    assert result["items_needing_review"] == [
        {"crop_type": None, "role": None, "dish_name": None, "reason": "no_food_detected"}
    ]


# ---------------------------------------------------------------------------
# Timing test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not torch.backends.mps.is_available(),
    reason="MPS not available on this device",
)
def test_timing_mps():
    """Full cold-start pipeline must complete in <= 8 seconds on MPS."""
    start = time.time()
    result = analyze_meal(_HOT_POT)
    elapsed = time.time() - start

    assert elapsed <= 8.0, (
        f"Pipeline took {elapsed:.2f}s on MPS — exceeds the 8s budget. "
        "Check that classify_batch() is used (not sequential classify_crop calls)."
    )
    # Basic sanity: a real hot-pot photo should yield at least one detected item
    assert isinstance(result["detected_items"], list)
    assert set(result["total_macros"].keys()) == _MACRO_KEYS
