"""
FOOD-006: Open-vocabulary CLIP classifier.
FOOD-009: Confidence thresholding and unknown detection.

Classifies food crop images by querying the ChromaDB embedding store.
No hardcoded class lists. Dish names come exclusively from ChromaDB query results.

Public API:
    is_food_crop(image, device="mps") -> bool
    classify_crop(image, store, top_k=3)  -> list[dict]
    classify_batch(images, store, top_k=3) -> list[list[dict]]
    apply_threshold(results, thresholds=None, queue_path=None, image_id=None) -> dict
"""

from __future__ import annotations

import clip
import torch
from PIL import Image

from pipeline.embedding_store import EmbeddingStore, _DEVICE, _get_clip

_UNKNOWN = [{"dish_name": "unknown", "score": 0.0}]

_FOOD_PROMPT = "a photo of food"
_NONFOOD_PROMPT = "a photo of a table, utensil, or background object"

# ---------------------------------------------------------------------------
# FOOD-009: Confidence thresholding helpers
# ---------------------------------------------------------------------------

_thresholds_cache: dict | None = None


def _load_thresholds(config_path: str = "config.yaml") -> dict:
    global _thresholds_cache
    if _thresholds_cache is None:
        import yaml
        with open(config_path) as f:
            _thresholds_cache = yaml.safe_load(f)["thresholds"]
    return _thresholds_cache


def _log_to_review_queue(entry: dict, queue_path: str) -> None:
    import json
    import os
    try:
        os.makedirs(os.path.dirname(queue_path) or ".", exist_ok=True)
        with open(queue_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # logging failure must never block a prediction


def is_food_crop(image: Image.Image, device: str = _DEVICE) -> bool:
    """
    Return True if CLIP scores the image higher as food than as background.

    Uses two text prompts and discards the crop if the non-food prompt wins.

    Args:
        image:  PIL.Image crop.
        device: Torch device for CLIP inference (default "mps").

    Returns:
        True if the crop is likely food, False if likely background/utensil.
    """
    model, preprocess = _get_clip(device)
    image_tensor = preprocess(image.convert("RGB")).unsqueeze(0).to(device)
    text_tokens = clip.tokenize([_FOOD_PROMPT, _NONFOOD_PROMPT]).to(device)

    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
        text_features = model.encode_text(text_tokens)

    image_features = image_features / image_features.norm(dim=-1, keepdim=True)
    text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    sims = (image_features @ text_features.T).squeeze(0)
    return bool(sims[0] >= sims[1])


def classify_crop(
    image: Image.Image,
    store: EmbeddingStore,
    top_k: int = 3,
) -> list[dict]:
    """
    Return the top-k dish matches for a single crop.

    Args:
        image:  PIL.Image crop (any size — letterboxing handled inside store).
        store:  Populated EmbeddingStore instance.
        top_k:  Number of candidates to return (default 3).

    Returns:
        List of dicts sorted by descending similarity:
        [{"dish_name": str, "score": float}, ...]
        Returns [{"dish_name": "unknown", "score": 0.0}] if store is empty.
    """
    raw = store.query_dish(image, top_k=top_k)
    if not raw:
        return _UNKNOWN
    return [{"dish_name": r["dish_name"], "score": r["score"]} for r in raw]


def classify_batch(
    images: list[Image.Image],
    store: EmbeddingStore,
    top_k: int = 3,
) -> list[list[dict]]:
    """
    Classify N crops in a single GPU pass.

    Encodes all images as one batch tensor and issues one ChromaDB query,
    achieving ~5–7x speedup over N sequential classify_crop calls on MPS.

    Args:
        images: List of PIL.Image crops.
        store:  Populated EmbeddingStore instance.
        top_k:  Number of candidates per image (default 3).

    Returns:
        List of N result lists, one per input image.
        Each result list has the same format as classify_crop.
        If store is empty, returns [_UNKNOWN] * N.
    """
    if not images:
        return []

    raw_batch = store.query_batch(images, top_k=top_k)

    output = []
    for raw in raw_batch:
        if not raw:
            output.append(_UNKNOWN)
        else:
            output.append([{"dish_name": r["dish_name"], "score": r["score"]} for r in raw])
    return output


def apply_threshold(
    results: list[dict],
    thresholds: dict | None = None,
    queue_path: str | None = None,
    image_id: str | None = None,
) -> dict:
    """
    Apply confidence thresholds to classify_crop() output.

    Args:
        results:    Output of classify_crop() — list of {"dish_name", "score"} dicts.
        thresholds: Optional override dict with keys "confident", "uncertain",
                    "review_queue_path". If None, loaded from config.yaml singleton.
        queue_path: Override path for review_queue.jsonl (used in tests).
        image_id:   Optional identifier logged to review queue for traceability.

    Returns:
        {
            "dish_name": str,       # top-1 dish name
            "confidence": float,    # top-1 score
            "status": str,          # "CONFIDENT" | "UNCERTAIN" | "UNKNOWN"
            "candidates": list,     # full top-k from classify_crop() — always present
        }
    """
    import datetime

    cfg = thresholds if thresholds is not None else _load_thresholds()
    score = results[0]["score"]
    dish = results[0]["dish_name"]

    if score >= cfg["confident"]:
        status = "CONFIDENT"
    elif score >= cfg["uncertain"]:
        status = "UNCERTAIN"
    else:
        status = "UNKNOWN"

    if status in ("UNCERTAIN", "UNKNOWN"):
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "status": status,
            "dish_name": dish,
            "confidence": round(score, 4),
            "candidates": results,
        }
        if image_id is not None:
            entry["image_id"] = image_id
        _log_to_review_queue(
            entry,
            queue_path or cfg.get("review_queue_path", "data/review_queue.jsonl"),
        )

    return {
        "dish_name": dish,
        "confidence": round(score, 4),
        "status": status,
        "candidates": results,
    }
