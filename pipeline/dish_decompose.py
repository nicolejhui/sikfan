"""
FOOD-019: Composite dish decomposition for macro lookup.

Real meal names are composite — "Japanese curry chicken katsu with white rice" —
and USDA FNDDS has no single entry for them, so pipeline.macro_lookup.lookup_macros()
returns source="no_results" and the app shows no macro info at all. See
plans/FOOD-019-plan.md for the full design and decision log.

This module parses a composite dish name into its component foods via a single
Anthropic call (cached to disk — a novel dish name is decomposed once), looks up
(or LLM-estimates) each component's macros independently, and folds the result
into an ordinary per-100g macro profile shaped exactly like a
pipeline.nutrition cache entry — so pipeline.portion and pipeline.nutrition need
zero changes to consume it. The decomposer supplies relative composition only;
grams still come from pipeline.portion's pixel-based portion estimate.

No rule-based parsing anywhere: no connective splitter ("with"/"over"/"and"),
no name->grams table. See plans/FOOD-019-plan.md, "No rule-based parsing
anywhere in this design".

Decomposition cache entries carry provenance (schema_version, model) and
self-invalidate on a prompt edit or a config model swap — see D9 in the plan.
A failed decomposition call (no key, network, malformed output) fails open to
a single-component passthrough that is NEVER cached, so one bad call can't
permanently pin a dish to "does not decompose".

FOOD-021 adds ingredient-level fix support: fold_components() is the fold
step of resolve_composite_macros() lifted out so a caller (API-013) can
re-fold a user-edited component list through identical math, and
suggest_alternatives()/suggest_additions() generate USDA-backed candidate
ingredient lists per dish — no hardcoded per-dish table anywhere.

Public API:
    decompose_dish(dish_name)                       -> list[dict]
    resolve_composite_macros(dish_name)              -> dict | None
    fold_components(components)                      -> dict
    suggest_alternatives(dish_name, component_name)  -> list[dict]
    suggest_additions(dish_name, present_components)  -> list[dict]
    clear_decomposition(dish_name)                    -> None
    clear_candidates(dish_name)                       -> None
"""

from __future__ import annotations

import json
import logging
import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import yaml

from pipeline.macro_lookup import lookup_macros
from pipeline.nutrition import CACHE_DIR, _atomic_write, _load_json, _slug

_log = logging.getLogger(__name__)

DECOMPOSITION_CACHE_DIR = CACHE_DIR / "decompositions"
CANDIDATES_CACHE_DIR = CACHE_DIR / "candidates"

# Bump whenever _DECOMPOSE_SYSTEM or the component schema changes below — this
# self-invalidates every cached decomposition (plans/FOOD-019-plan.md D9).
# FOOD-021 bumped this: decomposition records gained an optional
# `user_edited` key.
_SCHEMA_VERSION = 2

_DEFAULT_MODEL = "claude-sonnet-5"

_MACRO_FIELDS = ("calories", "carbs_g", "fiber_g", "protein_g", "fat_g")

_PROPORTION_MIN = 0.05
_PROPORTION_MAX = 0.85

_VALID_ROLES = {"base", "protein", "vegetable", "sauce", "other"}

