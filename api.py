"""
SikFan FastAPI server — Epic 8 API Layer.

Run:  uvicorn api:app --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import cv2
import numpy as np
import torch
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

# Load .env before any os.getenv/os.environ read below — API_KEY, USDA_API_KEY,
# and ANTHROPIC_API_KEY all live there. load_dotenv() never overrides a variable
# already exported in the shell (e.g. via ~/.zshrc), so existing setups are
# unaffected; it only fills in what the shell doesn't already provide.
load_dotenv()

from analyze_meal import analyze_meal as _analyze_meal
from glucose_analysis import MealNotFoundError, MissingCGMDataError
from glucose_analysis import analyze_glucose as _analyze_glucose
from glucose_analysis import analyze_glucose_preview as _analyze_glucose_preview
from pipeline.dish_decompose import (
    DECOMPOSITION_CACHE_DIR,
    fold_components,
    resolve_composite_macros,
    select_best_usda_candidate,
    suggest_additions,
    suggest_alternatives,
)
from pipeline.embedding_store import EmbeddingStore, _get_clip
from pipeline.feedback import FeedbackAction, _append_correction_log, normalize_dish_name, record_feedback
from pipeline.glucose_store import save_cgm_reading
from pipeline.macro_lookup import lookup_macros, select_candidate
from pipeline.nutrition import CACHE_DIR as _MACRO_CACHE_DIR
from pipeline.nutrition import _atomic_write as _write_macro_cache_entry
from pipeline.portion import (
    _FACTORS,
    PRIOR_CONTRIBUTION_TOLERANCE,
    save_portion_prior,
    scale_macros,
    touch_portion_prior,
)
from pipeline.segmentation import _MAX_LONG_EDGE, _get_model, segment_meal

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def _write_downscaled_original(image_bytes: bytes, dest: Path) -> None:
    """Decode, cap the long edge at `_MAX_LONG_EDGE`, and write as JPEG (API-015 D4)."""
    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    if max(h, w) > _MAX_LONG_EDGE:
        scale = _MAX_LONG_EDGE / max(h, w)
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dest), image)


def _cleanup_old_crops(days: int = 7) -> None:
    """Delete data/crops/{meal_id}/ directories older than TTL days."""
    crops_root = Path("data/crops")
    if not crops_root.exists():
        return
    cutoff = time.time() - days * 86400
    for meal_dir in crops_root.iterdir():
        if meal_dir.is_dir() and meal_dir.stat().st_mtime < cutoff:
            shutil.rmtree(meal_dir, ignore_errors=True)


def _sweep_old_meal_images(days: int = 7) -> None:
    """Delete data/meals/{meal_id}/ for unlogged meals older than TTL days.

    Only logged meals are retained long-term. Unconfirmed uploads from
    analysis sessions that were never logged are cleaned up after 7 days.
    """
    meals_dir = Path("data/meals")
    if not meals_dir.exists():
        return

    # Collect meal_ids that have been logged.
    logged_ids: set[str] = set()
    if _MEAL_LOGS_PATH.exists():
        text = _MEAL_LOGS_PATH.read_text().strip()
        if text:
            try:
                logged_ids = {entry["meal_id"] for entry in json.loads(text)}
            except Exception:
                pass

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for meal_dir in meals_dir.iterdir():
        if not meal_dir.is_dir():
            continue
        if meal_dir.name in logged_ids:
            continue
        mtime = datetime.fromtimestamp(meal_dir.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            shutil.rmtree(meal_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Module-level state (set once at startup, read-only thereafter)
# ---------------------------------------------------------------------------

_store: EmbeddingStore | None = None
_models_loaded: bool = False
_chromadb_ready: bool = False

# API-015: a single-worker pool serializes scans instead of letting concurrent
# requests stack their FastSAM/CLIP memory peaks on top of each other.
_analysis_executor = ThreadPoolExecutor(max_workers=1)

# Kept deliberately separate from _analysis_executor. The stored-original
# downscale is awaited *inside* the /analyze-meal request, while _run_analysis
# occupies its worker for seconds as a background task. Sharing one worker
# between them made scan N+1's request block until scan N's analysis finished,
# so /analyze-meal stopped returning immediately. A decode+resize is ~50 MB and
# short-lived, so it does not need the memory serialization.
_io_executor = ThreadPoolExecutor(max_workers=2)


# ---------------------------------------------------------------------------
# Lifespan — model loading and directory setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store, _models_loaded, _chromadb_ready

    # Step 0: Ensure transient directories exist.
    # The Fly.io persistent volume is empty on first deploy; these dirs must
    # exist before any request arrives.
    Path("data/uploads").mkdir(parents=True, exist_ok=True)
    Path("data/job_status").mkdir(parents=True, exist_ok=True)
    Path("data/crops").mkdir(parents=True, exist_ok=True)
    Path("data/meals").mkdir(parents=True, exist_ok=True)

    # Sweep crop directories older than 7 days (meals never confirmed).
    _cleanup_old_crops(days=7)
    # Sweep meal image directories older than 7 days for unlogged meals.
    _sweep_old_meal_images(days=7)

    # Step 1+2: FastSAM + CLIP.
    # Kept in one block — if either fails the server is degraded and cannot
    # run meal analysis, so models_loaded covers both.
    try:
        _get_model()          # loads FastSAM-s.pt → pipeline.segmentation singleton
        _get_clip()           # loads CLIP ViT-B/32 → pipeline.embedding_store singleton (device auto-detected)
        torch.set_num_threads(2)  # match shared-cpu-2x (API-015)
        _models_loaded = True
    except Exception:
        pass  # health will return 503 with models_loaded: false

    # Step 3: ChromaDB — separate block so a DB failure doesn't mask model state.
    try:
        _store = EmbeddingStore()   # opens data/embeddings/ via chromadb.PersistentClient
        _chromadb_ready = True
    except Exception:
        pass  # health will return 503 with chromadb_ready: false

    yield


app = FastAPI(title="SikFan", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def verify_api_key(x_api_key: str | None = Header(None)) -> None:
    """Reject requests with a missing, empty, or invalid API key with HTTP 401.

    Header(None) — not Header(...) — ensures a missing header returns 401,
    not FastAPI's auto-generated 422, so the iOS global 401 interceptor fires.
    """
    valid_keys = {k for k in [os.getenv("API_KEY"), os.getenv("API_KEY_PREV")] if k}
    if not x_api_key or x_api_key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_api_key", "message": "Invalid or missing API key."},
        )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

# --- GET /health ---

class HealthResponse(BaseModel):
    status: str           # "ok" | "degraded"
    models_loaded: bool
    chromadb_ready: bool


# --- POST /analyze-meal ---

class AnalyzeMealResponse(BaseModel):
    meal_id: str
    status: str           # always "pending" at submission time


# --- GET /meal-status/{meal_id} ---

class DishResult(BaseModel):
    crop_id: str          # synthetic key assigned by API-002: "crop_0", "crop_1", ...
    name: str
    confidence: float
    status: str           # "CONFIDENT" | "UNCERTAIN" | "UNKNOWN"
    carbs_g: float
    protein_g: float
    fat_g: float
    calories: float
    fiber_g: float | None = None  # GLUC-013: additive, 2026-09-06 — see CLAUDE.md Frozen API Schemas
    portion_g: float | None = None
    portion_bucket: str | None = None  # "small" | "medium" | "large"; None if not estimated
    needs_macro_entry: bool = False  # True if USDA had no match — carbs/macros above are 0, not verified-zero
    # FOOD-019: populated only when this dish went through the CORRECT/ADD_NEW
    # correction path and resolved via composite decomposition. A dish whose
    # macros came from the original scan (including one that transparently
    # hit an already-cached composite entry via get_macros()) keeps the
    # defaults below — the coverage/component breakdown is not (yet) plumbed
    # through pipeline.portion's scale_macros(), which strips it to the 5
    # numeric macro fields. See plans/FOOD-019-plan.md "Unchanged on purpose".
    components: list[dict] | None = None
    macro_coverage: float = 1.0  # 1.0 = not a composite, or fully USDA-resolved
    carb_coverage: float = 1.0   # 1.0 = not a composite, or fully USDA-resolved


class MealResult(BaseModel):
    meal_id: str
    dishes: list[DishResult]
    total_carbs_g: float
    image_url: str | None = None  # "/meal-image/{meal_id}"; None for pre-API-009 status files


class JobStatusResponse(BaseModel):
    meal_id: str
    status: str           # "pending" | "processing" | "complete" | "failed"
    result: MealResult | None = None
    error: str | None = None


# --- POST /log-meal ---

class LogMealRequest(BaseModel):
    meal_id: str
    confirmed_dishes: list[str]


class LogMealResponse(BaseModel):
    meal_id: str
    logged: bool
    meal_timestamp: str
    macros_incomplete: bool = False  # True if any confirmed dish had needs_macro_entry set
    unresolved_dishes: list[str] = Field(default_factory=list)
    carb_coverage: float = 1.0  # FOOD-019 D6: carb-weighted share of total_carbs_g backed by USDA


# --- GET /glucose/{meal_id} ---

class CurvePoint(BaseModel):
    minutes: int
    predicted_bg: float
    confidence_lower: float
    confidence_upper: float


class ActualCurvePoint(BaseModel):
    minutes: int
    glucose_mgdl: int
    timestamp: str


class GlucoseOutcome(BaseModel):
    label: str            # "spike" | "steady" | "drop"
    confidence: str       # "high" | "medium" | "low"
    predicted_peak_bg: float
    delta_from_baseline: float


class GlucosePrediction(BaseModel):
    curve: list[CurvePoint]
    predicted_peak_bg: float
    predicted_time_to_peak_minutes: int
    model_confidence: str
    outcome: GlucoseOutcome


class GlucoseActuals(BaseModel):
    curve: list[ActualCurvePoint]
    actual_peak_bg: float
    time_to_peak_minutes: int
    tir_ratio: float
    mard: float
    chart_path: str


# Stub — confirm exact field names from analyze_glucose() output in API-005 before finalising.
# DishResult cannot be reused here: it requires crop_id, which glucose dishes don't have.
class GlucoseDishEntry(BaseModel):
    name: str
    carbs_g: float


class GlucoseResponse(BaseModel):
    meal_id: str
    meal_timestamp: str
    dishes: list[GlucoseDishEntry]
    total_carbs_g: float
    pre_meal_glucose: int
    pre_meal_trend: str
    prediction: GlucosePrediction
    baseline_prediction: GlucosePrediction | None = None
    actuals: GlucoseActuals | None
    retrain_triggered: bool
    image_url: str  # "/meal-image/{meal_id}"


# --- POST /manual-glucose (API-011) ---

class ManualGlucoseRequest(BaseModel):
    meal_id: str
    glucose_mgdl: int


class ManualGlucoseResponse(BaseModel):
    status: str


# --- POST /confirm-dish ---

class ConfirmDishRequest(BaseModel):
    meal_id: str
    crop_id: str
    action: Literal["CONFIRM", "CORRECT", "ADD_NEW"]   # invalid values → 422 at Pydantic layer
    corrected_label: str | None = None


class ConfirmDishResponse(BaseModel):
    crop_id: str
    action: Literal["CONFIRM", "CORRECT", "ADD_NEW"]
    updated_label: str
    chromadb_updated: bool
    macros_changed: bool = False  # True if CORRECT/ADD_NEW's carbs_g differs from the pre-correction value


# --- POST /correct-macros (API-013) ---

class CorrectMacrosRequest(BaseModel):
    meal_id: str
    crop_id: str
    direction: Literal["too_high", "too_low", "looks_right"]
    # FOOD-023: narrowed from {"portion", "broth", "hidden", "leftover"} —
    # "broth"/"hidden" moved to the ingredient-edit flow (which now also
    # teaches the portion prior, via FOOD-022). "leftover" survives as a
    # scope checkbox, not a reason chip: it says "just this meal", not
    # "this dish is systematically mis-estimated".
    reason: Literal["portion", "leftover"] | None = None
    magnitude: Literal["little", "lot"] | None = None


class CorrectMacrosResponse(BaseModel):
    crop_id: str
    direction: Literal["too_high", "too_low", "looks_right"]
    old_portion_g: float | None
    new_portion_g: float | None
    old_carbs_g: float
    new_carbs_g: float
    multiplier_persisted: float | None
    prior_state: str
    dish: DishResult


# --- POST /correct-ingredients (API-013) ---

class IngredientEdit(BaseModel):
    action: Literal["remove", "swap", "add", "include", "set_amount"]
    component_name: str
    replacement_name: str | None = None   # required for "swap"
    grams: float | None = Field(None, gt=0)   # required for "add" and "set_amount" — positive only


class CorrectIngredientsRequest(BaseModel):
    meal_id: str
    crop_id: str
    edits: list[IngredientEdit]


class CorrectIngredientsResponse(BaseModel):
    crop_id: str
    old_portion_g: float
    new_portion_g: float
    old_carbs_g: float
    new_carbs_g: float
    dish: DishResult


# --- GET /ingredient-candidates/{meal_id}/{crop_id} (API-013) ---

class IngredientCandidatesResponse(BaseModel):
    alts: dict[str, list[dict]]
    addable: list[dict]


# --- POST /reset-corrections (API-013) ---

class ResetCorrectionsRequest(BaseModel):
    meal_id: str
    crop_id: str


class ResetCorrectionsResponse(BaseModel):
    crop_id: str
    decomposition_reverted: bool
    prior_retained: bool
    dish: DishResult


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    """Health check — unauthenticated so Fly.io's built-in check can reach it."""
    ok = _models_loaded and _chromadb_ready
    resp = HealthResponse(
        status="ok" if ok else "degraded",
        models_loaded=_models_loaded,
        chromadb_ready=_chromadb_ready,
    )
    if not ok:
        raise HTTPException(status_code=503, detail=resp.model_dump())
    return resp


