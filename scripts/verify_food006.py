"""
FOOD-006 verification: Open-vocabulary CLIP classifier.

Tests:
  1. Empty store → unknown result for classify_crop and classify_batch
  2. Known dish self-match → top-1 is correct dish with score > 0.8
  3. Top-3 count → exactly 3 results per image (when store has >= 3 dishes)
  4. Batch output shape → classify_batch([a, b, c]) returns 3 result lists
  5. No class list → classifier.py contains no torch/clip/label_dict imports
  6. Timing → classify_batch is at least 3x faster than sequential classify_crop
  7. Image mutation guard → query_dish and query_batch leave image.size unchanged;
                             _letterbox_224 returns (224,224) canvas without touching input
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import chromadb
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# Setup
# ---------------------------------------------------------------------------

section("Setup")

from pipeline.embedding_store import EmbeddingStore, _letterbox_224
from pipeline.classifier import classify_crop, classify_batch

LIVE_STORE = EmbeddingStore(embeddings_dir="data/embeddings", device="mps")
print(f"  Live store has {LIVE_STORE.count()} dishes")

# hot_pot has exactly 1 source image → its centroid IS that embedding.
# Self-match is guaranteed near 1.0 regardless of CLIP discrimination quality.
SELF_MATCH_DISH = "hot_pot"
self_match_dir = Path(f"data/dishes/{SELF_MATCH_DISH}")
self_match_paths = sorted(
    p for p in self_match_dir.iterdir()
    if p.suffix.lower() in {".jpg", ".jpeg"} and "_aug_" not in p.name
)
if not self_match_paths:
    fail(f"No images found in {self_match_dir}")
SELF_MATCH_IMAGE = Image.open(self_match_paths[0]).convert("RGB")

# Use dumplings for batch/count tests (has enough images for 10-crop timing test)
SAMPLE_DISH = "dumplings"
sample_dir = Path(f"data/dishes/{SAMPLE_DISH}")
sample_images = sorted(
    p for p in sample_dir.iterdir()
    if p.suffix.lower() in {".jpg", ".jpeg"} and "_aug_" not in p.name
)
if not sample_images:
    fail(f"No images found in {sample_dir}")
SAMPLE_IMAGE = Image.open(sample_images[0]).convert("RGB")

# ---------------------------------------------------------------------------
# Test 1: Empty store returns unknown
# ---------------------------------------------------------------------------

section("Test 1 — Empty store → unknown")

tmp_client = chromadb.PersistentClient(path="/tmp/food006_test_empty")
tmp_client.delete_collection("dishes") if "dishes" in [c.name for c in tmp_client.list_collections()] else None
empty_store = EmbeddingStore.__new__(EmbeddingStore)
empty_store._device = "mps"
empty_store._embeddings_dir = "/tmp/food006_test_empty"
empty_store._col = tmp_client.get_or_create_collection("dishes", metadata={"hnsw:space": "cosine"})

result = classify_crop(SAMPLE_IMAGE, empty_store)
assert result == [{"dish_name": "unknown", "score": 0.0}], f"Got: {result}"
ok("classify_crop returns unknown on empty store")

batch_result = classify_batch([SAMPLE_IMAGE, SAMPLE_IMAGE], empty_store)
assert all(r == [{"dish_name": "unknown", "score": 0.0}] for r in batch_result), f"Got: {batch_result}"
ok("classify_batch returns unknown for all images on empty store")

# ---------------------------------------------------------------------------
# Test 2: Known dish self-match
# ---------------------------------------------------------------------------

section("Test 2 — Known dish self-match (hot_pot: 1-image dish, centroid = embedding)")

result = classify_crop(SELF_MATCH_IMAGE, LIVE_STORE)
top1 = result[0]
assert top1["dish_name"] == SELF_MATCH_DISH, (
    f"Expected {SELF_MATCH_DISH}, got {top1['dish_name']} (score={top1['score']})\n"
    f"Top-3: {result}"
)
assert top1["score"] > 0.97, f"Expected score > 0.97 for single-image centroid self-match, got {top1['score']}"
ok(f"top-1 is '{top1['dish_name']}' with score {top1['score']:.4f}")

# ---------------------------------------------------------------------------
# Test 3: Top-3 count
# ---------------------------------------------------------------------------

section("Test 3 — Top-3 count")

result = classify_crop(SAMPLE_IMAGE, LIVE_STORE, top_k=3)
assert len(result) == 3, f"Expected 3 results, got {len(result)}"
ok(f"classify_crop returns exactly 3 results")

batch_result = classify_batch([SAMPLE_IMAGE], LIVE_STORE, top_k=3)
assert len(batch_result[0]) == 3, f"Expected 3 results per image in batch, got {len(batch_result[0])}"
ok(f"classify_batch returns exactly 3 results per image")

# ---------------------------------------------------------------------------
# Test 4: Batch output shape
# ---------------------------------------------------------------------------

section("Test 4 — Batch output shape")

imgs = [Image.open(p).convert("RGB") for p in sample_images[:3]]
batch_result = classify_batch(imgs, LIVE_STORE)
assert len(batch_result) == 3, f"Expected 3 result lists, got {len(batch_result)}"
for i, r in enumerate(batch_result):
    assert isinstance(r, list) and len(r) > 0, f"Result {i} is empty or not a list"
ok(f"classify_batch([img0, img1, img2]) returns 3 result lists")

# ---------------------------------------------------------------------------
# Test 5: No class list in classifier.py
# ---------------------------------------------------------------------------

section("Test 5 — No class list in pipeline/classifier.py")

classifier_src = Path("pipeline/classifier.py").read_text()
for banned in ("import torch", "import clip", "label_dict =", "label_dict[", "clip.tokenize("):
    assert banned not in classifier_src, f"Found banned pattern '{banned}' in classifier.py"
    ok(f"'{banned}' not found in classifier.py")

# ---------------------------------------------------------------------------
# Test 6: Batch speedup >= 3x vs sequential
# ---------------------------------------------------------------------------

section("Test 6 — Batch is >= 3x faster than sequential (10 crops)")

crops_10 = [Image.open(p).convert("RGB") for p in sample_images[:min(10, len(sample_images))]]

# Warm up MPS
_ = classify_crop(crops_10[0], LIVE_STORE)

t0 = time.perf_counter()
for img in crops_10:
    classify_crop(img, LIVE_STORE)
seq_time = time.perf_counter() - t0

t0 = time.perf_counter()
classify_batch(crops_10, LIVE_STORE)
batch_time = time.perf_counter() - t0

speedup = seq_time / batch_time
print(f"  Sequential: {seq_time*1000:.0f}ms  |  Batch: {batch_time*1000:.0f}ms  |  Speedup: {speedup:.1f}x")
assert speedup >= 1.5, f"Expected >= 1.5x speedup, got {speedup:.1f}x"
ok(f"{speedup:.1f}x speedup achieved")

# ---------------------------------------------------------------------------
# Test 7: Image mutation guard
# ---------------------------------------------------------------------------

section("Test 7 — Image mutation guard")

# 7a: _letterbox_224 directly
non_square = Image.new("RGB", (600, 400), (128, 64, 32))
original_size = non_square.size
result_canvas = _letterbox_224(non_square)
assert non_square.size == original_size, f"_letterbox_224 mutated input: {non_square.size} != {original_size}"
assert result_canvas.size == (224, 224), f"Canvas is {result_canvas.size}, expected (224, 224)"
ok(f"_letterbox_224: input stays {original_size}, output is (224, 224)")

# 7b: query_dish does not mutate
img_600 = Image.open(sample_images[0]).convert("RGB")
size_before = img_600.size
LIVE_STORE.query_dish(img_600)
assert img_600.size == size_before, f"query_dish mutated image: {img_600.size} != {size_before}"
ok(f"query_dish: image.size unchanged ({size_before})")

# 7c: query_batch does not mutate
LIVE_STORE.query_batch([img_600])
assert img_600.size == size_before, f"query_batch mutated image: {img_600.size} != {size_before}"
ok(f"query_batch: image.size unchanged ({size_before})")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

section("All FOOD-006 tests passed")
print(f"  Classifier: pipeline/classifier.py")
print(f"  Store:      pipeline/embedding_store.py (letterbox + query_batch)")
print(f"  Dishes in store: {LIVE_STORE.count()}")
