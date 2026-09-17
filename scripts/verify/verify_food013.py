"""
FOOD-013 verification: Manual macro entry and override flow.

Tests:
  1. set_manual_override saves to data/overrides/ with source: user_override
  2. get_macros returns overrides/ data even when macro_cache/ has the same filename
  3. fiber_g and reference_weight_g are correctly stored and retrieved
  4. reset_override deletes the override and get_macros falls back to API cache
  5. Validation rejects non-numeric and negative macro values
  6. Normalization: "Beef Stew" and "beef_stew" resolve to the same override file
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make project root importable when running as `python scripts/verify_food013.py`
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


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
# Patch storage dirs to use temp directories for all tests
# ---------------------------------------------------------------------------

import pipeline.nutrition as nutrition

section("Imports")
from pipeline.nutrition import set_manual_override, get_macros, reset_override
print("  pipeline.nutrition imported OK")

_VALID_MACROS = {
    "calories":   180.0,
    "carbs_g":    8.0,
    "fiber_g":    2.0,
    "protein_g":  16.0,
    "fat_g":      10.0,
}

_MAPO_MACROS = {
    "calories":   120.0,
    "carbs_g":    6.0,
    "fiber_g":    1.5,
    "protein_g":  8.0,
    "fat_g":      7.0,
}


def _patch_dirs(tmp: str) -> tuple[Path, Path]:
    """Redirect module-level storage paths into a temp directory."""
    overrides = Path(tmp) / "overrides"
    cache     = Path(tmp) / "macro_cache"
    overrides.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    nutrition.OVERRIDES_DIR = overrides
    nutrition.CACHE_DIR     = cache
    return overrides, cache


# ---------------------------------------------------------------------------
# Test 1 — set_manual_override saves to overrides/ with source: user_override
# ---------------------------------------------------------------------------

section("Test 1 — set_manual_override writes to overrides/ with source=user_override")

with tempfile.TemporaryDirectory() as tmp:
    overrides_dir, _ = _patch_dirs(tmp)

    result = set_manual_override("dried_tofu_sticks", _VALID_MACROS)

    assert result["source"] == "user_override", (
        f"Expected source='user_override', got '{result['source']}'"
    )
    ok("returned dict has source='user_override'")

    slug_file = overrides_dir / "dried_tofu_sticks.json"
    assert slug_file.exists(), f"Override file not found: {slug_file}"
    ok("file written to data/overrides/dried_tofu_sticks.json")

    on_disk = json.loads(slug_file.read_text())
    assert on_disk["source"] == "user_override", (
        f"On-disk source is '{on_disk['source']}', expected 'user_override'"
    )
    ok("on-disk JSON has source='user_override'")

    # Verify no file was written to macro_cache/
    cache_file = Path(tmp) / "macro_cache" / "dried_tofu_sticks.json"
    assert not cache_file.exists(), "Override must NOT be written to macro_cache/"
    ok("override was NOT written to macro_cache/")

# ---------------------------------------------------------------------------
# Test 2 — get_macros returns overrides/ data even if macro_cache/ has the same file
# ---------------------------------------------------------------------------

section("Test 2 — get_macros prioritises overrides/ over macro_cache/")

with tempfile.TemporaryDirectory() as tmp:
    overrides_dir, cache_dir = _patch_dirs(tmp)

    # Plant a stale API result in macro_cache/
    stale = {
        "dish_name":  "dried_tofu_sticks",
        "source":     "usda_api",
        "calories":   207.0,
        "carbs_g":    45.38,
        "fiber_g":    0.0,
        "protein_g":  4.05,
        "fat_g":      0.38,
    }
    (cache_dir / "dried_tofu_sticks.json").write_text(json.dumps(stale))

    # Save an override — should win
    set_manual_override("dried_tofu_sticks", _VALID_MACROS)

    result = get_macros("dried_tofu_sticks")
    assert result is not None, "get_macros returned None"
    assert result["source"] == "user_override", (
        f"Expected 'user_override', got '{result['source']}'"
    )
    ok("get_macros returned user_override, not stale API data")

    assert result["calories"] == _VALID_MACROS["calories"], (
        f"Expected calories={_VALID_MACROS['calories']}, got {result['calories']}"
    )
    ok("override macro values are returned, not API values")

# ---------------------------------------------------------------------------
# Test 3 — fiber_g and reference_weight_g are correctly stored and retrieved
# ---------------------------------------------------------------------------

section("Test 3 — fiber_g and reference_weight_g round-trip correctly")

with tempfile.TemporaryDirectory() as tmp:
    _patch_dirs(tmp)

    set_manual_override("dried_tofu_sticks", _VALID_MACROS, reference_weight_g=150.0)
    result = get_macros("dried_tofu_sticks")

    assert result["fiber_g"] == 2.0, f"Expected fiber_g=2.0, got {result['fiber_g']}"
    ok("fiber_g=2.0 stored and retrieved correctly")

    assert result["reference_weight_g"] == 150.0, (
        f"Expected reference_weight_g=150.0, got {result['reference_weight_g']}"
    )
    ok("reference_weight_g=150.0 stored and retrieved correctly")

    # Confirm default is 100.0
    set_manual_override("kimchi", _VALID_MACROS)
    r2 = get_macros("kimchi")
    assert r2["reference_weight_g"] == 100.0, (
        f"Default reference_weight_g should be 100.0, got {r2['reference_weight_g']}"
    )
    ok("default reference_weight_g=100.0 applied when not specified")

# ---------------------------------------------------------------------------
# Test 4 — reset_override deletes the override; get_macros falls back to API cache
# ---------------------------------------------------------------------------

section("Test 4 — reset_override reverts get_macros to API cache")

with tempfile.TemporaryDirectory() as tmp:
    overrides_dir, cache_dir = _patch_dirs(tmp)

    # Plant API cache for mapo_tofu
    api_record = {
        "dish_name":  "mapo_tofu",
        "source":     "usda_api",
        "calories":   95.0,
        "carbs_g":    5.0,
        "fiber_g":    1.0,
        "protein_g":  6.0,
        "fat_g":      6.0,
    }
    (cache_dir / "mapo_tofu.json").write_text(json.dumps(api_record))

    # Override it
    set_manual_override("mapo_tofu", _MAPO_MACROS)
    assert get_macros("mapo_tofu")["source"] == "user_override"
    ok("override in place before reset")

    # Reset
    deleted = reset_override("mapo_tofu")
    assert deleted is True, f"reset_override should return True, got {deleted}"
    ok("reset_override returned True")

    override_file = overrides_dir / "mapo_tofu.json"
    assert not override_file.exists(), "Override file must be deleted by reset_override"
    ok("override file deleted from data/overrides/")

    result = get_macros("mapo_tofu")
    assert result is not None, "get_macros should fall back to API cache after reset"
    assert result["source"] == "usda_api", (
        f"Expected source='usda_api' after reset, got '{result['source']}'"
    )
    ok("get_macros fell back to usda_api after reset")

    # reset on non-existent override
    deleted_again = reset_override("mapo_tofu")
    assert deleted_again is False, (
        f"reset_override on missing file should return False, got {deleted_again}"
    )
    ok("reset_override returns False when no override exists")

# ---------------------------------------------------------------------------
# Test 5 — Validation rejects non-numeric and negative macro values
# ---------------------------------------------------------------------------

section("Test 5 — Validation rejects bad macro values")

with tempfile.TemporaryDirectory() as tmp:
    _patch_dirs(tmp)

    # Non-numeric value
    bad_string = {**_VALID_MACROS, "carbs_g": "eight"}
    raised = False
    try:
        set_manual_override("test_dish", bad_string)
    except ValueError as e:
        raised = True
        ok(f"non-numeric carbs_g rejected: {e}")
    assert raised, "Expected ValueError for non-numeric carbs_g"

    # Negative value
    bad_negative = {**_VALID_MACROS, "fat_g": -1.0}
    raised = False
    try:
        set_manual_override("test_dish", bad_negative)
    except ValueError as e:
        raised = True
        ok(f"negative fat_g rejected: {e}")
    assert raised, "Expected ValueError for negative fat_g"

    # Missing required field
    bad_missing = {k: v for k, v in _VALID_MACROS.items() if k != "fiber_g"}
    raised = False
    try:
        set_manual_override("test_dish", bad_missing)
    except ValueError as e:
        raised = True
        ok(f"missing fiber_g rejected: {e}")
    assert raised, "Expected ValueError for missing fiber_g"

    # Zero is valid (e.g. fiber_g=0 for some foods)
    zero_fiber = {**_VALID_MACROS, "fiber_g": 0.0}
    result = set_manual_override("zero_fiber_dish", zero_fiber)
    assert result["fiber_g"] == 0.0
    ok("fiber_g=0.0 accepted (zero is a valid value)")

    # Invalid reference_weight_g
    raised = False
    try:
        set_manual_override("test_dish", _VALID_MACROS, reference_weight_g=0)
    except ValueError as e:
        raised = True
        ok(f"reference_weight_g=0 rejected: {e}")
    assert raised, "Expected ValueError for reference_weight_g=0"

# ---------------------------------------------------------------------------
# Test 6 — Normalization: "Beef Stew" and "beef_stew" hit the same override file
# ---------------------------------------------------------------------------

section("Test 6 — Normalization: 'Beef Stew' == 'beef_stew' == 'BEEF STEW'")

with tempfile.TemporaryDirectory() as tmp:
    overrides_dir, _ = _patch_dirs(tmp)

    # Write with mixed-case spaced name
    set_manual_override("Beef Stew", _VALID_MACROS)

    slug_file = overrides_dir / "beef_stew.json"
    assert slug_file.exists(), (
        f"Expected file at overrides/beef_stew.json, not found. "
        f"Contents: {list(overrides_dir.iterdir())}"
    )
    ok("'Beef Stew' saved as beef_stew.json")

    # Read back with underscore form
    result = get_macros("beef_stew")
    assert result is not None, "get_macros('beef_stew') returned None"
    assert result["source"] == "user_override"
    ok("get_macros('beef_stew') resolves to same file as 'Beef Stew'")

    # Read back with upper-case form
    result2 = get_macros("BEEF STEW")
    assert result2 is not None, "get_macros('BEEF STEW') returned None"
    assert result2["calories"] == _VALID_MACROS["calories"]
    ok("get_macros('BEEF STEW') resolves to same file")

    # reset_override with a different casing removes the file
    deleted = reset_override("beef stew")
    assert deleted is True, f"Expected True, got {deleted}"
    assert not slug_file.exists(), "File should be gone after reset with different casing"
    ok("reset_override('beef stew') deleted the beef_stew.json file")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

section("All FOOD-013 tests passed")
print("  Module:           pipeline/nutrition.py")
print("  Overrides dir:    data/overrides/")
print("  Cache dir:        data/macro_cache/")
print("  Priority:         overrides/ > macro_cache/ > None")
print("  Normalization:    normalize_dish_name() from pipeline.feedback")
print("  Atomic writes:    write-to-.tmp then os.replace()")
