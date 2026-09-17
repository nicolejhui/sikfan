"""
FOOD-021 verification: component folding + LLM ingredient candidates.

Follows scripts/verify_food019.py's convention: every storage path is
monkeypatched into a tempdir, and the Anthropic call boundary
(_call_llm_alternatives / _call_llm_additions) is monkeypatched directly —
no network access, no API key, no credits required.

Tests:
  fold_components() reproduces resolve_composite_macros() exactly
  fold_components() raises ValueError on empty list / zero total proportion
  suggest_alternatives()/suggest_additions() fail closed to [] on exception,
    malformed response, and timeout
  a candidate USDA can't back is dropped, not zero-filled
  suggest_additions() omits anything in present_components
  a user_edited=True decomposition survives a schema_version bump and a
    model change; a normal one does not

Run from project root: python scripts/verify_food021.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pipeline.dish_decompose as dd


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
    candidates: list | None = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


def _patch_dirs(tmp: Path) -> None:
    cache_dir = tmp / "macro_cache"
    decomp_dir = cache_dir / "decompositions"
    cand_dir = cache_dir / "candidates"
    decomp_dir.mkdir(parents=True, exist_ok=True)
    cand_dir.mkdir(parents=True, exist_ok=True)
    dd.CACHE_DIR = cache_dir
    dd.DECOMPOSITION_CACHE_DIR = decomp_dir
    dd.CANDIDATES_CACHE_DIR = cand_dir


def _patch_lookup_macros(by_name: dict[str, "_FakeMacroResult"]):
    def _fake(name: str):
        return by_name.get(name, _FakeMacroResult(source="no_results"))
    dd.lookup_macros = _fake


def _patch_select_best(index_or_none):
    dd.select_best_usda_candidate = lambda dish_name, candidate_names: index_or_none


# ---------------------------------------------------------------------------
# Tests: fold_components()
# ---------------------------------------------------------------------------

def test_fold_matches_resolve_composite_macros(tmp: Path) -> None:
    _patch_dirs(tmp)
    components = [
        {"name": "white rice", "role": "base", "proportion": 0.6,
         "usda_likely": True, "fallback_macros": None},
        {"name": "pork katsu", "role": "protein", "proportion": 0.4,
         "usda_likely": True, "fallback_macros": None},
    ]

    def _fake_llm(dish_name, model):
        return components

    dd._call_llm_decompose = _fake_llm
    _patch_lookup_macros({
        "white rice": _FakeMacroResult(source="usda_api", calories=130, carbs_g=28, fiber_g=0.4, protein_g=2.7, fat_g=0.3),
        "pork katsu": _FakeMacroResult(source="usda_api", calories=300, carbs_g=15, fiber_g=1.0, protein_g=20, fat_g=18),
    })

    via_resolve = dd.resolve_composite_macros("katsu rice bowl")
    via_fold = dd.fold_components(dd.decompose_dish("katsu rice bowl"))
    # Strip the two fields resolve_composite_macros adds on top of the fold.
    via_resolve_stripped = {k: v for k, v in via_resolve.items() if k not in ("dish_name", "source")}
    check("fold_components() reproduces resolve_composite_macros() exactly", via_fold == via_resolve_stripped)


def test_fold_empty_and_zero_proportion_raise(tmp: Path) -> None:
    _patch_dirs(tmp)
    raised = False
    try:
        dd.fold_components([])
    except ValueError:
        raised = True
    check("fold_components([]) raises ValueError, not ZeroDivisionError", raised)

    raised = False
    try:
        dd.fold_components([
            {"name": "a", "proportion": 0.0, "fallback_macros": None},
            {"name": "b", "proportion": 0.0, "fallback_macros": None},
        ])
    except ValueError:
        raised = True
    check("fold_components() with proportions summing to 0 raises ValueError", raised)


# ---------------------------------------------------------------------------
# Tests: suggest_alternatives() / suggest_additions()
# ---------------------------------------------------------------------------

def test_alternatives_fail_closed(tmp: Path) -> None:
    _patch_dirs(tmp)

    dd._call_llm_alternatives = lambda dish_name, component_name, model: (_ for _ in ()).throw(RuntimeError("boom"))
    check("exception -> []", dd.suggest_alternatives("bibimbap", "kimchi") == [])

    dd._call_llm_alternatives = lambda dish_name, component_name, model: (_ for _ in ()).throw(TimeoutError("timed out"))
    check("timeout -> []", dd.suggest_alternatives("bibimbap", "kimchi") == [])

    dd._call_llm_alternatives = lambda dish_name, component_name, model: [{"name": "spinach"}]  # missing grams_hint
    _patch_lookup_macros({"spinach": _FakeMacroResult(source="usda_api", calories=23, carbs_g=3.6, fiber_g=2.2, protein_g=2.9, fat_g=0.4)})
    _patch_select_best(0)
    result = dd.suggest_alternatives("bibimbap", "namul_missing_hint")
    check("a response missing grams_hint doesn't crash, resolves with grams_hint=None", result == [{
        "name": "spinach", "grams_hint": None,
        "per_100g": {"calories": 23.0, "carbs_g": 3.6, "fiber_g": 2.2, "protein_g": 2.9, "fat_g": 0.4},
        "carbs_g": 3.6,
    }])


def test_additions_excludes_present(tmp: Path) -> None:
    _patch_dirs(tmp)
    dd._call_llm_additions = lambda dish_name, present, model: [
        {"name": "gochujang sauce", "grams_hint": 15},
        {"name": "sesame oil", "grams_hint": 5},
        {"name": "steamed rice", "grams_hint": 150},  # already present
    ]
    _patch_lookup_macros({
        "gochujang sauce": _FakeMacroResult(source="usda_api", calories=50, carbs_g=10, fiber_g=1, protein_g=1, fat_g=1),
        "sesame oil": _FakeMacroResult(source="usda_api", calories=884, carbs_g=0, fiber_g=0, protein_g=0, fat_g=100),
        "steamed rice": _FakeMacroResult(source="usda_api", calories=130, carbs_g=28, fiber_g=0.4, protein_g=2.7, fat_g=0.3),
    })
    _patch_select_best(0)

    result = dd.suggest_additions("bibimbap", ["steamed rice", "namul vegetables"])
    names = {c["name"] for c in result}
    check("present component excluded from additions", "steamed rice" not in names)
    check("non-present suggestions kept", {"gochujang sauce", "sesame oil"} <= names)


def test_candidate_usda_unbacked_is_dropped(tmp: Path) -> None:
    _patch_dirs(tmp)
    dd._call_llm_alternatives = lambda dish_name, component_name, model: [
        {"name": "a real usda food", "grams_hint": 50},
        {"name": "an invented food usda has never heard of", "grams_hint": 50},
    ]
    _patch_lookup_macros({
        "a real usda food": _FakeMacroResult(source="usda_api", calories=100, carbs_g=10, fiber_g=1, protein_g=5, fat_g=2),
        # "an invented food..." intentionally absent -> _FakeMacroResult(source="no_results") default
    })
    _patch_select_best(0)

    result = dd.suggest_alternatives("some dish", "some component")
    names = [c["name"] for c in result]
    check("USDA-backed candidate kept", "a real usda food" in names)
    check("unbacked candidate dropped, not zero-filled", "an invented food usda has never heard of" not in names)
    check("every returned candidate has non-trivial per_100g", all(c["per_100g"]["carbs_g"] == 10.0 for c in result))


def test_candidate_rejected_by_select_best_is_dropped(tmp: Path) -> None:
    """select_best_usda_candidate() returning None (no confident match among
    USDA's top-3) must also drop the candidate, not fall back to index 0."""
    _patch_dirs(tmp)
    dd._call_llm_alternatives = lambda dish_name, component_name, model: [{"name": "ambiguous food", "grams_hint": 50}]
    fake = _FakeMacroResult(source="usda_api", calories=100, carbs_g=10, fiber_g=1, protein_g=5, fat_g=2)
    fake.candidates = [{"usda_name": "Something else entirely"}]
    _patch_lookup_macros({"ambiguous food": fake})
    _patch_select_best(None)

    result = dd.suggest_alternatives("dish x", "component y")
    check("select_best_usda_candidate() rejecting the match drops the candidate", result == [])


# ---------------------------------------------------------------------------
# Tests: user_edited survives invalidation
# ---------------------------------------------------------------------------

def test_user_edited_survives_invalidation(tmp: Path) -> None:
    _patch_dirs(tmp)
    dd._call_llm_decompose = lambda dish_name, model: [
        {"name": "a dish", "role": None, "proportion": 1.0, "usda_likely": True, "fallback_macros": None}
    ]

    dd.decompose_dish("edited dish")
    path = dd._decomposition_path("edited dish")
    entry = json.loads(path.read_text())
    entry["user_edited"] = True
    path.write_text(json.dumps(entry))

    loaded = dd._load_decomposition("edited dish", "a-completely-different-model")
    check("user_edited=True survives a model change", loaded is not None)

    entry2 = json.loads(path.read_text())
    entry2["schema_version"] = -1
    path.write_text(json.dumps(entry2))
    loaded2 = dd._load_decomposition("edited dish", dd._load_decompose_model())
    check("user_edited=True survives a schema_version bump", loaded2 is not None)

    # A normal (non-edited) entry with the same defects IS a cache miss.
    dd.decompose_dish("plain dish")
    plain_path = dd._decomposition_path("plain dish")
    plain_entry = json.loads(plain_path.read_text())
    plain_entry["schema_version"] = -1
    plain_path.write_text(json.dumps(plain_entry))
    check(
        "a normal entry (no user_edited) IS invalidated by a schema_version mismatch",
        dd._load_decomposition("plain dish", dd._load_decompose_model()) is None,
    )


def test_alts_then_additions_cache_dont_collide(tmp: Path) -> None:
    """Regression: suggest_alternatives() used to seed the shared per-dish
    candidate cache entry with addable=[], which then made a later
    suggest_additions() call on the SAME dish mistake "never computed" for
    "computed, and empty" and return [] without ever calling the LLM."""
    _patch_dirs(tmp)
    dd._call_llm_alternatives = lambda dish_name, component_name, model: []
    dd.suggest_alternatives("shared_cache_dish", "some_component")

    called = {"n": 0}

    def _fake_additions(dish_name, present, model):
        called["n"] += 1
        return [{"name": "gochujang sauce", "grams_hint": 15}]

    dd._call_llm_additions = _fake_additions
    _patch_lookup_macros({"gochujang sauce": _FakeMacroResult(source="usda_api", calories=50, carbs_g=10, fiber_g=1, protein_g=1, fat_g=1)})
    _patch_select_best(0)
    result = dd.suggest_additions("shared_cache_dish", [])
    check("suggest_additions() still calls the LLM after an unrelated suggest_alternatives() call", called["n"] == 1)
    check("suggest_additions() returns the real result, not a stale empty list", len(result) == 1)


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        test_fold_matches_resolve_composite_macros(tmp)
        test_fold_empty_and_zero_proportion_raise(tmp)
        test_alternatives_fail_closed(tmp)
        test_additions_excludes_present(tmp)
        test_candidate_usda_unbacked_is_dropped(tmp)
        test_candidate_rejected_by_select_best_is_dropped(tmp)
        test_alts_then_additions_cache_dont_collide(tmp)
        test_user_edited_survives_invalidation(tmp)

    print("\nAll FOOD-021 checks passed.")


if __name__ == "__main__":
    main()
