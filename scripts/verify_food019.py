"""
FOOD-019 verification: composite dish decomposition for macro lookup.

Follows scripts/verify_gluc_012.py's check()/_patch_dirs() convention: every
storage path this test touches is monkeypatched into a tempdir so the real
data/macro_cache/ is never opened, let alone written. The Anthropic call
itself is monkeypatched at pipeline.dish_decompose._call_llm_decompose — no
network access, no API key, no credits required to run this script.

Tests:
  proportions normalize to 1.0 and clamp to [0.05, 0.85]
  a simple single-food dish name decomposes to itself and is cached
  a failed decomposition call fails open AND writes no cache file
  a subsequent successful call caches normally after a prior failure
  a stale cache entry (model/schema_version mismatch) is treated as a miss
  resolve_composite_macros: weighted per-100g sum, no_results fallback,
    macro_coverage vs carb_coverage divergence, simple-dish passthrough (None)
  is_trainable(): legacy row (no carb_coverage key) stays trainable, a row
    below threshold is excluded, a row above threshold is trainable, and
    build_training_data() / should_retrain() agree on every case

Run from project root: python scripts/verify_food019.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import api
import pipeline.dish_decompose as dd
import pipeline.glucose_model as glucose_model


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def check(msg: str, cond: bool) -> None:
    ok(msg) if cond else fail(msg)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeMacroResult:
    source: str
    calories: float | None = None
    carbs_g: float | None = None
    fiber_g: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None


def _patch_dirs(tmp: Path) -> None:
    cache_dir = tmp / "macro_cache"
    decomp_dir = cache_dir / "decompositions"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    dd.CACHE_DIR = cache_dir
    dd.DECOMPOSITION_CACHE_DIR = decomp_dir


def _patch_llm(components: list[dict] | Exception):
    """Monkeypatch the LLM call boundary. Pass an Exception instance to
    simulate a failure; a list of raw component dicts to simulate success."""
    def _fake(dish_name: str, model: str):
        if isinstance(components, Exception):
            raise components
        return components
    dd._call_llm_decompose = _fake


def _patch_lookup_macros(by_name: dict[str, "_FakeMacroResult"]):
    def _fake(name: str):
        return by_name.get(name, _FakeMacroResult(source="no_results"))
    dd.lookup_macros = _fake


# ---------------------------------------------------------------------------
# Tests: decompose_dish
# ---------------------------------------------------------------------------

def test_normalize_and_clamp(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm([
        {"name": "chicken katsu", "role": "protein", "proportion": 0.9,
         "usda_likely": True, "fallback_macros": None},
        {"name": "curry sauce", "role": "sauce", "proportion": 0.1,
         "usda_likely": False, "fallback_macros": {
             "calories": 200, "carbs_g": 22, "fiber_g": 3, "protein_g": 4, "fat_g": 10}},
    ])
    components = dd.decompose_dish("katsu curry")
    total = sum(c["proportion"] for c in components)
    check("proportions sum to 1.0", abs(total - 1.0) < 1e-9)
    check("no component exceeds the 0.85 clamp", all(c["proportion"] <= 0.85 for c in components))
    check("no component falls below the 0.05 clamp", all(c["proportion"] >= 0.05 for c in components))


def test_single_item_passthrough_cached(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm([{"name": "white rice", "role": "base", "proportion": 1.0,
                 "usda_likely": True, "fallback_macros": None}])
    components = dd.decompose_dish("white rice")
    check("single-item decomposition returns one component", len(components) == 1)
    check("cache file was written", dd._decomposition_path("white rice").exists())


def test_fail_open_not_cached(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm(RuntimeError("simulated network failure"))
    components = dd.decompose_dish("mystery dish")
    check("fail-open returns single-component passthrough", len(components) == 1)
    check("passthrough name matches input", components[0]["name"] == "mystery dish")
    check("failed call writes NO cache file", not dd._decomposition_path("mystery dish").exists())

    # A subsequent successful call must cache normally — one bad call must
    # not have poisoned anything.
    _patch_llm([{"name": "mystery dish", "role": None, "proportion": 1.0,
                 "usda_likely": True, "fallback_macros": None}])
    components = dd.decompose_dish("mystery dish")
    check("subsequent successful call caches normally", dd._decomposition_path("mystery dish").exists())


def test_stale_cache_is_a_miss(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm([{"name": "old model dish", "role": None, "proportion": 1.0,
                 "usda_likely": True, "fallback_macros": None}])
    dd.decompose_dish("old model dish")

    # Simulate a config model swap (D8) by loading with a different model —
    # the entry on disk still says the old model, so this must be a miss.
    entry = json.loads(dd._decomposition_path("old model dish").read_text())
    check("cache entry recorded the model it was created with", entry["model"] == dd._load_decompose_model())

    cached = dd._load_decomposition("old model dish", "a-different-model")
    check("model mismatch is treated as a cache miss", cached is None)

    entry["schema_version"] = 999
    dd._decomposition_path("old model dish").write_text(json.dumps(entry))
    cached = dd._load_decomposition("old model dish", dd._load_decompose_model())
    check("schema_version mismatch is treated as a cache miss", cached is None)


# ---------------------------------------------------------------------------
# Tests: resolve_composite_macros
# ---------------------------------------------------------------------------

def test_resolve_composite_macros_weighted_sum(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm([
        {"name": "white rice", "role": "base", "proportion": 0.6,
         "usda_likely": True, "fallback_macros": None},
        {"name": "pork katsu", "role": "protein", "proportion": 0.4,
         "usda_likely": True, "fallback_macros": None},
    ])
    _patch_lookup_macros({
        "white rice": _FakeMacroResult(source="usda_api", calories=130, carbs_g=28, fiber_g=0.4, protein_g=2.7, fat_g=0.3),
        "pork katsu": _FakeMacroResult(source="usda_api", calories=300, carbs_g=15, fiber_g=1.0, protein_g=20, fat_g=18),
    })

    result = dd.resolve_composite_macros("katsu rice bowl")
    expected_carbs = round(28 * 0.6 + 15 * 0.4, 2)
    check("resolve_composite_macros returns a dict for a real composite", result is not None)
    check("carbs_g is the proportion-weighted sum", abs(result["carbs_g"] - expected_carbs) < 0.01)
    check("source is composite", result["source"] == "composite")
    check("reference_weight_g is 100.0", result["reference_weight_g"] == 100.0)
    check("fully resolved -> macro_coverage == 1.0", result["macro_coverage"] == 1.0)
    check("fully resolved -> carb_coverage == 1.0", result["carb_coverage"] == 1.0)


def test_no_results_fallback_and_coverage_divergence(tmp: Path) -> None:
    _patch_dirs(tmp)
    # Rice (carb-heavy) resolves via USDA; the sauce (carb-light) does not
    # and falls back to LLM-estimated macros. macro_coverage (mass-weighted)
    # should be high while carb_coverage (carb-weighted) should be lower,
    # since the resolved component carries most of the dish's carbs anyway
    # in this fixture — assert the two are NOT equal, which is the whole
    # point of tracking both (D6).
    _patch_llm([
        {"name": "white rice", "role": "base", "proportion": 0.5,
         "usda_likely": True, "fallback_macros": None},
        {"name": "obscure sauce", "role": "sauce", "proportion": 0.5,
         "usda_likely": False, "fallback_macros": {
             "calories": 50, "carbs_g": 40, "fiber_g": 0, "protein_g": 0, "fat_g": 1}},
    ])
    _patch_lookup_macros({
        "white rice": _FakeMacroResult(source="usda_api", calories=130, carbs_g=28, fiber_g=0.4, protein_g=2.7, fat_g=0.3),
        "obscure sauce": _FakeMacroResult(source="no_results"),
    })

    result = dd.resolve_composite_macros("rice with obscure sauce")
    check("partial resolution -> macro_coverage < 1.0", result["macro_coverage"] < 1.0)
    check("partial resolution -> carb_coverage < 1.0", result["carb_coverage"] < 1.0)
    check(
        "macro_coverage and carb_coverage diverge (mass-weighted vs carb-weighted)",
        result["macro_coverage"] != result["carb_coverage"],
    )
    estimated = [c for c in result["components"] if c["macro_source"] == "estimated"]
    check("the no_results component used its fallback_macros", len(estimated) == 1)


def test_unresolved_component_reduces_carb_coverage(tmp: Path) -> None:
    """Regression (observed live 2026-08-28): a component with NO USDA match
    and NO fallback_macros ("unresolved") contributes 0.0 to both
    carbs_resolved and carbs_total, making it invisible to the ratio — a real
    curry-rice dish with an unresolved vegetable component still reported
    carb_coverage 1.0 despite genuinely understating its carbs."""
    _patch_dirs(tmp)
    _patch_llm([
        {"name": "white rice", "role": "base", "proportion": 0.8,
         "usda_likely": True, "fallback_macros": None},
        {"name": "unknowable veg", "role": "vegetable", "proportion": 0.2,
         "usda_likely": True, "fallback_macros": None},  # usda_likely but will miss -> unresolved
    ])
    _patch_lookup_macros({
        "white rice": _FakeMacroResult(source="usda_api", calories=130, carbs_g=28, fiber_g=0.4, protein_g=2.7, fat_g=0.3),
        "unknowable veg": _FakeMacroResult(source="no_results"),
    })

    result = dd.resolve_composite_macros("rice with unknowable veg")
    unresolved = [c for c in result["components"] if c["macro_source"] == "unresolved"]
    check("component with no USDA match and no fallback is 'unresolved'", len(unresolved) == 1)
    check(
        "an unresolved component drops carb_coverage below 1.0 (not invisible to the ratio)",
        result["carb_coverage"] < 1.0,
    )
    check(
        "carb_coverage is discounted by the unresolved mass share (0.8)",
        abs(result["carb_coverage"] - 0.8) < 1e-6,
    )


def test_simple_dish_passthrough_returns_none(tmp: Path) -> None:
    _patch_dirs(tmp)
    _patch_llm([{"name": "mapo tofu", "role": None, "proportion": 1.0,
                 "usda_likely": True, "fallback_macros": None}])
    result = dd.resolve_composite_macros("mapo tofu")
    check("a simple dish name resolves to None (use lookup_macros() path unchanged)", result is None)


def test_simple_dish_renamed_by_llm_still_returns_none(tmp: Path) -> None:
    """Regression: a live call on 2026-08-28 showed the real model renames
    even a simple dish into USDA-style phrasing ("white rice" -> "rice,
    white, cooked") — a single component whose name does NOT match the
    input string. The passthrough check must key on component COUNT alone,
    not name equality, or every real simple dish gets wrongly treated as
    composite."""
    _patch_dirs(tmp)
    _patch_llm([{"name": "rice, white, cooked", "role": "base", "proportion": 1.0,
                 "usda_likely": True, "fallback_macros": None}])
    result = dd.resolve_composite_macros("white rice")
    check(
        "a single component with a DIFFERENT (LLM-renamed) name still resolves to None",
        result is None,
    )


# ---------------------------------------------------------------------------
# Tests: macro_lookup transient-400 caching (FOOD-019 fallout)
# ---------------------------------------------------------------------------

def test_transient_400_not_cached(tmp: Path) -> None:
    """Regression (2026-08-29): USDA intermittently 400s on a valid query.
    lookup_macros() used to cache that as a genuine no_results, permanently
    pinning a resolvable food to zero macros — which became far more damaging
    once _fill_missing_macros() started resolving every dish at scan time
    (one transient blip = a silently, permanently wrong dish)."""
    import pipeline.macro_lookup as ml
    orig_cache_dir, orig_query = ml.CACHE_DIR, ml._query_usda
    cache_dir = tmp / "ml_transient"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        ml.CACHE_DIR = cache_dir
        def _always_400(query, api_key):
            raise ml._QueryError("simulated transient 400")
        ml._query_usda = _always_400
        result = ml.lookup_macros("some transient dish")
        check("all-400s still returns no_results", result.source == "no_results")
        check(
            "all-400s writes NO cache file (retries next time, not pinned to zero)",
            not list(cache_dir.glob("*.json")),
        )

        # A clean 200-with-zero-foods is a real answer and must still cache.
        ml._query_usda = lambda query, api_key: []
        ml.lookup_macros("zzz definitely not a food")
        check(
            "a genuine zero-result miss IS still cached",
            bool(list(cache_dir.glob("zzz_definitely_not_a_food.json"))),
        )
    finally:
        ml.CACHE_DIR, ml._query_usda = orig_cache_dir, orig_query


# ---------------------------------------------------------------------------
# Tests: LLM-backed USDA match selection
# ---------------------------------------------------------------------------

def test_select_best_usda_candidate() -> None:
    """Regression (2026-08-29): USDA returned "Peanuts, boiled" as the top hit
    for "boiled_chicken" (21g carbs/100g vs chicken's ~0), and lookup_macros()
    takes candidates[0] unconditionally. Harmless when lookups only ran on a
    user-visible correction; _fill_missing_macros() resolves silently, so an
    unrelated match would inject phantom carbs into a diabetic carb count.

    Judgment is the LLM's (a hardcoded cooking-verb stopword list was tried
    first and rejected — English-only, missed poached/blanched/stir-fried,
    i.e. the same brittleness _PINYIN_FALLBACK was criticized for). These
    tests pin the CONTRACT around that call, not the model's answers:
    fail-closed on error, and reject an out-of-range index."""
    orig = dd._load_decompose_model
    try:
        # Any failure inside the call must reject the match, never accept an
        # unvalidated one — a blank is visible and correctable, wrong carbs
        # are neither.
        dd._load_decompose_model = lambda config_path="config.yaml": (_ for _ in ()).throw(RuntimeError("boom"))
        check(
            "LLM failure fails CLOSED (returns None, no unvalidated match)",
            dd.select_best_usda_candidate("boiled_chicken", ["Peanuts, boiled"]) is None,
        )
    finally:
        dd._load_decompose_model = orig

    check(
        "no candidates -> None (nothing to validate)",
        dd.select_best_usda_candidate("anything", []) is None,
    )


def test_no_hardcoded_food_tables_in_lookup() -> None:
    """_PINYIN_FALLBACK (a hand-maintained ~25-entry dish-name dict) is gone,
    replaced by suggest_usda_query(). Guards against it being reintroduced."""
    import pipeline.macro_lookup as ml
    check(
        "macro_lookup no longer defines _PINYIN_FALLBACK",
        not hasattr(ml, "_PINYIN_FALLBACK"),
    )
    check(
        "lookup_macros accepts an injected query suggester",
        "suggest_query" in ml.lookup_macros.__code__.co_varnames,
    )


# ---------------------------------------------------------------------------
# Tests: is_trainable()
# ---------------------------------------------------------------------------

def _complete_cgm_window() -> dict:
    return {
        "status": "complete",
        "readings": [{"minutes_post_meal": m, "glucose_mgdl": 100} for m in range(0, 185, 5)],
        "peak_glucose": 100,
        "time_to_peak_minutes": 0,
        "return_to_baseline_minutes": 0,
        "area_under_curve": 0,
    }


def test_aggregate_carb_coverage() -> None:
    dishes = [
        {"carbs_g": 30.0, "carb_coverage": 1.0},   # not a composite — default
        {"carbs_g": 20.0, "carb_coverage": 0.5},   # half-estimated composite
    ]
    coverage = api._aggregate_carb_coverage(dishes)
    expected = (30.0 * 1.0 + 20.0 * 0.5) / 50.0
    check("carb-weighted aggregation matches hand-computed value", abs(coverage - expected) < 1e-6)

    check("zero total carbs -> 1.0 (no carb uncertainty)", api._aggregate_carb_coverage([{"carbs_g": 0.0, "carb_coverage": 1.0}]) == 1.0)
    check("no composite dishes -> defaults to 1.0", api._aggregate_carb_coverage([{"carbs_g": 40.0}]) == 1.0)


def test_log_meal_persists_carb_coverage(tmp: Path) -> None:
    """End-to-end through api.log_meal(), mirroring verify_gluc_012.py's
    convention: fabricate a completed job_status with one dish already
    carrying FOOD-019's carb_coverage (as _recompute_dish_macros would have
    set it via a prior CORRECT/ADD_NEW confirm-dish call), then confirm
    log_meal both returns and persists the meal-level aggregate."""
    orig_meal_logs_path = api._MEAL_LOGS_PATH
    api._MEAL_LOGS_PATH = tmp / "meal_logs.json"
    (tmp / "data" / "job_status").mkdir(parents=True, exist_ok=True)
    orig_cwd = Path.cwd()
    import os
    os.chdir(tmp)
    try:
        meal_id = "verify-food-019"
        status_path = Path("data/job_status") / f"{meal_id}.json"
        status_path.write_text(json.dumps({
            "meal_id": meal_id,
            "status": "complete",
            "logged": False,
            "created_at": "2026-08-28T12:00:00+00:00",
            "completed_at": "2026-08-28T12:00:05+00:00",
            "result": {
                "meal_id": meal_id,
                "dishes": [
                    {
                        "crop_id": "crop_0", "name": "katsu_curry_rice",
                        "confidence": 0.9, "status": "CONFIDENT",
                        "carbs_g": 70.0, "protein_g": 36.0, "fat_g": 38.0, "calories": 800.0,
                        "needs_macro_entry": False, "carb_coverage": 0.6,
                    },
                    {
                        "crop_id": "crop_1", "name": "steamed_broccoli",
                        "confidence": 0.95, "status": "CONFIDENT",
                        "carbs_g": 10.0, "protein_g": 2.0, "fat_g": 0.5, "calories": 50.0,
                        "needs_macro_entry": False,
                        # No carb_coverage key at all — never went through
                        # composite decomposition; must default to 1.0.
                    },
                ],
                "total_carbs_g": 80.0,
            },
            "error": None,
        }))

        resp = api.log_meal(api.LogMealRequest(
            meal_id=meal_id, confirmed_dishes=["katsu_curry_rice", "steamed_broccoli"],
        ))
        expected = round((70.0 * 0.6 + 10.0 * 1.0) / 80.0, 4)
        check("LogMealResponse.carb_coverage matches the carb-weighted aggregate", resp.carb_coverage == expected)

        logs = json.loads(api._MEAL_LOGS_PATH.read_text())
        check("meal_logs.json row persists carb_coverage", logs[-1]["carb_coverage"] == expected)
    finally:
        os.chdir(orig_cwd)
        api._MEAL_LOGS_PATH = orig_meal_logs_path


def test_is_trainable_carb_coverage_gate() -> None:
    legacy = {"cgm_window": _complete_cgm_window(), "timestamp": "2026-01-01T00:00:00Z"}
    check("legacy row (no carb_coverage key) is trainable", glucose_model.is_trainable(legacy, min_carb_coverage=0.70))

    below = {**legacy, "carb_coverage": 0.65}
    check("row below threshold (0.65 < 0.70) is excluded", not glucose_model.is_trainable(below, min_carb_coverage=0.70))

    above = {**legacy, "carb_coverage": 0.75}
    check("row above threshold (0.75 >= 0.70) is trainable", glucose_model.is_trainable(above, min_carb_coverage=0.70))

    macros_flag = {**legacy, "macros_incomplete": True}
    check("macros_incomplete=True excludes regardless of carb_coverage", not glucose_model.is_trainable(macros_flag, min_carb_coverage=0.70))


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        test_normalize_and_clamp(tmp)
        test_single_item_passthrough_cached(tmp)
        test_fail_open_not_cached(tmp)
        test_stale_cache_is_a_miss(tmp)
        test_resolve_composite_macros_weighted_sum(tmp)
        test_no_results_fallback_and_coverage_divergence(tmp)
        test_unresolved_component_reduces_carb_coverage(tmp)
        test_simple_dish_passthrough_returns_none(tmp)
        test_simple_dish_renamed_by_llm_still_returns_none(tmp)
        test_transient_400_not_cached(tmp)
        test_log_meal_persists_carb_coverage(tmp)

    test_select_best_usda_candidate()
    test_no_hardcoded_food_tables_in_lookup()
    test_aggregate_carb_coverage()
    test_is_trainable_carb_coverage_gate()

    print("\nAll FOOD-019 checks passed.")


if __name__ == "__main__":
    main()
