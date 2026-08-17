"""
GLUC-012: Read-only audit of data/glucose/meal_logs.json macro-completeness.

Reports:
  - total rows
  - rows with macros_incomplete: true (+ their unresolved_dishes)
  - "legacy" rows with no macros_incomplete key at all (pre-GLUC-012 writes) —
    for these, best-effort cross-references each dish name against
    data/macro_cache/{slug}.json looking for source: "no_results" to suggest
    a verdict a human can act on via scripts/backfill_meal_log_macros.py
    --flag-legacy.

This script never writes to meal_logs.json or anywhere else.

Run from project root: python scripts/audit_meal_log_macros.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.feedback import normalize_dish_name

_MEAL_LOGS_PATH = Path("data/glucose/meal_logs.json")
_MACRO_CACHE_DIR = Path("data/macro_cache")


def _load_meal_logs() -> list[dict]:
    if not _MEAL_LOGS_PATH.exists():
        return []
    text = _MEAL_LOGS_PATH.read_text().strip()
    return json.loads(text) if text else []


def _legacy_dish_verdict(dish_name: str) -> str:
    """Best-effort verdict for a dish in a legacy (unflagged) row.

    Cross-references data/macro_cache/{slug}.json — a cached no_results
    entry strongly suggests this dish would have been flagged
    macros_incomplete had GLUC-012 existed when the meal was logged.
    """
    cache_path = _MACRO_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    if not cache_path.exists():
        return "unknown — no cache record"
    try:
        cached = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "unknown — cache record unreadable"
    if cached.get("source") == "no_results":
        return "likely macros_incomplete — dish had no USDA match"
    return f"likely fine — cached source={cached.get('source')!r}"


def main() -> None:
    meals = _load_meal_logs()

    flagged = [m for m in meals if m.get("macros_incomplete") is True]
    legacy = [m for m in meals if "macros_incomplete" not in m]
    clean = [m for m in meals if m.get("macros_incomplete") is False]

    print(f"Total rows:          {len(meals)}")
    print(f"Flagged (incomplete): {len(flagged)}")
    print(f"Clean (complete):     {len(clean)}")
    print(f"Legacy (no flag):     {len(legacy)}")

    if flagged:
        print("\n--- Flagged rows ---")
        for m in flagged:
            print(f"  meal_id={m.get('meal_id')} timestamp={m.get('timestamp')} "
                  f"unresolved_dishes={m.get('unresolved_dishes')}")

    if legacy:
        print("\n--- Legacy rows (best-effort verdict; not automatically flagged) ---")
        for m in legacy:
            dish_names = [d.get("dish_name") for d in m.get("dishes", [])]
            print(f"  meal_id={m.get('meal_id')} timestamp={m.get('timestamp')}")
            for name in dish_names:
                if not name:
                    continue
                verdict = _legacy_dish_verdict(name)
                print(f"    - {name}: {verdict}")

    print("\nRead-only audit — meal_logs.json was not modified.")
    print("To act on a legacy verdict, use:")
    print("  python scripts/backfill_meal_log_macros.py --flag-legacy MEAL_ID [MEAL_ID ...] --apply")


if __name__ == "__main__":
    main()
