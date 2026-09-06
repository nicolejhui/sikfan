"""SC-11 ... SC-14 — manual macros and precedence (pipeline/nutrition.py)."""

from __future__ import annotations

import json

import pytest

import pipeline.nutrition as nutrition
import pipeline.macro_lookup as macro_lookup


_VALID_MACROS = {"calories": 200.0, "carbs_g": 30.0, "fiber_g": 2.0, "protein_g": 10.0, "fat_g": 5.0}


@pytest.mark.safety
class TestSC11BadMacrosNeverReachADose:
    """As a user typing in my own numbers, I want a typo rejected loudly
    rather than stored. fiber_g is mandatory because net carbs drive the dose."""

    def test_missing_fiber_g_raises_and_writes_nothing(self):
        macros = {k: v for k, v in _VALID_MACROS.items() if k != "fiber_g"}
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_a", macros)
        assert not nutrition._override_path("dish_a").exists()

    def test_negative_carbs_raises_and_writes_nothing(self):
        macros = {**_VALID_MACROS, "carbs_g": -5.0}
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_b", macros)
        assert not nutrition._override_path("dish_b").exists()

    def test_string_value_raises_and_writes_nothing(self):
        macros = {**_VALID_MACROS, "carbs_g": "12"}
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_c", macros)
        assert not nutrition._override_path("dish_c").exists()

    def test_none_value_raises_and_writes_nothing(self):
        macros = {**_VALID_MACROS, "carbs_g": None}
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_d", macros)
        assert not nutrition._override_path("dish_d").exists()

    def test_nonpositive_reference_weight_raises_and_writes_nothing(self):
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_e", dict(_VALID_MACROS), reference_weight_g=0)
        with pytest.raises(ValueError):
            nutrition.set_manual_override("dish_e", dict(_VALID_MACROS), reference_weight_g=-100)
        assert not nutrition._override_path("dish_e").exists()


class TestSC12MyNumbersBeatUSDAs:
    """As a user who has corrected a dish's macros, I want my version used
    from then on — and I want to be able to undo that."""

    def test_override_wins_over_cache(self):
        cache_path = nutrition._cache_path("mapo_tofu")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "dish_name": "mapo_tofu", "source": "usda_api", "reference_weight_g": 100.0,
            "calories": 999, "carbs_g": 999, "fiber_g": 999, "protein_g": 999, "fat_g": 999,
        }))
        nutrition.set_manual_override("mapo_tofu", _VALID_MACROS)

        result = nutrition.get_macros("mapo_tofu")
        assert result["source"] == "user_override"
        assert result["carbs_g"] == 30.0

    def test_reset_override_reverts_to_cache(self):
        cache_path = nutrition._cache_path("mapo_tofu")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "dish_name": "mapo_tofu", "source": "usda_api", "reference_weight_g": 100.0,
            "calories": 999, "carbs_g": 999, "fiber_g": 999, "protein_g": 999, "fat_g": 999,
        }))
        nutrition.set_manual_override("mapo_tofu", _VALID_MACROS)

        deleted = nutrition.reset_override("mapo_tofu")
        assert deleted is True

        result = nutrition.get_macros("mapo_tofu")
        assert result["source"] == "usda_api"
        assert result["carbs_g"] == 999

    def test_reset_override_returns_false_when_nothing_to_delete(self):
        assert nutrition.reset_override("never_overridden") is False

    def test_pre_food013_cache_row_gets_default_reference_weight(self):
        cache_path = nutrition._cache_path("legacy_dish")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "dish_name": "legacy_dish", "source": "usda_api",
            "calories": 100, "carbs_g": 20, "fiber_g": 1, "protein_g": 5, "fat_g": 2,
        }))
        result = nutrition.get_macros("legacy_dish")
        assert result["reference_weight_g"] == 100.0


class TestSC13ScalingToTheEstimatedPortion:
    def test_scale_macros_at_250g_off_100g_reference(self):
        macros = {"reference_weight_g": 100.0, **_VALID_MACROS}
        from pipeline.portion import scale_macros
        result = scale_macros(macros, 250)
        assert result["carbs_g"] == 75.0   # 30 * 2.5
        assert result["calories"] == 500.0  # 200 * 2.5

    def test_scale_macros_returns_none_on_missing_field(self):
        from pipeline.portion import scale_macros
        macros = {**_VALID_MACROS, "carbs_g": None, "reference_weight_g": 100.0}
        assert scale_macros(macros, 250) is None

    def test_scale_macros_returns_none_when_reference_weight_zero(self):
        from pipeline.portion import scale_macros
        macros = {**_VALID_MACROS, "reference_weight_g": 0}
        assert scale_macros(macros, 250) is None


class TestSC14OneDishOneFile:
    """As a user, "Mapo Tofu" and "mapo_tofu" are the same dish."""

    @pytest.mark.parametrize("name", ["Mapo Tofu", "  mapo tofu  ", "MAPO_TOFU"])
    def test_name_variants_resolve_to_one_file(self, name):
        nutrition.set_manual_override("Mapo Tofu", _VALID_MACROS)
        result = nutrition.get_macros(name)
        assert result is not None
        assert result["carbs_g"] == 30.0

    def test_known_divergence_slug_functions_disagree_on_punctuation(self):
        """Characterization test, not a fix: nutrition._slug keeps
        punctuation while macro_lookup._slug strips it, so the same dish can
        land in two different cache files. See docs/TEST-SCENARIOS.md SC-14
        and CLAUDE.md's Macro Lookup section."""
        name = "Kung Pao (spicy)"
        assert nutrition._slug(name) == "kung_pao_(spicy)"
        assert macro_lookup._slug(name) == "kung_pao_spicy"
        assert nutrition._slug(name) != macro_lookup._slug(name)
