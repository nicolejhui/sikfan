"""
FOOD-009 verification: Confidence thresholding and unknown detection.

Tests:
  1. CONFIDENT     — score 0.90 -> status CONFIDENT, no log entry written
  2. UNCERTAIN     — score 0.73 -> status UNCERTAIN, entry logged to queue
  3. UNKNOWN       — score 0.50 -> status UNKNOWN, entry logged to queue
  4. Empty store   — _UNKNOWN sentinel (score 0.0) -> status UNKNOWN
  5. Custom thresh — inject confident=0.90; score 0.85 -> UNCERTAIN (not CONFIDENT)
  6. candidates    — always present in output, even for CONFIDENT result
  7. Backward compat — classify_crop() schema unchanged: [{"dish_name", "score"}]
  8. Queue format  — logged entry is valid JSON with required fields
  9. Independence  — classify_components() statuses not affected by apply_threshold()
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

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
# Imports
# ---------------------------------------------------------------------------

section("Setup")

from pipeline.classifier import apply_threshold, classify_crop, classify_batch, _UNKNOWN

# Synthetic helpers — avoids needing a live store for tests 1-5 & 8
def _results(top_score: float, top_name: str = "mapo_tofu") -> list[dict]:
    return [
        {"dish_name": top_name, "score": top_score},
        {"dish_name": "braised_pork", "score": top_score - 0.05},
        {"dish_name": "red_braised_pork", "score": top_score - 0.10},
    ]

DEFAULT_THRESHOLDS = {"confident": 0.82, "uncertain": 0.65}

print("  Imports OK")

# ---------------------------------------------------------------------------
# Test 1: CONFIDENT — no log entry
# ---------------------------------------------------------------------------

section("Test 1 — CONFIDENT (score 0.90)")

with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
    queue_path = tmp.name

Path(queue_path).write_text("")  # start empty

result = apply_threshold(_results(0.90), thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)
assert result["status"] == "CONFIDENT", f"Expected CONFIDENT, got {result['status']}"
ok("status is CONFIDENT")

lines = Path(queue_path).read_text().strip().splitlines()
assert lines == [] or lines == [""], f"Expected no log entries, got: {lines}"
ok("no entry written to review queue")

# ---------------------------------------------------------------------------
# Test 2: UNCERTAIN — logged
# ---------------------------------------------------------------------------

section("Test 2 — UNCERTAIN (score 0.73)")

Path(queue_path).write_text("")

result = apply_threshold(_results(0.73), thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)
assert result["status"] == "UNCERTAIN", f"Expected UNCERTAIN, got {result['status']}"
ok("status is UNCERTAIN")

lines = [l for l in Path(queue_path).read_text().strip().splitlines() if l]
assert len(lines) == 1, f"Expected 1 log entry, got {len(lines)}"
ok("one entry written to review queue")

# ---------------------------------------------------------------------------
# Test 3: UNKNOWN (low score) — logged
# ---------------------------------------------------------------------------

section("Test 3 — UNKNOWN (score 0.50)")

Path(queue_path).write_text("")

result = apply_threshold(_results(0.50), thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)
assert result["status"] == "UNKNOWN", f"Expected UNKNOWN, got {result['status']}"
ok("status is UNKNOWN for score below uncertain threshold")

lines = [l for l in Path(queue_path).read_text().strip().splitlines() if l]
assert len(lines) == 1, f"Expected 1 log entry, got {len(lines)}"
ok("one entry written to review queue")

# ---------------------------------------------------------------------------
# Test 4: Empty store sentinel
# ---------------------------------------------------------------------------

section("Test 4 — Empty store sentinel (_UNKNOWN)")

Path(queue_path).write_text("")

result = apply_threshold(_UNKNOWN, thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)
assert result["status"] == "UNKNOWN", f"Expected UNKNOWN for sentinel, got {result['status']}"
assert result["dish_name"] == "unknown", f"Expected 'unknown', got {result['dish_name']}"
ok("_UNKNOWN sentinel -> status UNKNOWN, dish_name 'unknown'")

# ---------------------------------------------------------------------------
# Test 5: Custom thresholds injected
# ---------------------------------------------------------------------------

section("Test 5 — Custom thresholds (confident=0.90)")

custom = {"confident": 0.90, "uncertain": 0.70}
result = apply_threshold(_results(0.85), thresholds=custom, queue_path=queue_path)
assert result["status"] == "UNCERTAIN", (
    f"score 0.85 should be UNCERTAIN with confident=0.90, got {result['status']}"
)
ok("score 0.85 is UNCERTAIN when confident threshold is 0.90")

result = apply_threshold(_results(0.92), thresholds=custom, queue_path=queue_path)
assert result["status"] == "CONFIDENT", (
    f"score 0.92 should be CONFIDENT with confident=0.90, got {result['status']}"
)
ok("score 0.92 is CONFIDENT when confident threshold is 0.90")

# ---------------------------------------------------------------------------
# Test 6: candidates always present
# ---------------------------------------------------------------------------

section("Test 6 — candidates always present")

for score, expected_status in [(0.90, "CONFIDENT"), (0.73, "UNCERTAIN"), (0.50, "UNKNOWN")]:
    r = apply_threshold(_results(score), thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)
    assert "candidates" in r, f"Missing 'candidates' key for status {expected_status}"
    assert isinstance(r["candidates"], list) and len(r["candidates"]) > 0, (
        f"candidates is empty for status {expected_status}"
    )
ok("candidates key present and non-empty for CONFIDENT, UNCERTAIN, UNKNOWN")

# ---------------------------------------------------------------------------
# Test 7: classify_crop() schema unchanged
# ---------------------------------------------------------------------------

section("Test 7 — classify_crop() backward compat schema")

import chromadb
from pipeline.embedding_store import EmbeddingStore
from PIL import Image

tmp_client = chromadb.PersistentClient(path="/tmp/food009_test_empty")
if "dishes" in [c.name for c in tmp_client.list_collections()]:
    tmp_client.delete_collection("dishes")
empty_store = EmbeddingStore.__new__(EmbeddingStore)
empty_store._device = "mps"
empty_store._embeddings_dir = "/tmp/food009_test_empty"
empty_store._col = tmp_client.get_or_create_collection("dishes", metadata={"hnsw:space": "cosine"})

dummy_img = Image.new("RGB", (224, 224), (128, 64, 32))
crop_result = classify_crop(dummy_img, empty_store)
assert isinstance(crop_result, list), f"classify_crop should return list, got {type(crop_result)}"
assert all("dish_name" in r and "score" in r for r in crop_result), (
    f"classify_crop items must have dish_name and score keys: {crop_result}"
)
assert all(len(r) == 2 for r in crop_result), (
    f"classify_crop items must have exactly 2 keys (no status added): {crop_result}"
)
ok("classify_crop() returns [{'dish_name', 'score'}] — schema unchanged")

batch_result = classify_batch([dummy_img], empty_store)
assert isinstance(batch_result, list) and isinstance(batch_result[0], list), (
    f"classify_batch should return list of lists"
)
ok("classify_batch() schema unchanged")

# ---------------------------------------------------------------------------
# Test 8: Queue entry format
# ---------------------------------------------------------------------------

section("Test 8 — Review queue JSONL format")

Path(queue_path).write_text("")

apply_threshold(
    _results(0.73),
    thresholds=DEFAULT_THRESHOLDS,
    queue_path=queue_path,
    image_id="test_crop_0",
)

lines = [l for l in Path(queue_path).read_text().strip().splitlines() if l]
assert len(lines) == 1, f"Expected 1 entry, got {len(lines)}"

entry = json.loads(lines[0])
for field in ("timestamp", "status", "dish_name", "confidence", "candidates"):
    assert field in entry, f"Missing field '{field}' in queue entry"
ok("queue entry has all required fields: timestamp, status, dish_name, confidence, candidates")

assert entry["image_id"] == "test_crop_0", f"image_id not recorded: {entry}"
ok("optional image_id field recorded when provided")

assert entry["status"] == "UNCERTAIN"
assert isinstance(entry["candidates"], list)
ok("entry values are correct types")

# ---------------------------------------------------------------------------
# Test 9: classify_components() statuses independent of apply_threshold()
# ---------------------------------------------------------------------------

section("Test 9 — mixed_bowl statuses independent of apply_threshold()")

from pipeline.component_classifier import classify_components

# Build a minimal crop_dict that will be detected as single_dish
# (small mask, low color complexity) so component_classifier won't run CLIP
small_crop = Image.new("RGB", (50, 50), (200, 100, 50))  # uniform-ish color
crop_dict = {
    "crop": small_crop,
    "bbox": (0, 0, 50, 50),
    "mask_pixels": 100,    # 100 / 1000000 = 0.0001 << large_crop_threshold
    "image_pixels": 1_000_000,
}
comp_result = classify_components(crop_dict)
assert comp_result["crop_type"] == "single_dish", (
    f"Expected single_dish for small crop, got {comp_result['crop_type']}"
)

# apply_threshold operates on classify_crop() output — completely separate
threshold_result = apply_threshold(_results(0.73), thresholds=DEFAULT_THRESHOLDS, queue_path=queue_path)

# Neither call affected the other — comp_result has no 'status', threshold_result has no 'crop_type'
assert "status" not in comp_result, "classify_components single_dish should not have 'status'"
assert "crop_type" not in threshold_result, "apply_threshold should not return 'crop_type'"
ok("classify_components() and apply_threshold() outputs are independent")

# ---------------------------------------------------------------------------
# Cleanup & summary
# ---------------------------------------------------------------------------

Path(queue_path).unlink(missing_ok=True)

section("All FOOD-009 tests passed")
print(f"  apply_threshold: pipeline/classifier.py")
print(f"  Thresholds:      config.yaml (confident=0.82, uncertain=0.65)")
print(f"  Review queue:    data/review_queue.jsonl (append mode)")
