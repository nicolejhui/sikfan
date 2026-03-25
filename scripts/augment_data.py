"""
FOOD-002: Data augmentation pipeline for food images.

Generates a 5x multiplier for every original image in data/dishes/
to make CLIP embeddings more robust to real-world phone photo variations.

Usage:
    conda activate carb_counter
    python scripts/augment_data.py --input data/dishes/ --multiplier 5
"""

import argparse
import sys
from pathlib import Path

import cv2
import albumentations as A

transform = A.Compose([
    A.RandomBrightnessContrast(
        brightness_limit=0.3,
        contrast_limit=0.3,
        p=0.5
    ),
    A.HueSaturationValue(
        hue_shift_limit=10,
        sat_shift_limit=30,
        val_shift_limit=10,
        p=0.5
    ),
    A.HorizontalFlip(p=0.5),
    A.RandomResizedCrop(
        size=(224, 224),
        scale=(0.8, 1.0),
        interpolation=1,   # cv2.INTER_LINEAR — do not change
        p=1.0
    ),
    A.GaussianBlur(
        blur_limit=(3, 7),
        p=0.2
    ),
])


def augment_dish(dish_folder: Path, multiplier: int) -> tuple[int, int]:
    """Augment all source images in a dish folder. Returns (skipped, created)."""
    source_images = [
        f for f in dish_folder.glob("*.jpg")
        if "_aug_" not in f.name
    ]

    if len(source_images) < 3:
        print(f"  WARNING: skipping {dish_folder.name} — only {len(source_images)} source images")
        return 0, 0

    created = 0
    for img_path in source_images:
        stem = img_path.stem
        for m in range(multiplier):
            out_path = dish_folder / f"{stem}_aug_{m}.jpg"
            if out_path.exists():
                continue

            img = cv2.imread(str(img_path))
            if img is None:
                print(f"  WARNING: could not read {img_path.name}, skipping")
                continue

            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            augmented = transform(image=img_rgb)["image"]
            out_bgr = cv2.cvtColor(augmented, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(out_path), out_bgr)
            created += 1

    return len(source_images), created


def main() -> None:
    parser = argparse.ArgumentParser(description="Augment food dish images.")
    parser.add_argument("--input", required=True, help="Path to data/dishes/ directory")
    parser.add_argument("--multiplier", type=int, default=5, help="Augmentation multiplier per image")
    args = parser.parse_args()

    dishes_dir = Path(args.input)
    if not dishes_dir.exists():
        print(f"ERROR: input directory not found: {dishes_dir}", file=sys.stderr)
        sys.exit(1)

    multiplier = args.multiplier
    dish_folders = sorted(p for p in dishes_dir.iterdir() if p.is_dir())

    if not dish_folders:
        print(f"No dish folders found in {dishes_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Augmenting {len(dish_folders)} dishes (multiplier={multiplier})...\n")

    total_sources = 0
    total_created = 0
    skipped_dishes = 0

    for dish_folder in dish_folders:
        sources, created = augment_dish(dish_folder, multiplier)
        if sources == 0:
            skipped_dishes += 1
            continue
        total_sources += sources
        total_created += created
        already = sources * multiplier - created
        print(f"  {dish_folder.name:<45}  {sources} originals -> {created} created, {already} already existed")

    print(f"\nDone.")
    print(f"  Dishes processed : {len(dish_folders) - skipped_dishes}")
    print(f"  Dishes skipped   : {skipped_dishes}")
    print(f"  Source images    : {total_sources}")
    print(f"  Augmented created: {total_created}")
    print(f"  Expected total   : {total_sources * multiplier}")


if __name__ == "__main__":
    main()
