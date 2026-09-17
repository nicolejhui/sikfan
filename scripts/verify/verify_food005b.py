"""
FOOD-005b verification: Mixed-component bowl detection.

Tests:
  Section A — Complexity detection (synthetic images, no CLIP):
    1. Single-color crop -> single_dish
    2. Multi-color crop  -> mixed_bowl via color signal
    3. Large crop        -> mixed_bowl via size signal
    4. Both thresholds just missed -> single_dish
    5. Schema: single_dish has required keys, no components key
    6. Schema: mixed_bowl has components list and macros: None

  Section B — Role classification (uses CLIP, real image from data/dishes/):
    7. components is a non-empty list for a real food crop
    8. Each component has role, dish_name, confidence, status, confirmed
    9. Base component is always status == UNCERTAIN regardless of confidence
   10. Score below threshold -> UNCERTAIN status
   11. Image mutation guard — crop size unchanged after classify_components
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `pipeline` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PIL import Image


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------

section("Setup")

from pipeline.component_classifier import classify_components, _count_color_clusters

print("  Imports OK")

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

# A 4-quadrant image with 4 visually distinct colors — reliably triggers color signal
def _make_multicolor(size: int = 300) -> Image.Image:
    h = size // 2
    img = Image.new("RGB", (size, size))
    img.paste(Image.new("RGB", (h, h), (220,  30,  30)), (0,   0))   # red
    img.paste(Image.new("RGB", (h, h), ( 30, 180,  30)), (h,   0))   # green
    img.paste(Image.new("RGB", (h, h), ( 30,  30, 220)), (0,   h))   # blue
    img.paste(Image.new("RGB", (h, h), (220, 200,  30)), (h,   h))   # yellow
    return img


def _make_crop_dict(image: Image.Image, mask_pixels: int, image_pixels: int) -> dict:
    return {
        "crop": image,
        "bbox": (0, 0, image.width, image.height),
        "mask_pixels": mask_pixels,
        "image_pixels": image_pixels,
    }


# ---------------------------------------------------------------------------
# Section A — Complexity detection (no CLIP)
# ---------------------------------------------------------------------------

section("Test 1 — Single-color crop -> single_dish")

solid_red = Image.new("RGB", (300, 300), (220, 30, 30))
total_pixels = 1000 * 1000  # large total so 300x300 is a small fraction
crop1 = _make_crop_dict(solid_red, mask_pixels=300 * 300, image_pixels=total_pixels)

result = classify_components(crop1)
assert result["crop_type"] == "single_dish", f"Expected single_dish, got {result['crop_type']}"
ok("solid-color small crop -> single_dish")


# ---------------------------------------------------------------------------

section("Test 2 — Multi-color crop -> mixed_bowl via color signal")

multicolor = _make_multicolor(300)
crop2 = _make_crop_dict(multicolor, mask_pixels=300 * 300, image_pixels=total_pixels)

# Inject minimal prompts to avoid CLIP load in Section A
_DUMMY_PROMPTS = {
    "base": ["white rice", "no grain base visible"],
    "protein": ["cooked meat", "no protein visible"],
}
result = classify_components(crop2, role_prompts=_DUMMY_PROMPTS)
assert result["crop_type"] == "mixed_bowl", f"Expected mixed_bowl, got {result['crop_type']}"
ok("4-color image -> mixed_bowl via color signal")


# ---------------------------------------------------------------------------

section("Test 3 — Large crop -> mixed_bowl via size signal")

solid_blue = Image.new("RGB", (200, 200), (30, 30, 200))
# mask_pixels / image_pixels = 0.30 > threshold 0.25
crop3 = _make_crop_dict(solid_blue, mask_pixels=30_000, image_pixels=100_000)

result = classify_components(crop3, role_prompts=_DUMMY_PROMPTS)
assert result["crop_type"] == "mixed_bowl", f"Expected mixed_bowl, got {result['crop_type']}"
ok("mask_pixels/image_pixels=0.30 -> mixed_bowl via size signal")


# ---------------------------------------------------------------------------

section("Test 4 — Both thresholds just missed -> single_dish")

two_color = Image.new("RGB", (300, 300))
two_color.paste(Image.new("RGB", (150, 300), (220, 30, 30)), (0,   0))
two_color.paste(Image.new("RGB", (150, 300), (30, 30, 220)), (150, 0))

# size: 0.249 < 0.25 threshold; color: 2 clusters < 3 threshold
crop4 = _make_crop_dict(two_color, mask_pixels=24_900, image_pixels=100_000)

result = classify_components(crop4, role_prompts=_DUMMY_PROMPTS)
assert result["crop_type"] == "single_dish", (
    f"Expected single_dish (both thresholds missed), got {result['crop_type']}"
)
ok("0.249 size fraction AND 2 color clusters -> single_dish")


# ---------------------------------------------------------------------------

section("Test 5 — Schema: single_dish")

result = classify_components(crop1)
required_keys = {"crop_type", "crop", "bbox", "mask_pixels", "image_pixels"}
missing = required_keys - result.keys()
assert not missing, f"single_dish missing keys: {missing}"
assert "components" not in result, "single_dish should not have a components key"
assert result["crop_type"] == "single_dish"
ok(f"single_dish has keys {sorted(required_keys)}, no components key")


# ---------------------------------------------------------------------------

section("Test 6 — Schema: mixed_bowl")

result = classify_components(crop2, role_prompts=_DUMMY_PROMPTS)
assert result["crop_type"] == "mixed_bowl", f"Expected mixed_bowl, got {result['crop_type']}"
assert isinstance(result["components"], list), "components must be a list"
assert result["macros"] is None, f"macros should be None before confirmation, got {result['macros']}"
assert "crop" in result and "bbox" in result and "mask_pixels" in result and "image_pixels" in result
ok("mixed_bowl has crop_type, components (list), macros=None, and all crop fields")


# ---------------------------------------------------------------------------
# Section B — Role classification (loads CLIP, uses real food image)
# ---------------------------------------------------------------------------

section("Section B setup — loading real food image")

# Use a dish that is visually multi-component (e.g. dumplings with visible filling)
sample_dirs = sorted(Path("data/dishes").iterdir()) if Path("data/dishes").exists() else []
if not sample_dirs:
    fail("No data/dishes/ found — cannot run Section B tests")

# Find any dish folder with at least one image
sample_image_path = None
for d in sample_dirs:
    imgs = [p for p in d.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"} and "_aug_" not in p.name]
    if imgs:
        sample_image_path = sorted(imgs)[0]
        break

if sample_image_path is None:
    fail("No images found in data/dishes/")

real_image = Image.open(sample_image_path).convert("RGB")
print(f"  Using: {sample_image_path} ({real_image.size})")

# Force mixed_bowl via size signal so CLIP is triggered
real_crop = _make_crop_dict(real_image, mask_pixels=100_000, image_pixels=100_000)  # 100% = definitely large


# ---------------------------------------------------------------------------

section("Test 7 — components is non-empty for a real food crop")

result = classify_components(real_crop)
assert result["crop_type"] == "mixed_bowl", f"Expected mixed_bowl, got {result['crop_type']}"
assert isinstance(result["components"], list), "components must be a list"
assert len(result["components"]) > 0, "Expected at least one component from real food image"
ok(f"Got {len(result['components'])} component(s): {[c['role'] for c in result['components']]}")


# ---------------------------------------------------------------------------

section("Test 8 — Each component has required fields")

for c in result["components"]:
    for field in ("role", "dish_name", "confidence", "status", "confirmed"):
        assert field in c, f"Component missing field '{field}': {c}"
    assert isinstance(c["confidence"], float), f"confidence must be float, got {type(c['confidence'])}"
    assert c["status"] in ("CONFIDENT", "UNCERTAIN"), f"Invalid status: {c['status']}"
    assert c["confirmed"] is False, f"confirmed should default to False, got {c['confirmed']}"
    ok(f"  {c['role']:12s}  dish={c['dish_name']!r:35s}  score={c['confidence']:.4f}  {c['status']}")


# ---------------------------------------------------------------------------

section("Test 9 — Base component is always UNCERTAIN")

base_components = [c for c in result["components"] if c["role"] == "base"]
if not base_components:
    print("  SKIP  no base component detected in this image (non-base food)")
else:
    for bc in base_components:
        assert bc["status"] == "UNCERTAIN", (
            f"Base must always be UNCERTAIN, got {bc['status']} (confidence={bc['confidence']})"
        )
    ok(f"base status=UNCERTAIN regardless of confidence {base_components[0]['confidence']:.4f}")


# ---------------------------------------------------------------------------

section("Test 10 — Score below threshold -> UNCERTAIN")

# Inject prompts where ALL are semantically distant so confidence will be low
low_conf_prompts = {
    "protein": ["abstract geometric pattern", "no protein visible"],
}
low_conf_crop = _make_crop_dict(real_image, mask_pixels=100_000, image_pixels=100_000)
low_result = classify_components(low_conf_crop, role_prompts=low_conf_prompts, role_confidence_threshold=0.9)

protein_components = [c for c in low_result["components"] if c["role"] == "protein"]
if protein_components:
    for pc in protein_components:
        if pc["confidence"] < 0.9:
            assert pc["status"] == "UNCERTAIN", (
                f"Low-confidence component must be UNCERTAIN, got {pc['status']}"
            )
    ok(f"protein with low confidence marked UNCERTAIN (score={protein_components[0]['confidence']:.4f})")
else:
    ok("protein sentinel won (abstract pattern) — role correctly excluded from output")


# ---------------------------------------------------------------------------

section("Test 11 — Image mutation guard")

original_size = real_image.size
_ = classify_components(real_crop)
assert real_image.size == original_size, (
    f"classify_components mutated input image: {real_image.size} != {original_size}"
)
ok(f"crop_dict['crop'].size unchanged: {original_size}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

section("All FOOD-005b tests passed")
print(f"  Module: pipeline/component_classifier.py")
print(f"  Config: config.yaml (component_detection)")
print(f"  Segmentation: pipeline/segmentation.py (image_pixels added)")
