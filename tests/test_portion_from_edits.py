"""SC-23 ... SC-28 — deriving the portion prior from ingredient edits
(api.py's _contribute_prior(), pipeline/portion.py's supersedes path).

FOOD-022 lets an ingredient edit (or a scalar too-high/too-low tap) derive a
portion-prior contribution, instead of the edit teaching the dish nothing.
Each crop contributes ONE revisable claim, computed from
new_portion_g / baseline.portion_g, never accumulated per edit.

These tests call api._contribute_prior() directly against a hand-built
dish_entry dict rather than going through the FastAPI /correct-ingredients or
/correct-macros endpoints — that's the actual derivation logic; the endpoints
just plumb request bodies into dish_entry['portion_g'] before calling it. This
is the same approach api.py's own callers use: mutate dish_entry['portion_g'],
then call _contribute_prior(dish_entry, dish_name).

Importing api.py pulls in the full FastAPI app plus analyze_meal /
glucose_analysis / segmentation / embedding_store — a real (~4s) one-time
import cost. Isolated to this file so the rest of the suite stays fast.
"""

from __future__ import annotations

import pytest

import api
import pipeline.portion as portion


def _dish_entry(name, baseline_g, portion_g):
    return {"name": name, "_baseline": {"portion_g": baseline_g}, "portion_g": portion_g}


@pytest.mark.safety
class TestSC23SteppingTheRiceUpTeachesTheDish:
    """As a user who steps the rice from 210g to 280g on the same dish across
    two separate scans, I want the second scan to start closer to the real total."""

    def test_first_edit_writes_pending_at_grams_ratio(self):
        dish_entry = _dish_entry("white_rice", 210.0, 280.0)
        record = api._contribute_prior(dish_entry, "white_rice")
        assert record["prior_state"] == "pending"
        assert record["pending_factor"] == pytest.approx(280.0 / 210.0)

    def test_second_scan_same_edit_activates_at_same_ratio(self):
        first_scan = _dish_entry("white_rice", 210.0, 280.0)
        api._contribute_prior(first_scan, "white_rice")

        second_scan = _dish_entry("white_rice", 210.0, 280.0)
        record = api._contribute_prior(second_scan, "white_rice")

        assert record["prior_state"] == "activated"
        assert record["portion_multiplier"] == round(280.0 / 210.0, 2)


@pytest.mark.safety
class TestSC24ThreeNudgesAreOneClaim:
    """As a user who nudges an ingredient's amount three times in one
    sitting, I don't want that read as three pieces of evidence."""

    def test_three_successive_edits_leave_one_correction(self):
        dish_entry = _dish_entry("noodles", 200.0, 200.0)
        last_record = None
        for new_g in (220.0, 260.0, 300.0):
            dish_entry["portion_g"] = new_g
            last_record = api._contribute_prior(dish_entry, "noodles")

        assert last_record["n_corrections"] == 1
        assert dish_entry["_prior_contribution"] == pytest.approx(300.0 / 200.0)

    def test_pending_contribution_is_final_ratio_not_compounded(self):
        dish_entry = _dish_entry("noodles", 200.0, 220.0)
        api._contribute_prior(dish_entry, "noodles")
        dish_entry["portion_g"] = 300.0
        record = api._contribute_prior(dish_entry, "noodles")

        # 300/200 = 1.5, NOT (220/200) * (300/220) treated as two separate
        # multiplicative corrections landing somewhere else.
        assert record["pending_factor"] == pytest.approx(1.5)


