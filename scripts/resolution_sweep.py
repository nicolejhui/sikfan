"""
API-015: measure the accuracy/memory tradeoff of downscaling before FastSAM.

Runs the segmentation → classification sequence (analyze_meal.py:222-255,
stopping before estimate_portion()) at several `max_long_edge` caps and
reports crop count, dish names, confidence scores, and peak RSS per
resolution — no network calls (no USDA lookup, no LLM decompose).

Usage:
    python scripts/resolution_sweep.py
    python scripts/resolution_sweep.py --source-dir path/to/real_phone_photos
    python scripts/resolution_sweep.py --synthetic     # memory only, NOT accuracy
"""

from __future__ import annotations

import argparse
import multiprocessing
import resource
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ru_maxrss is bytes on macOS/BSD but KB on Linux.
_RSS_TO_MB = 1024 * 1024 if sys.platform == "darwin" else 1024

_RESOLUTIONS = [4032, 2048, 1536, 1024]  # 4032 == effectively uncapped

_NAMED_TEST_IMAGES = [
    "data/assorted_breakfast.jpeg",
    "data/hot_pot_christmas.jpeg",
    "data/fungus_dessert_soup.jpeg",
]


# Long edge of a full-resolution iPhone capture (4032x3024). takePictureAsync()
# in mobile/screens/CameraScreen.tsx passes no options, so this is what the
# deployed API actually receives from a real device.
_PHONE_LONG_EDGE = 4032


def _synthesize(images: list[Path], out_dir: Path) -> list[Path]:
    """Upscale each image to a real phone's capture size.

    Memory is a function of pixel *dimensions*, not image content, so an
    upscaled copy reproduces the production allocation exactly. It invents no
    detail, so it says nothing about what downscaling costs in accuracy — use
    --source-dir with real photos for that.
    """
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    synthetic = []
    for p in images:
        with Image.open(p) as im:
            im = im.convert("RGB")
            scale = _PHONE_LONG_EDGE / max(im.size)
            if scale > 1:
                im = im.resize(
                    (round(im.size[0] * scale), round(im.size[1] * scale)), Image.LANCZOS
                )
            dest = out_dir / f"{p.stem}_synthetic.jpg"
            im.save(dest, format="JPEG", quality=95)
        synthetic.append(dest)
    return synthetic