_DECOMPOSE_SYSTEM = """You are a nutrition-labeling assistant. Given a meal or dish \
name, break it into its distinct component foods for macro-nutrient lookup.

For each component, provide:
- name: a USDA-searchable English name (e.g. "rice, white, cooked", not "rice")
- role: one of base | protein | vegetable | sauce | other
- proportion: this component's share of the total cooked weight (0-1). All \
proportions across components must sum to 1.0.
- usda_likely: true if you expect USDA FoodData Central to have a close match for \
this component, false if it's a composite sauce/preparation unlikely to have a \
direct USDA match.
- fallback_macros: your best estimate of this component's macros per 100g \
(calories, carbs_g, fiber_g, protein_g, fat_g) — REQUIRED (non-null) when \
usda_likely is false, null when usda_likely is true.

A simple, single-food dish name (e.g. "white rice") should return exactly one \
component with proportion 1.0 and name equal to the (cleaned) input.

Respond with ONLY valid JSON in this exact schema — no prose, no markdown fences:
{
  "components": [
    {
      "name": "string",
      "role": "base",
      "proportion": 0.5,
      "usda_likely": true,
      "fallback_macros": null
    }
  ]
}
"""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "components": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string", "enum": sorted(_VALID_ROLES)},
                    "proportion": {"type": "number"},
                    "usda_likely": {"type": "boolean"},
                    "fallback_macros": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "properties": {
                                    "calories": {"type": "number"},
                                    "carbs_g": {"type": "number"},
                                    "fiber_g": {"type": "number"},
                                    "protein_g": {"type": "number"},
                                    "fat_g": {"type": "number"},
                                },
                                "required": list(_MACRO_FIELDS),
                                "additionalProperties": False,
                            },
                        ]
                    },
                },
                "required": ["name", "role", "proportion", "usda_likely", "fallback_macros"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["components"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_decompose_model(config_path: str = "config.yaml") -> str:
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("llm", {}).get("decompose_model", _DEFAULT_MODEL)
    except FileNotFoundError:
        return _DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _decomposition_path(dish_name: str):
    return DECOMPOSITION_CACHE_DIR / f"{_slug(dish_name)}.json"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_decomposition(dish_name: str, model: str) -> Optional[dict]:
    """
    Return the cached decomposition entry, or None on a miss OR a stale entry.

    An entry is stale — and treated exactly like a cache miss — when its
    schema_version or model doesn't match current config. This is what makes
    a prompt edit or a config model swap (D8) self-invalidating: no manual
    cache-clear step, no migration script (D9).

    FOOD-021: an entry with user_edited=True skips this check entirely. A
    hand-corrected breakdown is a human judgement; discarding it on the next
    prompt/model bump would throw that away in favor of a machine one that
    was never re-confirmed by the user (plans/FOOD-021-plan.md, "User-edited
    decompositions must survive cache invalidation").
    """
    entry = _load_json(_decomposition_path(dish_name))
    if entry is None:
        return None
    if entry.get("user_edited"):
        return entry
    if entry.get("schema_version") != _SCHEMA_VERSION:
        return None
    if entry.get("model") != model:
        return None
    return entry


def _save_decomposition(dish_name: str, components: list[dict], model: str) -> None:
    entry = {
        "dish_name": _slug(dish_name),
        "components": components,
        "schema_version": _SCHEMA_VERSION,
        "model": model,
        "cached_at": _now_iso(),
    }
    _atomic_write(_decomposition_path(dish_name), entry)


def clear_decomposition(dish_name: str) -> None:
    """
    Delete the decomposition cache entry for a dish, plus the derived
    macro_cache entry if it was built from a decomposition (source ==
    "composite"), plus its ingredient-candidate cache entry (FOOD-021).
    Never touches data/overrides/ — user overrides outrank both layers in
    pipeline.nutrition.get_macros() and must survive.
    """
    decomp_path = _decomposition_path(dish_name)
    if decomp_path.exists():
        decomp_path.unlink()
        _log.info("dish_decompose: cleared decomposition cache for %r", dish_name)

    macro_path = CACHE_DIR / f"{_slug(dish_name)}.json"
    cached = _load_json(macro_path)
    if cached is not None and cached.get("source") == "composite":
        macro_path.unlink()
        _log.info("dish_decompose: cleared derived composite macro cache for %r", dish_name)

    clear_candidates(dish_name)


def clear_candidates(dish_name: str) -> None:
    """Delete the ingredient-candidate cache entry (alts/addable) for a dish."""
    path = _candidates_path(dish_name)
    if path.exists():
        path.unlink()
        _log.info("dish_decompose: cleared candidate cache for %r", dish_name)


# ---------------------------------------------------------------------------
# Decomposition (LLM call + post-processing)
# ---------------------------------------------------------------------------

def _passthrough(dish_name: str) -> list[dict]:
    """The fail-open result: one component, unchanged from input. Never cached."""
    return [{
        "name": dish_name.strip(),
        "role": None,
        "proportion": 1.0,
        "usda_likely": True,
        "fallback_macros": None,
    }]


def _normalize_components(raw_components: list[dict]) -> list[dict]:
    """
    Arithmetic sanity on the model's returned proportions — not knowledge
    about food. Never trust the model's own arithmetic to already sum to 1.0.
    """
    components = []
    for c in raw_components:
        name = str(c["name"]).strip().lower()
        role = c.get("role")
        if role not in _VALID_ROLES:
            role = None
        proportion = float(c["proportion"])
        usda_likely = bool(c.get("usda_likely", True))
        fallback_macros = c.get("fallback_macros")
        components.append({
            "name": name,
            "role": role,
            "proportion": proportion,
            "usda_likely": usda_likely,
            "fallback_macros": fallback_macros,
        })

    # Normalize to sum to 1.0.
    total = sum(c["proportion"] for c in components)
    if total <= 0:
        # Degenerate model output — spread evenly rather than divide by zero.
        even = 1.0 / len(components)
        for c in components:
            c["proportion"] = even
    else:
        for c in components:
            c["proportion"] = c["proportion"] / total

    _clamp_and_redistribute(components)
    return components


def _clamp_and_redistribute(components: list[dict]) -> None:
    """
    Enforce [0.05, 0.85] on every proportion while keeping the sum at 1.0, in
    place. A single clamp-then-renormalize pass is not sufficient: clamping
    the largest value down and renormalizing the rest can push a second value
    back over the ceiling (e.g. [0.9, 0.1] -> clamp -> [0.85, 0.1] -> naive
    renormalize by 1/0.95 -> [0.894, 0.105], re-violating the 0.85 ceiling).

    Water-filling instead: each pass fixes only the single worst violator at
    its bound, then redistributes the remaining budget proportionally among
    the components not yet fixed. Repeating re-derives whether any other
    component now violates after redistribution, converging in at most
    len(components) passes.
    """
    n = len(components)
    fixed = [False] * n

    for _ in range(n):
        violations = []
        for i, c in enumerate(components):
            if fixed[i]:
                continue
            if c["proportion"] > _PROPORTION_MAX:
                violations.append((i, c["proportion"] - _PROPORTION_MAX, _PROPORTION_MAX))
            elif c["proportion"] < _PROPORTION_MIN:
                violations.append((i, _PROPORTION_MIN - c["proportion"], _PROPORTION_MIN))
        if not violations:
            break

        # Fix only the worst violator this pass — fixing all simultaneously
        # can be infeasible (e.g. two components each wanting the opposite
        # extreme with nothing left over for the rest to absorb).
        worst_i, _, bound = max(violations, key=lambda v: v[1])
        fixed[worst_i] = True
        components[worst_i]["proportion"] = bound

        remaining_budget = 1.0 - sum(c["proportion"] for i, c in enumerate(components) if fixed[i])
        unfixed = [i for i in range(n) if not fixed[i]]
        if not unfixed:
            break
        unfixed_total = sum(components[i]["proportion"] for i in unfixed)
        if unfixed_total <= 0:
            even = remaining_budget / len(unfixed)
            for i in unfixed:
                components[i]["proportion"] = even
        else:
            for i in unfixed:
                components[i]["proportion"] = components[i]["proportion"] / unfixed_total * remaining_budget

    # Floating-point cleanup: nudge the largest component so the sum is
    # exactly 1.0 rather than 0.9999999999-something.
    total = sum(c["proportion"] for c in components)
    if abs(total - 1.0) > 1e-9:
        largest = max(range(n), key=lambda i: components[i]["proportion"])
        components[largest]["proportion"] += 1.0 - total


def _call_llm_decompose(dish_name: str, model: str) -> list[dict]:
    """
    One Anthropic call. Raises on any failure (no key, network, malformed
    output, schema violation) — the caller (decompose_dish) treats any
    exception here as fail-open and does not cache the result.

    NOTE: the exact output_config/format sub-schema shape for structured
    outputs has not been confirmed against a live billed account as of
    2026-08-28 (see plans/FOOD-019-plan.md "Dependencies to resolve before
    starting") — a request-shape rejection here is indistinguishable, from
    the caller's point of view, from any other failure, and safely fails
    open either way. Re-run scripts/verify_food019.py --live once credits
    are available to confirm the shape, and adjust here if the API rejects
    an unexpected sub-key.
    """
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA},
        },
        system=[{
            "type": "text",
            "text": _DECOMPOSE_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": dish_name}],
    )

    text = next(block.text for block in response.content if block.type == "text")
    parsed = json.loads(text)
    raw_components = parsed["components"]
    if not raw_components:
        raise ValueError("decomposition returned zero components")

    for c in raw_components:
        if not c.get("usda_likely", True) and not c.get("fallback_macros"):
            raise ValueError(
                f"component {c.get('name')!r} has usda_likely=false but no fallback_macros"
            )

    return raw_components


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decompose_dish(dish_name: str, config_path: str = "config.yaml") -> list[dict]:
    """
    Return the component breakdown for a dish name.

    Cache hit (matching schema_version and model) -> zero API calls.
    A simple, single-food dish name safely decomposes to itself (one
    component, proportion 1.0) and is cached like any other result.
    Any failure fails open to the same one-component shape, but is NEVER
    cached, so a transient failure can't permanently pin a dish to
    "does not decompose" (plans/FOOD-019-plan.md D9).
    """
    model = _load_decompose_model(config_path)

    cached = _load_decomposition(dish_name, model)
    if cached is not None:
        return cached["components"]

    try:
        raw_components = _call_llm_decompose(dish_name, model)
    except Exception as exc:
        _log.warning(
            "dish_decompose: decomposition failed for %r (%s: %s) — using "
            "single-component passthrough, NOT cached",
            dish_name, type(exc).__name__, exc,
        )
        return _passthrough(dish_name)

    components = _normalize_components(raw_components)
    _save_decomposition(dish_name, components, model)
    return components


def _component_per_100g(component: dict) -> tuple[dict, str]:
    """
    Resolve one component's per-100g macros.

    Returns (per_100g_dict, macro_source) where macro_source is one of:
      "usda_api"   — a real USDA match (lookup_macros() succeeded)
      "estimated"  — no USDA match; used the LLM's fallback_macros
      "unresolved" — no USDA match AND no fallback_macros provided; the
                     component contributes 0.0 to every macro field, same
                     "0.0 stays 0.0, a flag is the signal" convention FOOD-017
                     established for needs_macro_entry. Neither macro_coverage
                     nor carb_coverage counts this component as resolved.
    """
    macro_result = lookup_macros(component["name"])
    usda_resolved = macro_result.source != "no_results" and macro_result.carbs_g is not None
    if usda_resolved:
        per_100g = {field: (getattr(macro_result, field) or 0.0) for field in _MACRO_FIELDS}
        return per_100g, "usda_api"

    fallback = component.get("fallback_macros")
    if fallback:
        per_100g = {field: float(fallback.get(field, 0.0)) for field in _MACRO_FIELDS}
        return per_100g, "estimated"

    return {field: 0.0 for field in _MACRO_FIELDS}, "unresolved"


def fold_components(components: list[dict]) -> dict:
    """
    Fold a component list into a per-100g profile + coverage figures.

    Each component must carry "name", "proportion", and enough for
    _component_per_100g() to resolve macros ("fallback_macros" optional).
    Proportions are used as-is (not renormalized) — decompose_dish() already
    normalizes its output to sum to 1.0, and a caller re-folding a
    user-edited list is responsible for the same invariant.

    Returns {calories, carbs_g, fiber_g, protein_g, fat_g,
             reference_weight_g: 100.0, macro_coverage, carb_coverage,
             components}.

    Raises ValueError if `components` is empty or its proportions sum to <= 0
    — dividing by that total is what powers macro_coverage/carb_coverage
    below, and API-013 rejects an empty edited dish with 422 `empty_dish`
    before ever reaching here; this is fold_components() protecting its own
    other callers.
    """
    if not components:
        raise ValueError("fold_components() requires at least one component")
    if sum(float(c["proportion"]) for c in components) <= 0:
        raise ValueError("fold_components() requires proportions summing to > 0")

    totals = {field: Decimal("0") for field in _MACRO_FIELDS}
    mass_resolved = Decimal("0")
    carbs_total = Decimal("0")
    carbs_resolved = Decimal("0")
    resolved_components = []

    # NOTE (2026-08-28 finding): before this ticket, a correction made
    # exactly one lookup_macros() call; this loop can now fire several for
    # one correction. Live testing found USDA's search endpoint
    # intermittently 400s on a well-formed query (confirmed NOT a rate
    # limit — X-RateLimit-Remaining showed >99% headroom on the failing
    # response), which lookup_macros() then caches as a genuine no_results —
    # silently understating a resolvable component's macros. A client-side
    # inter-call delay was tried here first and did NOT reliably prevent it
    # (still failed at 3s spacing on retry) — the actual fix is the
    # retry-with-backoff now in pipeline.macro_lookup._query_usda(), which
    # benefits every caller, not just this loop.
    for component in components:
        proportion = Decimal(str(component["proportion"]))
        per_100g, macro_source = _component_per_100g(component)

        component_carbs = Decimal(str(per_100g["carbs_g"])) * proportion
        carbs_total += component_carbs
        if macro_source == "usda_api":
            mass_resolved += proportion
            carbs_resolved += component_carbs

        for field in _MACRO_FIELDS:
            totals[field] += Decimal(str(per_100g[field])) * proportion

        resolved_components.append({
            "name": component["name"],
            "role": component.get("role"),
            "proportion": float(proportion),
            "per_100g": per_100g,
            "macro_source": macro_source,
        })

    macro_coverage = float(mass_resolved)
    # No carbs anywhere in the dish -> no carb uncertainty to speak of; treat
    # as fully covered rather than dividing by zero or reporting 0 (which
    # would wrongly exclude a genuinely zero-carb meal from glucose training).
    carb_coverage = float(carbs_resolved / carbs_total) if carbs_total > 0 else 1.0

    # An "unresolved" component (no USDA match AND no fallback_macros)
    # contributes 0.0 to BOTH carbs_resolved and carbs_total, so it is
    # invisible to the ratio above — a dish with a wholly unknown component
    # would report carb_coverage 1.0 while genuinely understating its carbs.
    # Observed live 2026-08-28: a curry-rice dish whose vegetable component
    # didn't resolve still reported 1.0. Its true carbs are unknown, not
    # zero, so discount coverage by the share of the dish we know nothing
    # about. Deliberately NOT an imputation — we don't invent a carb value
    # for it (see D4); we just stop claiming full coverage.
    known_mass = sum(
        Decimal(str(c["proportion"]))
        for c in resolved_components
        if c["macro_source"] != "unresolved"
    )
    carb_coverage *= float(known_mass)

    result = {
        "reference_weight_g": 100.0,
        "macro_coverage": round(macro_coverage, 4),
        "carb_coverage": round(carb_coverage, 4),
        "components": resolved_components,
    }
    for field, value in totals.items():
        result[field] = float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return result


def resolve_composite_macros(dish_name: str, config_path: str = "config.yaml") -> Optional[dict]:
    """
    Decompose `dish_name` and fold its components into a single per-100g
    macro profile, shaped exactly like a pipeline.nutrition cache entry.

    Returns None when the dish didn't actually decompose (exactly one
    component) — the signal to the caller to fall back to the existing
    lookup_macros() path, unchanged, using the ORIGINAL dish_name (not
    whatever the LLM renamed it to). No behavior change for simple dishes.

    Deliberately checks component COUNT only, not name equality against the
    input. A real model reliably renames even a genuinely simple dish into
    USDA-style phrasing ("white rice" -> "rice, white, cooked") — confirmed
    via a live call on 2026-08-28. An exact-string check against the
    original name would treat every simple dish as composite, defeating the
    "no behavior change for simple dishes" guarantee this function exists to
    provide, for essentially every real dish name.
    """
    components = decompose_dish(dish_name, config_path=config_path)

    if len(components) == 1:
        return None

    result = fold_components(components)
    result["dish_name"] = _slug(dish_name)
    result["source"] = "composite"
    return result


# ---------------------------------------------------------------------------
# LLM-backed USDA match selection (replaces hardcoded heuristics)
# ---------------------------------------------------------------------------

_SELECT_SYSTEM = """You match a food name against candidate entries from the \
USDA FoodData Central database.

Given a dish name and a numbered list of candidate USDA food descriptions, \
pick the index of the candidate that refers to the SAME FOOD.

Rules:
- A shared cooking method is NOT a match. "Peanuts, boiled" is not a match for \
"boiled chicken" — the food itself (peanuts vs chicken) is different.
- A more specific or differently-prepared version of the same food IS a match. \
"Rice, white, cooked, glutinous" is an acceptable match for "white rice".
- If NO candidate is the same food, return null. Returning null is correct and \
expected; a wrong match is far worse than no match, because these numbers are \
used for diabetic carb counting.

Respond with ONLY valid JSON: {"best_index": <integer or null>}
"""

_SELECT_SCHEMA = {
    "type": "object",
    "properties": {"best_index": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
    "required": ["best_index"],
    "additionalProperties": False,
}


def select_best_usda_candidate(
    dish_name: str,
    candidate_names: list[str],
    config_path: str = "config.yaml",
) -> Optional[int]:
    """
    Return the index of the candidate that is the same food as `dish_name`,
    or None if none of them are.

    Replaces two pieces of guesswork at once:
      1. lookup_macros() blindly taking candidates[0]. Observed 2026-08-29:
         USDA's top hit for "boiled_chicken" was "Peanuts, boiled" (21g
         carbs/100g vs chicken's ~0).
      2. A hardcoded cooking-verb stopword list used to reject such matches —
         English-only, and missing poached/blanched/stir-fried/smoked, i.e.
         exactly the _PINYIN_FALLBACK brittleness this project already flags.

    Fails CLOSED (returns None) on any error. An unvalidated match would be
    recorded silently into a diabetic carb count; a blank is visible and the
    user can still correct the dish by hand.
    """
    if not candidate_names:
        return None

    numbered = "\n".join(f"{i}. {name}" for i, name in enumerate(candidate_names))
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=_load_decompose_model(config_path),
            max_tokens=256,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _SELECT_SCHEMA},
            },
            system=[{
                "type": "text",
                "text": _SELECT_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": f"Dish name: {dish_name}\n\nCandidates:\n{numbered}",
            }],
        )
        text = next(b.text for b in response.content if b.type == "text")
        index = json.loads(text).get("best_index")
    except Exception as exc:
        _log.warning(
            "select_best_usda_candidate failed for %r (%s: %s) — rejecting match "
            "rather than recording an unvalidated one",
            dish_name, type(exc).__name__, exc,
        )
        return None

    if index is None:
        return None
    if not isinstance(index, int) or not (0 <= index < len(candidate_names)):
        _log.warning("select_best_usda_candidate returned out-of-range index %r", index)
        return None
    return index


