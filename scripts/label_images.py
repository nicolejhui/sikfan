"""
FOOD-003: Personal labeling CLI for confirming new dish photos.

Displays each image in data/unlabeled/ and prompts for a dish name.
Labeled images are moved to data/dishes/{dish_name}/.

Usage:
    conda activate carb_counter
    python scripts/label_images.py
    python scripts/label_images.py --unlabeled data/unlabeled/ --dishes data/dishes/
"""

import argparse
import json
import readline
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_UNLABELED = PROJECT_ROOT / "data" / "unlabeled"
DEFAULT_DISHES = PROJECT_ROOT / "data" / "dishes"
SESSION_LOG = PROJECT_ROOT / "data" / "labeling_log.jsonl"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

COMMANDS = {
    "s": "skip this image (leave in unlabeled/)",
    "d": "delete this image permanently",
    "q": "quit the session",
    "?": "show this help",
}


def setup_autocomplete(dish_names: list[str]) -> None:
    """Enable tab-completion for known dish names."""
    def completer(text, state):
        matches = [d for d in dish_names if d.startswith(text)]
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")


def load_existing_dishes(dishes_dir: Path) -> list[str]:
    """Return sorted list of existing dish folder names."""
    if not dishes_dir.exists():
        return []
    return sorted(p.name for p in dishes_dir.iterdir() if p.is_dir())


def show_image(img_path: Path, index: int, total: int) -> None:
    """Display image in a matplotlib window."""
    plt.close("all")
    fig, ax = plt.subplots(figsize=(7, 7))
    try:
        img = mpimg.imread(str(img_path))
        ax.imshow(img)
    except Exception as e:
        ax.text(0.5, 0.5, f"Could not load image:\n{e}", ha="center", va="center",
                transform=ax.transAxes, color="red")
    ax.axis("off")
    ax.set_title(f"[{index}/{total}]  {img_path.name}", fontsize=11, pad=8)
    plt.tight_layout()
    plt.show(block=False)
    plt.pause(0.1)


def log_entry(log_path: Path, entry: dict) -> None:
    """Append a JSON entry to the session log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def move_image(img_path: Path, dish_name: str, dishes_dir: Path) -> Path:
    """Move image into data/dishes/{dish_name}/. Returns destination path."""
    dest_dir = dishes_dir / dish_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / img_path.name
    # Avoid collision: add suffix if file already exists
    if dest.exists():
        stem = img_path.stem
        suffix = img_path.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    shutil.move(str(img_path), dest)
    return dest


def prompt_dish(existing_dishes: list[str]) -> str:
    """Prompt for dish name with tab-completion. Returns raw input."""
    if existing_dishes:
        hint = "  [tab] autocomplete from existing dishes"
    else:
        hint = ""
    try:
        return input(f"Dish name (s=skip, d=delete, q=quit, ?=help){hint}: ").strip()
    except EOFError:
        return "q"


def print_help(existing_dishes: list[str]) -> None:
    print("\nCommands:")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd}  —  {desc}")
    if existing_dishes:
        print(f"\nKnown dishes ({len(existing_dishes)}):")
        for d in existing_dishes:
            print(f"  {d}")
    print()


def run(unlabeled_dir: Path, dishes_dir: Path, log_path: Path) -> None:
    if not unlabeled_dir.exists():
        print(f"Creating unlabeled directory: {unlabeled_dir}")
        unlabeled_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in unlabeled_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not images:
        print(f"No images found in {unlabeled_dir}")
        print("Add images to label there and re-run.")
        return

    print(f"Found {len(images)} image(s) to label in {unlabeled_dir}\n")

    existing_dishes = load_existing_dishes(dishes_dir)
    setup_autocomplete(existing_dishes)

    session_start = datetime.now(timezone.utc).isoformat()
    labeled = skipped = deleted = 0

    matplotlib.use("TkAgg") if sys.platform != "darwin" else matplotlib.use("MacOSX")

    for i, img_path in enumerate(images, start=1):
        show_image(img_path, i, len(images))

        while True:
            answer = prompt_dish(existing_dishes)

            if answer == "q":
                print("\nQuitting session.")
                break
            elif answer == "?":
                print_help(existing_dishes)
                continue
            elif answer == "s":
                print(f"  Skipped: {img_path.name}")
                log_entry(log_path, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": "skip",
                    "file": img_path.name,
                })
                skipped += 1
                break
            elif answer == "d":
                confirm = input(f"  Delete {img_path.name}? [y/N]: ").strip().lower()
                if confirm == "y":
                    img_path.unlink()
                    log_entry(log_path, {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "action": "delete",
                        "file": img_path.name,
                    })
                    deleted += 1
                    print(f"  Deleted: {img_path.name}")
                    break
                else:
                    print("  Delete cancelled.")
                    continue
            elif answer == "":
                print("  Please enter a dish name (or s/d/q).")
                continue
            else:
                dish_name = answer.lower().replace(" ", "_").replace("-", "_")
                dest = move_image(img_path, dish_name, dishes_dir)
                # Update autocomplete list if this is a new dish
                if dish_name not in existing_dishes:
                    existing_dishes.append(dish_name)
                    existing_dishes.sort()
                    setup_autocomplete(existing_dishes)
                    print(f"  New dish added: {dish_name}/")
                log_entry(log_path, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": "label",
                    "file": img_path.name,
                    "dish": dish_name,
                    "dest": str(dest),
                })
                labeled += 1
                print(f"  -> {dest.relative_to(PROJECT_ROOT)}")
                break

        if answer == "q":
            break

    plt.close("all")

    # Session summary
    print(f"\n--- Session summary ({session_start[:10]}) ---")
    print(f"  Labeled  : {labeled}")
    print(f"  Skipped  : {skipped}")
    print(f"  Deleted  : {deleted}")
    remaining = len(images) - labeled - skipped - deleted
    if remaining > 0:
        print(f"  Remaining: {remaining} (unlabeled)")
    print(f"  Log      : {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Label unlabeled food images.")
    parser.add_argument("--unlabeled", default=str(DEFAULT_UNLABELED),
                        help=f"Path to unlabeled images (default: {DEFAULT_UNLABELED})")
    parser.add_argument("--dishes", default=str(DEFAULT_DISHES),
                        help=f"Path to labeled dishes dir (default: {DEFAULT_DISHES})")
    parser.add_argument("--log", default=str(SESSION_LOG),
                        help=f"Path to session log (default: {SESSION_LOG})")
    args = parser.parse_args()

    run(
        unlabeled_dir=Path(args.unlabeled),
        dishes_dir=Path(args.dishes),
        log_path=Path(args.log),
    )


if __name__ == "__main__":
    main()
