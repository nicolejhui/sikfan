#!/usr/bin/env python3
"""
Meal bootstrapping tool for GlycoLens glucose training data.

Usage:
    python scripts/log_meals.py

Scans data/meals/ for unprocessed images, uses Claude Vision to estimate
macros, links to pre-meal CGM readings, and appends confirmed entries to
data/glucose/meal_logs.json.
"""

import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# Resolve project root regardless of where the script is invoked from
ROOT = Path(__file__).parent.parent
MEALS_DIR = ROOT / "data" / "meals"
CGM_FILE = ROOT / "data" / "glucose" / "cgm_readings.json"
MEAL_LOGS_FILE = ROOT / "data" / "glucose" / "meal_logs.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MODEL = "claude-sonnet-4-20250514"
TRAINING_THRESHOLD = 20

# Add project root to sys.path so pipeline imports work
sys.path.insert(0, str(ROOT))
from pipeline.glucose_store import get_pre_meal_glucose, save_meal_log, get_meal_logs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _processed_images(meal_logs: list) -> set[str]:
    """Return set of image filenames already linked to a meal log."""
    seen = set()
    for log in meal_logs:
        for name in log.get("source_images", []):
            seen.add(name)
    return seen


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file."""
    ext = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, mime


def _make_meal_id(timestamp: str) -> str:
    dt = datetime.fromisoformat(timestamp)
    return f"meal_{dt.strftime('%Y%m%d_%H%M%S')}"


# ---------------------------------------------------------------------------
# Claude Vision — macro estimation
# ---------------------------------------------------------------------------

def estimate_macros(images: list[Path], client: anthropic.Anthropic) -> dict:
    """Call Claude Vision with one or more meal images; return parsed estimate."""
    content = []
    for img_path in images:
        data, mime = _encode_image(img_path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": data},
        })

    content.append({
        "type": "text",
        "text": (
            "Analyze the meal photo(s) and estimate nutritional macros.\n\n"
            "Identify each distinct dish or food item visible. For each, provide:\n"
            "- dish_name: short snake_case label (e.g. white_rice, mapo_tofu)\n"
            "- portion_size: small | medium | large\n"
            "- macros: calories (kcal), carbs_g, protein_g, fat_g, fiber_g\n\n"
            "If multiple images show the same meal from different angles, produce "
            "one combined estimate (do not duplicate dishes).\n\n"
            "Respond with ONLY valid JSON in this exact schema — no prose, no markdown fences:\n"
            "{\n"
            '  "dishes": [\n'
            "    {\n"
            '      "dish_name": "string",\n'
            '      "portion_size": "medium",\n'
            '      "macros": {"calories": 0, "carbs_g": 0, "protein_g": 0, "fat_g": 0, "fiber_g": 0}\n'
            "    }\n"
            "  ],\n"
            '  "total_carbs_g": 0,\n'
            '  "notes": "brief description of what you see"\n'
            "}"
        ),
    })

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text.strip()
    # Strip accidental markdown fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    return json.loads(text)


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def prompt_timestamp() -> str:
    """Ask user for meal timestamp; return ISO 8601 string."""
    while True:
        raw = input("\nMeal timestamp (YYYY-MM-DD HH:MM): ").strip()
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            print("  Invalid format — use YYYY-MM-DD HH:MM, e.g. 2026-03-15 12:30")


def prompt_review(estimate: dict, cgm: dict | None) -> dict | None:
    """
    Show the estimate and CGM reading to the user.
    Returns the (possibly corrected) estimate, or None if the user skips.
    """
    print("\n--- Claude Macro Estimate ---")
    if estimate.get("notes"):
        print(f"  {estimate['notes']}")
    print()

    dishes = estimate["dishes"]
    for i, dish in enumerate(dishes):
        m = dish["macros"]
        print(
            f"  [{i + 1}] {dish['dish_name']}  ({dish['portion_size']})\n"
            f"       cal:{m['calories']}  carbs:{m['carbs_g']}g  "
            f"protein:{m['protein_g']}g  fat:{m['fat_g']}g  fiber:{m['fiber_g']}g"
        )

    print(f"\n  Total carbs: {estimate['total_carbs_g']}g")

    if cgm:
        print(
            f"\n--- Pre-meal CGM ---\n"
            f"  {cgm['glucose_mgdl']} mg/dL  ({cgm['trend']})  @ {cgm['timestamp']}"
        )
    else:
        print("\n--- Pre-meal CGM ---\n  No reading found within 15 min (will be flagged)")

    print()
    choice = input("Accept? [y / edit / skip]: ").strip().lower()

    if choice == "skip":
        return None

    if choice == "edit":
        print("\nPress Enter to keep the current value for each field.")
        for dish in dishes:
            print(f"\n  Dish: {dish['dish_name']}")
            m = dish["macros"]

            v = input(f"    dish_name [{dish['dish_name']}]: ").strip()
            if v:
                dish["dish_name"] = v

            v = input(f"    portion_size [{dish['portion_size']}]: ").strip()
            if v in ("small", "medium", "large"):
                dish["portion_size"] = v

            for field in ("calories", "carbs_g", "protein_g", "fat_g", "fiber_g"):
                v = input(f"    {field} [{m[field]}]: ").strip()
                if v:
                    try:
                        m[field] = float(v) if "." in v else int(v)
                    except ValueError:
                        pass

        estimate["total_carbs_g"] = sum(d["macros"]["carbs_g"] for d in dishes)
        print(f"\n  Updated total carbs: {estimate['total_carbs_g']}g")

        confirm = input("  Save with these values? [y/n]: ").strip().lower()
        if confirm != "y":
            return None

    return estimate


def prompt_manual_cgm(meal_timestamp: str) -> dict | None:
    """Offer user a chance to manually enter a glucose value."""
    v = input(
        "  Enter pre-meal glucose (mg/dL) manually, or press Enter to skip: "
    ).strip()
    if v:
        try:
            return {
                "timestamp": meal_timestamp,
                "glucose_mgdl": int(v),
                "trend": "flat",
                "source": "manual",
            }
        except ValueError:
            print("  Not a valid number — skipping CGM.")
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    MEALS_DIR.mkdir(parents=True, exist_ok=True)

    meal_logs = get_meal_logs()
    processed = _processed_images(meal_logs)

    all_images = sorted(
        [p for p in MEALS_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    )
    unprocessed = [p for p in all_images if p.name not in processed]

    print("\nGlycoLens — Meal Bootstrap Tool")
    print("=" * 40)
    print(f"CGM readings loaded : {len(_load_json(CGM_FILE))}")
    print(f"Existing meal logs  : {len(meal_logs)}")
    print(f"Unprocessed images  : {len(unprocessed)}")

    if not unprocessed:
        print(f"\nNo new images in {MEALS_DIR}/")
        print("Add JPEG/PNG meal photos there and re-run.")
        print(f"\nTraining set size: {len(meal_logs)} / {TRAINING_THRESHOLD} meals")
        return

    print()
    for i, img in enumerate(unprocessed):
        print(f"  [{i + 1}] {img.name}")

    session_saved = 0
    i = 0

    while i < len(unprocessed):
        img = unprocessed[i]
        print(f"\n{'=' * 50}")
        print(f"Image {i + 1}/{len(unprocessed)}: {img.name}")

        # Allow user to bundle additional images for the same meal
        selected = [img]
        if i + 1 < len(unprocessed):
            extra = input(
                f"Include more images for this meal? "
                f"(comma-separated numbers from the list, or Enter to skip): "
            ).strip()
            if extra:
                for tok in extra.split(","):
                    try:
                        idx = int(tok.strip()) - 1
                        if 0 <= idx < len(unprocessed) and idx != i:
                            selected.append(unprocessed[idx])
                    except ValueError:
                        pass

        print(
            f"\n  Analyzing {len(selected)} image(s) with Claude Vision..."
            if len(selected) > 1
            else "\n  Analyzing image with Claude Vision..."
        )

        try:
            estimate = estimate_macros(selected, client)
        except Exception as e:
            print(f"  Claude API error: {e}")
            if input("  Skip this image? [y/n]: ").strip().lower() == "y":
                i += 1
            continue

        meal_timestamp = prompt_timestamp()

        cgm = get_pre_meal_glucose(meal_timestamp, window_minutes=15)
        if not cgm:
            cgm = prompt_manual_cgm(meal_timestamp)

        final = prompt_review(estimate, cgm)
        if final is None:
            print("  Skipped.")
            i += 1
            continue

        # Build and save meal log
        meal_log = {
            "meal_id": _make_meal_id(meal_timestamp),
            "timestamp": meal_timestamp,
            "dishes": final["dishes"],
            "total_carbs_g": final["total_carbs_g"],
            "pre_meal_glucose_mgdl": cgm["glucose_mgdl"] if cgm else None,
            "pre_meal_trend": cgm["trend"] if cgm else None,
            "cgm_window": [],
            "source_images": [p.name for p in selected],
            "cgm_complete": cgm is not None,
        }

        try:
            save_meal_log(meal_log)
        except ValueError as e:
            print(f"  Validation error saving meal log: {e}")
            i += 1
            continue

        session_saved += 1
        total = len(get_meal_logs())

        print(f"\n  Saved: {meal_log['meal_id']}")
        if total >= TRAINING_THRESHOLD:
            print(f"  Training set: {total} meals  [threshold reached!]")
        else:
            print(f"  Training set: {total} / {TRAINING_THRESHOLD} meals")

        # Reload processed set so bundled extra images are skipped
        meal_logs = get_meal_logs()
        processed = _processed_images(meal_logs)

        i += 1
        while i < len(unprocessed) and unprocessed[i].name in processed:
            i += 1

    # Session summary
    final_logs = get_meal_logs()
    cgm_present = sum(1 for m in final_logs if m.get("pre_meal_glucose_mgdl") is not None)
    cgm_missing = len(final_logs) - cgm_present

    print(f"\n{'=' * 50}")
    print("Session Summary")
    print(f"  Meals logged this session : {session_saved}")
    print(f"  Total meals in dataset    : {len(final_logs)}")
    print(f"  With CGM data             : {cgm_present}")
    if cgm_missing:
        print(f"  Missing CGM (flagged)     : {cgm_missing}")

    if len(final_logs) >= TRAINING_THRESHOLD:
        print(f"\n  Ready to train — run GLUC-003 (pipeline/glucose_model.py).")
    else:
        print(f"\n  Need {TRAINING_THRESHOLD - len(final_logs)} more meals to reach training threshold.")


if __name__ == "__main__":
    main()