def suggest_usda_query(dish_name: str, config_path: str = "config.yaml") -> Optional[str]:
    """
    A USDA-searchable rewrite of `dish_name`, or None if it's already fine.

    Reuses the decomposition we already pay for: decompose_dish() returns
    USDA-style component names ("white rice" -> "rice, white, cooked"), and
    for a single-component dish that name IS the better search term — which
    the old code discarded, then hand-maintained a _PINYIN_FALLBACK dict to
    approximate. Costs no extra API call on a decomposition cache hit.
    """
    try:
        components = decompose_dish(dish_name, config_path=config_path)
    except Exception:
        return None
    if len(components) != 1:
        return None
    suggested = components[0]["name"].strip()
    if not suggested or _slug(suggested) == _slug(dish_name):
        return None
    return suggested


# ---------------------------------------------------------------------------
# FOOD-021: component-fix ingredient candidates (alternatives / additions)
# ---------------------------------------------------------------------------
#
# No hardcoded per-dish table anywhere here — see plans/FOOD-021-plan.md,
# "Candidate lists must be LLM-generated, not a table". Each candidate name
# is validated through the same lookup_macros()/select_best_usda_candidate()
# path as everything else in this module and dropped if USDA can't back it;
# a wrong "correction" would be worse than the scan it replaced.