# ---------------------------------------------------------------------------
# API-002 helpers
# ---------------------------------------------------------------------------

def _build_dish_results(detected_items: list[dict]) -> list[dict]:
    """Convert analyze_meal() detected_items to a flat list of DishResult dicts.

    single_dish items map 1-to-1. mixed_bowl items are flattened — one entry
    per component — with crop IDs of the form "crop_{i}_{j}".
    """
    dishes: list[dict] = []
    for i, item in enumerate(detected_items):
        if item["crop_type"] == "single_dish":
            macros = item.get("macros") or {}
            dishes.append({
                "crop_id": f"crop_{i}",
                "name": item["dish_name"],
                "confidence": item["confidence"],
                "status": item["status"],
                "carbs_g": macros.get("carbs_g", 0.0),
                "protein_g": macros.get("protein_g", 0.0),
                "fat_g": macros.get("fat_g", 0.0),
                "calories": macros.get("calories", 0.0),
                "fiber_g": macros.get("fiber_g"),
                "portion_g": item.get("portion_g") or None,
                "portion_bucket": item.get("portion") or None,  # pipeline stores bucket under "portion"
                "needs_macro_entry": item.get("needs_macro_entry", False),
            })
        elif item["crop_type"] == "mixed_bowl":
            for j, comp in enumerate(item.get("components", [])):
                macros = comp.get("macros") or {}
                dishes.append({
                    "crop_id": f"crop_{i}_{j}",
                    "name": comp["dish_name"],
                    "confidence": comp["confidence"],
                    "status": comp["status"],
                    "carbs_g": macros.get("carbs_g", 0.0),
                    "protein_g": macros.get("protein_g", 0.0),
                    "fat_g": macros.get("fat_g", 0.0),
                    "calories": macros.get("calories", 0.0),
                    "fiber_g": macros.get("fiber_g"),
                    "portion_g": comp.get("portion_g") or None,
                    "portion_bucket": comp.get("portion_bucket") or None,
                    "needs_macro_entry": comp.get("needs_macro_entry", False),
                })
    return dishes


