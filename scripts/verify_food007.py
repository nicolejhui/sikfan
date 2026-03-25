"""FOOD-007 verification: smoke tests for all four EmbeddingStore methods.

Uses real test images already in the repo — no external downloads required.
Creates a temporary store in /tmp so it does not pollute data/embeddings/.
"""

import shutil
import tempfile

from PIL import Image

from pipeline.embedding_store import EmbeddingStore

TEST_IMAGES = [
    ("hot_pot",          "data/dishes/hot_pot/hot_pot_christmas.jpeg"),
    ("breakfast_plate",  "data/assorted_breakfast.jpeg"),
    ("fungus_soup",      "data/fungus_dessert_Soup.jpeg"),
]

# ── Temp store (discarded after test) ────────────────────────────────────────
tmp_dir = tempfile.mkdtemp(prefix="food007_test_")
try:
    store = EmbeddingStore(embeddings_dir=tmp_dir)

    # ── 1. Empty store returns [] ─────────────────────────────────────────────
    assert store.list_dishes() == [], "Expected empty list on fresh store"
    assert store.count() == 0, "Expected count 0"
    dummy = Image.open(TEST_IMAGES[0][1])
    assert store.query_dish(dummy, top_k=3) == [], "query on empty store should return []"
    print("PASS  1. empty-store behaviour")

    # ── 2. add_dish + list_dishes ─────────────────────────────────────────────
    for name, path in TEST_IMAGES:
        store.add_dish(name, path)

    dishes = store.list_dishes()
    assert set(dishes) == {name for name, _ in TEST_IMAGES}, \
        f"list_dishes mismatch: {dishes}"
    assert store.count() == len(TEST_IMAGES)
    print(f"PASS  2. add_dish + list_dishes  ({store.count()} dishes)")

    # ── 3. query_dish — querying with same image should be top-1 self-match ───
    for name, path in TEST_IMAGES:
        results = store.query_dish(path, top_k=3)
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"
        assert results[0]["dish_name"] == name, \
            f"Top-1 for {name} was {results[0]['dish_name']} (score={results[0]['score']})"
        assert 0.0 <= results[0]["score"] <= 1.0, \
            f"Score out of range: {results[0]['score']}"
    print("PASS  3. query_dish self-match (top-1 = self for all 3 dishes)")

    # ── 4. Upsert deduplication — re-adding should not grow count ────────────
    before = store.count()
    store.add_dish("hot_pot", TEST_IMAGES[0][1])
    assert store.count() == before, \
        f"Upsert should not grow count: {before} → {store.count()}"
    print("PASS  4. upsert deduplication (count unchanged after re-add)")

    # ── 5. delete_dish ────────────────────────────────────────────────────────
    store.delete_dish("hot_pot")
    assert store.count() == len(TEST_IMAGES) - 1
    assert "hot_pot" not in store.list_dishes()
    print("PASS  5. delete_dish")

    # ── 6. update_centroid changes the stored embedding ───────────────────────
    # Re-add hot_pot first
    store.add_dish("hot_pot", TEST_IMAGES[0][1])
    before_count = store.count()

    # Query before update
    r_before = store.query_dish(TEST_IMAGES[1][1], top_k=1)[0]["score"]

    store.update_centroid("hot_pot", TEST_IMAGES[1][1])
    assert store.count() == before_count, "update_centroid must not change dish count"

    # confirmed_count should have incremented
    result = store._col.get(ids=["hot_pot"], include=["metadatas"])
    confirmed = result["metadatas"][0]["confirmed_count"]
    assert confirmed == 1, f"confirmed_count should be 1, got {confirmed}"
    print(f"PASS  6. update_centroid (confirmed_count={confirmed})")

    # ── 7. Persistence — new store instance reads the same data ──────────────
    store2 = EmbeddingStore(embeddings_dir=tmp_dir)
    assert store2.count() == store.count(), \
        f"Persistence failed: {store.count()} written, {store2.count()} read back"
    print(f"PASS  7. persistence across instances ({store2.count()} dishes)")

finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

print("\nAll FOOD-007 tests passed.")