CANDIDATES_SCHEMA_VERSION = 1

_ALTERNATIVES_SYSTEM = """You help correct a photo-based food identification. \
Given a dish name and one component a vision system detected in it, list \
foods that component might plausibly actually have been — visually similar \
or easily-confused items, for THIS kind of dish.

Respond with ONLY valid JSON in this exact schema — no prose, no markdown \
fences:
{
  "alternatives": [
    {"name": "a USDA-searchable English name", "grams_hint": 80}
  ]
}

Return at most 5 alternatives, most plausible first. grams_hint is a rough \
typical serving weight in grams. If nothing plausible comes to mind, return \
an empty list.
"""

_ADDITIONS_SYSTEM = """You help complete a photo-based food identification. \
Given a dish name and the components already detected in it, list foods that \
are commonly part of this dish but easy for a photo to miss — hidden under \
other food, poured on after plating, or served alongside just off-camera.

Do NOT repeat, rename, or re-describe anything already detected.

Respond with ONLY valid JSON in this exact schema — no prose, no markdown \
fences:
{
  "additions": [
    {"name": "a USDA-searchable English name", "grams_hint": 30}
  ]
}

Return at most 5 additions, most likely first. grams_hint is a rough typical \
serving weight in grams. If nothing plausible comes to mind, return an empty \
list.
"""

