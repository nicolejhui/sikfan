"""
FOOD-019: manual recourse for a bad or stale dish decomposition.

The decomposition cache (data/macro_cache/decompositions/{slug}.json)
self-invalidates on a prompt edit or a config model swap (schema_version /
model stamp — see plans/FOOD-019-plan.md D9), so normal drift needs no manual
step. This script covers what that can't: a specific dish that decomposed
badly and needs to be re-derived on the next lookup.

Usage:
    python scripts/clear_decompositions.py --dish "japanese curry chicken katsu with white rice"
    python scripts/clear_decompositions.py --stale
    python scripts/clear_decompositions.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.dish_decompose import (
    DECOMPOSITION_CACHE_DIR,
    _load_decompose_model,
    _load_decomposition,
    clear_decomposition,
)
from pipeline.nutrition import _load_json


def _dish_name_from_entry(path: Path) -> str:
    entry = _load_json(path)
    return entry.get("dish_name", path.stem) if entry else path.stem


def clear_all() -> int:
    paths = sorted(DECOMPOSITION_CACHE_DIR.glob("*.json"))
    for path in paths:
        clear_decomposition(_dish_name_from_entry(path))
    return len(paths)


def clear_stale() -> int:
    """Clear only entries whose schema_version or model no longer matches
    current config — i.e. entries _load_decomposition() would already treat
    as a miss on next read. Provided as an explicit, auditable bulk action
    rather than waiting for each dish to be re-looked-up organically."""
    current_model = _load_decompose_model()
    count = 0
    for path in sorted(DECOMPOSITION_CACHE_DIR.glob("*.json")):
        dish_name = _dish_name_from_entry(path)
        if _load_decomposition(dish_name, current_model) is None:
            clear_decomposition(dish_name)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dish", metavar="NAME", help="Clear one dish's decomposition (and its derived composite macro cache entry).")
    group.add_argument("--stale", action="store_true", help="Clear only entries whose schema_version/model no longer matches config.yaml.")
    group.add_argument("--all", action="store_true", help="Clear every decomposition cache entry.")
    args = parser.parse_args()

    if not DECOMPOSITION_CACHE_DIR.exists():
        print(f"No decomposition cache directory at {DECOMPOSITION_CACHE_DIR} — nothing to clear.")
        return

    if args.dish:
        clear_decomposition(args.dish)
        print(f"Cleared decomposition (if any existed) for: {args.dish!r}")
    elif args.stale:
        count = clear_stale()
        print(f"Cleared {count} stale decomposition(s).")
    elif args.all:
        count = clear_all()
        print(f"Cleared {count} decomposition(s).")


if __name__ == "__main__":
    main()
