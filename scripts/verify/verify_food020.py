"""
FOOD-020 verification: durable per-dish portion priors.

Follows scripts/verify_food014.py's style: a fixed test config patched in,
temp dirs monkeypatched onto pipeline.portion / pipeline.nutrition so real
data/ is never touched.

Tests:
  1. Leftover never applies (D4) — counts only, no portion_multiplier ever
  2. Corrections are reversible (D6) — too_high/lot twice then too_low/lot -> 1.0
  3. Unknown reason raises ValueError; leftover cannot be forced to write a multiplier
  4. Activation needs two consistent corrections (D5)
  5. Conflicting directions reset pending evidence, don't activate
  6. looks_right clears pending evidence, spares an active prior, is dispatched
     with reason=None (D8)
  7. Clamping at both ends ([0.5, 2.0])
  8. Staleness (D7): stale prior ignored; touch_portion_prior revives it
  9. Precedence: prior > override > macro_cache
  10. Early-return regression: macro-only override + prior resolves to prior
  11. Durability regression: prior survives a macro_cache overwrite
  12. estimate_portion() end-to-end with an active prior
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pipeline.nutrition as nutrition
import pipeline.portion as portion_module
from pipeline.portion import (
    estimate_portion,
    save_portion_prior,
    touch_portion_prior,
)


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def check(msg: str, cond: bool) -> None:
    ok(msg) if cond else fail(msg)


def section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


_TEST_CFG = {
    "bucket_thresholds": {"small_max": 0.15, "medium_max": 0.35},
    "bowl_total": {"small": 300, "medium": 500, "large": 700},
    "role_weights": {"base": 2.0, "protein": 1.0, "vegetable": 1.0, "default": 1.0},
    "grain": {"small": 100, "medium": 180, "large": 280},
    "protein": {"small": 80, "medium": 150, "large": 220},
    "vegetable": {"small": 60, "medium": 120, "large": 180},
    "default": {"small": 90, "medium": 160, "large": 240},
}


def _patch_dirs(tmp: str):
    overrides = Path(tmp) / "overrides"
    cache = Path(tmp) / "macro_cache"
    priors = Path(tmp) / "portion_priors"
    for d in (overrides, cache, priors):
        d.mkdir(parents=True, exist_ok=True)
    nutrition.OVERRIDES_DIR = overrides
    portion_module.OVERRIDES_DIR = overrides
    nutrition.CACHE_DIR = cache
    portion_module.CACHE_DIR = cache
    portion_module.PRIORS_DIR = priors
    return overrides, cache, priors


def _crop(mask_pixels: int, image_pixels: int = 10_000) -> dict:
    return {"mask_pixels": mask_pixels, "image_pixels": image_pixels}


def main() -> None:
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        overrides, cache, priors = _patch_dirs(tmp)

        # -------------------------------------------------------------
        section("Test 1 — Leftover never applies (D4)")
        # -------------------------------------------------------------
        for _ in range(5):
            rec = save_portion_prior("fried_rice", 0.85, "leftover", "too_high")
        check("n_corrections tallies leftover corrections", rec["n_corrections"] == 5)
        check("no portion_multiplier key was ever written", "portion_multiplier" not in rec)
        check("prior_state is 'counted'", rec["prior_state"] == "counted")
        check("_read_multiplier still returns 1.0", portion_module._read_multiplier("fried_rice") == 1.0)

        with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
             patch("pipeline.portion.get_macros", return_value=None):
            r = estimate_portion(_crop(2000, 10_000), "fried_rice")
            check("estimate_portion unchanged by leftover tally", r["multiplier"] == 1.0)

        # -------------------------------------------------------------
        section("Test 2 — Corrections are reversible (D6)")
        # -------------------------------------------------------------
        save_portion_prior("braised_beef_noodles", 0.60, "portion", "too_high")
        rec = save_portion_prior("braised_beef_noodles", 0.60, "portion", "too_high")
        check("activates at 0.60 on the second too_high/lot", rec.get("portion_multiplier") == 0.60)

        rec = save_portion_prior("braised_beef_noodles", 1 / 0.60, "portion", "too_low")
        check(
            "too_low reciprocal brings the multiplier back to exactly 1.0",
            abs(rec["portion_multiplier"] - 1.0) < 1e-9,
        )

        # -------------------------------------------------------------
        section("Test 3 — Validation")
        # -------------------------------------------------------------
        raised = False
        try:
            save_portion_prior("kimchi", 0.85, "spoiled", "too_high")
        except ValueError:
            raised = True
        check("unknown reason raises ValueError", raised)

        raised = False
        try:
            save_portion_prior("kimchi", 1.0, "portion", "looks_right")
        except ValueError:
            raised = True
        check("looks_right with a reason raises ValueError", raised)

        raised = False
        try:
            save_portion_prior("kimchi", 0.9, None, "looks_right")
        except ValueError:
            raised = True
        check("looks_right with factor != 1.0 raises ValueError", raised)

        # -------------------------------------------------------------
        section("Test 4 — Activation needs two (D5)")
        # -------------------------------------------------------------
        rec = save_portion_prior("mapo_tofu", 0.85, "portion", "too_high")
        check("one correction leaves the prior inert", "portion_multiplier" not in rec)
        check("prior_state is 'pending'", rec["prior_state"] == "pending")

        with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
             patch("pipeline.portion.get_macros", return_value=None):
            r = estimate_portion(_crop(2000, 10_000), "mapo_tofu")
            check("estimate_portion unaffected by pending evidence", r["multiplier"] == 1.0)

        rec = save_portion_prior("mapo_tofu", 0.85, "portion", "too_high")
        check("second consistent correction activates", rec.get("portion_multiplier") == 0.85)

        with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
             patch("pipeline.portion.get_macros", return_value=None):
            r = estimate_portion(_crop(2000, 10_000), "mapo_tofu")
            check("estimate_portion scales once activated", r["multiplier"] == 0.85)

        # -------------------------------------------------------------
        section("Test 5 — Conflicting directions reset evidence")
        # -------------------------------------------------------------
        save_portion_prior("dumplings", 0.85, "portion", "too_high")
        rec = save_portion_prior("dumplings", 1 / 0.85, "portion", "too_low")
        check("conflicting direction does not activate", "portion_multiplier" not in rec)
        check("pending_direction replaced with the new direction", rec["pending_direction"] == "too_low")
        check("n_corrections reset to 1", rec["n_corrections"] == 1)
        check("reasons reset to just the new reason", rec["reasons"] == {"portion": 1})

        # -------------------------------------------------------------
        section("Test 6 — looks_right (D8)")
        # -------------------------------------------------------------
        save_portion_prior("bibimbap", 0.85, "portion", "too_high")
        rec = save_portion_prior("bibimbap", 1.0, None, "looks_right")
        check("looks_right clears pending evidence", "pending_direction" not in rec)
        check("prior_state is 'cleared'", rec["prior_state"] == "cleared")

        rec = save_portion_prior("bibimbap", 1.0, None, "looks_right")
        check("looks_right with no pending evidence is a no-op confirm", rec["prior_state"] == "confirmed")

        save_portion_prior("kimbap", 0.60, "portion", "too_high")
        save_portion_prior("kimbap", 0.60, "portion", "too_high")
        rec = save_portion_prior("kimbap", 1.0, None, "looks_right")
        check("looks_right spares an already-active prior", rec.get("portion_multiplier") == 0.60)
        check("active prior stays active after looks_right", rec["active"] is True)

        # -------------------------------------------------------------
        section("Test 7 — Clamping")
        # -------------------------------------------------------------
        save_portion_prior("gochujang_wings", 0.60, "portion", "too_high")
        save_portion_prior("gochujang_wings", 0.60, "portion", "too_high")
        for _ in range(10):
            rec = save_portion_prior("gochujang_wings", 0.60, "portion", "too_high")
        check("multiplier clamps at the floor 0.5", rec["portion_multiplier"] == 0.5)

        save_portion_prior("japchae", 1.667, "portion", "too_low")
        save_portion_prior("japchae", 1.667, "portion", "too_low")
        for _ in range(10):
            rec = save_portion_prior("japchae", 1.667, "portion", "too_low")
        check("multiplier clamps at the ceiling 2.0", rec["portion_multiplier"] == 2.0)

        # -------------------------------------------------------------
        section("Test 8 — Staleness (D7)")
        # -------------------------------------------------------------
        save_portion_prior("old_dish", 0.60, "portion", "too_high")
        save_portion_prior("old_dish", 0.60, "portion", "too_high")
        stale_path = priors / "old_dish.json"
        stale_rec = json.loads(stale_path.read_text())
        stale_rec["last_scanned"] = "2025-01-01T00:00:00Z"  # >180 days before "now"
        stale_path.write_text(json.dumps(stale_rec))
        check(
            "stale prior (last_scanned > 180d ago) is not applied",
            portion_module._read_multiplier("old_dish") == 1.0,
        )
        touch_portion_prior("old_dish")
        check(
            "touch_portion_prior revives it -> multiplier applies again",
            portion_module._read_multiplier("old_dish") == 0.60,
        )

        # -------------------------------------------------------------
        section("Test 9 — Precedence: prior > override > macro_cache")
        # -------------------------------------------------------------
        (overrides / "layered_dish.json").write_text(
            json.dumps({"dish_name": "layered_dish", "source": "user_override", "portion_multiplier": 1.5})
        )
        (cache / "layered_dish.json").write_text(
            json.dumps({"dish_name": "layered_dish", "source": "usda_api", "portion_multiplier": 2.0})
        )
        check(
            "override beats macro_cache absent a prior",
            portion_module._read_multiplier("layered_dish") == 1.5,
        )
        save_portion_prior("layered_dish", 0.60, "portion", "too_high")
        save_portion_prior("layered_dish", 0.60, "portion", "too_high")
        check(
            "an active prior beats both override and macro_cache",
            portion_module._read_multiplier("layered_dish") == 0.60,
        )

        # -------------------------------------------------------------
        section("Test 10 — Early-return regression")
        # -------------------------------------------------------------
        (overrides / "mapo_tofu_macro_only.json").write_text(
            json.dumps({"dish_name": "mapo_tofu_macro_only", "source": "user_override",
                        "calories": 200, "carbs_g": 10, "fiber_g": 2, "protein_g": 15, "fat_g": 12})
        )
        (cache / "mapo_tofu_macro_only.json").write_text(
            json.dumps({"dish_name": "mapo_tofu_macro_only", "source": "usda_api", "portion_multiplier": 1.5})
        )
        check(
            "macro-only override falls through to macro_cache's multiplier",
            portion_module._read_multiplier("mapo_tofu_macro_only") == 1.5,
        )
        save_portion_prior("mapo_tofu_macro_only", 0.85, "portion", "too_high")
        save_portion_prior("mapo_tofu_macro_only", 0.85, "portion", "too_high")
        check(
            "a prior on top of a macro-only override resolves to the prior, not 1.0",
            portion_module._read_multiplier("mapo_tofu_macro_only") == 0.85,
        )

        # -------------------------------------------------------------
        section("Test 11 — Durability regression")
        # -------------------------------------------------------------
        save_portion_prior("durable_dish", 0.60, "portion", "too_high")
        save_portion_prior("durable_dish", 0.60, "portion", "too_high")
        check("prior active before cache overwrite", portion_module._read_multiplier("durable_dish") == 0.60)
        (cache / "durable_dish.json").write_text(
            json.dumps({"dish_name": "durable_dish", "source": "composite", "carbs_g": 40.0})
        )
        check(
            "prior survives a macro_cache overwrite (api.py-style composite re-resolution)",
            portion_module._read_multiplier("durable_dish") == 0.60,
        )

        # -------------------------------------------------------------
        section("Test 12 — estimate_portion() end to end")
        # -------------------------------------------------------------
        save_portion_prior("e2e_dish", 0.5, "portion", "too_high")
        save_portion_prior("e2e_dish", 0.5, "portion", "too_high")
        with patch.object(portion_module, "_load_portion_cfg", return_value=_TEST_CFG), \
             patch("pipeline.portion.get_macros", return_value=None):
            r = estimate_portion(_crop(2000, 10_000), "e2e_dish")
            # default category, medium bucket -> 160g base
            check("active prior of 0.5 halves the estimated portion", r["portion_g"] == 80.0)

    section("All FOOD-020 tests passed")


if __name__ == "__main__":
    main()
