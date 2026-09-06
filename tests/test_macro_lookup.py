"""SC-15 ... SC-18 — USDA lookup (pipeline/macro_lookup.py), HTTP mocked."""

from __future__ import annotations

import json

import pytest

import pipeline.macro_lookup as macro_lookup


_FOOD_HIT = {
    "fdcId": 12345,
    "description": "Rice, white, cooked",
    "dataType": "Survey (FNDDS)",
    "foodNutrients": [
        {"nutrientId": 1008, "value": 130.0},
        {"nutrientId": 1005, "value": 28.0},
        {"nutrientId": 1079, "value": 0.4},
        {"nutrientId": 1003, "value": 2.7},
        {"nutrientId": 1004, "value": 0.3},
    ],
}


@pytest.mark.safety
class TestSC15AUSDABlipMustNotBecomePermanentZeroCarbs:
    """As a user scanning a food on a bad network day, I want SikFan to retry
    next time - not decide forever that this food has no carbohydrates.

    Why this test exists: USDA's search endpoint intermittently 400s on a
    well-formed query. Caching that answer pins a resolvable food to zero
    carbs permanently.
    """

    def test_three_400s_return_no_results_without_caching(self, mock_usda):
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)

        result = macro_lookup.lookup_macros("boiled_chicken", suggest_query=lambda name: None)

        assert result.source == "no_results"
        assert not macro_lookup._cache_path("boiled_chicken").exists()

    def test_retry_makes_three_attempts(self, mock_usda):
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)

        macro_lookup.lookup_macros("boiled_chicken", suggest_query=lambda name: None)

        assert len(mock_usda.calls) == 3

    def test_subsequent_successful_call_caches_real_macros(self, mock_usda):
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        macro_lookup.lookup_macros("boiled_chicken", suggest_query=lambda name: None)
        assert not macro_lookup._cache_path("boiled_chicken").exists()

        mock_usda.queue(status_code=200, foods=[_FOOD_HIT])
        result = macro_lookup.lookup_macros("boiled_chicken")

        assert result.source == "usda_api"
        assert result.carbs_g == 28.0
        assert macro_lookup._cache_path("boiled_chicken").exists()


class TestSC16AGenuineMissIsCached:
    def test_zero_foods_200_response_is_cached_as_no_results(self, mock_usda):
        mock_usda.queue(status_code=200, foods=[])

        result = macro_lookup.lookup_macros("nonexistent_dish", suggest_query=lambda name: None)

        assert result.source == "no_results"
        assert macro_lookup._cache_path("nonexistent_dish").exists()

        cached = json.loads(macro_lookup._cache_path("nonexistent_dish").read_text())
        assert cached["source"] == "no_results"


class TestSC17ACachedDishNeverHitsTheNetwork:
    def test_cache_hit_makes_zero_http_calls(self, mock_usda):
        macro_lookup.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        macro_lookup._save_cache(
            macro_lookup.MacroResult(
                dish_name="cached_dish", usda_name="Cached Dish", fdc_id=1,
                calories=100, carbs_g=20, fiber_g=1, protein_g=5, fat_g=2,
                source="usda_api",
            )
        )

        result = macro_lookup.lookup_macros("cached_dish")

        assert result.source == "cache"
        assert mock_usda.calls == []


class TestSC18TheLLMRewriteIsAFallbackNotTheDefault:
    def test_fallback_fires_only_when_first_query_empty(self, mock_usda):
        mock_usda.queue(status_code=200, foods=[])       # primary query: no results
        mock_usda.queue(status_code=200, foods=[_FOOD_HIT])  # fallback query: hit

        suggest_calls = []

        def suggest_query(dish_name):
            suggest_calls.append(dish_name)
            return "rice porridge"

        result = macro_lookup.lookup_macros("congee", suggest_query=suggest_query)

        assert suggest_calls == ["congee"]
        assert result.source == "usda_api"
        assert result.carbs_g == 28.0

    def test_fallback_not_used_when_primary_query_succeeds(self, mock_usda):
        mock_usda.queue(status_code=200, foods=[_FOOD_HIT])

        suggest_calls = []
        macro_lookup.lookup_macros(
            "white_rice", suggest_query=lambda name: suggest_calls.append(name) or "x"
        )

        assert suggest_calls == []

    def test_never_calls_a_real_llm_default_fallback_bypassed(self, mock_usda):
        # suggest_query is always injected in these tests, never the module's
        # own _default_suggest_query (which would import dish_decompose and
        # could reach for a live LLM call).
        mock_usda.queue(status_code=200, foods=[])
        mock_usda.queue(status_code=200, foods=[])
        result = macro_lookup.lookup_macros("mystery_food", suggest_query=lambda name: None)
        assert result.source == "no_results"

    def test_fallback_success_clears_query_rejected_flag_and_caches(self, mock_usda):
        # Primary query 400s (query_rejected=True); fallback succeeds -> must
        # still cache (not silently blocked by the earlier rejection).
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=400)
        mock_usda.queue(status_code=200, foods=[_FOOD_HIT])

        result = macro_lookup.lookup_macros(
            "boiled_chicken", suggest_query=lambda name: "chicken breast boiled"
        )

        assert result.source == "usda_api"
        assert macro_lookup._cache_path("boiled_chicken").exists()

    def test_select_candidate_rewrites_cache_to_second_candidate(self, mock_usda):
        second_hit = {**_FOOD_HIT, "fdcId": 999, "description": "Rice, brown, cooked"}
        mock_usda.queue(status_code=200, foods=[_FOOD_HIT, second_hit])
        macro_lookup.lookup_macros("rice_dish")

        updated = macro_lookup.select_candidate("rice_dish", 1)

        assert updated.usda_name == "Rice, brown, cooked"
        cached = json.loads(macro_lookup._cache_path("rice_dish").read_text())
        assert cached["usda_name"] == "Rice, brown, cooked"

    def test_select_candidate_out_of_range_raises_index_error(self, mock_usda):
        mock_usda.queue(status_code=200, foods=[_FOOD_HIT])
        macro_lookup.lookup_macros("rice_dish")

        with pytest.raises(IndexError):
            macro_lookup.select_candidate("rice_dish", 5)
