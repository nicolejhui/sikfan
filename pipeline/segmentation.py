"""
FOOD-004: FastSAM blob detection on a single meal photo.

Returns per-region crops so each food item can be classified independently.
"""

import gc
import math

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.ops import nms
from ultralytics import FastSAM

_fastsam_model = None

# Auto-detect device: MPS on Apple Silicon, CPU elsewhere (e.g. Fly.io Linux)
_DEVICE: str = "mps" if torch.backends.mps.is_available() else "cpu"

# Defaults — mirrors config.yaml segmentation section
_MIN_AREA_PCT = 0.015
_MAX_ASPECT_RATIO = 4.0
_NMS_IOU_THRESHOLD = 0.4
_MAX_CROPS = 20
_MAX_LONG_EDGE = 1536
_MAX_DET = 100


def _get_model(model_path: str = "FastSAM-s.pt") -> FastSAM:
    global _fastsam_model
    if _fastsam_model is None:
        _fastsam_model = FastSAM(model_path)
    return _fastsam_model


def _apply_filters(
    crops: list[dict],
    max_aspect_ratio: float = _MAX_ASPECT_RATIO,
    nms_iou_threshold: float = _NMS_IOU_THRESHOLD,
    max_crops: int = _MAX_CROPS,
    img_w: int = 0,
    img_h: int = 0,
    verbose: bool = False,
) -> list[dict]:
    """Apply aspect ratio filter, NMS, and crop cap to a list of crop dicts."""
    if verbose:
        print(f"  After area filter:        {len(crops)}")

    # 1. Aspect ratio filter — discard thin/elongated artifacts
    candidates = []
    for c in crops:
        xmin, ymin, xmax, ymax = c["bbox"]
        bw, bh = xmax - xmin, ymax - ymin
        if bw == 0 or bh == 0:
            continue
        if max(bw / bh, bh / bw) > max_aspect_ratio:
            continue
        candidates.append(c)

    if verbose:
        print(f"  After AR filter ({max_aspect_ratio}:1):  {len(candidates)}")

    if not candidates:
        return []

    # 2. NMS — collapse overlapping masks for the same ingredient
    boxes = torch.tensor([c["bbox"] for c in candidates], dtype=torch.float32)
    scores = torch.tensor([c["mask_pixels"] for c in candidates], dtype=torch.float32)
    keep = nms(boxes, scores, iou_threshold=nms_iou_threshold)
    candidates = [candidates[i] for i in keep.tolist()]

    if verbose:
        print(f"  After NMS (IoU>{nms_iou_threshold}):     {len(candidates)}")

    # 3. Sort by center-weighted score (when n > 3 and image dims known), then cap
    if len(candidates) > 3 and img_w > 0 and img_h > 0:
        img_cx = img_w / 2
        img_cy = img_h / 2
        max_dist = math.sqrt((img_w / 2) ** 2 + (img_h / 2) ** 2)

        def _score(c):
            xmin, ymin, xmax, ymax = c["bbox"]
            cx = (xmin + xmax) / 2
            cy = (ymin + ymax) / 2
            dist = math.sqrt((cx - img_cx) ** 2 + (cy - img_cy) ** 2)
            return c["mask_pixels"] * (1 - dist / max_dist)

        candidates.sort(key=_score, reverse=True)
        if verbose:
            print(f"  After center-weight sort:  {len(candidates)}")
    else:
        candidates.sort(key=lambda c: c["mask_pixels"], reverse=True)

    candidates = candidates[:max_crops]

    if verbose:
        print(f"  After {max_crops}-crop cap:        {len(candidates)}  ← final")

    return candidates


