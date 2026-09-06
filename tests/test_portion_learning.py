"""SC-01 ... SC-07 — portion correction learning (pipeline/portion.py).

See docs/TEST-SCENARIOS.md section 1 for the user-story spec each class implements.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.portion as portion


def _crop(mask_pixels, image_pixels=1000):
    return {"mask_pixels": mask_pixels, "image_pixels": image_pixels}


class TestSC01OneCorrectionDoesNotMoveAnything:
    """As a user who tells SikFan "too little rice" once, my next scan of rice
    should not change — one data point isn't a pattern."""

    def test_pending_state_no_multiplier_written(self):
        record = portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        assert record["prior_state"] == "pending"
        assert "portion_multiplier" not in record

    def test_estimate_portion_still_reports_multiplier_one(self):
        portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        result = portion.estimate_portion(_crop(250), "white_rice")
        assert result["multiplier"] == 1.0


class TestSC02TwoCorrectionsTheSameWayStick:
    """As a user who has said "too little" twice, I want SikFan to have learned it."""

    def test_second_same_direction_activates(self):
        portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        record = portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        assert record["prior_state"] == "activated"
        assert record["portion_multiplier"] == round(1 / 0.85, 2) == 1.18

    def test_estimate_portion_scales_medium_grain(self):
        portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        portion.save_portion_prior("white_rice", 1 / 0.85, "portion", "too_low")
        result = portion.estimate_portion(_crop(250), "white_rice")  # ratio 0.25 -> medium
        assert result["portion_bucket"] == "medium"
        assert result["portion_g"] == 212.4  # 180 * 1.18


@pytest.mark.safety
class TestSC03OverCorrectingIsReversible:
    """As a user who corrected too aggressively, I want to correct back and
    land exactly where I started — not slightly below it.

    Why this test exists: the design (results.jsx:612) uses a symmetric
    +-15%/+-40%, which is NOT invertible (down-a-lot then up-a-lot lands at
    0.84). _FACTORS uses reciprocals instead so a round trip returns to
    exactly 1.0. If anyone "simplifies" _FACTORS back to the symmetric
    values, this test fails.
    """

    def test_lot_round_trip_returns_to_exactly_one(self):
        portion.save_portion_prior("beef", 0.60, "portion", "too_high")
        record = portion.save_portion_prior("beef", 0.60, "portion", "too_high")
        assert record["portion_multiplier"] == 0.60

        record = portion.save_portion_prior("beef", 1 / 0.60, "portion", "too_low")
        assert record["portion_multiplier"] == 1.0

    @pytest.mark.parametrize(
        "direction,opposite",
        [
            ("too_high", "too_low"),
            ("too_low", "too_high"),
        ],
    )
    @pytest.mark.parametrize("magnitude", ["little", "lot"])
    def test_round_trip_all_factor_pairs(self, direction, opposite, magnitude):
        dish = f"dish_{direction}_{opposite}_{magnitude}"
        factor = portion._FACTORS[(direction, magnitude)]
        inverse = portion._FACTORS[(opposite, magnitude)]

        portion.save_portion_prior(dish, factor, "portion", direction)
        record = portion.save_portion_prior(dish, factor, "portion", direction)
        assert record["active"] is True

        record = portion.save_portion_prior(dish, inverse, "portion", opposite)
        assert record["portion_multiplier"] == 1.0


@pytest.mark.safety
class TestSC04CorrectionsCanNeverRunAway:
    """As a user who has corrected a dish many times, I want the estimate to stay sane."""

    def test_ten_consecutive_too_high_lot_clamps_at_min(self):
        for _ in range(10):
            record = portion.save_portion_prior("beef", 0.60, "portion", "too_high")
        assert record["portion_multiplier"] == portion._MULTIPLIER_MIN

    def test_ten_consecutive_too_low_lot_clamps_at_max(self):
        for _ in range(10):
            record = portion.save_portion_prior("beef", 1 / 0.60, "portion", "too_low")
        assert record["portion_multiplier"] == portion._MULTIPLIER_MAX

    def test_multiplier_always_in_bounds_alternating(self):
        dish = "alternating_dish"
        directions = [("too_high", 0.60), ("too_low", 1 / 0.60)] * 5 + [("too_high", 0.60)] * 3
        for direction, factor in directions:
            record = portion.save_portion_prior(dish, factor, "portion", direction)
            if "portion_multiplier" in record:
                assert portion._MULTIPLIER_MIN <= record["portion_multiplier"] <= portion._MULTIPLIER_MAX


