"""
FOOD-005b: Mixed-component bowl detection.

Detects whether a crop from segment_meal() is a single dish or a mixed-ingredient
bowl, then classifies each role (base/protein/vegetable) via CLIP text prompts.

Role prompts live in config.yaml under component_detection.role_prompts — edit
that file to add new ingredients or roles without touching this code.

Public API:
    classify_components(crop_dict, device="mps", role_prompts=None, ...) -> dict

Returns:
    single_dish: {"crop_type": "single_dish", "crop", "bbox", "mask_pixels", "image_pixels"}
    mixed_bowl:  {"crop_type": "mixed_bowl", "crop", "bbox", "mask_pixels", "image_pixels",
                  "components": [...], "macros": None}
"""

from __future__ import annotations

import clip
import numpy as np
import torch
from PIL import Image

from pipeline.embedding_store import _get_clip

# Auto-detect device: MPS on Apple Silicon, CPU elsewhere (e.g. Fly.io Linux)
_DEVICE: str = "mps" if torch.backends.mps.is_available() else "cpu"


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_role_prompts(config_path: str = "config.yaml") -> dict[str, list[str]]:
    """Load role prompts from config.yaml component_detection.role_prompts section."""
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    return cfg["component_detection"]["role_prompts"]


# ---------------------------------------------------------------------------
# Complexity detection helpers
# ---------------------------------------------------------------------------

def _count_color_clusters(image: Image.Image, k: int, min_fraction: float = 0.05) -> int:
    """
    Count visually distinct color clusters in an image using PIL median-cut quantization.

    Runs on a 64x64 thumbnail for speed. Returns the number of clusters that
    cover more than min_fraction of the thumbnail's pixels.

    Note: crops from segment_meal() are rectangular bbox slices of the original
    RGB image — no mask-applied zero-fill — so there are no black background
    pixels to filter out. Any extra pixels (plate rim, tablecloth) are real image
    content, typically a single neutral color that adds at most 1 cluster.
    """
    thumb = image.copy()
    thumb.thumbnail((64, 64), Image.BICUBIC)
    quantized = thumb.convert("RGB").quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pixel_indices = np.array(quantized).flatten()
    counts = np.bincount(pixel_indices, minlength=k)[:k]
    total = len(pixel_indices)
    return int(np.sum(counts > total * min_fraction))


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------

def _classify_roles(
    image: Image.Image,
    device: str,
    role_prompts: dict[str, list[str]],
    role_confidence_threshold: float,
) -> list[dict]:
    """
    Classify each role in a mixed crop using CLIP text-image matching.

    Encodes the crop image once, then compares against each role's prompts.
    Roles where the top-1 prompt is a sentinel ("no X visible") are excluded.

    Sentinel convention: any prompt starting with "no " and containing "visible"
    signals the component is absent — that role is skipped in the output.

    dish_name is the full matched prompt text normalized to underscores:
      "cooked white rice" -> "cooked_white_rice"
    This gives FOOD-015 a descriptive label for the confirmation UI.
    """
    model, preprocess = _get_clip(device)

    image_tensor = preprocess(image).unsqueeze(0).to(device)
    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
    image_features = image_features / image_features.norm(dim=-1, keepdim=True)

    components = []
    for role, prompts in role_prompts.items():
        text_tokens = clip.tokenize(prompts).to(device)
        with torch.no_grad():
            text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        sims = (image_features @ text_features.T).squeeze(0)
        best_idx = int(sims.argmax().item())
        best_score = float(sims[best_idx])
        best_prompt = prompts[best_idx]

        # Skip roles where CLIP says the component isn't present
        if best_prompt.startswith("no ") and "visible" in best_prompt:
            continue

        dish_name = best_prompt.replace(" ", "_")
        # Base is always UNCERTAIN regardless of score — it is the primary
        # glycemic driver; a wrong assumption causes the largest glucose error
        status = (
            "UNCERTAIN"
            if (role == "base" or best_score < role_confidence_threshold)
            else "CONFIDENT"
        )

        components.append({
            "role": role,
            "dish_name": dish_name,
            "confidence": round(best_score, 4),
            "status": status,
            "confirmed": False,
        })

    return components


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_components(
    crop_dict: dict,
    device: str = _DEVICE,
    role_prompts: dict | None = None,
    kmeans_k: int = 4,
    kmeans_min_clusters: int = 3,
    large_crop_threshold: float = 0.25,
    role_confidence_threshold: float = 0.5,
) -> dict:
    """
    Determine if a crop is a single dish or a mixed-ingredient bowl, then
    classify roles if mixed.

    Args:
        crop_dict:               Output dict from segment_meal():
                                 {"crop", "bbox", "mask_pixels", "image_pixels"}
        device:                  Torch device for CLIP inference (default "mps").
        role_prompts:            Role->prompts dict. If None, loaded from config.yaml.
                                 Pass explicitly in tests to avoid disk reads.
        kmeans_k:                Number of color clusters for complexity heuristic.
        kmeans_min_clusters:     Min clusters to flag as mixed via color signal.
        large_crop_threshold:    mask_pixels / image_pixels threshold for size signal.
        role_confidence_threshold: CLIP score below this -> UNCERTAIN for any role.

    Returns:
        single_dish dict (crop_type="single_dish") or
        mixed_bowl dict  (crop_type="mixed_bowl") with components list.
    """
    # Complexity detection — two independent signals, AND gate
    large = crop_dict["mask_pixels"] / crop_dict["image_pixels"] > large_crop_threshold
    colorful = _count_color_clusters(crop_dict["crop"], k=kmeans_k) >= kmeans_min_clusters
    is_mixed = large and colorful

    if not is_mixed:
        return {
            "crop_type": "single_dish",
            "crop": crop_dict["crop"],
            "bbox": crop_dict["bbox"],
            "mask_pixels": crop_dict["mask_pixels"],
            "image_pixels": crop_dict["image_pixels"],
        }

    # Load prompts from config if not provided
    if role_prompts is None:
        role_prompts = _load_role_prompts()

    components = _classify_roles(
        crop_dict["crop"],
        device,
        role_prompts,
        role_confidence_threshold,
    )

    return {
        "crop_type": "mixed_bowl",
        "crop": crop_dict["crop"],
        "bbox": crop_dict["bbox"],
        "mask_pixels": crop_dict["mask_pixels"],
        "image_pixels": crop_dict["image_pixels"],
        "components": components,
        "macros": None,
    }
