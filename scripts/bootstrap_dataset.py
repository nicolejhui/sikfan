"""
FOOD-001: Bootstrap personal meal image dataset.

Copies selected dish class folders from CNFOOD-241/train600x600/ into
data/dishes/{dish_name}/ using English names from class_name.xls.

Selected: 35 dishes prioritized by everyday Chinese home cooking.
Usage:
    conda activate carb_counter
    python scripts/bootstrap_dataset.py
"""

import re
import shutil
from pathlib import Path

import xlrd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
CNFOOD_TRAIN = PROJECT_ROOT / "data" / "CNFOOD-241" / "train600x600"
CLASS_XLS = PROJECT_ROOT / "data" / "CNFOOD-241" / "class_name.xls"
DISHES_DIR = PROJECT_ROOT / "data" / "dishes"

# ---------------------------------------------------------------------------
# Curated class IDs — 35 everyday dishes
# ---------------------------------------------------------------------------
SELECTED_IDS = [
    0,    # Mapo Tofu
    19,   # Sauteed Vegetable
    26,   # Broccoli with Oyster Sauce
    50,   # Scrambled Egg with Tomato
    53,   # Steamed Egg Custard
    56,   # Roast Pork (Char Siu)
    58,   # Sweet and Sour Spareribs
    60,   # Cola Chicken Wings
    63,   # Steamed Chicken with Chili Sauce (Kou Shui Ji)
    65,   # Boiled Chicken (Bai Zhan Ji)
    68,   # Braised Chicken (Huang Men Ji)
    71,   # Kung Pao Chicken
    77,   # Braised Pork Belly (Hong Shao Rou)
    78,   # Braised Beef
    80,   # Beef with Tomato (Xi Hong Shi Niu Nan)
    84,   # Double Cooked Pork Slices (Hui Guo Rou)
    86,   # Boiled Pork in Chili Oil (Shui Zhu Rou Pian)
    87,   # Sweet and Sour Tenderloin (Tang Cu Li Ji)
    96,   # Shredded Pork with Green Pepper
    97,   # Yu-Shiang Shredded Pork (Yu Xiang Rou Si)
    106,  # Boiled Fish with Pickled Cabbage (Suan Cai Yu)
    112,  # Fish in Hot Chili Oil (Shui Zhu Yu)
    118,  # Spicy Crayfish
    130,  # Fried Rice
    132,  # Xiaolongbao (Steamed Soup Dumplings)
    137,  # Marinated Egg (Lu Dan)
    138,  # Poached Egg
    148,  # Youtiao (Deep-Fried Dough Sticks)
    150,  # Chongqing Hot and Sour Rice Noodles
    152,  # Noodles with Egg and Tomato
    159,  # Dumplings (Jiaozi)
    161,  # Braised Beef Noodle
    172,  # Rice
    175,  # Black Bone Chicken Soup
    193,  # Spicy Pot (Ma La Xiang Guo)
    209,  # Beijing Roast Duck
]


def slug(name: str) -> str:
    """Convert English dish name to a clean folder name."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def load_class_map() -> dict[int, tuple[str, str]]:
    """Return {class_id: (chinese_name, english_name)} from class_name.xls."""
    wb = xlrd.open_workbook(str(CLASS_XLS))
    ws = wb.sheet_by_index(0)
    return {
        int(ws.cell_value(row, 0)): (ws.cell_value(row, 1), ws.cell_value(row, 2))
        for row in range(ws.nrows)
    }


def main() -> None:
    class_map = load_class_map()
    DISHES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Seeding {len(SELECTED_IDS)} dishes into {DISHES_DIR}\n")

    for class_id in SELECTED_IDS:
        if class_id not in class_map:
            print(f"  [SKIP] ID {class_id:03d} not found in class_name.xls")
            continue

        chinese, english = class_map[class_id]
        folder_name = slug(english)
        src = CNFOOD_TRAIN / f"{class_id:03d}"
        dst = DISHES_DIR / folder_name

        if not src.exists():
            print(f"  [SKIP] Source missing: {src}")
            continue

        images = list(src.glob("*.jpg"))
        if not images:
            print(f"  [SKIP] No .jpg images found in {src}")
            continue

        dst.mkdir(parents=True, exist_ok=True)
        copied = 0
        for img in images:
            target = dst / img.name
            if not target.exists():
                shutil.copy2(img, target)
                copied += 1

        total = len(list(dst.glob("*.jpg")))
        print(
            f"  [{class_id:03d}] {english:<45} -> {folder_name}/"
            f"  ({copied} copied, {total} total)"
        )

    print("\nDone.")
    print(f"Dishes directory: {DISHES_DIR}")
    dish_count = sum(1 for p in DISHES_DIR.iterdir() if p.is_dir())
    print(f"Total dish folders: {dish_count}")


if __name__ == "__main__":
    main()
