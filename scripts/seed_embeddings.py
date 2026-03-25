"""
FOOD-008: Seed ChromaDB embedding store from labeled dish images.

Encodes all JPEGs in data/dishes/{dish_name}/ via CLIP ViT-B/32,
computes a unit-norm centroid per dish, and upserts into ChromaDB.

Usage:
    python scripts/seed_embeddings.py
    python scripts/seed_embeddings.py --source data/dishes/ --batch-size 32
    python scripts/seed_embeddings.py --cnfood data/CNFOOD-241/train600x600/
    python scripts/seed_embeddings.py --source data/dishes/ --cnfood data/CNFOOD-241/train600x600/

Idempotent: re-running overwrites existing centroids rather than duplicating.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

import clip
import chromadb
import numpy as np
import torch
from PIL import Image

# Use the same letterbox preprocessing as the embedding store so that
# centroid embeddings and query embeddings live in the same space.
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.embedding_store import _letterbox_224, _CLIP_NORMALIZE, _TO_TENSOR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EMBEDDINGS_DIR = "data/embeddings"
_COLLECTION_NAME = "dishes"
_DEFAULT_SOURCE = "data/dishes"
_VALID_EXTS = {".jpg", ".jpeg", ".png"}


# ---------------------------------------------------------------------------
# CLIP helpers
# ---------------------------------------------------------------------------

def _load_clip(device: str = "mps"):
    model, _preprocess = clip.load("ViT-B/32", device=device)
    model.eval()
    return model  # preprocess not used — letterbox handles it


def _encode_batch(
    paths: list[Path],
    model,
    device: str,
) -> np.ndarray:
    """Encode a list of image paths with letterbox preprocessing.

    Uses the same _letterbox_224 / _CLIP_NORMALIZE pipeline as EmbeddingStore
    so that seed-time and query-time embeddings share the same space.
    """
    tensors = []
    for p in paths:
        try:
            img = Image.open(p).convert("RGB")
            tensors.append(_CLIP_NORMALIZE(_TO_TENSOR(_letterbox_224(img))))
        except Exception as exc:
            print(f"  WARNING: could not load {p.name}: {exc}", flush=True)

    if not tensors:
        return np.empty((0, 512), dtype=np.float32)

    batch = torch.stack(tensors).to(device)
    with torch.no_grad():
        embeddings = model.encode_image(batch)
    embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
    return embeddings.cpu().float().numpy()


# ---------------------------------------------------------------------------
# Centroid computation
# ---------------------------------------------------------------------------

def _compute_centroid(embeddings: np.ndarray) -> np.ndarray:
    """Return the unit-norm mean of a (N, D) embedding matrix."""
    centroid = embeddings.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm
    return centroid


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def seed_folder(
    folder: Path,
    dish_name: str,
    collection: chromadb.Collection,
    model,
    device: str,
    batch_size: int,
) -> int:
    """
    Encode all images in `folder`, compute a centroid, and upsert into `collection`.

    Returns the number of images encoded (0 if folder was skipped).
    """
    image_paths = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in _VALID_EXTS
    )

    if not image_paths:
        print(f"  SKIP {dish_name} — no images found")
        return 0

    # Encode in batches
    all_embeddings: list[np.ndarray] = []
    for i in range(0, len(image_paths), batch_size):
        batch_paths = image_paths[i : i + batch_size]
        batch_emb = _encode_batch(batch_paths, model, device)
        if batch_emb.shape[0] > 0:
            all_embeddings.append(batch_emb)

    if not all_embeddings:
        print(f"  SKIP {dish_name} — all images failed to load")
        return 0

    embeddings = np.concatenate(all_embeddings, axis=0)
    centroid = _compute_centroid(embeddings)
    n = len(embeddings)

    collection.upsert(
        ids=[dish_name],
        embeddings=[centroid.tolist()],
        metadatas=[{
            "dish_name": dish_name,
            "cuisine_type": "",
            "date_added": datetime.date.today().isoformat(),
            "confirmed_count": 0,
            "source_image_count": n,
        }],
    )
    return n


def seed_source(
    source_dir: Path,
    collection: chromadb.Collection,
    model,
    device: str,
    batch_size: int,
    label: str = "",
) -> dict[str, int]:
    """
    Iterate over all subdirectories of `source_dir` and seed each as a dish.

    Returns a {dish_name: image_count} summary dict.
    """
    summary: dict[str, int] = {}

    dish_dirs = sorted(d for d in source_dir.iterdir() if d.is_dir())
    if not dish_dirs:
        print(f"No subdirectories found in {source_dir}")
        return summary

    prefix = f"[{label}] " if label else ""
    for dish_dir in dish_dirs:
        dish_name = dish_dir.name
        print(f"{prefix}Seeding {dish_name} ...", end=" ", flush=True)
        n = seed_folder(
            folder=dish_dir,
            dish_name=dish_name,
            collection=collection,
            model=model,
            device=device,
            batch_size=batch_size,
        )
        if n > 0:
            print(f"{n} images → centroid upserted")
            summary[dish_name] = n

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed ChromaDB with CLIP centroid embeddings from labeled dish images."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=_DEFAULT_SOURCE,
        help=f"Path to data/dishes/ directory (default: {_DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--cnfood",
        type=str,
        default=None,
        help="Optional path to CNFOOD-241/train600x600/ to additionally seed from",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="CLIP encoding batch size (default: 32)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="mps",
        help="Torch device for CLIP inference (default: mps)",
    )
    parser.add_argument(
        "--embeddings-dir",
        type=str,
        default=_EMBEDDINGS_DIR,
        help=f"ChromaDB persistence directory (default: {_EMBEDDINGS_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    source_dir = Path(args.source)
    if not source_dir.exists():
        print(f"ERROR: source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    cnfood_dir = Path(args.cnfood) if args.cnfood else None
    if cnfood_dir and not cnfood_dir.exists():
        print(f"ERROR: CNFOOD directory not found: {cnfood_dir}", file=sys.stderr)
        sys.exit(1)

    # Init ChromaDB
    Path(args.embeddings_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=args.embeddings_dir)
    collection = client.get_or_create_collection(
        name=_COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    print(f"Loading CLIP ViT-B/32 on {args.device} ...", flush=True)
    model = _load_clip(args.device)
    print("Model loaded.\n")

    combined_summary: dict[str, int] = {}

    # Seed from primary source (data/dishes/)
    print(f"=== Seeding from {source_dir} ===")
    summary = seed_source(
        source_dir=source_dir,
        collection=collection,
        model=model,
        device=args.device,
        batch_size=args.batch_size,
    )
    combined_summary.update(summary)

    # Optionally seed from CNFOOD-241
    if cnfood_dir:
        print(f"\n=== Seeding from {cnfood_dir} ===")
        cnfood_summary = seed_source(
            source_dir=cnfood_dir,
            collection=collection,
            model=model,
            device=args.device,
            batch_size=args.batch_size,
            label="CNFOOD",
        )
        combined_summary.update(cnfood_summary)

    # Final summary
    total_dishes = len(combined_summary)
    total_images = sum(combined_summary.values())
    print(f"\n{'='*50}")
    print(f"Seeding complete: {total_dishes} dishes, {total_images} images encoded")
    print(f"ChromaDB now contains {collection.count()} dish centroids")
    print(f"{'='*50}")
    print("\nPer-dish summary:")
    for dish, count in sorted(combined_summary.items()):
        print(f"  {dish:<45} {count:>5} images")


if __name__ == "__main__":
    main()
