# Over-segmented plates — root cause and the three-layer fix

**Written:** 2026-09-08
**Status:** diagnosed. No implementation yet.
**Supersedes:** `docs/OPEN-multi-dish-scan-hypotheses.md` (deleted — its H2 is ruled
out, its H1/H3 are folded in below).

---

## The finding

Three separately-reported symptoms are one bug:

- duplicate rows in the breakdown (the same dish listed 2–7 times)
- a "phantom" dish that was never on the plate
- a scan reporting 200+ g carbs / 2000+ kcal

**One plate becomes many crops. Each crop is independently labelled, independently
portioned, and all of them are summed into `total_carbs_g`.** Where the crops agree on a
label you see duplicate rows; where they disagree you see a phantom dish. The 200 g scan
is simply the worst instance.

**The error is systematically upward — over-counting carbs, over-dosing insulin.**

## Evidence

Four prod `meal-status` records pulled 2026-09-08 via
`GET /meal-status/{meal_id}` (the meal_ids were recovered from `fly logs`;
`fly ssh console` is blocked by the permission classifier — see
`docs/API-015-POST-DEPLOY.md` §1).

| scan | crops | real dishes | reported carbs | grams counted |
|---|---|---|---|---|
| `85e775c6` | 5 | 1 | 116.18 | 916 g |
| `cd2fa865` | 6 | 1 | 63.16 | 528 g |
| `31fc4ae3` | 8 | 1 | **207.92** | 1,563 g |
| `8c6806fa` / `45e5bf0c` (local) | 8 | ~5 | 35.62 | — |

### `cd2fa865` — 528 g of food on one plate, hence 793 kcal on screen

| crop | label | g | carbs | conf | status |
|---|---|---|---|---|---|
| crop_0 | bean_rice_with_bbq_duck… | 108 | 11.48 | 0.8775 | CONFIDENT |
| crop_1 | beef_radish_rice_with_kimchi | 120 | 16.27 | 0.7153 | UNCERTAIN |
| crop_2 | beef_radish_rice_with_kimchi | 120 | 16.27 | 0.7300 | UNCERTAIN |
| crop_3 | bean_rice_with_bbq_duck… | 60 | 6.38 | 0.6899 | UNCERTAIN |
| crop_4 | bean_rice_with_bbq_duck… | 60 | 6.38 | 0.7685 | UNCERTAIN |
| crop_5 | bean_rice_with_bbq_duck… | 60 | 6.38 | 0.7392 | UNCERTAIN |

The photographed plate was beef radish rice with kimchi. `bean_rice_with_bbq_duck` is
the phantom — and note it is the **CONFIDENT** row while the real dish is UNCERTAIN.

### `85e775c6` — why it looked like "two dishes with two breakdowns"

Only the two composite dishes carry `components`, so the UI showed 8 component rows
(4 + 4) summing to ~35 g. The other three crops — `beef_radish_rice_with_kimchi` ×3 at
27.12 g each, 81.36 g total — had `components: 0` and were off-screen. 35 + 81.36 =
116.18. Same duplication bug, different presentation.

### `31fc4ae3` — the amplification

Seven copies of `beef_radish_rice_with_kimchi` at **200 g each** (not 60 g): each
duplicate mask is independently large enough to bucket as a big portion, so the error
grows **super-linearly** in the duplicate count. 1,563 g of food.

## Mechanism, confirmed in code

1. **Over-segmentation survives NMS.** `torchvision.ops.nms`
   (`pipeline/segmentation.py:68-72`) is intersection-over-**union**, so a mask nested
   inside a larger one scores `small/large` and survives the 0.4 threshold.
2. **Nothing dedupes after classification.** `_tiebreak_confident`
   (`analyze_meal.py:69-95`) keeps one CONFIDENT winner and returns `winner + rest`
   with every UNCERTAIN crop unfiltered. Every scan above has **at most one** CONFIDENT
   crop, so it collapses nothing.
3. **Portions are estimated per crop, independently.** `pipeline/portion.py` buckets on
   `mask_pixels / image_pixels`, so overlapping crops each claim a full portion and the
   fractions can sum well past 1.0.
4. **A label is emitted at every confidence level.** `apply_threshold`
   (`pipeline/classifier.py:168-199`) returns `results[0]["dish_name"]` regardless of
   score; `status` degrades to UNKNOWN but the name never does. A crop scoring 0.44
   still leaves carrying the nearest centroid's label *and its macros*. There is no
   "no answer" return path.
5. **Everything is summed.** `_aggregate_macros` (`analyze_meal.py:38-63`) and
   `_build_dish_results` (`api.py:496-537`) are pure 1-to-1 fan-out plus summation.

## Ruled out: cross-request state leak

The prior hypotheses doc's H2 (scan #2 inheriting scan #1's dishes from process-level
state, motivated by the API-015 caching work) is **dead**:

- crop IDs are sequential within each scan; no foreign IDs appear
- same-session scans `234a7767` and `305aa6f4` are completely clean
- the "phantom" labels are always visually similar rice plates, never arbitrary dishes