def segment_meal(
    image_path: str,
    model_path: str = "FastSAM-s.pt",
    min_area_pct: float = _MIN_AREA_PCT,
    max_aspect_ratio: float = _MAX_ASPECT_RATIO,
    nms_iou_threshold: float = _NMS_IOU_THRESHOLD,
    max_crops: int = _MAX_CROPS,
    max_long_edge: int = _MAX_LONG_EDGE,
    max_det: int = _MAX_DET,
    verbose: bool = False,
) -> list[dict]:
    """
    Segment a meal photo into individual food region crops.

    Args:
        image_path: Path to a JPEG/PNG meal photo.
        model_path: Path to FastSAM weights (default: FastSAM-s.pt in project root).
        min_area_pct: Minimum mask area as fraction of total image pixels (default 1.5%).
        max_aspect_ratio: Discard crops with bw/bh or bh/bw above this (default 4.0).
        nms_iou_threshold: IoU threshold for non-maximum suppression (default 0.4).
        max_crops: Maximum number of crops to return (default 20).
        max_long_edge: Downscale the image so its longer edge is at most this many
            pixels before segmentation (default 1536). Bounds FastSAM's retina-mask
            memory, which scales with input resolution, not the model's imgsz.
        max_det: Maximum number of masks FastSAM returns per image (default 100).
        verbose: Print per-stage crop counts when True.

    Returns:
        List of dicts, one per detected food region that passes all filters:
            {
                "crop":        PIL.Image (RGB),
                "bbox":        (xmin, ymin, xmax, ymax),  # int pixels
                "mask_pixels": int,                        # active pixels in mask
            }
        Returns an empty list if no regions pass the filters.
    """
    model = _get_model(model_path)

    # Decode once here; hand the (possibly downscaled) array to the model
    # instead of the path, so ultralytics doesn't decode the file a second time.
    image_bgr = cv2.imread(image_path)
    h0, w0 = image_bgr.shape[:2]
    if max(h0, w0) > max_long_edge:
        scale = max_long_edge / max(h0, w0)
        image_bgr = cv2.resize(
            image_bgr, (round(w0 * scale), round(h0 * scale)), interpolation=cv2.INTER_AREA
        )

    results = model(
        image_bgr, device=_DEVICE, retina_masks=True, imgsz=1024, conf=0.4, iou=0.9, max_det=max_det
    )

    if results[0].masks is None:
        if verbose:
            print("  FastSAM raw masks:        0")
        return []

    # Bool, not float32 — masks are only ever used via `> 0` comparisons below,
    # and this is 1 byte/px instead of 4 (see API-015).
    masks = (results[0].masks.data > 0).cpu().numpy()  # shape: (N, H, W)

    if verbose:
        print(f"  FastSAM raw masks:        {len(masks)}")

    # BGR → RGB immediately after imread; all downstream work stays in RGB
    image = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

    h, w = image.shape[:2]
    min_pixels = h * w * min_area_pct

    crops = []
    for mask in masks:
        mask_pixels = int(np.sum(mask > 0))
        if mask_pixels < min_pixels:
            continue

        pos = np.where(mask > 0)
        ymin, ymax = int(np.min(pos[0])), int(np.max(pos[0]))
        xmin, xmax = int(np.min(pos[1])), int(np.max(pos[1]))

        crop_array = image[ymin:ymax, xmin:xmax]
        crops.append({
            "crop": Image.fromarray(crop_array),
            "bbox": (xmin, ymin, xmax, ymax),
            "mask_pixels": mask_pixels,
            "image_pixels": h * w,
        })

    # Release the mask tensor and the predictor's retained results before
    # returning — otherwise the last scan's full-res data stays referenced
    # on the module-level singleton indefinitely (API-015 root cause #4).
    del masks
    predictor = getattr(model, "predictor", None)
    if predictor is not None and hasattr(predictor, "results"):
        predictor.results = None
    del results
    gc.collect()

    crops = _apply_filters(
        crops,
        max_aspect_ratio=max_aspect_ratio,
        nms_iou_threshold=nms_iou_threshold,
        max_crops=max_crops,
        img_w=w,
        img_h=h,
        verbose=verbose,
    )

    if not crops:
        # Whole image is the food (uniform texture, soup bowl, etc.)
        if verbose:
            print("  Fallback: returning whole image as single crop")
        crops = [{
            "crop": Image.fromarray(image),
            "bbox": (0, 0, w, h),
            "mask_pixels": h * w,
            "image_pixels": h * w,
        }]

    return crops