def _write_job_status(path: Path, data: dict) -> None:
    """Atomically write job status JSON to disk via os.replace (POSIX-atomic)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _load_macro_cache_entry(dish_name: str) -> dict | None:
    """Read the macro_cache entry lookup_macros() just wrote, to inspect the
    matched usda_name. Returns None if absent or unreadable."""
    path = _MACRO_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _validate_usda_match(dish_name: str, portion_g: float | None) -> dict | None:
    """
    Confirm the USDA entry lookup_macros() picked is actually the same food,
    re-selecting a better candidate when it isn't.

    lookup_macros() takes candidates[0] unconditionally. Observed 2026-08-29:
    USDA's top hit for "boiled_chicken" was "Peanuts, boiled" (21g carbs/100g
    against chicken's ~0). Harmless when lookups only ran on a user-visible
    correction; _fill_missing_macros() resolves silently, so a bad match would
    land phantom carbs in a diabetic carb count with nothing on screen.

    Judgment is delegated to the LLM rather than a hardcoded cooking-verb list
    (which would be English-only and miss poached/blanched/stir-fried — the
    same brittleness _PINYIN_FALLBACK is criticized for). It inspects the top-3
    candidates lookup_macros() already stores, so it can also pick candidate 1
    or 2 when 0 is wrong, via the pre-existing select_candidate().

    Returns the (possibly re-scaled) macro dict, or None if no candidate matches.
    """
    cached = _load_macro_cache_entry(dish_name)
    if cached is None:
        return None
    # A user override or a composite entry was not chosen by USDA's ranking,
    # so there is nothing to second-guess.
    if cached.get("source") != "usda_api":
        return _recompute_dish_macros(dish_name, portion_g)

    candidates = cached.get("candidates") or []
    candidate_names = [c.get("usda_name", "") for c in candidates]
    if not candidate_names:
        return None

    best = select_best_usda_candidate(dish_name, candidate_names)
    if best is None:
        _log.warning(
            "no USDA candidate matches %r (offered %s) — leaving needs_macro_entry "
            "set rather than recording an unrelated food's macros",
            dish_name, candidate_names,
        )
        return None

    if candidate_names[best] != cached.get("usda_name"):
        _log.info(
            "re-selected USDA match for %r: %r -> %r",
            dish_name, cached.get("usda_name"), candidate_names[best],
        )
        try:
            select_candidate(dish_name, best)
        except (ValueError, IndexError) as exc:
            _log.warning("select_candidate failed for %r: %s", dish_name, exc)
            return None

    return _recompute_dish_macros(dish_name, portion_g)


def _fill_missing_macros(dish_results: list[dict], total_carbs_g: float) -> float:
    """Resolve macros for scanned dishes that had no cached nutrition data.

    pipeline.portion's get_macros() is deliberately cache-only — it reads
    data/overrides/ then data/macro_cache/ and returns None on a miss, so no
    network call ever enters the pixel pipeline (plans/FOOD-019-plan.md D3).
    The consequence, hit in real use 2026-08-29: a freshly-scanned dish with
    no cache entry reports needs_macro_entry with 0.0 macros — even for
    something as ordinary as "rice", which USDA obviously has. Before this,
    the ONLY path that actually queried USDA was POST /confirm-dish, so a
    dish only ever got macros if the user had previously corrected it.

    This runs in the async analysis job (already network-bound and polled by
    AnalyzingScreen), which is where D3 always intended resolution to happen —
    keeping analyze_meal.py and pipeline/portion.py themselves untouched.

    Mutates dish_results in place and returns the recomputed total carbs.
    Never raises: a lookup failure leaves that dish exactly as it was
    (needs_macro_entry, zeroed macros), i.e. the pre-fix behavior.
    """
    changed = False
    for dish in dish_results:
        if not dish.get("needs_macro_entry"):
            continue
        try:
            # Same resolution the correction path uses, so a dish resolved at
            # scan time and one resolved via a correction can never disagree.
            resolved = _recompute_dish_macros(dish["name"], dish.get("portion_g"))
        except Exception as exc:
            _log.warning("macro fill failed for %r: %s", dish.get("name"), exc)
            continue
        if resolved.get("needs_macro_entry"):
            continue  # genuinely no USDA match — leave the flag set

        # Guard the direct-USDA case against an unrelated top hit (see
        # _match_is_plausible). A composite result is exempt: its components
        # were resolved individually against LLM-supplied USDA-style names,
        # and its own dish_name intentionally doesn't appear in any single
        # component's usda_name.
        if not resolved.get("components"):
            validated = _validate_usda_match(dish["name"], dish.get("portion_g"))
            if validated is None:
                continue  # no candidate is the same food — leave the flag set
            resolved = validated

        dish.update(resolved)
        changed = True

    if not changed:
        return total_carbs_g
    return round(sum(d.get("carbs_g", 0.0) for d in dish_results), 2)


def _run_analysis(meal_id: str, image_path: Path, status_path: Path) -> None:
    """Sync worker: runs analyze_meal(), updates job status, deletes upload."""
    # Save per-crop images for POST /confirm-dish before analyze_meal() runs.
    # segment_meal() is deterministic — same image → same crops in same order.
    # Non-fatal: confirm-dish falls back to metadata-only if crops are missing.
    crop_dir = Path("data/crops") / meal_id
    try:
        crop_dir.mkdir(parents=True, exist_ok=True)
        segments = segment_meal(str(image_path))
        for k, seg in enumerate(segments):
            seg["crop"].convert("RGB").save(crop_dir / f"crop_{k}.jpg", format="JPEG")
    except Exception as exc:
        _log.warning("crop save failed for %s: %s", meal_id, exc)

    # pending → processing
    status_data = json.loads(status_path.read_text())
    status_data["status"] = "processing"
    _write_job_status(status_path, status_data)

    try:
        result = _analyze_meal(str(image_path), store=_store)
        dish_results = _build_dish_results(result["detected_items"])
        total_carbs = _fill_missing_macros(dish_results, result["total_macros"].get("carbs_g", 0.0))
        # FOOD-020 D7: a portion prior goes stale after 180 days without a
        # scan of its dish — touch it on every completed scan so a dish
        # that's still being eaten never falls out of an active prior.
        for dish in dish_results:
            touch_portion_prior(dish["name"])
        meal_result = {
            "meal_id": meal_id,
            "dishes": dish_results,
            "total_carbs_g": total_carbs,
            "image_url": f"/meal-image/{meal_id}",
        }
        status_data["status"] = "complete"
        status_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        status_data["result"] = meal_result
        status_data["error"] = None
        _write_job_status(status_path, status_data)
    except Exception as exc:
        status_data["status"] = "failed"
        status_data["completed_at"] = datetime.now(timezone.utc).isoformat()
        status_data["error"] = str(exc)
        _write_job_status(status_path, status_data)
    finally:
        image_path.unlink(missing_ok=True)


async def _run_analysis_bg(meal_id: str, image_path: Path, status_path: Path) -> None:
    """Async wrapper: offloads the CPU/MPS-heavy _run_analysis to a thread.

    Uses the single-worker `_analysis_executor` (API-015) so concurrent scans
    serialize instead of stacking their FastSAM/CLIP memory peaks.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_analysis_executor, _run_analysis, meal_id, image_path, status_path)


# ---------------------------------------------------------------------------
# API-002 route
# ---------------------------------------------------------------------------

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


