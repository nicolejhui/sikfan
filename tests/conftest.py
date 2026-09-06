"""
Shared fixtures for the offline scenario suite (docs/TEST-SCENARIOS.md).

Isolation is the highest-risk part of this file: pipeline/portion.py does
`from pipeline.nutrition import CACHE_DIR, OVERRIDES_DIR` — a *separate*
binding from pipeline.nutrition's own module-level names. Patching
pipeline.nutrition.CACHE_DIR alone silently leaves pipeline.portion writing
to the real data/ directory. Every module-level storage-root binding is
patched below, independently, even where two names point at "the same"
directory in production.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Isolation: redirect every storage root at a per-test tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    import pipeline.nutrition as nutrition
    import pipeline.portion as portion
    import pipeline.macro_lookup as macro_lookup
    import pipeline.glucose_store as glucose_store

    overrides_dir = tmp_path / "overrides"
    cache_dir = tmp_path / "macro_cache"
    priors_dir = tmp_path / "portion_priors"
    glucose_dir = tmp_path / "glucose"

    # pipeline.nutrition's own bindings
    monkeypatch.setattr(nutrition, "OVERRIDES_DIR", overrides_dir)
    monkeypatch.setattr(nutrition, "CACHE_DIR", cache_dir)

    # pipeline.portion's SEPARATE bindings of the same two, plus its own PRIORS_DIR
    monkeypatch.setattr(portion, "OVERRIDES_DIR", overrides_dir)
    monkeypatch.setattr(portion, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(portion, "PRIORS_DIR", priors_dir)

    # pipeline.macro_lookup's own CACHE_DIR binding
    monkeypatch.setattr(macro_lookup, "CACHE_DIR", cache_dir)

    # pipeline.glucose_store's private module globals
    monkeypatch.setattr(glucose_store, "_DATA_DIR", glucose_dir)
    monkeypatch.setattr(glucose_store, "_CGM_FILE", glucose_dir / "cgm_readings.json")
    monkeypatch.setattr(glucose_store, "_MEAL_FILE", glucose_dir / "meal_logs.json")

    yield


# ---------------------------------------------------------------------------
# Network: any unmocked HTTP call fails loudly instead of hitting USDA
# ---------------------------------------------------------------------------

class _NoNetwork(Exception):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests

    def _blocked(*args, **kwargs):
        raise _NoNetwork(f"unmocked network call blocked: args={args} kwargs={kwargs}")

    monkeypatch.setattr(requests, "get", _blocked)
    monkeypatch.setattr(requests, "post", _blocked)


@pytest.fixture
def mock_usda(monkeypatch):
    """Opt-in fixture: queue canned USDA search responses.

    Usage: mock_usda.queue(status_code=200, foods=[...]) or
           mock_usda.queue(status_code=400)
    Each call to requests.get consumes the next queued response; raises
    IndexError if the queue runs dry (surfaces an unexpectedly-extra call).
    """
    import requests

    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code}")

    class _Queue:
        def __init__(self):
            self._responses = []
            self.calls = []

        def queue(self, status_code=200, foods=None):
            self._responses.append(_Resp(status_code, {"foods": foods or []}))

        def __call__(self, url, params=None, timeout=None):
            self.calls.append({"url": url, "params": params})
            return self._responses.pop(0)

    q = _Queue()
    monkeypatch.setattr(requests, "get", q)
    return q


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    import pipeline.macro_lookup as macro_lookup

    monkeypatch.setattr(macro_lookup.time, "sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# Fake embedding store — stands in for ChromaDB/CLIP for feedback.py tests
# ---------------------------------------------------------------------------

class FakeCollection:
    def __init__(self):
        self._metas: dict[str, dict] = {}

    def get(self, ids=None, include=None):
        ids = ids if ids is not None else list(self._metas.keys())
        return {"ids": ids, "metadatas": [self._metas.get(i) for i in ids]}

    def update(self, ids, metadatas):
        for i, m in zip(ids, metadatas):
            self._metas[i] = m


class FakeStore:
    """Duck-types the slice of EmbeddingStore that pipeline/feedback.py uses:
    store._col.get/.update, list_dishes(), add_dish(), update_centroid().
    Never touches ChromaDB or CLIP."""

    def __init__(self):
        self._col = FakeCollection()
        self.update_centroid_calls: list[str] = []
        self.add_dish_calls: list[str] = []

    def list_dishes(self) -> list[str]:
        return list(self._col._metas.keys())

    def add_dish(self, name, crop):
        self.add_dish_calls.append(name)
        self._col._metas[name] = {"confirmed_count": 0}

    def update_centroid(self, name, crop):
        self.update_centroid_calls.append(name)
        meta = self._col._metas[name]
        meta["confirmed_count"] = int(meta.get("confirmed_count", 0)) + 1
        self._col._metas[name] = meta

    def seed(self, name, confirmed_count=0):
        self._col._metas[name] = {"confirmed_count": confirmed_count}


@pytest.fixture
def fake_store():
    return FakeStore()


# ---------------------------------------------------------------------------
# Crop fixture — a real, tiny in-memory image, no disk fixtures needed
# ---------------------------------------------------------------------------

@pytest.fixture
def crop():
    return Image.new("RGB", (64, 64), color=(200, 120, 80))
