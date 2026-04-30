"""
FOOD-015: End-to-end pipeline integration.

Single entry point for the mobile app: accepts a meal photo path and returns
a fully structured meal log with per-item macros, portion sizes, and a review queue.

Pipeline:
  FastSAM (segment) → food filter → component classifier → CLIP batch embed
  → confidence threshold → portion estimation → macro lookup → assemble

Public API:
    analyze_meal(image_path, store=None, config_path="config.yaml") -> dict
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pipeline.classifier import apply_threshold, classify_batch, is_food_crop
from pipeline.component_classifier import classify_components
from pipeline.embedding_store import EmbeddingStore
from pipeline.portion import estimate_portion, estimate_portions_mixed
from pipeline.segmentation import segment_meal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MACRO_FIELDS = ("calories", "carbs_g", "protein_g", "fat_g", "fiber_g")
_ZERO_MACROS = {k: 0.0 for k in _MACRO_FIELDS}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _aggregate_macros(detected_items: list[dict]) -> dict:
    """
    Sum macros across all items and components using Decimal arithmetic to
    avoid floating-point accumulation error. Rounds once at the end.

    None macros_scaled entries are skipped silently — they represent dishes
    with no macro data yet and should not crash the aggregation.
    """
    total = {k: Decimal("0") for k in _MACRO_FIELDS}

    for item in detected_items:
        if item["crop_type"] == "single_dish":
            if item["macros"] is not None:
                for k in _MACRO_FIELDS:
                    total[k] += Decimal(str(item["macros"].get(k, 0)))
        elif item["crop_type"] == "mixed_bowl":
            for comp in item["components"]:
                if comp["macros"] is not None:
                    for k in _MACRO_FIELDS:
                        total[k] += Decimal(str(comp["macros"].get(k, 0)))

    # Single rounding step — no per-addition rounding creep
    return {
        k: float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        for k, v in total.items()
    }


_TIEBREAK_BAND = 0.05


def _tiebreak_confident(candidates: list[dict]) -> list[dict]:
    """
    Among multiple CONFIDENT single_dish crops, keep one winner using a score-band
    + size rule. Non-CONFIDENT crops pass through unchanged alongside the winner.

    Rule:
      1. Find the max confidence score among CONFIDENT crops.
      2. All CONFIDENT crops within _TIEBREAK_BAND of that max are "score-peers".
      3. Among peers, keep the one with the largest _mask_pixels value.
      4. If exactly one CONFIDENT crop exceeds the band (gap > _TIEBREAK_BAND), it wins.

    The _mask_pixels scratch field is stripped from all returned dicts before returning.
    If there is 0 or 1 CONFIDENT crop, returns candidates unchanged (minus scratch field).
    """
    confident = [c for c in candidates if c["status"] == "CONFIDENT"]
    rest = [c for c in candidates if c["status"] != "CONFIDENT"]

    if len(confident) <= 1:
        winner = confident
    else:
        max_score = max(c["confidence"] for c in confident)
        peers = [c for c in confident if max_score - c["confidence"] <= _TIEBREAK_BAND]
        winner = [max(peers, key=lambda c: c["_mask_pixels"])]

    for item in winner + rest:
        item.pop("_mask_pixels", None)
    return winner + rest


def _build_review_items(detected_items: list[dict]) -> list[dict]:
    """
    Scan assembled detected_items and return all entries that need user review.

    Conditions (in order of evaluation per item):
      single_dish UNKNOWN      → reason: "unknown_dish"
      single_dish UNCERTAIN    → reason: "low_confidence"
      mixed_bowl base, 2+ comps → reason: "base_always_confirm"
      mixed_bowl lone base only → reason: "partial_detection"
      mixed_bowl non-base UNCERTAIN → reason: "low_confidence"
      mixed_bowl non-base UNKNOWN   → reason: "unknown_dish"
    """
    review = []

    for item in detected_items:
        if item["crop_type"] == "single_dish":
            if item["status"] == "UNKNOWN":
                review.append({
                    "crop_type": "single_dish",
                    "role": None,
                    "dish_name": item["dish_name"],
                    "reason": "unknown_dish",
                })
            elif item["status"] == "UNCERTAIN":
                review.append({
                    "crop_type": "single_dish",
                    "role": None,
                    "dish_name": item["dish_name"],
                    "reason": "low_confidence",
                })

        elif item["crop_type"] == "mixed_bowl":
            components = item["components"]
            # Detect partial-detection guard path: lone base component
            is_partial = (
                len(components) == 1 and components[0]["role"] == "base"
            )

            for comp in components:
                if comp["role"] == "base":
                    review.append({
                        "crop_type": "mixed_bowl",
                        "role": "base",
                        "dish_name": comp["dish_name"],
                        "reason": "partial_detection" if is_partial else "base_always_confirm",
                    })
                else:
                    if comp["status"] == "UNKNOWN":
                        review.append({
                            "crop_type": "mixed_bowl",
                            "role": comp["role"],
                            "dish_name": comp["dish_name"],
                            "reason": "unknown_dish",
                        })
                    elif comp["status"] == "UNCERTAIN":
                        review.append({
                            "crop_type": "mixed_bowl",
                            "role": comp["role"],
                            "dish_name": comp["dish_name"],
                            "reason": "low_confidence",
                        })

    return review


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_meal(
    image_path: str,
    store: Optional[EmbeddingStore] = None,
    config_path: str = "config.yaml",
) -> dict:
    """
    Run the full food detection and nutrition pipeline on a single meal photo.

    Args:
        image_path:  Path to a JPEG or PNG meal photo (480–4000px recommended).
        store:       Optional pre-built EmbeddingStore. Pass in tests to avoid
                     re-loading ChromaDB. Defaults to EmbeddingStore() if None.
        config_path: Path to config.yaml (injectable for tests).

    Returns:
        {
            "detected_items": list[dict],          # one entry per detected food crop
            "total_macros":   dict,                # summed across all items/components
            "items_needing_review": list[dict],    # UNKNOWN/UNCERTAIN/base/no_food
        }

    The return schema is the stable mobile API boundary — do not change field
    names or types without a versioning decision.

    Raises:
        Any exception from segment_meal() (e.g. FileNotFoundError) propagates
        to the caller. analyze_meal() does not swallow pipeline errors.
    """
    if store is None:
        store = EmbeddingStore()

    # ------------------------------------------------------------------
    # Step 1: Segment the image into per-region crops
    # ------------------------------------------------------------------
    raw_crops = segment_meal(image_path)

    # ------------------------------------------------------------------
    # Step 2: Filter non-food and classify each crop as single_dish or
    #         mixed_bowl. Collect into separate lists for batch efficiency.
    # ------------------------------------------------------------------
    single_dish_results: list[tuple[dict, dict]] = []  # (comp_result, raw_crop_dict)
    mixed_bowl_results: list[dict] = []    # classify_components() outputs

    for crop_dict in raw_crops:
        if not is_food_crop(crop_dict["crop"]):
            continue

        comp_result = classify_components(crop_dict, device=store._device)

        if comp_result["crop_type"] == "single_dish":
            single_dish_results.append((comp_result, crop_dict))
        elif comp_result["crop_type"] == "mixed_bowl":
            # Skip degenerate mixed results with no components at all
            # (all CLIP sentinels fired — nothing detected in the bowl)
            if comp_result.get("components"):
                mixed_bowl_results.append(comp_result)

    detected_items: list[dict] = []

    # ------------------------------------------------------------------
    # Step 3: Single-dish path — one batch GPU pass for all crops
    # ------------------------------------------------------------------
    if single_dish_results:
        images = [r["crop"] for r, _ in single_dish_results]
        batch_classifications = classify_batch(images, store, top_k=3)

        single_dish_candidates: list[dict] = []
        for (comp_result, crop_dict), clf_results in zip(single_dish_results, batch_classifications):
            threshold = apply_threshold(clf_results)
            dish_name = threshold["dish_name"]
            portion = estimate_portion(
                comp_result,
                dish_name,
                config_path=config_path,
            )
            single_dish_candidates.append({
                "crop_type":   "single_dish",
                "dish_name":   dish_name,
                "confidence":  threshold["confidence"],
                "status":      threshold["status"],
                "macros":      portion["macros_scaled"],
                "portion":     portion["portion_bucket"],
                "_mask_pixels": crop_dict["mask_pixels"],  # scratch field for tiebreak
            })

        detected_items.extend(_tiebreak_confident(single_dish_candidates))

    # ------------------------------------------------------------------
    # Step 4: Mixed-bowl path — estimate portions per component
    # ------------------------------------------------------------------
    for comp_result in mixed_bowl_results:
        portion_result = estimate_portions_mixed(comp_result, config_path=config_path)

        # Merge classify_components() fields (dish_name, confidence, status,
        # confirmed) with estimate_portions_mixed() fields (macros_scaled,
        # portion_fraction). Both lists are in the same role order.
        classify_comps = comp_result["components"]
        portion_comps = portion_result["components"]

        merged_components = [
            {
                "role":             clf["role"],
                "dish_name":        clf["dish_name"],
                "confidence":       clf["confidence"],
                "status":           clf["status"],
                "confirmed":        clf["confirmed"],
                "macros":           por["macros_scaled"],
                "portion_fraction": por["portion_fraction"],
            }
            for clf, por in zip(classify_comps, portion_comps)
        ]

        detected_items.append({
            "crop_type":  "mixed_bowl",
            "components": merged_components,
            "macros":     None,  # populated after all components confirmed by user
        })

    # ------------------------------------------------------------------
    # Step 5: Build items_needing_review and no-food sentinel
    # ------------------------------------------------------------------
    items_needing_review = _build_review_items(detected_items)

    if not detected_items:
        # Nothing survived segmentation or food-filter — signal to the user
        # to retake the photo or enter the meal manually.
        items_needing_review = [
            {
                "crop_type": None,
                "role":      None,
                "dish_name": None,
                "reason":    "no_food_detected",
            }
        ]

    # ------------------------------------------------------------------
    # Step 6: Aggregate total_macros
    # ------------------------------------------------------------------
    total_macros = _aggregate_macros(detected_items)

    return {
        "detected_items":       detected_items,
        "total_macros":         total_macros,
        "items_needing_review": items_needing_review,
    }