@pytest.mark.safety
class TestSC25SteppingBackRetracts:
    """As a user who raises an ingredient's amount and then puts it back, I
    want the dish's learned size to return to exactly where it started."""

    def test_pending_contribution_cleared_on_restore(self):
        dish_entry = _dish_entry("beef", 150.0, 200.0)
        api._contribute_prior(dish_entry, "beef")

        dish_entry["portion_g"] = 150.0
        record = api._contribute_prior(dish_entry, "beef")

        assert record["prior_state"] == "retracted"
        assert record["n_corrections"] == 0
        assert "_prior_contribution" not in dish_entry

    def test_active_prior_returns_to_exactly_one(self):
        # Activate the prior for this dish via two prior scans first (each
        # its own crop/dish_entry, as separate meals would be).
        for _ in range(2):
            fresh_entry = _dish_entry("beef", 150.0, 200.0)
            api._contribute_prior(fresh_entry, "beef")
        assert portion._read_multiplier("beef") == round(200.0 / 150.0, 2)

        # THIS session's crop: raise the amount (compounds the now-active
        # prior), then put it back to baseline before the scan ends — same
        # dish_entry throughout, since _prior_contribution is per-crop state.
        dish_entry = _dish_entry("beef", 150.0, 150.0)
        dish_entry["portion_g"] = 220.0
        api._contribute_prior(dish_entry, "beef")

        dish_entry["portion_g"] = 150.0  # stepped back to baseline
        record = api._contribute_prior(dish_entry, "beef")

        assert record["prior_state"] == "retracted"
        assert portion._read_multiplier("beef") == round(200.0 / 150.0, 2)


class TestSC26IngredientEditPlusScalarTapIsOneWrite:
    """As a user who both resizes an ingredient and taps "too low" on the
    same dish in one sitting, I want that to register as one correction."""

    def test_edit_then_scalar_on_same_crop_shares_one_slot(self):
        dish_entry = _dish_entry("beef", 150.0, 150.0)
        dish_entry["portion_g"] = 180.0  # ingredient edit
        r1 = api._contribute_prior(dish_entry, "beef")
        assert r1["n_corrections"] == 1

        dish_entry["portion_g"] = 200.0  # scalar too-low tap, same crop
        r2 = api._contribute_prior(dish_entry, "beef")

        assert r2["n_corrections"] == 1  # still one claim, not two
        assert r2["pending_factor"] == pytest.approx(200.0 / 150.0)


@pytest.mark.safety
class TestSC27LeftoverSuppresses:
    """As a user who says "I didn't finish this" about a dish I also
    resized, I don't want SikFan to learn a permanently smaller portion."""

    def test_leftover_retracts_existing_contribution(self):
        dish_entry = _dish_entry("noodles", 200.0, 260.0)
        api._contribute_prior(dish_entry, "noodles")
        assert "_prior_contribution" in dish_entry

        dish_entry["_prior_suppressed"] = True
        record = api._contribute_prior(dish_entry, "noodles")

        assert record["prior_state"] == "retracted"
        assert "_prior_contribution" not in dish_entry

    def test_leftover_blocks_further_contributions_this_session(self):
        dish_entry = _dish_entry("noodles", 200.0, 260.0)
        api._contribute_prior(dish_entry, "noodles")
        dish_entry["_prior_suppressed"] = True
        api._contribute_prior(dish_entry, "noodles")  # retracts

        # A further edit on this same (suppressed) crop must write nothing.
        dish_entry["portion_g"] = 300.0
        record = api._contribute_prior(dish_entry, "noodles")

        assert record is None


class TestSC28BelowToleranceEditsWriteNothingAtAll:
    """As a user who nudges an ingredient by a couple of grams, I don't want
    that noise treated as a claim about the dish's real size."""

    def test_edit_within_tolerance_writes_nothing(self):
        # ~1.3% change, below PRIOR_CONTRIBUTION_TOLERANCE (0.02)
        dish_entry = _dish_entry("tofu", 150.0, 152.0)
        record = api._contribute_prior(dish_entry, "tofu")

        assert record is None
        assert "_prior_contribution" not in dish_entry

    def test_tolerance_only_skips_first_write_not_a_revision(self):
        # Once a crop has already contributed, a small further nudge still
        # goes through supersedes as a revision/retraction — the "no first
        # write" rule doesn't apply once evidence for this crop exists.
        dish_entry = _dish_entry("tofu", 150.0, 200.0)
        api._contribute_prior(dish_entry, "tofu")

        dish_entry["portion_g"] = 152.0  # now within tolerance of baseline
        record = api._contribute_prior(dish_entry, "tofu")

        assert record["prior_state"] == "retracted"
