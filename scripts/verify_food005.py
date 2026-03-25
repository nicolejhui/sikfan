"""FOOD-005 verification: before/after crop counts + bowl/plate boundary assertions.

All assertions are resolution-independent ratios so this script generalises
to any image size, not just the 768x1024 test set.
"""

from PIL import Image
from ultralytics import FastSAM
from pipeline.segmentation import segment_meal

# Three structurally different test images — resist overfitting to any one
TEST_CASES = [
    # (path,                                          min_crops, max_crops)
    ("data/dishes/hot_pot/hot_pot_christmas.jpeg",    2,         14),
    ("data/assorted_breakfast.jpeg",                  1,         20),
    ("data/fungus_dessert_Soup.jpeg",                 1,         20),
]

fastsam = FastSAM("FastSAM-s.pt")

for img_path, min_expected, max_expected in TEST_CASES:
    # Read actual image dimensions — never hardcode
    with Image.open(img_path) as im:
        img_w, img_h = im.size

    # ── Raw count (bypass segment_meal filters) ─────────────────────────────
    raw_results = fastsam(img_path, device="mps", retina_masks=True, imgsz=1024, conf=0.4, iou=0.9)
    raw_count = len(raw_results[0].masks.data) if raw_results[0].masks else 0

    # ── Filtered count ──────────────────────────────────────────────────────
    print(f"\n{img_path}")
    crops = segment_meal(img_path, verbose=True)
    n = len(crops)

    print(f"  Raw masks : {raw_count}")
    print(f"  Filtered  : {n}  (expect {min_expected}–{max_expected})")

    # Assertion 1 — filters reduced the count (not a no-op)
    assert n < raw_count, f"Filters had no effect: {raw_count} → {n}"

    # Assertion 2 — count in expected range for this image type
    assert min_expected <= n <= max_expected, \
        f"Expected {min_expected}–{max_expected} crops, got {n}"

    # Assertion 3 — hard cap
    assert n <= 20, "20-crop cap violated"

    for i, c in enumerate(crops):
        xmin, ymin, xmax, ymax = c["bbox"]
        bw = xmax - xmin
        bh = ymax - ymin
        bbox_area = bw * bh

        # Assertion 4 — aspect ratio (dimensionless, resolution-independent)
        if bw > 0 and bh > 0:
            ar = max(bw / bh, bh / bw)
            assert ar <= 4.0, \
                f"{img_path} crop {i}: AR={ar:.2f} > 4.0"

        if bbox_area > 0:
            fill_ratio = c["mask_pixels"] / bbox_area

            # Assertion 5 — bowl/plate ring detection
            # A full circular bowl rim passes the AR filter (ring AR ≈ 1:1).
            # Its signature: bbox covers a large fraction of the image
            # AND mask pixels cover only a small fraction of that bbox.
            bbox_w_frac = bw / img_w
            bbox_h_frac = bh / img_h
            spans_majority = (bbox_w_frac > 0.5) and (bbox_h_frac > 0.5)
            assert not (spans_majority and fill_ratio < 0.30), (
                f"{img_path} crop {i}: plate/bowl rim leaked through — "
                f"bbox={bbox_w_frac:.0%}w × {bbox_h_frac:.0%}h, fill={fill_ratio:.2f}"
            )

    print(f"  PASS")

print("\nAll test cases passed.")
