"""SC-08 ... SC-10 — portion estimation from the photo (pipeline/portion.py).

Numbers are asserted against the real config.yaml (via the config_path param
that estimate_portion()/estimate_portions_mixed() already accept), so a test
here catches an accidental edit to the shipped gram tables.
"""

from __future__ import annotations

import pytest

import pipeline.portion as portion


def _crop(mask_pixels, image_pixels=1000, **extra):
    return {"mask_pixels": mask_pixels, "image_pixels": image_pixels, **extra}


class TestSC08CategoryTables:
    """As a user photographing different foods, I want the gram estimate to
    reflect what the food actually is."""

    @pytest.mark.parametrize(
        "dish_name,expected_category_bucket_g",
        [
            ("white_rice", 180),      # grain, medium
            ("braised_beef", 150),    # protein, medium
            ("bok_choy", 120),        # vegetable, medium
            ("mystery_sauce", 160),   # default, medium
        ],
    )
    def test_dish_maps_to_expected_category(self, dish_name, expected_category_bucket_g):
        result = portion.estimate_portion(_crop(250), dish_name)  # ratio 0.25 -> medium
        assert result["portion_bucket"] == "medium"
        assert result["portion_g"] == expected_category_bucket_g

    def test_explicit_role_base_beats_keyword_scan(self):
        # "mystery_sauce" would default to 'default' by keyword scan, but an
        # explicit role="base" must win and use the grain table instead.
        result = portion.estimate_portion(_crop(250), "mystery_sauce", role="base")
        assert result["portion_g"] == 180  # grain, medium

    def test_bucket_boundary_small_max_is_pinned(self):
        # ratio just under 0.15 -> small; just at/over -> medium
        just_under = portion.estimate_portion(_crop(149, 1000), "white_rice")
        at_boundary = portion.estimate_portion(_crop(150, 1000), "white_rice")
        assert just_under["portion_bucket"] == "small"
        assert at_boundary["portion_bucket"] == "medium"

    def test_bucket_boundary_medium_max_is_pinned(self):
        just_under = portion.estimate_portion(_crop(349, 1000), "white_rice")
        at_boundary = portion.estimate_portion(_crop(350, 1000), "white_rice")
        assert just_under["portion_bucket"] == "medium"
        assert at_boundary["portion_bucket"] == "large"


class TestSC09AMixedBowlSplitsSensibly:
    """As a user photographing a rice bowl, I want the rice, meat and greens
    counted separately."""

    def test_role_weights_2_1_1_split(self):
        crop_result = {
            "mask_pixels": 600,
            "image_pixels": 1000,  # ratio 0.6 -> large -> bowl_total 700
            "components": [
                {"dish_name": "white_rice", "role": "base"},
                {"dish_name": "braised_beef", "role": "protein"},
                {"dish_name": "bok_choy", "role": "vegetable"},
            ],
        }
        result = portion.estimate_portions_mixed(crop_result)
        assert result["warnings"] == []
        fractions = [c["portion_fraction"] for c in result["components"]]
        assert fractions == [0.5, 0.25, 0.25]  # weights 2:1:1
        assert sum(fractions) == 1.0

        grams = [c["portion_g"] for c in result["components"]]
        assert sum(grams) == 700  # bowl_total large


class TestSC10OnlyTheRiceWasDetected:
    """As a user whose photo only resolved the rice, I want to be told the
    bowl was partially read - and I do not want the rice alone counted as if
    it were the whole bowl.

    Why this test exists: the alternative silently attributes a full bowl's
    grams to one component - an over-count on a plate the user can see is
    wrong.
    """

    def test_guard_uses_grain_table_not_bowl_total(self):
        crop_result = {
            "mask_pixels": 250,
            "image_pixels": 1000,  # ratio 0.25 -> medium
            "components": [{"dish_name": "white_rice", "role": "base"}],
        }
        result = portion.estimate_portions_mixed(crop_result)
        component = result["components"][0]
        assert component["portion_g"] == 180  # grain medium, NOT bowl_total medium (500)
        assert component["partial_detection"] is True

    def test_guard_emits_only_base_detected_warning(self):
        crop_result = {
            "mask_pixels": 250,
            "image_pixels": 1000,
            "components": [{"dish_name": "white_rice", "role": "base"}],
        }
        result = portion.estimate_portions_mixed(crop_result)
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["reason"] == "only_base_detected"