@app.post("/analyze-meal", response_model=AnalyzeMealResponse, dependencies=[Depends(verify_api_key)])
async def analyze_meal_route(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Accept a meal image, return a meal_id immediately, run analysis in background."""
    # Normalize: strip any parameters (e.g. "image/jpeg; charset=...")
    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_file_type", "message": "Only JPEG and PNG images are accepted."},
        )

    meal_id = str(uuid.uuid4())
    image_path = Path("data/uploads") / f"{meal_id}.jpg"
    status_path = Path("data/job_status") / f"{meal_id}.json"

    # Read once, write to both locations before returning.
    image_bytes = await file.read()
    image_path.write_bytes(image_bytes)

    # Persist original for GET /meal-image/{meal_id} (survives on Fly.io volume).
    # Downscaled to the same long-edge cap as segmentation (API-015 D4) — the
    # feedback loop saves the in-memory *crop*, not this file, so there is no
    # accuracy/training-data impact. Decode/resize is pushed to the shared
    # single-worker executor so a 12 MP JPEG doesn't block the event loop.
    meal_dir = Path("data/meals") / meal_id
    meal_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        _io_executor, _write_downscaled_original, image_bytes, meal_dir / "original.jpg"
    )

    # Write initial job status (pending, logged: false)
    _write_job_status(status_path, {
        "meal_id": meal_id,
        "status": "pending",
        "logged": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    })

    background_tasks.add_task(_run_analysis_bg, meal_id, image_path, status_path)

    return AnalyzeMealResponse(meal_id=meal_id, status="pending")


# ---------------------------------------------------------------------------
# API-003 route
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API-004 helpers
# ---------------------------------------------------------------------------

_MEAL_LOGS_PATH = Path("data/glucose/meal_logs.json")


def _append_meal_log(entry: dict) -> None:
    """Append a meal log entry to meal_logs.json (JSON array, compatible with glucose_store.py)."""
    _MEAL_LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list = []
    if _MEAL_LOGS_PATH.exists():
        text = _MEAL_LOGS_PATH.read_text().strip()
        if text:
            existing = json.loads(text)
    existing.append(entry)
    tmp = _MEAL_LOGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing))
    os.replace(tmp, _MEAL_LOGS_PATH)


# ---------------------------------------------------------------------------
# API-004 route
# ---------------------------------------------------------------------------


@app.post("/log-meal", response_model=LogMealResponse, dependencies=[Depends(verify_api_key)])
def log_meal(body: LogMealRequest):
    """Record a confirmed meal to meal_logs.json for the glucose pipeline."""
    if not body.confirmed_dishes:
        raise HTTPException(
            status_code=400,
            detail={"code": "no_dishes", "message": "confirmed_dishes cannot be empty."},
        )

    status_path = Path("data/job_status") / f"{body.meal_id}.json"
    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No analysis job found for this meal ID. Run /analyze-meal first."},
        )

    job = json.loads(status_path.read_text())

    if job["status"] in ("pending", "processing"):
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_not_complete", "message": "Meal analysis is still in progress. Wait for status: complete before logging."},
        )

    if job.get("logged"):
        raise HTTPException(
            status_code=409,
            detail={"code": "already_logged", "message": "This meal has already been logged."},
        )

    # Reuse scan-time timestamp (API-010) so a pre-log preview and the
    # post-log tracked prediction share the same CGM anchor — using
    # datetime.now() here instead would let the two disagree on "when this
    # meal happened," visibly shifting the curve the instant it's logged.
    meal_timestamp = job.get("created_at") or datetime.now(timezone.utc).isoformat()

    # Build GLUC-001-compatible dish entries from job_status result, matched by name.
    result_dishes = {d["name"]: d for d in (job.get("result") or {}).get("dishes", [])}
    dishes = []
    total_carbs = 0.0
    for name in body.confirmed_dishes:
        d = result_dishes.get(name, {})
        macros = {
            "calories": d.get("calories", 0.0),
            "carbs_g": d.get("carbs_g", 0.0),
            "protein_g": d.get("protein_g", 0.0),
            "fat_g": d.get("fat_g", 0.0),
            "fiber_g": d.get("fiber_g") or 0.0,
        }
        dishes.append({"dish_name": name, "portion_size": "medium", "macros": macros})
        total_carbs += macros["carbs_g"]

    # GLUC-012: a dish with needs_macro_entry set contributed 0.0 to total_carbs
    # above without USDA ever actually confirming zero carbs. Flag the row so
    # train_model() can exclude it instead of silently learning from it.
    # Iterate the crop-keyed dish list (not the name-collapsed result_dishes
    # dict above) — mixed_bowl components can share a dish name across crops,
    # and collapsing by name first would let one crop's flag hide another's.
    result_dish_list = (job.get("result") or {}).get("dishes", [])
    confirmed_set = set(body.confirmed_dishes)
    unresolved_dishes = sorted({
        d["name"] for d in result_dish_list
        if d["name"] in confirmed_set and d.get("needs_macro_entry", False)
    })
    macros_incomplete = bool(unresolved_dishes)

    # FOOD-019 D6: meal-level carb_coverage, carb-weighted across confirmed
    # dishes (a dish that never went through composite decomposition
    # defaults to 1.0 — see DishResult.carb_coverage). Persisted even though
    # the glucose_training.min_carb_coverage gate may exclude nothing today —
    # coverage cannot be reconstructed after the fact, and recording it now
    # is what makes the threshold re-tunable later with no backfill.
    confirmed_dish_list = [d for d in result_dish_list if d["name"] in confirmed_set]
    carb_coverage = _aggregate_carb_coverage(confirmed_dish_list)

    log_entry = {
        "meal_id": body.meal_id,
        "timestamp": meal_timestamp,
        "dishes": dishes,
        "total_carbs_g": round(total_carbs, 2),
        "macros_incomplete": macros_incomplete,
        "unresolved_dishes": unresolved_dishes,
        "carb_coverage": carb_coverage,
        "pre_meal_glucose_mgdl": None,
        "pre_meal_trend": None,
        # Dict, not []: pipeline/meal_tracker.py's attach_cgm_window() and
        # pipeline/glucose_model.py's should_retrain() both call
        # .get("status") on this — matches the exact "pending" shape
        # attach_cgm_window() itself would set before any real CGM data
        # exists (meal_tracker.py:138-146). A bare [] crashed should_retrain()
        # on the first analyze_glucose() call for any freshly-logged meal.
        "cgm_window": {
            "status": "pending",
            "readings": [],
            "peak_glucose": None,
            "time_to_peak_minutes": None,
            "return_to_baseline_minutes": None,
            "area_under_curve": None,
        },
    }

    # Step 3: append → step 4: flip logged: true (atomic).
    # If step 4 fails after step 3, meal is in meal_logs.json but logged stays false.
    # A retry will double-append; this is logged as a warning and 200 is still returned.
    _append_meal_log(log_entry)

    job["logged"] = True
    _write_job_status(status_path, job)

    return LogMealResponse(
        meal_id=body.meal_id,
        logged=True,
        meal_timestamp=meal_timestamp,
        macros_incomplete=macros_incomplete,
        unresolved_dishes=unresolved_dishes,
        carb_coverage=carb_coverage,
    )

# ---------------------------------------------------------------------------
# API-011 route
# ---------------------------------------------------------------------------

@app.post("/manual-glucose", response_model=ManualGlucoseResponse, dependencies=[Depends(verify_api_key)])
def manual_glucose(body: ManualGlucoseRequest):
    """No-CGM fallback: record a manually-entered pre-meal glucose reading.

    Stored as a regular CGM reading (source: "manual") in the same store
    real CGM data lives in — GET /glucose/{meal_id} needs no changes to pick
    it up; get_pre_meal_glucose() already does a source-agnostic nearest-
    reading lookup. Forward-compatible with a future real CGM integration
    by construction, not by a special case that'll need removing later.
    """
    status_path = Path("data/job_status") / f"{body.meal_id}.json"
    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No analysis job found for this meal ID. Run /analyze-meal first."},
        )

    job = json.loads(status_path.read_text())

    try:
        save_cgm_reading({
            "timestamp": job["created_at"],
            "glucose_mgdl": body.glucose_mgdl,
            "trend": "flat",  # no trend arrow inferable from a single manual point
            "source": "manual",
        })
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_glucose_value", "message": str(exc)},
        )

    return ManualGlucoseResponse(status="ok")

# ---------------------------------------------------------------------------
# API-005 route
# ---------------------------------------------------------------------------

def _resolve_glucose_result(meal_id: str) -> dict:
    """Logged path first; falls back to the preview path (API-010) if the
    meal hasn't been logged yet. Exceptions from whichever path actually
    runs propagate to the caller for the route's single exception→HTTP map."""
    try:
        return _analyze_glucose(meal_id)
    except MealNotFoundError:
        return _analyze_glucose_preview(meal_id)


@app.get("/glucose/{meal_id}", response_model=GlucoseResponse, dependencies=[Depends(verify_api_key)])
def get_glucose(meal_id: str):
    """Return the glucose prediction for a meal.

    Tries the logged path first (full CGM-window tracking, actuals, retrain
    trigger). If the meal hasn't been logged yet, falls back to the preview
    path (API-010) — a prediction anchored to scan time, available as soon
    as analysis completes, with actuals always null. Same URL, same
    response schema either way; only accuracy/completeness improves once
    the meal is actually logged.
    """
    try:
        result = _resolve_glucose_result(meal_id)
    except MealNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No logged meal found with this ID."},
        )
    except MissingCGMDataError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_pre_meal_glucose",
                "message": "No CGM reading found near this meal time. Enter a manual pre-meal BG to enable glucose prediction.",
            },
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "glucose_model_not_ready", "message": str(exc)},
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"code": "glucose_model_error", "message": "Glucose prediction failed due to a server error. Try again shortly."},
        )

    dishes = [GlucoseDishEntry(**d) for d in result["dishes"]]

    actuals = GlucoseActuals(**result["actuals"]) if result["actuals"] else None

    return GlucoseResponse(
        meal_id=result["meal_id"],
        meal_timestamp=result["meal_timestamp"],
        dishes=dishes,
        total_carbs_g=result["total_carbs_g"],
        pre_meal_glucose=result["pre_meal_glucose"],
        pre_meal_trend=result["pre_meal_trend"],
        prediction=GlucosePrediction(**result["prediction"]),
        baseline_prediction=(
            GlucosePrediction(**result["baseline_prediction"])
            if result.get("baseline_prediction") else None
        ),
        actuals=actuals,
        retrain_triggered=result["retrain_triggered"],
        image_url=f"/meal-image/{result['meal_id']}",
    )


@app.get("/meal-status/{meal_id}", response_model=JobStatusResponse, dependencies=[Depends(verify_api_key)])
def get_meal_status(meal_id: str):
    """Poll for the result of an async analyze-meal job. Pure disk read."""
    status_path = Path("data/job_status") / f"{meal_id}.json"
    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No analysis job found for this meal ID."},
        )
    data = json.loads(status_path.read_text())
    result = MealResult(**data["result"]) if data.get("result") else None
    return JobStatusResponse(
        meal_id=data["meal_id"],
        status=data["status"],
        result=result,
        error=data.get("error"),
    )


# ---------------------------------------------------------------------------
# API-006 helpers
# ---------------------------------------------------------------------------

def _confirm_metadata_only(dish_name: str) -> bool:
    """Increment confirmed_count in ChromaDB without a crop image.

    Used as a fallback when the crop file is absent (mixed_bowl component or
    server restart before confirmation). Only increments the count — does not
    update the centroid embedding. Returns True if the update succeeded.
    """
    try:
        normalized = normalize_dish_name(dish_name)
        existing = _store._col.get(ids=[normalized], include=["metadatas"])
        if not existing["metadatas"]:
            return False
        meta = existing["metadatas"][0]
        meta["confirmed_count"] = int(meta.get("confirmed_count", 0)) + 1
        _store._col.update(ids=[normalized], metadatas=[meta])
        return True
    except Exception as exc:
        _log.warning("metadata-only confirm failed for %s: %s", dish_name, exc)
        return False


# ---------------------------------------------------------------------------
# API-006 route
# ---------------------------------------------------------------------------

def _aggregate_carb_coverage(dishes: list[dict]) -> float:
    """
    Carb-weighted carb_coverage across a meal's confirmed dishes (FOOD-019
    D6). A dish that never went through composite decomposition contributes
    its default 1.0 (DishResult.carb_coverage), so a meal with no composite
    dishes at all reports 1.0 here, matching pre-FOOD-019 behavior exactly.

    Zero total carbs -> 1.0 (no carb uncertainty to speak of), mirroring
    resolve_composite_macros()'s own convention for the same edge case.
    """
    total_carbs = sum(d.get("carbs_g", 0.0) for d in dishes)
    if total_carbs <= 0:
        return 1.0
    weighted = sum(d.get("carbs_g", 0.0) * d.get("carb_coverage", 1.0) for d in dishes)
    return round(weighted / total_carbs, 4)