class TestSC05ActuallyIChangedMyMind:
    """As a user who said "too much" then immediately "too little", I want
    that treated as me correcting myself, not two pieces of evidence."""

    def test_contradiction_replaces_pending_and_resets_count(self):
        first = portion.save_portion_prior("dish5", 0.85, "portion", "too_high")
        assert first["prior_state"] == "pending"
        assert first["n_corrections"] == 1

        second = portion.save_portion_prior("dish5", 1 / 0.85, "portion", "too_low")
        assert second["prior_state"] == "pending"
        assert second["n_corrections"] == 1
        assert second["pending_direction"] == "too_low"
        assert second["reasons"] == {"portion": 1}


@pytest.mark.safety
class TestSC06IJustDidntFinishThisPlate:
    """As a user who left half my noodles, I want today's carb count adjusted
    but I do NOT want SikFan to decide this dish is permanently smaller.

    Why this test exists: `leftover` is a scope ("just this meal"), not a
    reason. Confusing the two teaches a permanent under-estimate from a
    one-off event -> chronic under-dosing.
    """

    def test_leftover_never_activates_regardless_of_repetition(self):
        for i in range(5):
            record = portion.save_portion_prior("noodles", 0.60, "leftover", "too_high")
            assert record["prior_state"] == "counted"
            assert record["active"] is False
            assert record["n_corrections"] == i + 1
        assert portion._read_multiplier("noodles") == 1.0


class TestSC06bOmittingTheReasonIsTheSameAsStatingIt:
    """As a user tapping "Too low -> A little" with no other controls, I want
    that to teach the dish exactly as if I'd said "the portion was different."
    """

    def test_none_reason_matches_explicit_portion_reason(self):
        r1 = portion.save_portion_prior("dish6b", 0.85, None, "too_high")
        assert r1["prior_state"] == "pending"
        assert r1["reasons"] == {"portion": 1}

        r2 = portion.save_portion_prior("dish6b", 0.85, None, "too_high")
        assert r2["prior_state"] == "activated"
        assert r2["reasons"] == {"portion": 2}

    def test_persisted_reasons_is_portion_only(self):
        assert portion.PERSISTED_REASONS == {"portion"}

    def test_reason_outside_allowed_set_raises(self):
        with pytest.raises(ValueError):
            portion.save_portion_prior("dish6b_bad", 0.85, "broth", "too_high")
        with pytest.raises(ValueError):
            portion.save_portion_prior("dish6b_bad", 0.85, "hidden", "too_high")


class TestSC06cOldRecordsWithRetiredReasonsStillWork:
    """As someone who corrected a dish before this change, I don't want my
    learned portion prior to reset just because "hidden"/"broth" aren't
    offered anymore."""

    def _write_legacy_record(self, dish_name="old_dish"):
        path = portion._prior_path(dish_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "dish_name": dish_name,
            "active": True,
            "n_corrections": 2,
            "reasons": {"hidden": 1, "broth": 1},
            "portion_multiplier": 0.85,
            "last_scanned": portion._now_iso(),
        }
        path.write_text(json.dumps(record))
        return record

    def test_legacy_multiplier_still_applies(self):
        self._write_legacy_record()
        assert portion._read_multiplier("old_dish") == 0.85

    def test_legacy_record_still_accepts_and_compounds_new_corrections(self):
        self._write_legacy_record()
        record = portion.save_portion_prior("old_dish", 0.85, "portion", "too_high")
        assert record["prior_state"] == "compounded"
        assert record["reasons"] == {"hidden": 1, "broth": 1, "portion": 1}
        assert record["portion_multiplier"] == round(0.85 * 0.85, 2)


