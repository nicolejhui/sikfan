"""
FOOD-019: manual recourse for a bad or stale dish decomposition.

The decomposition cache (data/macro_cache/decompositions/{slug}.json)
self-invalidates on a prompt edit or a config model swap (schema_version /
model stamp — see plans/FOOD-019-plan.md D9), so normal drift needs no manual
step. This script covers what that can't: a specific dish that decomposed
badly and needs to be re-derived on the next lookup.

FOOD-021: also reaches data/macro_cache/candidates/{slug}.json (the
ingredient-alternatives/additions cache) — cleared alongside a dish's
decomposition, or independently by --stale, since the two caches invalidate
on the same schema_version/model contract but are generated separately. A
decomposition with user_edited=True is a human judgement and is never
treated as stale by --stale (it can still be cleared explicitly by --dish
or --all).

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
    CANDIDATES_CACHE_DIR,
    CANDIDATES_SCHEMA_VERSION,
    DECOMPOSITION_CACHE_DIR,
    _load_decompose_model,
    _load_decomposition,
    clear_candidates,
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
    rather than waiting for each dish to be re-looked-up organically.

    Also sweeps data/macro_cache/candidates/ (FOOD-021) the same way — a
    stale candidate entry (from a since-changed decompose_model) is not
    necessarily paired with a stale decomposition entry, since the two are
    independently generated and cached."""
    current_model = _load_decompose_model()
    count = 0
    for path in sorted(DECOMPOSITION_CACHE_DIR.glob("*.json")):
        dish_name = _dish_name_from_entry(path)
        entry = _load_json(path)
        if entry is not None and entry.get("user_edited"):
            continue  # FOOD-021: a hand-corrected decomposition never expires
        if _load_decomposition(dish_name, current_model) is None:
            clear_decomposition(dish_name)
            count += 1

    if CANDIDATES_CACHE_DIR.exists():
        for path in sorted(CANDIDATES_CACHE_DIR.glob("*.json")):
            entry = _load_json(path)
            if entry is None:
                continue
            if entry.get("schema_version") != CANDIDATES_SCHEMA_VERSION or entry.get("model") != current_model:
                clear_candidates(_dish_name_from_entry(path))
                count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dish", metavar="NAME", help="Clear one dish's decomposition (and its derived composite macro cache entry).")
    group.add_argument("--stale", action="store_true", help="Clear only entries whose schema_version/model no longer matches config.yaml.")
    group.add_argument("--all", action="store_true", help="Clear every decomposition cache entry.")
    args = parser.parse_args()

    if not DECOMPOSITION_CACHE_DIR.exists() and not CANDIDATES_CACHE_DIR.exists():
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