_CANDIDATE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "grams_hint": {"type": "number"},
    },
    "required": ["name", "grams_hint"],
    "additionalProperties": False,
}

_ALTERNATIVES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "alternatives": {"type": "array", "items": _CANDIDATE_ITEM_SCHEMA},
    },
    "required": ["alternatives"],
    "additionalProperties": False,
}

_ADDITIONS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "additions": {"type": "array", "items": _CANDIDATE_ITEM_SCHEMA},
    },
    "required": ["additions"],
    "additionalProperties": False,
}


def _candidates_path(dish_name: str):
    return CANDIDATES_CACHE_DIR / f"{_slug(dish_name)}.json"


def _load_candidate_cache(dish_name: str, model: str) -> Optional[dict]:
    """Same schema_version + model invalidation as _load_decomposition()."""
    entry = _load_json(_candidates_path(dish_name))
    if entry is None:
        return None
    if entry.get("schema_version") != CANDIDATES_SCHEMA_VERSION:
        return None
    if entry.get("model") != model:
        return None
    return entry


def _save_candidate_entry(
    dish_name: str,
    model: str,
    alts_update: Optional[dict] = None,
    addable: Optional[list] = None,
) -> None:
    """Read-modify-write the per-dish candidate cache entry. Starts fresh
    (dropping anything on disk) whenever the existing entry is stale by
    schema_version/model, so a model bump can't leave old-model alternatives
    sitting under a new-model stamp."""
    # No "addable" key by default — only set once suggest_additions() has
    # actually generated it. Defaulting it to [] here would make
    # suggest_additions() mistake "never computed" for "computed, and empty"
    # on a dish that only ever had suggest_alternatives() called on it.
    path = _candidates_path(dish_name)
    entry = _load_json(path)
    if entry is None or entry.get("schema_version") != CANDIDATES_SCHEMA_VERSION or entry.get("model") != model:
        entry = {"dish_name": _slug(dish_name), "alts": {}}
    entry["schema_version"] = CANDIDATES_SCHEMA_VERSION
    entry["model"] = model
    entry["cached_at"] = _now_iso()
    if alts_update:
        entry.setdefault("alts", {}).update(alts_update)
    if addable is not None:
        entry["addable"] = addable
    _atomic_write(path, entry)


