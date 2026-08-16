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
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel

from analyze_meal import analyze_meal as _analyze_meal
from glucose_analysis import MealNotFoundError, MissingCGMDataError
from glucose_analysis import analyze_glucose as _analyze_glucose
from glucose_analysis import analyze_glucose_preview as _analyze_glucose_preview
from pipeline.embedding_store import EmbeddingStore, _get_clip
from pipeline.feedback import FeedbackAction, _append_correction_log, normalize_dish_name, record_feedback
from pipeline.glucose_store import save_cgm_reading
from pipeline.macro_lookup import lookup_macros
from pipeline.portion import _scale_macros  # private but stable; reuse (matches nutrition.py's pattern)
from pipeline.segmentation import _get_model, segment_meal

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

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
    portion_g: float | None = None
    portion_bucket: str | None = None  # "small" | "medium" | "large"; None if not estimated
    needs_macro_entry: bool = False  # True if USDA had no match — carbs/macros above are 0, not verified-zero


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
        meal_result = {
            "meal_id": meal_id,
            "dishes": dish_results,
            "total_carbs_g": result["total_macros"].get("carbs_g", 0.0),
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
    """Async wrapper: offloads the CPU/MPS-heavy _run_analysis to a thread."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_analysis, meal_id, image_path, status_path)


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
    meal_dir = Path("data/meals") / meal_id
    meal_dir.mkdir(parents=True, exist_ok=True)
    (meal_dir / "original.jpg").write_bytes(image_bytes)

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
        }
        dishes.append({"dish_name": name, "portion_size": "medium", "macros": macros})
        total_carbs += macros["carbs_g"]

    log_entry = {
        "meal_id": body.meal_id,
        "timestamp": meal_timestamp,
        "dishes": dishes,
        "total_carbs_g": round(total_carbs, 2),
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

    return LogMealResponse(meal_id=body.meal_id, logged=True, meal_timestamp=meal_timestamp)

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

def _recompute_dish_macros(corrected_label: str, portion_g: float | None) -> dict:
    """Live USDA lookup for a corrected dish label, rescaled to portion_g.

    Returns fields to merge into a DishResult dict: carbs_g, protein_g,
    fat_g, calories, needs_macro_entry. Reuses lookup_macros() (not
    portion.py's cache-only get_macros()) because a just-corrected label has
    by definition never been looked up before.
    """
    macro_result = lookup_macros(corrected_label)
    needs_macro_entry = macro_result.source == "no_results"
    if needs_macro_entry:
        return {
            "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
            "needs_macro_entry": True,
        }

    macros = {
        "calories": macro_result.calories,
        "carbs_g": macro_result.carbs_g,
        "fiber_g": macro_result.fiber_g,
        "protein_g": macro_result.protein_g,
        "fat_g": macro_result.fat_g,
        "reference_weight_g": 100.0,  # USDA values are always per-100g
    }
    scaled = _scale_macros(macros, portion_g or 100.0)
    if scaled is None:
        return {
            "carbs_g": 0.0, "protein_g": 0.0, "fat_g": 0.0, "calories": 0.0,
            "needs_macro_entry": True,
        }
    return {
        "carbs_g": scaled["carbs_g"],
        "protein_g": scaled["protein_g"],
        "fat_g": scaled["fat_g"],
        "calories": scaled["calories"],
        "needs_macro_entry": False,
    }


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

    # 2. Read job_status to resolve crop_id → dish info.
    status_path = Path("data/job_status") / f"{body.meal_id}.json"
    if not status_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No analysis job found for this meal ID."},
        )

    job = json.loads(status_path.read_text())
    dishes = (job.get("result") or {}).get("dishes") or []

    # 3. Find the DishResult entry matching crop_id.
    dish_entry = next((d for d in dishes if d["crop_id"] == body.crop_id), None)
    if dish_entry is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "crop_not_found", "message": "No crop found with this crop ID for the given meal."},
        )

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