def _recompute_dish_macros(corrected_label: str, portion_g: float | None) -> dict:
    """Live macro resolution for a corrected dish label, rescaled to portion_g.

    Returns fields to merge into a DishResult dict: carbs_g, protein_g,
    fat_g, calories, needs_macro_entry, components, macro_coverage,
    carb_coverage. Every return path sets all seven keys explicitly — a
    second correction on the same crop can move a dish from composite back
    to simple (or vice versa), and dish_entry.update() only overwrites keys
    present in the new dict, so a missing key here would leave stale
    composite metadata from a *previous* correction lingering on the entry.

    FOOD-019: tries composite decomposition first (for names like "japanese
    curry chicken katsu with white rice" that have no single USDA match).
    resolve_composite_macros() returns None for a dish that doesn't actually
    decompose (a single component matching the input) — the signal to fall
    through to the pre-FOOD-019 lookup_macros() path, unchanged below.
    """
    composite = resolve_composite_macros(corrected_label)
    if composite is not None:
        # Persist so a future lookup of this exact label — a fresh scan via
        # pipeline.portion's get_macros(), or another correction — hits the
        # cache with no further API call. Same file pipeline.nutrition and
        # pipeline.portion already read; no new read path needed there.
        cache_path = _MACRO_CACHE_DIR / f"{normalize_dish_name(corrected_label)}.json"
        _write_macro_cache_entry(cache_path, composite)

        scaled = scale_macros(composite, portion_g or 100.0)
        if scaled is None:
            return {
                "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
                "fiber_g": 0.0,
                "needs_macro_entry": True,
                "components": composite["components"],
                "macro_coverage": composite["macro_coverage"],
                "carb_coverage": composite["carb_coverage"],
            }
        return {
            "carbs_g": scaled["carbs_g"],
            "protein_g": scaled["protein_g"],
            "fat_g": scaled["fat_g"],
            "calories": scaled["calories"],
            "fiber_g": scaled["fiber_g"],
            # Only a dish where NO component resolved via USDA is treated as
            # unresolved — a partially-estimated composite still has a real
            # (if partly estimated) carb total, unlike a genuine no_results.
            "needs_macro_entry": composite["macro_coverage"] == 0.0,
            "components": composite["components"],
            "macro_coverage": composite["macro_coverage"],
            "carb_coverage": composite["carb_coverage"],
        }

    macro_result = lookup_macros(corrected_label)
    needs_macro_entry = macro_result.source == "no_results"
    if needs_macro_entry:
        return {
            "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
            "fiber_g": 0.0,
            "needs_macro_entry": True,
            "components": None, "macro_coverage": 1.0, "carb_coverage": 1.0,
        }

    macros = {
        "calories": macro_result.calories,
        "carbs_g": macro_result.carbs_g,
        "fiber_g": macro_result.fiber_g,
        "protein_g": macro_result.protein_g,
        "fat_g": macro_result.fat_g,
        "reference_weight_g": 100.0,  # USDA values are always per-100g
    }
    scaled = scale_macros(macros, portion_g or 100.0)
    if scaled is None:
        return {
            "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
            "fiber_g": 0.0,
            "needs_macro_entry": True,
            "components": None, "macro_coverage": 1.0, "carb_coverage": 1.0,
        }
    return {
        "carbs_g": scaled["carbs_g"],
        "protein_g": scaled["protein_g"],
        "fat_g": scaled["fat_g"],
        "calories": scaled["calories"],
        "fiber_g": scaled["fiber_g"],
        "needs_macro_entry": False,
        "components": None, "macro_coverage": 1.0, "carb_coverage": 1.0,
    }


def _resolve_dish_entry(meal_id: str, crop_id: str) -> tuple[Path, dict, dict]:
    """Resolve a (meal_id, crop_id) pair to its job_status file, the parsed
    job dict, and the mutable dish dict within it — the same 404s
    confirm_dish() has always raised, lifted out (API-013) so every
    correction route resolves a crop identically."""
    status_path = Path("data/job_status") / f"{meal_id}.json"
    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No analysis job found for this meal ID."},
        )

    job = json.loads(status_path.read_text())
    dishes = (job.get("result") or {}).get("dishes") or []

    dish_entry = next((d for d in dishes if d["crop_id"] == crop_id), None)
    if dish_entry is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "crop_not_found", "message": "No crop found with this crop ID for the given meal."},
        )
    return status_path, job, dish_entry


# API-013: fields a correction can mutate on a dish entry, and so the exact
# set _snapshot_baseline() must preserve for reset-corrections to restore.
# Defaults mirror DishResult's own field defaults — a plainly-scanned
# (non-composite) dish entry never carries macro_coverage/carb_coverage/
# needs_macro_entry keys at all (_build_dish_results() doesn't set the first
# two), so a bare .get() would snapshot None and reset() would then write an
# explicit None back, which DishResult rejects (a missing key defaults to
# 1.0/False; an explicit None does not).
_BASELINE_DEFAULTS = {
    "portion_g": None, "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
    "fiber_g": None,
    "components": None, "macro_coverage": 1.0, "carb_coverage": 1.0, "needs_macro_entry": False,
}


def _snapshot_baseline(dish_entry: dict) -> None:
    """Copy the pristine (pre-correction) macro fields into dish_entry['_baseline']
    on the FIRST correction to this crop only — a no-op if already present, so a
    second correction can never overwrite the true original (plans/API-013-plan.md).

    Also snapshots the raw pre-edit decomposition-cache file verbatim, under
    '_decomposition'. This is NOT the same shape as dish_entry['components']:
    the latter is fold_components()'s OUTPUT shape (name/role/proportion/
    per_100g/macro_source, used for display), while the decomposition cache
    holds decompose_dish()'s INPUT shape (name/role/proportion/usda_likely/
    fallback_macros). Restoring the decomposition cache FROM the display
    shape would silently drop fallback_macros for any component that only
    resolves via the LLM's estimate rather than a USDA match — reset must
    write back the real original file, not reconstruct a lossy approximation
    of it.
    """
    if "_baseline" in dish_entry:
        return
    baseline = {k: dish_entry.get(k, default) for k, default in _BASELINE_DEFAULTS.items()}
    decomp_path = DECOMPOSITION_CACHE_DIR / f"{normalize_dish_name(dish_entry['name'])}.json"
    try:
        baseline["_decomposition"] = json.loads(decomp_path.read_text())
    except (OSError, ValueError):
        baseline["_decomposition"] = None
    dish_entry["_baseline"] = baseline


def _component_fallback_lookup(dish_name: str, baseline: dict | None) -> dict[str, dict | None]:
    """Map normalized component name -> fallback_macros, sourced from the
    decomposition cache's INPUT shape (that's where fallback_macros lives;
    dish_entry['components'] is fold_components()'s lossy display OUTPUT —
    see _snapshot_baseline()). The baseline snapshot is consulted too (and
    never overrides a current-cache hit) so a component restored via
    'include' finds its fallback data even after later edits moved on.

    API-013a fix (MOB-016 rev-2): Step 5 previously hardcoded
    fallback_macros=None for every surviving component on every edit, which
    silently zeroed a component that only ever resolved through the LLM's
    estimate the first time the user touched any *other* ingredient in the
    dish. This lookup carries that data forward instead."""
    lookup: dict[str, dict | None] = {}
    decomp_path = DECOMPOSITION_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    try:
        current = json.loads(decomp_path.read_text())
        for c in current.get("components", []):
            lookup[normalize_dish_name(c["name"])] = c.get("fallback_macros")
    except (OSError, ValueError):
        pass
    if baseline is not None:
        original_decomp = baseline.get("_decomposition")
        if original_decomp:
            for c in original_decomp.get("components", []):
                lookup.setdefault(normalize_dish_name(c["name"]), c.get("fallback_macros"))
    return lookup


def _write_corrected(status_path: Path, job: dict, dishes: list[dict]) -> None:
    """Recompute total_carbs_g and write job_status — mirrors confirm_dish()'s
    own L1070-1072 write exactly, reused by every API-013 correction route."""
    result = job.setdefault("result", {})
    result["total_carbs_g"] = round(sum(d.get("carbs_g", 0.0) for d in dishes), 2)
    _write_job_status(status_path, job)


