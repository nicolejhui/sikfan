"""
FOOD-022 verification: derive the portion prior from ingredient edits.

Follows scripts/verify_food020.py's style: PRIORS_DIR monkeypatched onto
pipeline.portion into a tempdir so real data/ is never touched. Exercises
two layers:
  1. pipeline.portion.save_portion_prior()'s new `supersedes` param directly
     (revise / retract, "revised" / "retracted" prior_state, n_corrections
     and reasons left untouched).
  2. api._contribute_prior() against plain dish_entry dicts (no FastAPI
     TestClient / job_status files needed — it's a pure function of a dict
     and a dish name).

Tests map to docs/TEST-SCENARIOS.md:
  SC-23 Stepping the rice up teaches the dish (pending -> activated)
  SC-24 Three nudges are one claim (n_corrections stays 1)
  SC-25 Stepping back retracts (returns to exactly 1.0, pending cleared)
  SC-26 One session, two flows -> one net contribution
  SC-27 leftover suppresses and retracts
  SC-28 Below-tolerance edits write nothing at all
  Plus: save_portion_prior(supersedes=None) is byte-for-byte the old
  behavior (FOOD-020 regression), and reset_corrections-style retraction.

Run from project root: python scripts/verify_food022.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import api
import pipeline.portion as portion_module
from pipeline.portion import save_portion_prior


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


def _dish_entry(baseline_portion_g: float, portion_g: float, **extra) -> dict:
    entry = {"name": "curry_rice", "portion_g": portion_g, "_baseline": {"portion_g": baseline_portion_g}}
    entry.update(extra)
    return entry


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _patch_dirs(tmp)

        # -------------------------------------------------------------
        section("Test 1 — supersedes=None reproduces FOOD-020 unchanged")
        # -------------------------------------------------------------
        rec = save_portion_prior("regression_dish", 0.85, "portion", "too_high")
        check("no supersedes -> plain pending, exactly as before", rec["prior_state"] == "pending")
        check("n_corrections increments normally", rec["n_corrections"] == 1)

        # -------------------------------------------------------------
        section("SC-23 — Stepping the rice up teaches the dish")
        # -------------------------------------------------------------
        entry = _dish_entry(210.0, 280.0)  # +33%
        rec = api._contribute_prior(entry, "sc23_dish")
        check("first contribution is pending", rec["prior_state"] == "pending")
        check("direction is too_low (factor > 1)", rec["pending_direction"] == "too_low")
        check("_prior_contribution recorded on the dish entry", abs(entry["_prior_contribution"] - 280 / 210) < 1e-9)

        # a second meal doing the same activates the prior
        entry2 = _dish_entry(210.0, 280.0)
        rec = api._contribute_prior(entry2, "sc23_dish")
        check("second meal's identical contribution activates", rec["prior_state"] == "activated")
        check("activated multiplier matches the grams ratio", abs(rec["portion_multiplier"] - 280 / 210) < 0.01)

        # -------------------------------------------------------------
        section("SC-24 — Three nudges on one crop are one claim")
        # -------------------------------------------------------------
        entry = _dish_entry(200.0, 200.0)
        rec1 = api._contribute_prior(entry, "sc24_dish")
        check("first nudge (0% yet) writes nothing", rec1 is None)

        entry["portion_g"] = 230.0  # +15%
        rec2 = api._contribute_prior(entry, "sc24_dish")
        check("second nudge (+15%) is pending", rec2["prior_state"] == "pending")
        check("n_corrections is 1 after one contribution", rec2["n_corrections"] == 1)

        entry["portion_g"] = 260.0  # +30%
        rec3 = api._contribute_prior(entry, "sc24_dish")
        check("third nudge (+30%) REVISES, doesn't accumulate", rec3["prior_state"] == "revised")
        check("n_corrections still 1 — not incremented across revisions", rec3["n_corrections"] == 1)
        check(
            "pending_factor equals the FINAL ratio, not 1.15 * 1.somthing",
            abs(rec3["pending_factor"] - 260 / 200) < 1e-9,
        )

        # -------------------------------------------------------------
        section("SC-25 — Stepping back retracts")
        # -------------------------------------------------------------
        entry = _dish_entry(200.0, 260.0)  # +30%, pending
        rec = api._contribute_prior(entry, "sc25_dish")
        check("pending after the raise", rec["prior_state"] == "pending")

        entry["portion_g"] = 200.0  # back to baseline
        rec = api._contribute_prior(entry, "sc25_dish")
        check("stepping back to baseline retracts", rec["prior_state"] == "retracted")
        check("pending evidence cleared", "pending_direction" not in rec)
        check("n_corrections decremented back to 0", rec["n_corrections"] == 0)
        check("_prior_contribution cleared off the dish entry", "_prior_contribution" not in entry)

        # An ACTIVE prior retracting a contribution returns to exactly 1.0.
        entry_a = _dish_entry(200.0, 260.0)
        api._contribute_prior(entry_a, "sc25_active_dish")
        entry_a2 = _dish_entry(200.0, 260.0)
        rec = api._contribute_prior(entry_a2, "sc25_active_dish")
        check("second identical contribution activates", rec["prior_state"] == "activated")
        entry_a2["portion_g"] = 200.0
        rec = api._contribute_prior(entry_a2, "sc25_active_dish")
        check("retracting an ACTIVE contribution returns exactly to 1.0", abs(rec["portion_multiplier"] - 1.0) < 1e-9)
        check("retracted prior_state on the active path too", rec["prior_state"] == "retracted")

        # -------------------------------------------------------------
        section("SC-26 — Ingredient edit + scalar tap = one write")
        # -------------------------------------------------------------
        # Ingredient edit raises the dish (correct_ingredients-style call).
        entry = _dish_entry(200.0, 260.0)
        rec_ingredient = api._contribute_prior(entry, "sc26_dish")
        check("ingredient edit contributes pending", rec_ingredient["prior_state"] == "pending")

        # Scalar tap (correct_macros-style) then further changes portion_g in
        # the SAME session and re-derives against the same baseline.
        entry["portion_g"] = 300.0
        rec_scalar = api._contribute_prior(entry, "sc26_dish")
        check("scalar tap on top REVISES the same slot, not a second entry", rec_scalar["prior_state"] == "revised")
        check(
            "pending_factor reflects the FINAL portion_g / baseline, not both edits compounded",
            abs(rec_scalar["pending_factor"] - 300 / 200) < 1e-9,
        )
        check("still exactly one contribution's worth of evidence", rec_scalar["n_corrections"] == 1)

        # -------------------------------------------------------------
        section("SC-27 — leftover suppresses and retracts")
        # -------------------------------------------------------------
        entry = _dish_entry(200.0, 260.0)
        rec = api._contribute_prior(entry, "sc27_dish")
        check("contribution exists before leftover", rec["prior_state"] == "pending")

        entry["_prior_suppressed"] = True
        rec = api._contribute_prior(entry, "sc27_dish")
        check("leftover retracts the existing contribution", rec["prior_state"] == "retracted")
        check("_prior_contribution cleared", "_prior_contribution" not in entry)

        # Further edits on the same (still-suppressed) crop write nothing.
        entry["portion_g"] = 280.0
        rec = api._contribute_prior(entry, "sc27_dish")
        check("suppressed crop blocks further contributions", rec is None)

        # -------------------------------------------------------------
        section("SC-28 — Below-tolerance edits write nothing at all")
        # -------------------------------------------------------------
        entry = _dish_entry(200.0, 201.5)  # 0.75% change
        rec = api._contribute_prior(entry, "sc28_dish")
        check("a <2% edit with no prior contribution writes nothing", rec is None)
        check("no _prior_contribution recorded", "_prior_contribution" not in entry)

        # -------------------------------------------------------------
        section("Reset retraction — Undo all retracts a derived prior")
        # -------------------------------------------------------------
        entry = _dish_entry(200.0, 260.0)
        api._contribute_prior(entry, "sc_reset_dish")
        entry2 = _dish_entry(200.0, 260.0)
        api._contribute_prior(entry2, "sc_reset_dish")  # activates at 1.3
        rec_before = portion_module._load_json(portion_module._prior_path("sc_reset_dish"))
        check("prior active before reset", rec_before.get("active") is True)

        contribution = entry2["_prior_contribution"]
        rec_after = save_portion_prior("sc_reset_dish", 1.0, "portion", "too_high", supersedes=contribution)
        check("reset-style retraction returns exactly to 1.0", abs(rec_after["portion_multiplier"] - 1.0) < 1e-9)
        check("prior_state is 'retracted'", rec_after["prior_state"] == "retracted")

    section("All FOOD-022 tests passed")


if __name__ == "__main__":
    main()
