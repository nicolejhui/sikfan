# Test Scenarios

User-story spec for the offline `pytest` suite (`tests/`). Every test names the scenario
it implements (`SC-nn`) in its class docstring. This file is the reviewable version —
read it as product spec, not as test documentation.

**⚠️ marks a scenario that exists because getting it wrong mis-doses insulin.** Those are
tagged `@pytest.mark.safety` and can be run alone with `pytest -m safety`.

Everything here runs **offline**: no USDA, no Anthropic, no FastSAM/CLIP/ChromaDB, no
real `data/` directory. Integration coverage (the `scripts/verify_food0XX.py` family) is a
separate, later pass.

---

## 1. Portion correction learning — `pipeline/portion.py`

Background: after a scan, the user can say the portion looked *too high* / *too low* by
*a little* / *a lot*, with a reason. Corrections teach a durable per-dish multiplier
(`data/portion_priors/{slug}.json`) that biases every future scan of that dish.
See `plans/FOOD-020-plan.md` for the decision log.

### SC-01 — One correction doesn't move anything yet
> As a user who tells SikFan "that looked like too little rice" once, I don't want my
> next scan of rice to change — one data point isn't a pattern.

`save_portion_prior("white_rice", 1/0.85, "portion", "too_low")` returns
`prior_state="pending"`, writes no `portion_multiplier`, and the next `estimate_portion()`
still reports `multiplier == 1.0`.

### SC-02 — Two corrections the same way stick
> As a user who has said "too little" about the same dish twice, I want SikFan to have
> learned it, so I stop having to correct it.

Second same-direction correction → `prior_state="activated"`, `portion_multiplier == 1.18`
(`round(1/0.85, 2)`). A following `estimate_portion()` on a medium grain crop scales
180 g → **212.4 g**.

### SC-03 ⚠️ — Over-correcting is reversible
> As a user who corrected too aggressively, I want to be able to correct back and land
> exactly where I started — not slightly below it.

`too_high/lot` ×2 (multiplier → 0.60), then `too_low/lot` ×2 (compounds ×1.667) returns to
exactly **1.0**. Parametrized across all four `_FACTORS` pairs.

*Why this test exists:* the design (`results.jsx:612`) uses a symmetric ±15% / ±40%, which
is **not invertible** — down-a-lot then up-a-lot lands at 0.84. A user who oscillates
ratchets their own portion prior downward forever, so the dish is permanently
under-counted and therefore chronically under-dosed. `_FACTORS` uses reciprocals instead.
If anyone "simplifies" that back to 15/40, this test fails.

### SC-04 — Corrections can never run away
> As a user who has corrected a dish many times, I want the estimate to stay sane.

Any sequence of corrections keeps the multiplier inside `[0.5, 2.0]`; ten consecutive
`too_high/lot` clamps at 0.5 rather than trending toward zero.

### SC-05 — "Actually, I changed my mind"
> As a user who said "too much", then immediately "too little", I want SikFan to treat that
> as me correcting myself, not as two pieces of evidence.

A pending `too_high` followed by `too_low` replaces the pending evidence and resets
`n_corrections` to 1.

### SC-06 ⚠️ — "I just didn't finish this plate"
> As a user who left half my noodles, I want today's carb count adjusted — but I do *not*
> want SikFan to decide that this dish is permanently smaller than it is.

`reason="leftover"` → `prior_state="counted"`. `n_corrections` increments, but `active`
stays `False` no matter how many times it's repeated, and `_read_multiplier()` stays 1.0.

*Why this test exists:* `leftover` describes the meal in front of the user; `portion`,
`broth` and `hidden` describe the dish. Confusing the two teaches a permanent
under-estimate from a one-off event.

### SC-07 — Priors expire; evidence doesn't
> As a user coming back after six months, I want a stale learned portion to stop applying —
> but if I confirm the dish again, I don't want to start from scratch.

A prior whose `last_scanned` is 200 days old is skipped by `_read_multiplier()` while its
file and `n_corrections` survive; `touch_portion_prior()` revives it. Also covered here:
`looks_right` clears a *pending* correction but leaves an *active* prior alone (D8), and
`looks_right` carrying a reason or a non-1.0 factor raises.

Sub-case: layer precedence is priors > overrides > macro_cache, and a macro-only override
with no `portion_multiplier` key falls **through** to a multiplier stored underneath it.

---

## 2. Portion estimation from the photo — `pipeline/portion.py`

### SC-08 — Rice, pork and bok choy are not the same weight
> As a user photographing different foods, I want the gram estimate to reflect what the
> food actually is.

`white_rice` → grain, `braised_beef` → protein, `bok_choy` → vegetable, `mystery_sauce` →
default. An explicit `role="base"` beats the keyword scan. Bucket boundaries at exactly
0.15 and 0.35 are pinned against `config.yaml`.

### SC-09 — A mixed bowl splits sensibly
> As a user photographing a rice bowl, I want the rice, meat and greens counted separately.

Role weights 2:1:1 — component fractions sum to 1.0 and grams sum to `bowl_total`.

### SC-10 ⚠️ — Only the rice was detected
> As a user whose photo only resolved the rice, I want to be told the bowl was partially
> read — and I do not want the rice alone to be counted as if it were the whole bowl.

The guard path uses the grain table (180 g medium), not `bowl_total` (500 g), sets
`partial_detection: True`, and emits `warnings[0]["reason"] == "only_base_detected"`.

