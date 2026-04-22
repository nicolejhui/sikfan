"""
FOOD-010: Confirmation and correction feedback loop.

Three post-prediction user actions update the embedding store immediately:
  CONFIRM  — user confirms the model's top-1 prediction was correct
  CORRECT  — user selects a different dish from top-3 candidates or types a label
  ADD_NEW  — user types a new dish name not yet in the store

All three actions:
  1. Update or add the dish embedding in ChromaDB
  2. Save the confirmed crop image to data/dishes/{dish_name}/
  3. Append an entry to data/correction_log.jsonl

Centroid gate:
  confirmed_count < 2  → count-only increment (centroid unchanged, seed preserved)
  confirmed_count >= 2 → rolling average update via update_centroid()

Rolling average kicks in on the 3rd confirmation. The first two preserve the seed
centroid quality before real-world crops are incorporated.

Public API:
    FeedbackAction          — Enum: CONFIRM | CORRECT | ADD_NEW
    record_feedback(...)    -> dict
"""

from __future__ import annotations

import datetime
import enum
import json
import os
from pathlib import Path

from PIL import Image

from pipeline.embedding_store import EmbeddingStore


class FeedbackAction(str, enum.Enum):
    CONFIRM = "CONFIRM"   # user confirms top-1 was correct
    CORRECT = "CORRECT"   # user selects a different label from candidates or types one
    ADD_NEW = "ADD_NEW"   # user types a new dish name


def normalize_dish_name(name: str) -> str:
    """Canonical dish name normalizer shared across the pipeline.

    "Mapo Tofu" → "mapo_tofu", "beef_stew" → "beef_stew"
    """
    return name.strip().lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_crop(
    crop: Image.Image,
    dish_name: str,
    dishes_dir: str,
) -> str | None:
    """
    Save crop to {dishes_dir}/{dish_name}/{timestamp_ms}.jpg.
    Returns saved path on success, None on failure. Never raises.
    """
    try:
        dish_dir = Path(dishes_dir) / dish_name
        dish_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(datetime.datetime.utcnow().timestamp() * 1000)
        save_path = dish_dir / f"{ts_ms}.jpg"
        crop.convert("RGB").save(save_path, format="JPEG")
        return str(save_path)
    except Exception:
        return None


def _append_correction_log(
    entry: dict,
    log_path: str,
) -> bool:
    """
    Append a JSON entry to log_path in JSONL format.
    Returns True on success, False on failure. Never raises.
    """
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception:
        return False


def _apply_gate(
    store: EmbeddingStore,
    dish_name: str,
    crop: Image.Image,
) -> int:
    """
    Apply centroid update gate for an existing dish:
      confirmed_count < 2  → increment count only (centroid unchanged)
      confirmed_count >= 2 → rolling average update via update_centroid()

    Returns confirmed_count after the operation.
    """
    existing = store._col.get(ids=[dish_name], include=["metadatas"])
    meta = existing["metadatas"][0]
    n = int(meta.get("confirmed_count", 0))  # read BEFORE any increment

    # Gate check uses pre-increment n:
    #   n=0 → count-only (confirmation 1)
    #   n=1 → count-only (confirmation 2)
    #   n=2 → rolling average fires (confirmation 3) ← intended boundary
    if n < 2:
        # Preserve seed centroid — only update the count in metadata
        meta["confirmed_count"] = n + 1  # increment happens AFTER the gate check
        store._col.update(ids=[dish_name], metadatas=[meta])
        return n + 1
    else:
        # Rolling average kicks in
        store.update_centroid(dish_name, crop)
        result = store._col.get(ids=[dish_name], include=["metadatas"])
        return int(result["metadatas"][0].get("confirmed_count", n + 1))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_feedback(
    crop: Image.Image,
    original_prediction: str,
    corrected_label: str,
    confidence: float,
    action: FeedbackAction,
    store: EmbeddingStore,
    dishes_dir: str = "data/dishes",
    log_path: str = "data/correction_log.jsonl",
) -> dict:
    """
    Record a user feedback action and update the embedding store.

    Args:
        crop:                PIL.Image crop shown to the user.
        original_prediction: Top-1 dish_name from apply_threshold() output.
        corrected_label:     Final dish name after user action.
                             For CONFIRM, equals original_prediction (pre-normalization).
                             For CORRECT, the selected or typed alternative.
                             For ADD_NEW, the typed new name.
        confidence:          Top-1 score from apply_threshold() output.
        action:              FeedbackAction enum value.
        store:               Live EmbeddingStore instance.
        dishes_dir:          Root directory for dish image folders (default: data/dishes).
        log_path:            Path to correction log JSONL file (default: data/correction_log.jsonl).

    Returns:
        {
            "action": str,               # "CONFIRM" | "CORRECT" | "ADD_NEW"
            "dish_name": str,            # normalized corrected_label
            "confirmed_count": int,      # confirmed_count in store after update
            "crop_saved_path": str | None,
            "logged": bool,
        }

    Raises:
        ValueError: if action is CONFIRM but normalized corrected_label does not match
                    normalized original_prediction.
    """
    # Normalize labels: "Mapo Tofu" -> "mapo_tofu"
    dish_name = normalize_dish_name(corrected_label)
    orig_norm = normalize_dish_name(original_prediction)

    if action == FeedbackAction.CONFIRM and dish_name != orig_norm:
        raise ValueError(
            f"CONFIRM action requires corrected_label to match original_prediction "
            f"after normalization, got '{dish_name}' vs '{orig_norm}'"
        )

    # --- Update embedding store ---
    known_dishes = store.list_dishes()

    if action == FeedbackAction.ADD_NEW:
        if dish_name in known_dishes:
            # Preserve rolling average history — do not reset with add_dish()
            confirmed_count = _apply_gate(store, dish_name, crop)
        else:
            store.add_dish(dish_name, crop)
            confirmed_count = 0  # add_dish sets confirmed_count=0
    elif action == FeedbackAction.CORRECT:
        if dish_name in known_dishes:
            confirmed_count = _apply_gate(store, dish_name, crop)
        else:
            store.add_dish(dish_name, crop)
            confirmed_count = 0
    else:  # CONFIRM — dish is always in store by definition
        confirmed_count = _apply_gate(store, dish_name, crop)

    # --- Save crop image ---
    crop_saved_path = _save_crop(crop, dish_name, dishes_dir)

    # --- Log correction ---
    log_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "action": action.value,
        "original_prediction": orig_norm,
        "corrected_label": dish_name,
        "confidence": round(float(confidence), 4),
        "crop_saved_path": crop_saved_path,
    }
    logged = _append_correction_log(log_entry, log_path)

    return {
        "action": action.value,
        "dish_name": dish_name,
        "confirmed_count": confirmed_count,
        "crop_saved_path": crop_saved_path,
        "logged": logged,
    }
