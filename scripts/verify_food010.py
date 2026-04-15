"""
FOOD-010 verification: Confirmation and correction feedback loop.

Tests:
   1. CONFIRM (n=0→1): count increments, centroid unchanged (gate n < 2)
   2. CONFIRM (n=1→2): count increments, centroid unchanged (gate n < 2)
   3. CONFIRM (n=2→3): update_centroid() fires, centroid changes
   4. CORRECT to existing dish: same gate applies
   5. ADD_NEW (new name): dish added, immediately queryable as top-1
   6. ADD_NEW (existing name): gate applies, confirmed_count not reset to 0
   7. Crop saved to {dishes_dir}/{dish_name}/{ts_ms}.jpg
   8. Correction log: valid JSON with all required fields
   9. CORRECT to dish not in store: new entry created via add_dish path
  10. Save failure: no crash, crop_saved_path=None, log still written
  11. CONFIRM with mismatched corrected_label: raises ValueError
  12. Label normalization: "Mapo Tofu" resolves to "mapo_tofu"
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
# Setup
# ---------------------------------------------------------------------------

section("Setup")

import chromadb
from PIL import Image
from pipeline.embedding_store import EmbeddingStore
from pipeline.feedback import record_feedback, FeedbackAction

def _make_store(emb_dir: str) -> EmbeddingStore:
    """
    Fresh isolated store seeded with one dish: mapo_tofu.
    Bypasses the module-level _collection singleton so each test gets its own
    ChromaDB instance — same pattern as verify_food009.py test 7.
    """
    client = chromadb.PersistentClient(path=emb_dir)
    col = client.get_or_create_collection("dishes", metadata={"hnsw:space": "cosine"})
    store = EmbeddingStore.__new__(EmbeddingStore)
    store._device = "mps"
    store._embeddings_dir = emb_dir
    store._col = col
    store.add_dish("mapo_tofu", Image.new("RGB", (224, 224), (200, 100, 50)))
    return store

def _img(color=(128, 64, 32)) -> Image.Image:
    return Image.new("RGB", (100, 100), color)

def _read_emb(store: EmbeddingStore, dish: str) -> list:
    return list(store._col.get(ids=[dish], include=["embeddings"])["embeddings"][0])

print("  Imports OK")

# ---------------------------------------------------------------------------
# Test 1 — CONFIRM n=0→1: count increments, centroid unchanged
# ---------------------------------------------------------------------------

section("Test 1 — CONFIRM (n=0→1): centroid unchanged")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    emb_before = _read_emb(store, "mapo_tofu")

    result = record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="mapo_tofu",
        confidence=0.88,
        action=FeedbackAction.CONFIRM,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["confirmed_count"] == 1, f"Expected 1, got {result['confirmed_count']}"
    ok("confirmed_count incremented to 1")

    emb_after = _read_emb(store, "mapo_tofu")
    assert emb_before == emb_after, "Centroid must not change for n < 2"
    ok("centroid unchanged (seed preserved)")

# ---------------------------------------------------------------------------
# Test 2 — CONFIRM n=1→2: count increments, centroid unchanged
# ---------------------------------------------------------------------------

section("Test 2 — CONFIRM (n=1→2): centroid unchanged")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    emb_before = _read_emb(store, "mapo_tofu")

    for _ in range(2):
        result = record_feedback(
            crop=_img(),
            original_prediction="mapo_tofu",
            corrected_label="mapo_tofu",
            confidence=0.88,
            action=FeedbackAction.CONFIRM,
            store=store,
            dishes_dir=f"{tmp}/dishes",
            log_path=f"{tmp}/log.jsonl",
        )

    assert result["confirmed_count"] == 2, f"Expected 2, got {result['confirmed_count']}"
    ok("confirmed_count reached 2")

    emb_after = _read_emb(store, "mapo_tofu")
    assert emb_before == emb_after, "Centroid must not change for n < 2"
    ok("centroid still unchanged after 2nd confirmation")

# ---------------------------------------------------------------------------
# Test 3 — CONFIRM n=2→3: centroid updates (rolling average fires)
# ---------------------------------------------------------------------------

section("Test 3 — CONFIRM (n=2→3): centroid updates")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")

    # First two confirmations — count-only
    for _ in range(2):
        record_feedback(
            crop=_img(),
            original_prediction="mapo_tofu",
            corrected_label="mapo_tofu",
            confidence=0.88,
            action=FeedbackAction.CONFIRM,
            store=store,
            dishes_dir=f"{tmp}/dishes",
            log_path=f"{tmp}/log.jsonl",
        )

    emb_before = _read_emb(store, "mapo_tofu")

    # Third confirmation — rolling average fires with a clearly different crop
    result = record_feedback(
        crop=_img(color=(0, 200, 255)),  # different color → different CLIP embedding
        original_prediction="mapo_tofu",
        corrected_label="mapo_tofu",
        confidence=0.88,
        action=FeedbackAction.CONFIRM,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["confirmed_count"] == 3, f"Expected 3, got {result['confirmed_count']}"
    ok("confirmed_count reached 3")

    emb_after = _read_emb(store, "mapo_tofu")
    assert emb_before != emb_after, "Centroid must change after 3rd confirmation"
    ok("centroid changed — rolling average fired on 3rd confirmation")

# ---------------------------------------------------------------------------
# Test 4 — CORRECT to existing dish: same gate applies
# ---------------------------------------------------------------------------

section("Test 4 — CORRECT to existing dish uses same gate")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    store.add_dish("braised_pork", _img((50, 150, 200)))
    emb_before = _read_emb(store, "braised_pork")

    result = record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="braised_pork",
        confidence=0.73,
        action=FeedbackAction.CORRECT,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["action"] == "CORRECT"
    assert result["dish_name"] == "braised_pork"
    assert result["confirmed_count"] == 1, f"Expected 1, got {result['confirmed_count']}"
    ok("CORRECT: confirmed_count incremented to 1")

    emb_after = _read_emb(store, "braised_pork")
    assert emb_before == emb_after, "Centroid must not change on first CORRECT (gate n < 2)"
    ok("CORRECT: centroid unchanged for n < 2 (gate applies)")

# ---------------------------------------------------------------------------
# Test 5 — ADD_NEW (new name): dish added, immediately queryable
# ---------------------------------------------------------------------------

section("Test 5 — ADD_NEW (new name): added and queryable")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    assert "kimchi" not in store.list_dishes()

    new_crop = _img((255, 50, 50))
    result = record_feedback(
        crop=new_crop,
        original_prediction="unknown",
        corrected_label="kimchi",
        confidence=0.40,
        action=FeedbackAction.ADD_NEW,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["dish_name"] == "kimchi"
    assert "kimchi" in store.list_dishes(), "kimchi must be in store after ADD_NEW"
    ok("ADD_NEW: dish added to store")

    query = store.query_dish(new_crop, top_k=1)
    assert query[0]["dish_name"] == "kimchi", (
        f"Expected kimchi as top-1 query after ADD_NEW, got {query[0]['dish_name']}"
    )
    ok("ADD_NEW: dish immediately queryable as top-1")

# ---------------------------------------------------------------------------
# Test 6 — ADD_NEW (existing name): gate applies, confirmed_count not reset
# ---------------------------------------------------------------------------

section("Test 6 — ADD_NEW (existing name): preserves confirmed_count")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")

    # Accumulate 2 confirms so confirmed_count = 2
    for _ in range(2):
        record_feedback(
            crop=_img(),
            original_prediction="mapo_tofu",
            corrected_label="mapo_tofu",
            confidence=0.88,
            action=FeedbackAction.CONFIRM,
            store=store,
            dishes_dir=f"{tmp}/dishes",
            log_path=f"{tmp}/log.jsonl",
        )

    # ADD_NEW with existing name — must NOT reset to 0
    result = record_feedback(
        crop=_img((0, 100, 200)),
        original_prediction="unknown",
        corrected_label="mapo_tofu",
        confidence=0.45,
        action=FeedbackAction.ADD_NEW,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["confirmed_count"] == 3, (
        f"Expected 3 (gate update, not reset), got {result['confirmed_count']}"
    )
    ok("ADD_NEW on existing dish: confirmed_count not reset, reached 3 via gate")

# ---------------------------------------------------------------------------
# Test 7 — Crop saved to correct path
# ---------------------------------------------------------------------------

section("Test 7 — Crop saved to {dishes_dir}/{dish_name}/{ts_ms}.jpg")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")

    result = record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="mapo_tofu",
        confidence=0.90,
        action=FeedbackAction.CONFIRM,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    saved = result["crop_saved_path"]
    assert saved is not None, "crop_saved_path should not be None"
    assert Path(saved).exists(), f"Saved crop file does not exist: {saved}"
    ok("crop file exists on disk")

    assert Path(saved).suffix == ".jpg", f"Expected .jpg, got {Path(saved).suffix}"
    ok("crop saved as .jpg")

    assert "mapo_tofu" in saved, f"dish_name not in path: {saved}"
    ok("dish_name present in saved path")

# ---------------------------------------------------------------------------
# Test 8 — Correction log: valid JSON with required fields
# ---------------------------------------------------------------------------

section("Test 8 — Correction log JSONL format")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    log_path = f"{tmp}/log.jsonl"

    record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="braised_pork",
        confidence=0.71,
        action=FeedbackAction.CORRECT,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=log_path,
    )

    lines = [l for l in Path(log_path).read_text().splitlines() if l]
    assert len(lines) == 1, f"Expected 1 log entry, got {len(lines)}"
    ok("one log entry written")

    entry = json.loads(lines[0])
    for field in ("timestamp", "action", "original_prediction", "corrected_label", "confidence", "crop_saved_path"):
        assert field in entry, f"Missing field '{field}' in log entry"
    ok("all required fields present")

    assert entry["action"] == "CORRECT"
    assert entry["original_prediction"] == "mapo_tofu"
    assert entry["corrected_label"] == "braised_pork"
    assert entry["confidence"] == 0.71
    ok("field values correct")

# ---------------------------------------------------------------------------
# Test 9 — CORRECT to dish not in store: new entry created
# ---------------------------------------------------------------------------

section("Test 9 — CORRECT to unknown dish creates new entry")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    assert "new_dish_xyz" not in store.list_dishes()

    result = record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="new_dish_xyz",
        confidence=0.70,
        action=FeedbackAction.CORRECT,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert "new_dish_xyz" in store.list_dishes(), "new_dish_xyz must be in store"
    assert result["dish_name"] == "new_dish_xyz"
    assert result["confirmed_count"] == 0, (
        f"New dish via add_dish should have confirmed_count=0, got {result['confirmed_count']}"
    )
    ok("CORRECT to unknown dish: new entry created, confirmed_count=0")

# ---------------------------------------------------------------------------
# Test 10 — Save failure: no crash, crop_saved_path=None, log still written
# ---------------------------------------------------------------------------

section("Test 10 — Save failure: no crash, log still succeeds")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")

    result = record_feedback(
        crop=_img(),
        original_prediction="mapo_tofu",
        corrected_label="mapo_tofu",
        confidence=0.88,
        action=FeedbackAction.CONFIRM,
        store=store,
        dishes_dir="/nonexistent_readonly_path/dishes",  # will fail
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["crop_saved_path"] is None, (
        f"crop_saved_path should be None on save failure, got {result['crop_saved_path']}"
    )
    ok("crop_saved_path is None on save failure")

    assert result["logged"] is True, "log write should still succeed even if image save failed"
    ok("log entry still written despite save failure")

    assert "mapo_tofu" in store.list_dishes()
    ok("centroid update still completed despite save failure")

# ---------------------------------------------------------------------------
# Test 11 — CONFIRM with mismatched label raises ValueError
# ---------------------------------------------------------------------------

section("Test 11 — CONFIRM with mismatched corrected_label raises ValueError")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")
    raised = False
    try:
        record_feedback(
            crop=_img(),
            original_prediction="mapo_tofu",
            corrected_label="braised_pork",  # mismatch
            confidence=0.88,
            action=FeedbackAction.CONFIRM,
            store=store,
            dishes_dir=f"{tmp}/dishes",
            log_path=f"{tmp}/log.jsonl",
        )
    except ValueError:
        raised = True

    assert raised, "CONFIRM with mismatched label must raise ValueError"
    ok("CONFIRM with mismatched corrected_label raises ValueError")

# ---------------------------------------------------------------------------
# Test 12 — Label normalization: "Mapo Tofu" -> "mapo_tofu"
# ---------------------------------------------------------------------------

section("Test 12 — Label normalization: 'Mapo Tofu' == 'mapo_tofu'")

with tempfile.TemporaryDirectory() as tmp:
    store = _make_store(f"{tmp}/emb")

    result = record_feedback(
        crop=_img(),
        original_prediction="Mapo Tofu",   # mixed case with space
        corrected_label="Mapo Tofu",
        confidence=0.88,
        action=FeedbackAction.CONFIRM,
        store=store,
        dishes_dir=f"{tmp}/dishes",
        log_path=f"{tmp}/log.jsonl",
    )

    assert result["dish_name"] == "mapo_tofu", (
        f"Expected 'mapo_tofu' after normalization, got '{result['dish_name']}'"
    )
    ok("'Mapo Tofu' normalized to 'mapo_tofu'")

    assert result["confirmed_count"] == 1, (
        f"Normalized label must update the correct store entry, got count={result['confirmed_count']}"
    )
    ok("normalized label updated the correct store entry (mapo_tofu confirmed_count=1)")

# ---------------------------------------------------------------------------
# Cleanup & summary
# ---------------------------------------------------------------------------

section("All FOOD-010 tests passed")
print(f"  Feedback module:  pipeline/feedback.py")
print(f"  FeedbackAction:   CONFIRM | CORRECT | ADD_NEW")
print(f"  Centroid gate:    n < 2 → count only | n >= 2 → rolling average")
print(f"  Correction log:   data/correction_log.jsonl (append mode)")
