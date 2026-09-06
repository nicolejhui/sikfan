"""
FOOD-023 verification: cut the reason chips; scalar owns portion, ingredients
own composition.

Follows scripts/verify_food020.py's style: PRIORS_DIR monkeypatched onto
pipeline.portion into a tempdir so real data/ is never touched.

Tests map to docs/TEST-SCENARIOS.md:
  SC-06 rewrite: leftover as SCOPE, not reason — unchanged behavior
  SC-06b: reason=None on a directional correction behaves exactly as
          reason="portion" (persists, activates on the second)
  SC-06c: a pre-existing prior record carrying legacy reasons ("hidden",
          "broth") still loads, still applies its multiplier, and still
          accepts new corrections (no migration, D4)
  Plus: PERSISTED_REASONS narrowed to {"portion"}; an unknown reason
  ("hidden"/"broth") passed to a NEW call still raises; the API-level
  Literal/guard changes (CorrectMacrosRequest.reason, missing_correction_detail
  no longer requires reason).

Run from project root: python scripts/verify_food023.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline.portion as portion_module
from pipeline.portion import PERSISTED_REASONS, COUNTED_REASONS, save_portion_prior


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def check(msg: str, cond: bool) -> None:
    ok(msg) if cond else fail(msg)


def section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def _patch_dirs(tmp: str) -> Path:
    priors = Path(tmp) / "portion_priors"
    priors.mkdir(parents=True, exist_ok=True)
    portion_module.PRIORS_DIR = priors
    return priors


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        priors = _patch_dirs(tmp)

        # -------------------------------------------------------------
        section("PERSISTED_REASONS narrowed")
        # -------------------------------------------------------------
        check("PERSISTED_REASONS is just {'portion'}", PERSISTED_REASONS == {"portion"})
        check("COUNTED_REASONS unchanged", COUNTED_REASONS == {"leftover"})

        raised = False
        try:
            save_portion_prior("kimchi", 0.85, "hidden", "too_high")
        except ValueError:
            raised = True
        check("a NEW call with reason='hidden' now raises", raised)

        raised = False
        try:
            save_portion_prior("kimchi", 0.85, "broth", "too_high")
        except ValueError:
            raised = True
        check("a NEW call with reason='broth' now raises", raised)

        # -------------------------------------------------------------
        section("SC-06 rewrite — leftover is a SCOPE, behavior unchanged")
        # -------------------------------------------------------------
        for _ in range(5):
            rec = save_portion_prior("fried_rice", 0.85, "leftover", "too_high")
        check("n_corrections tallies leftover corrections", rec["n_corrections"] == 5)
        check("no portion_multiplier key was ever written", "portion_multiplier" not in rec)
        check("prior_state is 'counted'", rec["prior_state"] == "counted")
        check("_read_multiplier still returns 1.0", portion_module._read_multiplier("fried_rice") == 1.0)

        # -------------------------------------------------------------
        section("SC-06b — reason=None behaves exactly as reason='portion'")
        # -------------------------------------------------------------
        rec = save_portion_prior("none_reason_dish", 0.85, None, "too_high")
        check("reason=None persists as a pending correction", rec["prior_state"] == "pending")
        check("stored reason tally records it under 'portion'", rec["reasons"] == {"portion": 1})

        rec = save_portion_prior("none_reason_dish", 0.85, None, "too_high")
        check("a second reason=None correction activates, exactly like reason='portion'", rec["prior_state"] == "activated")
        check("activated multiplier matches", rec["portion_multiplier"] == 0.85)

        # Cross-check: explicit "portion" and omitted reason are indistinguishable.
        rec_a = save_portion_prior("equivalence_a", 0.85, "portion", "too_high")
        rec_b = save_portion_prior("equivalence_b", 0.85, None, "too_high")
        check(
            "explicit 'portion' and omitted reason produce the same record shape",
            {k: v for k, v in rec_a.items() if k != "dish_name"}
            == {k: v for k, v in rec_b.items() if k != "dish_name"},
        )

        # -------------------------------------------------------------
        section("SC-06c — legacy hidden/broth records still load and apply")
        # -------------------------------------------------------------
        legacy_path = priors / "legacy_curry.json"
        legacy_path.write_text(json.dumps({
            "dish_name": "legacy_curry",
            "active": True,
            "portion_multiplier": 1.18,
            "n_corrections": 2,
            "reasons": {"hidden": 2},
            "last_updated": portion_module._now_iso(),
            "last_scanned": portion_module._now_iso(),
        }))
        check(
            "a pre-existing 'hidden'-reasoned active prior still applies its multiplier",
            portion_module._read_multiplier("legacy_curry") == 1.18,
        )

        rec = save_portion_prior("legacy_curry", 0.85, "portion", "too_high")
        check("a NEW correction on a legacy record still compounds normally", rec["prior_state"] == "compounded")
        check(
            "the legacy 'hidden' key in the reasons tally survives untouched",
            rec["reasons"].get("hidden") == 2,
        )
        check(
            "the new 'portion' reason is added alongside the legacy tally",
            rec["reasons"].get("portion") == 1,
        )

        legacy_broth_path = priors / "legacy_soup.json"
        legacy_broth_path.write_text(json.dumps({
            "dish_name": "legacy_soup",
            "active": False,
            "pending_direction": "too_high",
            "pending_factor": 0.60,
            "n_corrections": 1,
            "reasons": {"broth": 1},
            "last_updated": portion_module._now_iso(),
            "last_scanned": portion_module._now_iso(),
        }))
        rec = save_portion_prior("legacy_soup", 0.60, "portion", "too_high")
        check("a pending legacy 'broth' record still activates on a consistent new correction", rec["prior_state"] == "activated")
        check("activated multiplier is correct", rec["portion_multiplier"] == 0.60)

    section("All FOOD-023 tests passed")


if __name__ == "__main__":
    main()