def _resolve_candidate(name: str, grams_hint: Optional[float]) -> Optional[dict]:
    """
    Resolve one LLM-suggested ingredient name to real USDA-backed per-100g
    macros, or None if USDA can't back it — dropped rather than shown with
    invented/zeroed macros (plans/FOOD-021-plan.md, "Candidates are resolved
    through USDA before being shown").
    """
    try:
        result = lookup_macros(name)
    except Exception:
        return None
    if result.source == "no_results" or result.carbs_g is None:
        return None

    if result.candidates:
        idx = select_best_usda_candidate(name, [c["usda_name"] for c in result.candidates])
        if idx is None:
            return None
        chosen = result.candidates[idx]
    else:
        chosen = {
            "calories": result.calories, "carbs_g": result.carbs_g,
            "fiber_g": result.fiber_g, "protein_g": result.protein_g,
            "fat_g": result.fat_g,
        }

    per_100g = {field: float(chosen.get(field) or 0.0) for field in _MACRO_FIELDS}
    return {
        "name": name,
        "grams_hint": float(grams_hint) if grams_hint is not None else None,
        "per_100g": per_100g,
        "carbs_g": per_100g["carbs_g"],
    }


def _call_llm_alternatives(dish_name: str, component_name: str, model: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=512,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _ALTERNATIVES_RESPONSE_SCHEMA},
        },
        system=[{
            "type": "text",
            "text": _ALTERNATIVES_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": f"Dish: {dish_name}\nDetected component: {component_name}",
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["alternatives"]


def _call_llm_additions(dish_name: str, present_components: list[str], model: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    present_list = ", ".join(present_components) if present_components else "(none detected)"
    response = client.messages.create(
        model=model,
        max_tokens=512,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _ADDITIONS_RESPONSE_SCHEMA},
        },
        system=[{
            "type": "text",
            "text": _ADDITIONS_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": f"Dish: {dish_name}\nAlready detected: {present_list}",
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["additions"]


def suggest_alternatives(
    dish_name: str,
    component_name: str,
    config_path: str = "config.yaml",
) -> list[dict]:
    """
    Plausible alternatives for `component_name` as detected in `dish_name` —
    what a photo-based vision system might have misread it as. One cached
    Anthropic call per (dish, component) pair; every name is USDA-validated
    before being returned. Fails closed to [] on any error, malformed
    response, or timeout — a missing suggestion is invisible, a wrong one
    silently changes a diabetic carb count.
    """
    model = _load_decompose_model(config_path)
    key = _slug(component_name)

    cached = _load_candidate_cache(dish_name, model)
    if cached is not None and key in cached.get("alts", {}):
        return cached["alts"][key]

    try:
        raw = _call_llm_alternatives(dish_name, component_name, model)
    except Exception as exc:
        _log.warning(
            "dish_decompose: suggest_alternatives failed for dish=%r component=%r "
            "(%s: %s) — returning []",
            dish_name, component_name, type(exc).__name__, exc,
        )
        return []

    resolved = [c for c in (_resolve_candidate(item["name"], item.get("grams_hint")) for item in raw) if c is not None]
    _save_candidate_entry(dish_name, model, alts_update={key: resolved})
    return resolved


def suggest_additions(
    dish_name: str,
    present_components: list[str],
    config_path: str = "config.yaml",
) -> list[dict]:
    """
    Foods commonly part of `dish_name` but easy for a photo to miss, always
    excluding whatever's already in `present_components`. One cached
    Anthropic call per dish (the exclusion is re-applied on every call, not
    baked into the cache, so it stays correct as the present list changes
    across calls); every name is USDA-validated. Fails closed to [].

    Deliberately does NOT use a string-similarity matcher to dedupe against
    present_components (plans/FOOD-021-plan.md, "Do not port sameFood()") —
    only exact normalized-slug equality, which is identity matching, not a
    food-name heuristic.
    """
    model = _load_decompose_model(config_path)

    cached = _load_candidate_cache(dish_name, model)
    if cached is not None and "addable" in cached:
        addable = cached["addable"]
    else:
        try:
            raw = _call_llm_additions(dish_name, present_components, model)
        except Exception as exc:
            _log.warning(
                "dish_decompose: suggest_additions failed for dish=%r (%s: %s) — "
                "returning []",
                dish_name, type(exc).__name__, exc,
            )
            return []
        addable = [c for c in (_resolve_candidate(item["name"], item.get("grams_hint")) for item in raw) if c is not None]
        _save_candidate_entry(dish_name, model, addable=addable)

    present_slugs = {_slug(name) for name in present_components}
    return [c for c in addable if _slug(c["name"]) not in present_slugs]