*Why this test exists:* the alternative is silently attributing a full bowl's grams to one
component — an over-count on a plate the user can see is wrong, which erodes trust in
every number on the screen.

---

## 3. Manual macros and precedence — `pipeline/nutrition.py`

### SC-11 ⚠️ — Bad macros never reach a dose
> As a user typing in my own numbers, I want a typo rejected loudly rather than stored.

`set_manual_override` raises `ValueError` and writes **no file** for: a missing `fiber_g`,
a negative `carbs_g`, `"12"` as a string, `None`, and `reference_weight_g <= 0`.

*Why `fiber_g` is mandatory:* net carbs drive the dose.

### SC-12 — My numbers beat USDA's
> As a user who has corrected a dish's macros, I want my version used from then on — and I
> want to be able to undo that.

Override wins over cache in `get_macros`; `reset_override` deletes it and reverts to the
cached USDA row; a pre-FOOD-013 cache row with no `reference_weight_g` gets 100.0 injected.

### SC-13 — Scaling to the estimated portion
`scale_macros` at 250 g against a 100 g reference; returns `None` (not partial numbers)
when a macro field is missing or `reference_weight_g` is 0.

### SC-14 — One dish, one file
> As a user, "Mapo Tofu" and "mapo_tofu" are the same dish.

`"Mapo Tofu"`, `"  mapo tofu  "` and `"MAPO_TOFU"` all resolve to one file.

**Known divergence, characterized not fixed:** `nutrition._slug` keeps punctuation while
`macro_lookup._slug` strips it, so `"Kung Pao (spicy)"` can land in two different cache
files. The test pins today's behaviour so a future fix has a starting assertion.

---

## 4. USDA lookup — `pipeline/macro_lookup.py` (HTTP mocked)

### SC-15 ⚠️ — A USDA blip must not become permanent zero carbs
> As a user scanning a food on a bad network day, I want SikFan to retry next time — not to
> decide forever that this food has no carbohydrates.

Three consecutive 400s → `source == "no_results"` returned **with no cache file written**,
and a subsequent successful call caches real macros. The retry is asserted to make 3
attempts with the documented backoff.

*Why this test exists:* USDA's search endpoint intermittently 400s on a well-formed query
(confirmed not a rate limit). Caching that answer pins a resolvable food to zero carbs
permanently — a silent, unbounded under-count.

### SC-16 — A genuine miss is cached
A 200 response with `foods: []` *is* cached as `no_results`, so we don't re-query a food
USDA truly doesn't have.

### SC-17 — A cached dish never hits the network
Cache hit short-circuits with zero HTTP calls and `source == "cache"`.

### SC-18 — The LLM rewrite is a fallback, not the default
The suggested-query rewrite fires only when the first query returns nothing, arrives via the
injected `suggest_query` parameter (never a live LLM call in tests), and a fallback success
clears the `query_rejected` flag so the result caches. `select_candidate(1)` rewrites the
cache to the second USDA candidate; an out-of-range index raises `IndexError`.

---

## 5. Glucose anchoring — `pipeline/glucose_store.py`

### SC-19 ⚠️ — No fake baseline, ever
> As a user without a recent CGM reading, I want to be asked for my glucose — not shown an
> impact prediction built on a number nobody measured.

A reading 20 minutes before the meal → `get_pre_meal_glucose` returns `None`. With readings
at 5 and 10 minutes, the closest wins. Exactly 15 minutes is inclusive.

*Why this test exists:* confirmed user story #4 (`CLAUDE.md`) — there is no default baseline
glucose value. A fabricated anchor produces a confident-looking curve with no basis.

### SC-20 — Manual entry is a first-class reading
> As a user typing my glucose in by hand, I want the same prediction quality as a CGM user.

`source: "manual"` is accepted and served by the same path as `dexcom_csv`; `source: "guess"`
is rejected. `glucose_mgdl` of 19, 601, `95.0` (float) and `"95"` are rejected, as is an
unknown trend — and each rejection appends nothing to the file. Mixed timezones: a naive
timestamp is read as UTC and a `+08:00` timestamp is converted, so `get_cgm_window`
boundaries are correct across both.

---

## 6. Learning a dish from confirmations — `pipeline/feedback.py` (fake store)

### SC-21 — The seed image survives the first two confirmations
> As a user confirming a dish SikFan already knows, I want it to get better at recognizing
> it — without one bad photo of mine degrading it.

`_apply_gate` at `confirmed_count` 0 and 1 increments the count only, leaving the seed
centroid untouched; at 2 it calls `update_centroid` — i.e. the rolling average starts on the
**third** confirmation.

### SC-22 — Confirmations and corrections don't lose history
`CONFIRM` with a label that doesn't match the prediction raises. `ADD_NEW` for a dish already
in the store goes through the gate rather than `add_dish()` (which would reset its history).
A failed crop save or log write degrades to `crop_saved_path: None` / `logged: False` instead
of raising — recording feedback must never crash the app mid-meal.

---

## Running

```bash
pytest -q                      # everything, target < 10s
pytest -m safety -q            # the ⚠️ subset
git status --porcelain data/   # must be empty after any run
```

The last one is part of the suite's contract: tests are isolated to `tmp_path` and must
never write into a real user's `data/portion_priors/`, `data/overrides/`, `data/macro_cache/`
or `data/glucose/`.