def _collect_images(source_dir: Path | None = None) -> list[Path]:
    if source_dir is not None:
        return sorted(
            p for p in source_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
    images = [Path(p) for p in _NAMED_TEST_IMAGES if Path(p).exists()]
    unlabeled_dir = Path("data/unlabeled")
    if unlabeled_dir.exists():
        images += sorted(
            p for p in unlabeled_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")
        )
    return images


def _run_one(
    image_path: str, max_long_edge: int, result_queue: multiprocessing.Queue,
    device: str = "cpu", max_det: int = 100
) -> None:
    """Runs in its own process — ru_maxrss is a whole-process high-water mark,
    so each resolution needs a fresh process to get an isolated peak reading."""
    from pipeline.classifier import apply_threshold, classify_batch, is_food_crop
    from pipeline.component_classifier import classify_components
    from pipeline.embedding_store import EmbeddingStore
    from pipeline.segmentation import segment_meal

    import pipeline.segmentation as _seg
    from pipeline.segmentation import _get_model

    # Fly runs CPU-only. On an Apple-Silicon dev box _DEVICE auto-detects "mps",
    # which parks the float32 retina-mask tensor in GPU memory where ru_maxrss
    # never sees it — understating production RSS by ~4x. Pin the device so the
    # measurement reflects the deployment target.
    _seg._DEVICE = device

    # Whole-process ru_maxrss is dominated by torch/CLIP/ChromaDB weights (~1.4 GB)
    # and varies run-to-run by more than the mask allocation we are trying to
    # measure. Load everything FIRST, take the high-water mark as a baseline, then
    # attribute the subsequent rise to segmentation. ru_maxrss only ever climbs, so
    # (peak - baseline) is the peak *added* by the scan.
    _get_model()
    store = EmbeddingStore()
    store.__class__  # touch, keep import live
    baseline_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _RSS_TO_MB

    raw_crops = segment_meal(image_path, max_long_edge=max_long_edge, max_det=max_det)
    seg_peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _RSS_TO_MB

    dishes = []
    for crop_dict in raw_crops:
        if not is_food_crop(crop_dict["crop"]):
            continue
        comp_result = classify_components(crop_dict, device=store._device)
        if comp_result["crop_type"] != "single_dish":
            continue
        clf_results = classify_batch([crop_dict["crop"]], store, top_k=3)[0]
        threshold = apply_threshold(clf_results)
        dishes.append((threshold["dish_name"], threshold.get("score")))

    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / _RSS_TO_MB
    result_queue.put({
        "crop_count": len(raw_crops),
        "dishes": dishes,
        "peak_rss_mb": peak_rss_mb,
        "baseline_rss_mb": baseline_rss_mb,
        "seg_added_mb": seg_peak_rss_mb - baseline_rss_mb,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Directory of images to sweep instead of the default data/ corpus. "
             "Use this with real full-resolution phone photos for the accuracy gate.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help=f"Upscale every input to {_PHONE_LONG_EDGE}px long edge first. Valid for "
             "measuring memory, NOT for judging accuracy.",
    )
    parser.add_argument(
        "--resolutions",
        type=lambda v: [int(x) for x in v.split(",")],
        default=_RESOLUTIONS,
        help="Comma-separated max_long_edge caps to sweep (default: "
             f"{','.join(str(r) for r in _RESOLUTIONS)}). Note that an uncapped run on a "
             "4032px input allocates several GB by design — that is the bug being measured.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "mps"],
        help="Torch device for segmentation. Defaults to cpu to mirror Fly; mps on a dev "
             "box hides the float32 mask tensor from ru_maxrss.",
    )
    parser.add_argument(
        "--max-det", type=int, default=100,
        help="Cap on masks FastSAM returns per image. Memory scales linearly with this; "
             "note it truncates by model confidence BEFORE the area filter runs.",
    )
    args = parser.parse_args()
    resolutions = args.resolutions

    images = _collect_images(args.source_dir)
    if not images:
        where = args.source_dir or "data/ or data/unlabeled/"
        print(f"No test images found under {where}.")
        return

    # A resolution sweep only means something if the inputs are larger than the
    # caps being swept; otherwise every run processes identical pixels.
    from PIL import Image

    max_long_edge = 0
    for p in images:
        with Image.open(p) as im:
            max_long_edge = max(max_long_edge, max(im.size))

    if args.synthetic:
        scratch = Path("outputs/synthetic_sweep")
        print(f"Upscaling {len(images)} image(s) to {_PHONE_LONG_EDGE}px long edge -> {scratch}")
        images = _synthesize(images, scratch)
        max_long_edge = _PHONE_LONG_EDGE
        print(
            "\n!! SYNTHETIC MODE: peak_rss numbers below are valid; dish names and\n"
            "!! confidence scores are NOT — upscaling invents no detail. Use\n"
            "!! --source-dir with real phone photos to gate accuracy.\n"
        )
    elif max_long_edge <= min(resolutions):
        print(
            f"\n!! WARNING: the largest input is {max_long_edge}px on its long edge, at or\n"
            f"!! below the smallest cap being swept ({min(resolutions)}px). Every resolution\n"
            f"!! will process identical pixels, so identical results prove nothing.\n"
            f"!! Use --synthetic (memory) or --source-dir with real photos (accuracy).\n"
        )
    else:
        skipped = [r for r in resolutions if r >= max_long_edge]
        if len(skipped) > 1:
            print(
                f"\n!! NOTE: largest input is {max_long_edge}px; caps {skipped} are all no-ops\n"
                f"!! and will produce identical results by construction.\n"
            )

    ctx = multiprocessing.get_context("spawn")

    for image_path in images:
        print(f"\n=== {image_path} ===  (device={args.device}, max_det={args.max_det})")
        for res in resolutions:
            queue: multiprocessing.Queue = ctx.Queue()
            proc = ctx.Process(
                target=_run_one, args=(str(image_path), res, queue, args.device, args.max_det)
            )
            proc.start()
            result = queue.get()
            proc.join()

            dish_str = ", ".join(
                f"{name} ({score:.3f})" if score is not None else name
                for name, score in result["dishes"]
            ) or "(none)"
            print(
                f"  max_long_edge={res:>5}  crops={result['crop_count']:>3}  "
                f"seg_added={result['seg_added_mb']:>7.1f} MB  "
                f"(baseline {result['baseline_rss_mb']:.0f} / peak {result['peak_rss_mb']:.0f} MB)  "
                f"dishes: {dish_str}"
            )


if __name__ == "__main__":
    main()