def _contribute_prior(dish_entry: dict, dish_name: str) -> Optional[dict]:
    """FOOD-022: derive this crop's ONE portion-prior contribution from the
    ratio of its CURRENT portion_g to the pristine baseline scan value, and
    write it through save_portion_prior()'s supersedes path so N edits to one
    crop register as one restated claim, not N pieces of evidence (D1/D2).

    Called from both correct_macros (after it applies its _FACTORS-derived
    portion_g) and correct_ingredients (after it applies its edits) — one
    write path into one slot per crop, so a session touching both flows
    can't double-count (D3). Reason is always "portion" (D5); direction is
    the sign of the ratio.

    Must run AFTER dish_entry['portion_g'] reflects the correction just
    applied, and AFTER _snapshot_baseline() has run at least once this crop.

    Returns the written prior record, or None when nothing was written:
    no baseline yet, or a below-tolerance edit with no prior contribution to
    revise (D6).
    """
    baseline = dish_entry.get("_baseline")
    if baseline is None:
        return None
    baseline_portion_g = baseline.get("portion_g")
    new_portion_g = dish_entry.get("portion_g")
    if not baseline_portion_g or new_portion_g is None:
        return None

    prev_contribution = dish_entry.get("_prior_contribution")

    if dish_entry.get("_prior_suppressed"):
        # A leftover correction on this crop suppresses derived contributions
        # (FOOD-022 D4) — retract whatever this crop already contributed,
        # since "I'm not finishing this" and a durable size claim can't both
        # stand for the same crop. Failing toward not learning is the safe
        # direction here.
        if prev_contribution is None:
            return None
        record = save_portion_prior(dish_name, 1.0, "portion", "too_high", supersedes=prev_contribution)
        dish_entry.pop("_prior_contribution", None)
        return record

    factor = new_portion_g / baseline_portion_g

    if prev_contribution is None and abs(factor - 1.0) < PRIOR_CONTRIBUTION_TOLERANCE:
        return None

    direction = "too_low" if factor > 1.0 else "too_high"
    record = save_portion_prior(dish_name, factor, "portion", direction, supersedes=prev_contribution)

    if abs(factor - 1.0) < PRIOR_CONTRIBUTION_TOLERANCE:
        dish_entry.pop("_prior_contribution", None)  # stepped back to baseline -> retracted
    else:
        dish_entry["_prior_contribution"] = factor
    return record


_MACRO_CORRECTION_LOG_PATH = "data/macro_correction_log.jsonl"


def _log_macro_correction(entry: dict) -> None:
    """Deliberately a separate file from data/correction_log.jsonl, whose
    schema offline scripts consume as *name* corrections (API-006), not
    quantity corrections."""
    _append_correction_log(entry, _MACRO_CORRECTION_LOG_PATH)


def _validate_ingredient_name(name: str) -> bool:
    """
    Confirm `name` resolves to a real USDA match before it enters a dish's
    component list via an ingredient edit — same judgment-call pattern as
    _validate_usda_match() above, reused here rather than trusting
    lookup_macros()'s unconditional candidates[0].

    Re-selects the correct candidate in the cache (via select_candidate())
    when the best match isn't index 0, so fold_components()'s own
    lookup_macros() call (which always reads whatever the cache currently
    points at) picks up the validated match rather than silently re-reading
    the wrong one.

    Fails closed: any error or "no match" returns False rather than letting
    the edit through with the wrong (or invented) macros.
    """
    try:
        macro_result = lookup_macros(name)
    except Exception:
        return False
    if macro_result.source == "no_results" or macro_result.carbs_g is None:
        return False
    if not macro_result.candidates:
        return True

    candidate_names = [c.get("usda_name", "") for c in macro_result.candidates]
    best = select_best_usda_candidate(name, candidate_names)
    if best is None:
        return False
    if candidate_names[best] != macro_result.usda_name:
        try:
            select_candidate(name, best)
        except (ValueError, IndexError) as exc:
            _log.warning("select_candidate failed for %r: %s", name, exc)
            return False
    return True


@app.post("/confirm-dish", response_model=ConfirmDishResponse, dependencies=[Depends(verify_api_key)])
def confirm_dish(body: ConfirmDishRequest):
    """Send CONFIRM / CORRECT / ADD_NEW feedback for a detected crop.

    Updates ChromaDB centroid (full record_feedback() when crop is available)
    and appends to correction_log.jsonl.
    """
    # 1. CORRECT/ADD_NEW require corrected_label.
    if body.action in ("CORRECT", "ADD_NEW") and not body.corrected_label:
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_corrected_label", "message": "corrected_label is required for CORRECT and ADD_NEW actions."},
        )

    # 2-3. Resolve job_status + the dish entry for this crop_id.
    status_path, job, dish_entry = _resolve_dish_entry(body.meal_id, body.crop_id)
    dishes = (job.get("result") or {}).get("dishes") or []

    original_name = dish_entry["name"]
    confidence = float(dish_entry["confidence"])
    corrected_label = original_name if body.action == "CONFIRM" else body.corrected_label
    updated_label = normalize_dish_name(corrected_label)
    action = FeedbackAction(body.action)

    # 3b. CORRECT/ADD_NEW: the dish identity actually changed, so its macros
    # (looked up for the *old*, wrong label) are stale — recompute for the
    # new label and persist back into job_status. CONFIRM leaves macros as
    # they were computed during the scan (the name was already right).
    macros_changed = False
    if body.action in ("CORRECT", "ADD_NEW"):
        old_carbs_g = dish_entry.get("carbs_g", 0.0)
        dish_entry.update(_recompute_dish_macros(corrected_label, dish_entry.get("portion_g")))
        dish_entry["name"] = updated_label
        macros_changed = dish_entry["carbs_g"] != old_carbs_g

        result = job.setdefault("result", {})
        result["total_carbs_g"] = round(sum(d.get("carbs_g", 0.0) for d in dishes), 2)
        _write_job_status(status_path, job)

    # 4. Resolve crop file.
    # "crop_1"   → parent_idx=1 → data/crops/{meal_id}/crop_1.jpg  (single_dish)
    # "crop_1_2" → parent_idx=1 → data/crops/{meal_id}/crop_1.jpg  (mixed_bowl parent)
    parent_idx = int(body.crop_id.split("_")[1])
    crop_path = Path("data/crops") / body.meal_id / f"crop_{parent_idx}.jpg"

    chromadb_updated: bool

    # UNKNOWN dishes have no ChromaDB entry — record_feedback/_apply_gate would crash.
    # For CONFIRM on UNKNOWN, skip the centroid update and fall through to the
    # metadata-only path below. CORRECT/ADD_NEW are unaffected (they create new entries).
    dish_is_unknown = dish_entry.get("status") == "UNKNOWN" and action == FeedbackAction.CONFIRM

    if crop_path.exists() and not dish_is_unknown:
        # Full feedback loop: centroid update + correction log via record_feedback().
        crop_img = Image.open(crop_path).convert("RGB")
        record_feedback(
            crop=crop_img,
            original_prediction=original_name,
            corrected_label=corrected_label,
            confidence=confidence,
            action=action,
            store=_store,
        )
        # Delete single_dish crops immediately (crop_id has exactly one "_").
        # Mixed_bowl parent crops (crop_i used by multiple crop_i_j entries) are
        # left for the TTL sweep — we can't know here when all components are done.
        if body.crop_id.count("_") == 1:
            crop_path.unlink(missing_ok=True)
        chromadb_updated = True
    else:
        # Fallback: crop file absent (mixed_bowl component or post-restart).
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": body.action,
            "original_prediction": normalize_dish_name(original_name),
            "corrected_label": updated_label,
            "confidence": round(confidence, 4),
            "crop_saved_path": None,
            "centroid_updated": False,
        }
        _append_correction_log(log_entry, "data/correction_log.jsonl")

        if action == FeedbackAction.CONFIRM:
            chromadb_updated = _confirm_metadata_only(original_name)
        else:
            # CORRECT / ADD_NEW: cannot update centroid without crop.
            _log.warning(
                "crop missing for %s/%s — %s centroid update skipped",
                body.meal_id, body.crop_id, body.action,
            )
            chromadb_updated = False

    return ConfirmDishResponse(
        crop_id=body.crop_id,
        action=body.action,
        updated_label=updated_label,
        chromadb_updated=chromadb_updated,
        macros_changed=macros_changed,
    )


# ---------------------------------------------------------------------------
# API-013 routes
# ---------------------------------------------------------------------------

