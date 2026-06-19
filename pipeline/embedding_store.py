"""
FOOD-007: Persistent ChromaDB embedding store for dish CLIP embeddings.

Stores one centroid embedding per dish. Supports add, query, delete, and list.
Embeddings persist to data/embeddings/ across process restarts.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Union

import clip
import chromadb
import numpy as np
import torch
from PIL import Image
from torchvision import transforms as _T

_COLLECTION_NAME = "dishes"
_EMBEDDINGS_DIR = "data/embeddings"

# Auto-detect device: MPS on Apple Silicon, CPU elsewhere (e.g. Fly.io Linux)
_DEVICE: str = "mps" if torch.backends.mps.is_available() else "cpu"

# Lazy singletons
_clip_model = None
_clip_preprocess = None
_chroma_client = None
_collection = None

# ---------------------------------------------------------------------------
# Letterbox preprocessing (replaces CLIP's default CenterCrop preprocess)
# ---------------------------------------------------------------------------
# CLIP's built-in preprocess does Resize(shortest-side=224) + CenterCrop(224),
# which discards content from non-square crops. Letterboxing preserves the full
# image by padding with black rather than cropping.

def _letterbox_224(image: Image.Image) -> Image.Image:
    """Fit image into 224×224 with black padding. Never mutates the input."""
    img_to_process = image.copy()               # thumbnail() mutates in-place
    img_to_process.thumbnail((224, 224), Image.BICUBIC)
    canvas = Image.new("RGB", (224, 224), (0, 0, 0))
    x = (224 - img_to_process.width) // 2
    y = (224 - img_to_process.height) // 2
    canvas.paste(img_to_process, (x, y))
    return canvas                               # caller's image.size is unchanged


# Same normalization constants as CLIP's built-in preprocess
_CLIP_NORMALIZE = _T.Normalize(
    mean=(0.48145466, 0.4578275, 0.40821073),
    std=(0.26862954, 0.26130258, 0.27577711),
)
_TO_TENSOR = _T.ToTensor()


def _get_clip(device: str = _DEVICE):
    global _clip_model, _clip_preprocess
    if _clip_model is None:
        _clip_model, _clip_preprocess = clip.load("ViT-B/32", device=device)
        _clip_model.eval()
    return _clip_model, _clip_preprocess


def _get_collection(embeddings_dir: str = _EMBEDDINGS_DIR) -> chromadb.Collection:
    global _chroma_client, _collection
    if _collection is None:
        Path(embeddings_dir).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=embeddings_dir)
        _collection = _chroma_client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _encode_image(
    image: Union[Image.Image, str],
    device: str = _DEVICE,
) -> list[float]:
    """Return a unit-norm CLIP ViT-B/32 embedding as a plain Python list."""
    model, preprocess = _get_clip(device)

    if isinstance(image, str):
        image = Image.open(image).convert("RGB")
    else:
        image = image.convert("RGB")

    tensor = _CLIP_NORMALIZE(_TO_TENSOR(_letterbox_224(image))).unsqueeze(0).to(device)
    with torch.no_grad():
        embedding = model.encode_image(tensor)

    embedding = embedding / embedding.norm(dim=-1, keepdim=True)  # unit norm
    return embedding.squeeze(0).cpu().float().tolist()


class EmbeddingStore:
    """
    Persistent ChromaDB-backed store for dish CLIP embeddings.

    Args:
        embeddings_dir: Path to ChromaDB persistence directory (default: data/embeddings/).
        device: Torch device for CLIP inference (default: mps).
    """

    def __init__(
        self,
        embeddings_dir: str = _EMBEDDINGS_DIR,
        device: str = _DEVICE,
    ):
        self._embeddings_dir = embeddings_dir
        self._device = device
        self._col = _get_collection(embeddings_dir)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def add_dish(
        self,
        name: str,
        image: Union[Image.Image, str],
        cuisine_type: str = "",
    ) -> None:
        """
        Encode image with CLIP and upsert into the store under `name`.

        If `name` already exists, the embedding and metadata are replaced.
        Use this for the initial add; for rolling-average updates (FOOD-010)
        use `update_centroid()`.

        Args:
            name:         Dish identifier (e.g. "mapo_tofu").
            image:        PIL.Image or file path to any JPEG/PNG.
            cuisine_type: Optional free-text cuisine tag stored in metadata.
        """
        embedding = _encode_image(image, self._device)
        self._col.upsert(
            ids=[name],
            embeddings=[embedding],
            metadatas=[{
                "dish_name": name,
                "cuisine_type": cuisine_type,
                "date_added": datetime.date.today().isoformat(),
                "confirmed_count": 0,
            }],
        )

    def query_dish(
        self,
        image: Union[Image.Image, str],
        top_k: int = 3,
    ) -> list[dict]:
        """
        Return the top-k closest dishes to the given image crop.

        Args:
            image: PIL.Image or file path.
            top_k: Number of results to return.

        Returns:
            List of dicts, sorted by descending similarity:
            [{"dish_name": str, "score": float, "metadata": dict}, ...]
            `score` is cosine similarity ∈ [0, 1].
            Returns [] if the store is empty.
        """
        if self._col.count() == 0:
            return []

        embedding = _encode_image(image, self._device)
        n = min(top_k, self._col.count())
        results = self._col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["metadatas", "distances"],
        )

        output = []
        for dist, meta in zip(
            results["distances"][0],
            results["metadatas"][0],
        ):
            # ChromaDB cosine space: distance = 1 - cosine_similarity
            score = float(1.0 - dist)
            output.append({
                "dish_name": meta["dish_name"],
                "score": round(score, 4),
                "metadata": meta,
            })

        return output

    def query_batch(
        self,
        images: list,
        top_k: int = 3,
    ) -> list[list[dict]]:
        """
        Encode N images in one GPU pass and query ChromaDB once.

        Args:
            images: List of PIL.Image objects (any size/aspect ratio).
            top_k:  Number of matches to return per image.

        Returns:
            List of length N; each element is a list of top-k dicts:
            [{"dish_name": str, "score": float}, ...]
            Returns [[]] * N if the store is empty.
        """
        if self._col.count() == 0:
            return [[]] * len(images)

        model, _ = _get_clip(self._device)
        tensors = [
            _CLIP_NORMALIZE(_TO_TENSOR(_letterbox_224(img.convert("RGB"))))
            for img in images
        ]
        batch = torch.stack(tensors).to(self._device)
        with torch.no_grad():
            embeddings = model.encode_image(batch)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
        embeddings_list = embeddings.cpu().float().tolist()

        n = min(top_k, self._col.count())
        results = self._col.query(
            query_embeddings=embeddings_list,
            n_results=n,
            include=["metadatas", "distances"],
        )

        output = []
        for dists, metas in zip(results["distances"], results["metadatas"]):
            matches = [
                {"dish_name": m["dish_name"], "score": round(float(1.0 - d), 4)}
                for d, m in zip(dists, metas)
            ]
            output.append(matches)
        return output

    def delete_dish(self, name: str) -> None:
        """Remove a dish and its embedding from the store."""
        self._col.delete(ids=[name])

    def list_dishes(self) -> list[str]:
        """Return all dish names currently in the store."""
        if self._col.count() == 0:
            return []
        result = self._col.get(include=["metadatas"])
        return [m["dish_name"] for m in result["metadatas"]]

    def update_centroid(
        self,
        name: str,
        new_image: Union[Image.Image, str],
    ) -> None:
        """
        Update the stored embedding via rolling average (for FOOD-010).

        new_centroid = (old_centroid * n + new_embedding) / (n + 1)
        where n = confirmed_count in metadata.

        Args:
            name:      Dish to update. Must already exist in the store.
            new_image: Newly confirmed image to fold into the centroid.
        """
        existing = self._col.get(ids=[name], include=["embeddings", "metadatas"])
        if not existing["ids"]:
            raise KeyError(f"Dish '{name}' not found in embedding store.")

        old_emb = np.array(existing["embeddings"][0], dtype=np.float32)
        meta = existing["metadatas"][0]
        n = int(meta.get("confirmed_count", 0))

        new_emb = np.array(_encode_image(new_image, self._device), dtype=np.float32)
        centroid = (old_emb * n + new_emb) / (n + 1)
        centroid = centroid / np.linalg.norm(centroid)  # re-normalise

        meta["confirmed_count"] = n + 1
        self._col.upsert(
            ids=[name],
            embeddings=[centroid.tolist()],
            metadatas=[meta],
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return number of dishes in the store."""
        return self._col.count()