class TestSC07PriorsExpireEvidenceDoesnt:
    """As a user coming back after six months, I want a stale learned portion
    to stop applying — but if I confirm again, I don't want to start from scratch."""

    def _write_stale_record(self, dish_name="stale_dish", days_old=200):
        path = portion._prior_path(dish_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        record = {
            "dish_name": dish_name,
            "active": True,
            "n_corrections": 2,
            "reasons": {"portion": 2},
            "portion_multiplier": 0.85,
            "last_scanned": old_ts,
        }
        path.write_text(json.dumps(record))
        return record

    def test_stale_prior_skipped_but_file_and_count_survive(self):
        self._write_stale_record()
        assert portion._read_multiplier("stale_dish") == 1.0
        path = portion._prior_path("stale_dish")
        record = json.loads(path.read_text())
        assert record["n_corrections"] == 2

    def test_touch_revives_a_stale_prior(self):
        self._write_stale_record()
        assert portion._read_multiplier("stale_dish") == 1.0
        portion.touch_portion_prior("stale_dish")
        assert portion._read_multiplier("stale_dish") == 0.85

    def test_touch_is_noop_for_unknown_dish(self):
        portion.touch_portion_prior("never_scanned")  # must not raise

    def test_looks_right_clears_pending_correction(self):
        portion.save_portion_prior("pend_dish", 0.85, "portion", "too_high")
        record = portion.save_portion_prior("pend_dish", 1.0, None, "looks_right")
        assert record["prior_state"] == "cleared"
        assert "pending_direction" not in record
        assert "pending_factor" not in record

    def test_looks_right_leaves_active_prior_untouched(self):
        portion.save_portion_prior("act_dish", 0.85, "portion", "too_high")
        portion.save_portion_prior("act_dish", 0.85, "portion", "too_high")
        before = portion._read_multiplier("act_dish")

        record = portion.save_portion_prior("act_dish", 1.0, None, "looks_right")

        assert record["prior_state"] == "confirmed"
        assert portion._read_multiplier("act_dish") == before

    def test_looks_right_with_reason_raises(self):
        with pytest.raises(ValueError):
            portion.save_portion_prior("x", 1.0, "portion", "looks_right")

    def test_looks_right_with_nonone_factor_raises(self):
        with pytest.raises(ValueError):
            portion.save_portion_prior("x", 0.85, None, "looks_right")

    def test_looks_right_with_supersedes_raises(self):
        with pytest.raises(ValueError):
            portion.save_portion_prior("x", 1.0, None, "looks_right", supersedes=0.85)

    def test_layer_order_priors_beat_overrides_beat_cache(self):
        portion.PRIORS_DIR.mkdir(parents=True, exist_ok=True)
        portion.OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
        portion.CACHE_DIR.mkdir(parents=True, exist_ok=True)

        (portion.CACHE_DIR / "layer_dish.json").write_text(
            json.dumps({"dish_name": "layer_dish", "portion_multiplier": 2.0})
        )
        assert portion._read_multiplier("layer_dish") == 2.0

        (portion.OVERRIDES_DIR / "layer_dish.json").write_text(
            json.dumps({"dish_name": "layer_dish", "portion_multiplier": 1.5})
        )
        assert portion._read_multiplier("layer_dish") == 1.5

        portion.save_portion_prior("layer_dish", 0.85, "portion", "too_high")
        portion.save_portion_prior("layer_dish", 0.85, "portion", "too_high")
        assert portion._read_multiplier("layer_dish") == 0.85

    def test_macro_only_override_falls_through_to_cache_multiplier(self):
        portion.OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
        portion.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (portion.OVERRIDES_DIR / "fall_dish.json").write_text(
            json.dumps({"dish_name": "fall_dish", "source": "user_override", "carbs_g": 10})
        )
        (portion.CACHE_DIR / "fall_dish.json").write_text(
            json.dumps({"dish_name": "fall_dish", "source": "usda_api", "portion_multiplier": 1.5})
        )
        assert portion._read_multiplier("fall_dish") == 1.5