@app.post("/correct-macros", response_model=CorrectMacrosResponse, dependencies=[Depends(verify_api_key)])
def correct_macros(body: CorrectMacrosRequest):
    """Scalar "does this look right?" correction — scales portion_g by a
    factor from pipeline.portion._FACTORS and recomputes macros wholesale.
    FOOD-023: direction + magnitude only, no reason chips — `reason` is now
    just the "leftover" scope checkbox (too_high only) and is otherwise
    omitted. `looks_right` and every directional correction still route
    through _contribute_prior()/save_portion_prior(); the store — not this
    route — decides whether it persists (FOOD-020 D4)."""
    if body.direction != "looks_right" and body.magnitude is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "missing_correction_detail",
                "message": "magnitude is required unless direction is looks_right.",
            },
        )

    status_path, job, dish_entry = _resolve_dish_entry(body.meal_id, body.crop_id)
    dishes = (job.get("result") or {}).get("dishes") or []
    dish_name = dish_entry["name"]
    old_portion_g = dish_entry.get("portion_g")
    old_carbs_g = dish_entry.get("carbs_g", 0.0)
    now = datetime.now(timezone.utc).isoformat()

    if body.direction == "looks_right":
        # Must run before any early return — this is what lets a
        # confirmation clear PENDING evidence (FOOD-020 D8).
        record = save_portion_prior(dish_name, 1.0, None, "looks_right")
        _log_macro_correction({
            "timestamp": now, "meal_id": body.meal_id, "crop_id": body.crop_id,
            "dish_name": dish_name, "flow": "scalar", "direction": body.direction,
            "reason": None, "magnitude": None, "factor": 1.0, "edits": None,
            "old_portion_g": old_portion_g, "new_portion_g": old_portion_g,
            "old_carbs_g": old_carbs_g, "new_carbs_g": old_carbs_g,
            "multiplier_persisted": None, "prior_state": record.get("prior_state"),
        })
        return CorrectMacrosResponse(
            crop_id=body.crop_id, direction=body.direction,
            old_portion_g=old_portion_g, new_portion_g=old_portion_g,
            old_carbs_g=old_carbs_g, new_carbs_g=old_carbs_g,
            multiplier_persisted=None, prior_state=record.get("prior_state"),
            dish=DishResult(**dish_entry),
        )

    if old_portion_g is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_portion_estimate", "message": "This dish has no portion estimate to correct."},
        )

    _snapshot_baseline(dish_entry)

    factor = _FACTORS[(body.direction, body.magnitude)]
    new_portion_g = round(old_portion_g * factor, 1)

    dish_entry["portion_g"] = new_portion_g
    dish_entry.update(_recompute_dish_macros(dish_name, new_portion_g))
    new_carbs_g = dish_entry["carbs_g"]

    # FOOD-022: the prior CONTRIBUTION is always derived from the resulting
    # grams vs. the pristine baseline (not from _FACTORS' discrete multiplier
    # applied to old_portion_g) — this is the same slot correct_ingredients
    # writes through, so a session touching both flows nets to one claim
    # (D3). "leftover" describes the meal, not the dish (D4): it suppresses
    # this crop's contribution rather than teaching a permanent under-count.
    if body.reason == "leftover":
        dish_entry["_prior_suppressed"] = True
    record = _contribute_prior(dish_entry, dish_name)
    prior_state = record.get("prior_state") if record else "unchanged"
    multiplier_persisted = record.get("portion_multiplier") if record else None

    _write_corrected(status_path, job, dishes)

    _log_macro_correction({
        "timestamp": now, "meal_id": body.meal_id, "crop_id": body.crop_id,
        "dish_name": dish_name, "flow": "scalar", "direction": body.direction,
        "reason": body.reason, "magnitude": body.magnitude, "factor": factor, "edits": None,
        "old_portion_g": old_portion_g, "new_portion_g": new_portion_g,
        "old_carbs_g": old_carbs_g, "new_carbs_g": new_carbs_g,
        "multiplier_persisted": multiplier_persisted, "prior_state": prior_state,
    })

    return CorrectMacrosResponse(
        crop_id=body.crop_id, direction=body.direction,
        old_portion_g=old_portion_g, new_portion_g=new_portion_g,
        old_carbs_g=old_carbs_g, new_carbs_g=new_carbs_g,
        multiplier_persisted=multiplier_persisted, prior_state=prior_state,
        dish=DishResult(**dish_entry),
    )


