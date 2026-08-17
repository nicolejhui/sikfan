"""
GLUC-012: Backfill macros_incomplete rows in data/glucose/meal_logs.json.

Two modes (mutually exclusive per run):

  --apply
      For every row with macros_incomplete: true, try to resolve each name in
      unresolved_dishes via pipeline.nutrition.get_macros() (the FOOD-013
      layered override/cache lookup — prefers a user override, falls back to
      the USDA cache; does NOT hit the network). If every unresolved dish
      resolves AND the row already has a portion_g to scale against for each
      of those dishes, recompute total_carbs_g, clear macros_incomplete /
      unresolved_dishes, and print a before/after diff.

      CURRENT STATE: meal_logs.json dish entries only ever store
      "portion_size": "medium" (a string) — no per-dish portion_g field
      exists in the schema written by api.py's /log-meal today. That means
      every flagged row will hit the "cannot recompute — no portion data"
      path until a future ticket persists portion grams per dish. --flag-legacy
      is the only mode that can actually write something until then; --apply's
      recompute path is implemented for forward-compatibility but is
      effectively a no-op against today's data.

  --flag-legacy MEAL_ID [MEAL_ID ...]
      Set macros_incomplete: true and unresolved_dishes on specified LEGACY
      rows (rows with no macros_incomplete key at all) — turns a human
      verdict from scripts/audit_meal_log_macros.py into an actual write.
      unresolved_dishes is computed the same way the audit script's verdict
      is: dishes whose data/macro_cache/{slug}.json has source: "no_results".

Both modes default to a dry run — nothing is written unless --apply is also
passed alongside --flag-legacy. Before any write, meal_logs.json is copied to
meal_logs.json.bak-{ISO8601 timestamp} and the backup path is printed. Writes
use atomic temp-then-rename (matching api.py's _append_meal_log).

Run from project root:
  python scripts/backfill_meal_log_macros.py                       # dry run, recompute mode
  python scripts/backfill_meal_log_macros.py --apply                # write, recompute mode
  python scripts/backfill_meal_log_macros.py --flag-legacy m1 m2    # dry run, flag mode
  python scripts/backfill_meal_log_macros.py --flag-legacy m1 --apply  # write, flag mode
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.feedback import normalize_dish_name
from pipeline.nutrition import get_macros
from pipeline.portion import scale_macros

_MEAL_LOGS_PATH = Path("data/glucose/meal_logs.json")
_MACRO_CACHE_DIR = Path("data/macro_cache")


def _load_meal_logs() -> list[dict]:
    if not _MEAL_LOGS_PATH.exists():
        return []
    text = _MEAL_LOGS_PATH.read_text().strip()
    return json.loads(text) if text else []


def _backup(path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.bak-{ts}")
    backup_path.write_text(path.read_text())
    print(f"Backup written: {backup_path}")
    return backup_path


def _atomic_write(path: Path, meals: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meals, indent=2))
    os.replace(tmp, path)


def _dish_has_no_usda_match(dish_name: str) -> bool:
    cache_path = _MACRO_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    if not cache_path.exists():
        return False
    try:
        cached = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return cached.get("source") == "no_results"


# ---------------------------------------------------------------------------
# Mode: --apply (recompute)
# ---------------------------------------------------------------------------

def _try_recompute_row(meal: dict) -> tuple[bool, dict | None, str]:
    """Attempt to resolve a flagged row's unresolved dishes.

    Returns (changed, updated_row_or_None, note).
    """
    unresolved = meal.get("unresolved_dishes", [])
    dish_by_name = {d["dish_name"]: d for d in meal.get("dishes", [])}

    resolved_macros: dict[str, dict] = {}
    for name in unresolved:
        dish_entry = dish_by_name.get(name, {})
        # No portion_g field exists in today's schema (see module docstring) —
        # never infer a scale factor. Skip the whole row if any dish lacks it.
        portion_g = dish_entry.get("portion_g")
        if portion_g is None:
            return False, None, "cannot recompute — no portion data, handle manually"

        macros = get_macros(name)
        if macros is None or macros.get("source") == "no_results":
            return False, None, f"cannot recompute — '{name}' still unresolved via get_macros()"

        scaled = scale_macros(macros, portion_g)
        if scaled is None:
            return False, None, f"cannot recompute — scale_macros() failed for '{name}'"
        resolved_macros[name] = scaled

    # All unresolved dishes resolved — rebuild total_carbs_g from every dish.
    updated = json.loads(json.dumps(meal))  # deep copy
    for d in updated["dishes"]:
        if d["dish_name"] in resolved_macros:
            d["macros"] = resolved_macros[d["dish_name"]]
            d["portion_size"] = d.get("portion_size", "medium")
    new_total = round(sum(d["macros"].get("carbs_g", 0.0) for d in updated["dishes"]), 2)
    updated["total_carbs_g"] = new_total
    updated["macros_incomplete"] = False
    updated["unresolved_dishes"] = []
    return True, updated, f"resolved: total_carbs_g {meal.get('total_carbs_g')} -> {new_total}"


def _run_apply(apply: bool) -> None:
    meals = _load_meal_logs()
    flagged = [(i, m) for i, m in enumerate(meals) if m.get("macros_incomplete") is True]

    if not flagged:
        print("No macros_incomplete rows found. Nothing to do.")
        return

    changes: list[tuple[int, dict]] = []
    skipped: list[tuple[dict, str]] = []

    for i, meal in flagged:
        changed, updated, note = _try_recompute_row(meal)
        if changed:
            changes.append((i, updated))
            print(f"  RESOLVABLE  meal_id={meal.get('meal_id')}  {note}")
        else:
            skipped.append((meal, note))

    if skipped:
        print("\n--- cannot recompute — no portion data, handle manually ---")
        for meal, note in skipped:
            print(f"  meal_id={meal.get('meal_id')}  unresolved_dishes={meal.get('unresolved_dishes')}  ({note})")

    if not changes:
        print("\nNothing resolvable. No write performed.")
        return

    if not apply:
        print(f"\nDry run — {len(changes)} row(s) would be updated. Re-run with --apply to write.")
        return

    _backup(_MEAL_LOGS_PATH)
    for i, updated in changes:
        meals[i] = updated
    _atomic_write(_MEAL_LOGS_PATH, meals)
    print(f"\nApplied — {len(changes)} row(s) updated in {_MEAL_LOGS_PATH}.")


# ---------------------------------------------------------------------------
# Mode: --flag-legacy
# ---------------------------------------------------------------------------

def _run_flag_legacy(meal_ids: list[str], apply: bool) -> None:
    meals = _load_meal_logs()
    by_id = {m.get("meal_id"): (i, m) for i, m in enumerate(meals)}

    to_flag: list[tuple[int, list[str]]] = []
    for meal_id in meal_ids:
        entry = by_id.get(meal_id)
        if entry is None:
            print(f"  SKIP  {meal_id}: not found in meal_logs.json")
            continue
        i, meal = entry
        if "macros_incomplete" in meal:
            print(f"  SKIP  {meal_id}: already has macros_incomplete={meal['macros_incomplete']} — not legacy")
            continue
        dish_names = [d.get("dish_name") for d in meal.get("dishes", []) if d.get("dish_name")]
        unresolved = [n for n in dish_names if _dish_has_no_usda_match(n)]
        print(f"  FLAG  {meal_id}: macros_incomplete=True, unresolved_dishes={unresolved}")
        to_flag.append((i, unresolved))

    if not to_flag:
        print("\nNothing to flag. No write performed.")
        return

    if not apply:
        print(f"\nDry run — {len(to_flag)} row(s) would be flagged. Re-run with --apply to write.")
        return

    _backup(_MEAL_LOGS_PATH)
    for i, unresolved in to_flag:
        meals[i]["macros_incomplete"] = True
        meals[i]["unresolved_dishes"] = unresolved
    _atomic_write(_MEAL_LOGS_PATH, meals)
    print(f"\nApplied — {len(to_flag)} row(s) flagged in {_MEAL_LOGS_PATH}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill / flag macros_incomplete rows in data/glucose/meal_logs.json. "
            "Defaults to a dry run in both modes. NOTE: the --apply recompute path is "
            "currently a no-op against real data because meal_logs.json dish entries "
            "don't yet store per-dish portion_g — --flag-legacy is the only mode that "
            "can actually write until portion grams are persisted."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    parser.add_argument(
        "--flag-legacy", nargs="+", metavar="MEAL_ID", default=None,
        help="Set macros_incomplete=True on these legacy (unflagged) meal_ids instead of recomputing.",
    )
    args = parser.parse_args()

    if args.flag_legacy:
        _run_flag_legacy(args.flag_legacy, args.apply)
    else:
        _run_apply(args.apply)


if __name__ == "__main__":
    main()