**Confirmed instead (the old H1 + H3):** the store holds three visually similar rice
plates learned from Nicole's own photos — `beef_radish_rice_with_kimchi`,
`bean_rice_with_bbq_duck_and_snow_pea_leaves`,
`japanese_curry_chicken_katsu_over_white_rice`. Confidences across these scans run
0.44–0.88; nothing is a strong match, so different crops of one plate land on different
nearest neighbours. `data/dishes/` also shows ~10 `japanese_curry_*` and ~9
`tomato_egg_rice_*` near-duplicate labels differing only by word order or typo — the
ADD_NEW flow mints a new dish per phrasing rather than matching an existing one, which
crowds the embedding space further. Worth its own cleanup ticket.

## Also checked and ruled out: zero-carb dishes

An initial survey showed 67% of local scans containing a 0-carb dish (`noodles` 18/20,
`rice` 6/8) — an *under*-count, the more dangerous direction. It is **historical**: the
oldest 25 scans are 100% affected (pre-macro-lookup), the newest 25 are 28% and every
instance is `saute_vegetable` (7/7), plausible for sautéed greens. Not a live bug.

---

## The three-layer fix

Dedup alone is **necessary and not sufficient**. Applying it by hand to `cd2fa865`:
63.16 g → 27.75 g, but still two dishes and 228 g for one plate.

### Layer 1 — Spatial dedup (`plans/FOOD-024-plan.md`, rescoped)
Collapse crops describing the same physical food, keeping one survivor with the largest
mask's `portion_g` — explicitly **not** summing. Two tiers:
- **same label + containment** → merge
- **different label + high spatial overlap** → still one food; keep the
  highest-confidence label

Dominant term. Ship first.

### Layer 2 — Portion normalization (new ticket)
Even with perfect dedup, overlapping crops each claim a full portion. Compute portion
from the **union** of retained masks, or normalize so the summed mask fraction cannot
exceed 1.0, and add a plate-level plausibility check on total grams — a scan reporting
>1 kg of food is wrong however the crops divide.

Constraint: API-015 frees mask arrays right after crop construction
(`segmentation.py:191-198`) to keep the 4 GB VM alive, so this must work from `bbox` and
`mask_pixels`, not retained masks.

### Layer 3 — UNKNOWN handling (FOOD-009/010, already deferred in CLAUDE.md)
Give the classifier a "no answer" path so a low-confidence crop stops carrying a
nearest-neighbour label and its macros. This is where the residual after Layers 1–2
lives, and it is what actually kills the phantom dish.

**Order: 1 → 2 → 3.** Each is independently shippable and each reduces the over-count.

---

## Measure before building Layer 1

Local only, using `8c6806fa` / `45e5bf0c` (stable reruns of one photo, `dumplings` ×3 at
identical 90 g / 9.96 g):

1. Run `segment_image()` with `verbose=True` for the per-stage counts it already prints
   (AR filter → NMS → `max_crops` cap, `segmentation.py:96`, `_MAX_CROPS = 20`). Dump
   `bbox` and `mask_pixels` per crop.
2. Per crop pair, compute `intersection/union` vs. `intersection/area(smaller)`,
   tabulated against whether the pair is a true duplicate. This yields both thresholds
   from data rather than a guess — this is the carbs→bolus path.
3. **Test the one-line alternative, but expect it to fail.** Lowering
   `nms_iou_threshold` (currently 0.4) would collapse duplicates before classification
   with no new code. Two reasons to be sceptical: these crops **survived** NMS at 0.4,
   so their IoU is already below it and catching them needs ~0.2, which is aggressive;
   and a lower threshold merges legitimately adjacent dishes (rice beside chicken with
   touching bboxes), producing an **under**-count — the more dangerous direction.
   Note: identical `portion_g` (120/120, 60×3) is **not** evidence of similar mask
   sizes — `config.yaml:56-70` maps each (category, bucket) pair to a fixed gram value,
   and the medium bucket spans 0.15–0.35 of the image, a 2.3x range. Only the bbox dump
   settles it.
4. Check whether duplicates consumed slots in the 20-crop cap. If real dishes were
   dropped *before* classification, dedup leaves a residual **under**-count and
   `segmentation.py` cannot stay untouched.

## Verification targets

- `cd2fa865` → 1–2 dishes (from 6); `31fc4ae3` → ~1 dish (from 8); `8c6806fa` →
  `dumplings` ×1 (from ×3), total 15.70 g (from 35.62).
- After Layer 2: total grams plausible for one plate (<600 g), not 1,563 g.
- Regression fixtures unchanged: `data/job_status/c2d6d228-….json` (3 dishes /
  54.38 g) and `a884408f-….json` (2 dishes / 42.68 g). Copy both into the test suite —
  `data/` is gitignored (`.gitignore:2`), so fixtures left there are not
  version-controlled.
- A photo with a genuinely nested distinct food (fried egg on rice) must keep both
  entries. No current fixture covers this; create one.