def _write_edited_decomposition(dish_name: str, components: list[dict]) -> None:
    """Persist a user-edited component list to the decomposition cache with
    user_edited: True — decompose_dish()'s _load_decomposition() short-circuits
    on that flag ahead of its schema_version/model staleness check, so this
    survives a later prompt or model bump untouched (FOOD-021)."""
    path = DECOMPOSITION_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    entry = {
        "dish_name": normalize_dish_name(dish_name),
        "components": components,
        "user_edited": True,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_macro_cache_entry(path, entry)


@app.post("/correct-ingredients", response_model=CorrectIngredientsResponse, dependencies=[Depends(verify_api_key)])
def correct_ingredients(body: CorrectIngredientsRequest):
    """Ingredient-level correction — remove / swap / add components, computed
    in absolute grams (proportion x portion_g) so removing rice shrinks the
    dish rather than inflating everything else to refill the bowl."""
    status_path, job, dish_entry = _resolve_dish_entry(body.meal_id, body.crop_id)
    dishes = (job.get("result") or {}).get("dishes") or []
    dish_name = dish_entry["name"]

    components = dish_entry.get("components")
    if not components:
        raise HTTPException(
            status_code=422,
            detail={"code": "no_components", "message": "This dish has no ingredient breakdown to edit."},
        )

    portion_g = dish_entry.get("portion_g")
    if portion_g is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_portion_estimate",
                "message": "This dish has no portion estimate to convert ingredient edits against.",
            },
        )

    _snapshot_baseline(dish_entry)

    old_portion_g = portion_g
    old_carbs_g = dish_entry.get("carbs_g", 0.0)

    # Step 1: components (proportions) -> absolute grams.
    grams_list = [
        {"name": c["name"], "role": c.get("role"), "grams": float(c["proportion"]) * portion_g}
        for c in components
    ]

    # Step 2: apply edits.
    for edit in body.edits:
        target_slug = normalize_dish_name(edit.component_name)
        if edit.action == "remove":
            grams_list = [g for g in grams_list if normalize_dish_name(g["name"]) != target_slug]
        elif edit.action == "swap":
            idx = next((i for i, g in enumerate(grams_list) if normalize_dish_name(g["name"]) == target_slug), None)
            if idx is None:
                continue  # nothing to swap — component already absent
            if not edit.replacement_name:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "missing_replacement_name", "message": "replacement_name is required for a swap edit."},
                )
            if not _validate_ingredient_name(edit.replacement_name):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unresolved_ingredient",
                        "message": f"Could not find a USDA match for {edit.replacement_name!r}.",
                    },
                )
            grams_list[idx] = {
                "name": edit.replacement_name,
                "role": grams_list[idx]["role"],
                "grams": grams_list[idx]["grams"],
            }
        elif edit.action == "add":
            if not edit.grams:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "missing_grams", "message": "grams is required for an add edit."},
                )
            if not _validate_ingredient_name(edit.component_name):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unresolved_ingredient",
                        "message": f"Could not find a USDA match for {edit.component_name!r}.",
                    },
                )
            grams_list.append({"name": edit.component_name, "role": None, "grams": edit.grams})
        elif edit.action == "set_amount":
            if not edit.grams:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "missing_grams", "message": "grams is required for a set_amount edit."},
                )
            idx = next((i for i, g in enumerate(grams_list) if normalize_dish_name(g["name"]) == target_slug), None)
            if idx is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unknown_component",
                        "message": f"{edit.component_name!r} is not part of this dish.",
                    },
                )
            grams_list[idx] = {
                "name": grams_list[idx]["name"],
                "role": grams_list[idx]["role"],
                "grams": edit.grams,
            }
        elif edit.action == "include":
            # API-013a: put a removed component back at the size it was
            # scanned at — restored from the pre-correction snapshot, not
            # re-added via _validate_ingredient_name()'s LLM/USDA round trip
            # (which would refuse re-entry to a component whose macros only
            # ever came from fallback_macros). See plans/MOB_016b-plan.md.
            already_present = any(normalize_dish_name(g["name"]) == target_slug for g in grams_list)
            if already_present:
                continue  # idempotent — mirrors swap's skip-if-absent above
            baseline = dish_entry.get("_baseline")
            if baseline is None:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "nothing_to_restore", "message": "No pre-correction snapshot for this dish."},
                )
            baseline_portion_g = baseline.get("portion_g")
            baseline_component = None
            original_decomp = baseline.get("_decomposition")
            if original_decomp is not None:
                baseline_component = next(
                    (c for c in original_decomp.get("components", []) if normalize_dish_name(c["name"]) == target_slug),
                    None,
                )
            if baseline_component is None:
                baseline_component = next(
                    (c for c in (baseline.get("components") or []) if normalize_dish_name(c["name"]) == target_slug),
                    None,
                )
            if baseline_component is None or baseline_portion_g is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unknown_component",
                        "message": f"{edit.component_name!r} was not part of the original dish.",
                    },
                )
            grams_list.append({
                "name": baseline_component["name"],
                "role": baseline_component.get("role"),
                "grams": float(baseline_component["proportion"]) * baseline_portion_g,
            })

    # Step 3-4: guard on the NET result, after every edit is applied.
    total_grams = sum(g["grams"] for g in grams_list)
    if not grams_list or total_grams <= 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_dish", "message": "This edit would remove every ingredient from the dish."},
        )

    # Step 5: new portion_g = surviving grams; renormalize proportions.
    # API-013a: carry fallback_macros forward per component (keyed by
    # normalized name) instead of hardcoding None for every survivor — see
    # _component_fallback_lookup(). A genuinely new component (from "add",
    # or "include" of one that never had fallback data) isn't in the lookup
    # and falls through to the same usda_likely=True/fallback_macros=None
    # pair as before.
    fallback_lookup = _component_fallback_lookup(dish_name, dish_entry.get("_baseline"))
    new_components = [
        {
            "name": g["name"], "role": g.get("role"), "proportion": g["grams"] / total_grams,
            "usda_likely": fallback_lookup.get(normalize_dish_name(g["name"])) is None,
            "fallback_macros": fallback_lookup.get(normalize_dish_name(g["name"])),
        }
        for g in grams_list
    ]

    # Step 6: fold -> per-100g + coverages; scale to the new portion_g.
    folded = fold_components(new_components)
    new_portion_g = round(total_grams, 1)
    scaled = scale_macros(folded, new_portion_g)

    dish_entry["portion_g"] = new_portion_g
    if scaled is None:
        dish_entry["carbs_g"] = 0.0
        dish_entry["protein_g"] = 0.0
        dish_entry["fat_g"] = 0.0
        dish_entry["calories"] = 0.0
        dish_entry["needs_macro_entry"] = True
    else:
        dish_entry["carbs_g"] = scaled["carbs_g"]
        dish_entry["protein_g"] = scaled["protein_g"]
        dish_entry["fat_g"] = scaled["fat_g"]
        dish_entry["calories"] = scaled["calories"]
        dish_entry["needs_macro_entry"] = folded["macro_coverage"] == 0.0
    dish_entry["components"] = folded["components"]
    dish_entry["macro_coverage"] = folded["macro_coverage"]
    dish_entry["carb_coverage"] = folded["carb_coverage"]
    new_carbs_g = dish_entry["carbs_g"]

    # FOOD-022: teach the dish's SIZE, not just its shape — an ingredient
    # edit changes total grams but taught nothing durable before this. Same
    # slot correct_macros writes through (D3), so a session touching both
    # flows nets to one claim rather than double-counting.
    prior_record = _contribute_prior(dish_entry, dish_name)

    _write_corrected(status_path, job, dishes)

    # Step 7: persist the edited breakdown so the next scan starts from it —
    # both the decomposition cache (read by a future correction) and the
    # plain per-100g macro cache (read by pipeline.portion's get_macros() at
    # scan time, which never consults the decomposition cache directly).
    _write_edited_decomposition(dish_name, new_components)
    composite_cache_entry = dict(folded)
    composite_cache_entry["dish_name"] = normalize_dish_name(dish_name)
    composite_cache_entry["source"] = "composite"
    _write_macro_cache_entry(_MACRO_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json", composite_cache_entry)

    baseline_portion_g = dish_entry.get("_baseline", {}).get("portion_g")
    contributed_factor = new_portion_g / baseline_portion_g if baseline_portion_g else None

    _log_macro_correction({
        "timestamp": datetime.now(timezone.utc).isoformat(), "meal_id": body.meal_id,
        "crop_id": body.crop_id, "dish_name": dish_name, "flow": "ingredient",
        "direction": None, "reason": None, "magnitude": None, "factor": contributed_factor,
        "edits": [e.model_dump() for e in body.edits],
        "old_portion_g": old_portion_g, "new_portion_g": new_portion_g,
        "old_carbs_g": old_carbs_g, "new_carbs_g": new_carbs_g,
        "multiplier_persisted": prior_record.get("portion_multiplier") if prior_record else None,
        "prior_state": prior_record.get("prior_state") if prior_record else None,
    })

    return CorrectIngredientsResponse(
        crop_id=body.crop_id,
        old_portion_g=old_portion_g, new_portion_g=new_portion_g,
        old_carbs_g=old_carbs_g, new_carbs_g=new_carbs_g,
        dish=DishResult(**dish_entry),
    )


@app.get(
    "/ingredient-candidates/{meal_id}/{crop_id}",
    response_model=IngredientCandidatesResponse,
    dependencies=[Depends(verify_api_key)],
)
def get_ingredient_candidates(meal_id: str, crop_id: str):
    """Alternatives per detected component + additions the photo might have
    missed. Always 200 — suggest_alternatives()/suggest_additions() already
    fail closed to [] on any LLM error, and this route never turns that into
    a 5xx: the UI degrades to hiding the affordance, never an error state."""
    _, _, dish_entry = _resolve_dish_entry(meal_id, crop_id)
    dish_name = dish_entry["name"]
    components = dish_entry.get("components") or []
    component_names = [c["name"] for c in components]

    alts: dict[str, list[dict]] = {}
    for name in component_names:
        try:
            alts[name] = suggest_alternatives(dish_name, name)
        except Exception as exc:
            _log.warning("suggest_alternatives failed for %r/%r: %s", dish_name, name, exc)
            alts[name] = []

    try:
        addable = suggest_additions(dish_name, component_names)
    except Exception as exc:
        _log.warning("suggest_additions failed for %r: %s", dish_name, exc)
        addable = []

    return IngredientCandidatesResponse(alts=alts, addable=addable)


@app.post("/reset-corrections", response_model=ResetCorrectionsResponse, dependencies=[Depends(verify_api_key)])
def reset_corrections(body: ResetCorrectionsRequest):
    """Undo all — restores a dish's macros/portion/components from the
    snapshot taken on its first correction. Reverts an edited decomposition
    (if any). Also retracts this crop's derived portion-prior contribution
    (FOOD-022): the prior is computed FROM the numbers this route just threw
    away, so keeping it would preserve a conclusion whose entire evidence was
    deleted — see plans/FOOD-022-plan.md, "Open decision — what 'Undo all'
    does to a derived prior" (superseding API-013-plan.md's original
    always-retain decision, made before a prior could be derived)."""
    status_path, job, dish_entry = _resolve_dish_entry(body.meal_id, body.crop_id)
    dishes = (job.get("result") or {}).get("dishes") or []
    dish_name = dish_entry["name"]

    baseline = dish_entry.get("_baseline")
    if baseline is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "no_corrections", "message": "No corrections have been made to this dish."},
        )

    decomposition_reverted = False
    decomp_path = DECOMPOSITION_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
    try:
        current_decomp = json.loads(decomp_path.read_text())
    except (OSError, ValueError):
        current_decomp = None

    if current_decomp is not None and current_decomp.get("user_edited"):
        original_decomp = baseline.get("_decomposition")
        composite_cache_path = _MACRO_CACHE_DIR / f"{normalize_dish_name(dish_name)}.json"
        if original_decomp is not None:
            # Write the real pre-edit file back verbatim — not a
            # reconstruction from dish_entry['components'], which is a
            # different (lossy, for fallback_macros) shape. See
            # _snapshot_baseline().
            _write_macro_cache_entry(decomp_path, original_decomp)
            try:
                restored_composite = fold_components(original_decomp["components"])
                restored_composite["dish_name"] = normalize_dish_name(dish_name)
                restored_composite["source"] = "composite"
                _write_macro_cache_entry(composite_cache_path, restored_composite)
            except (ValueError, KeyError):
                pass  # original components can't fold (shouldn't happen — they were valid once)
        else:
            # No decomposition existed before the edit — both cache entries
            # were created fresh by it. Remove them so a future scan
            # re-derives from scratch rather than keeping stale records with
            # nothing genuine to fall back to.
            decomp_path.unlink(missing_ok=True)
            composite_cache_path.unlink(missing_ok=True)
        decomposition_reverted = True

    # FOOD-022: retract this crop's derived contribution BEFORE the baseline
    # restore loop below clears _prior_contribution — its evidence (the
    # corrected numbers) is about to be deleted.
    prior_contribution = dish_entry.get("_prior_contribution")
    prior_retracted_record = None
    if prior_contribution is not None:
        prior_retracted_record = save_portion_prior(
            dish_name, 1.0, "portion", "too_high", supersedes=prior_contribution
        )
    prior_retained = prior_contribution is None

    for key in _BASELINE_DEFAULTS:
        dish_entry[key] = baseline[key]
    dish_entry.pop("_baseline", None)
    dish_entry.pop("_prior_contribution", None)
    dish_entry.pop("_prior_suppressed", None)

    _write_corrected(status_path, job, dishes)

    _log_macro_correction({
        "timestamp": datetime.now(timezone.utc).isoformat(), "meal_id": body.meal_id,
        "crop_id": body.crop_id, "dish_name": dish_name, "flow": "reset",
        "direction": None, "reason": None, "magnitude": None, "factor": None, "edits": None,
        "old_portion_g": None, "new_portion_g": dish_entry.get("portion_g"),
        "old_carbs_g": None, "new_carbs_g": dish_entry.get("carbs_g"),
        "multiplier_persisted": prior_retracted_record.get("portion_multiplier") if prior_retracted_record else None,
        "prior_state": prior_retracted_record.get("prior_state") if prior_retracted_record else "reset",
    })

    return ResetCorrectionsResponse(
        crop_id=body.crop_id,
        decomposition_reverted=decomposition_reverted,
        prior_retained=prior_retained,
        dish=DishResult(**dish_entry),
    )


# ---------------------------------------------------------------------------
# API-009 route
# ---------------------------------------------------------------------------

@app.get("/meal-image/{meal_id}", dependencies=[Depends(verify_api_key)])
async def get_meal_image(meal_id: str):
    """Return the original meal photo for a given meal_id."""
    image_path = Path("data/meals") / meal_id / "original.jpg"
    if not image_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No image found for this meal ID."},
        )
    return FileResponse(image_path, media_type="image/jpeg")
