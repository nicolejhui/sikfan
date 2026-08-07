"""
glucose_analysis.py — End-to-end glucose analysis integration (GLUC-006).

Single entry point: analyze_glucose(meal_id) -> dict
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from pipeline.glucose_model import classify_glucose_outcome, predict_glucose_curve
from pipeline.glucose_model import should_retrain as _model_should_retrain
from pipeline.glucose_overlay import generate_overlay
from pipeline.glucose_store import get_meal_logs, get_pre_meal_glucose
from pipeline.meal_tracker import attach_cgm_window

class MealNotFoundError(Exception):
    """Raised when meal_id is not found in meal_logs.json."""


class MissingCGMDataError(Exception):
    """Raised when no pre-meal glucose source is available for the meal."""


_METADATA_FILE = Path(__file__).parent / "data" / "models" / "training_metadata.json"
_MODEL_PATH = Path(__file__).parent / "data" / "models" / "glucose_model.joblib"
_JOB_STATUS_DIR = Path(__file__).parent / "data" / "job_status"
_STALE_RETRAIN_MINUTES = 30


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def _load_metadata() -> dict:
    if not _METADATA_FILE.exists():
        return {}
    with open(_METADATA_FILE) as f:
        return json.load(f)


def _save_metadata(data: dict) -> None:
    _METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_METADATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Retrain logic
# ---------------------------------------------------------------------------

def _is_retrain_stale(metadata: dict) -> bool:
    started_at = metadata.get("retrain_started_at")
    if not started_at:
        return True  # flag set but no timestamp — treat as crashed
    elapsed = (
        datetime.now(timezone.utc) - datetime.fromisoformat(started_at)
    ).total_seconds()
    return elapsed > _STALE_RETRAIN_MINUTES * 60


def _set_retrain_flag(active: bool) -> None:
    metadata = _load_metadata()
    if active:
        metadata["retrain_in_progress"] = True
        metadata["retrain_started_at"] = datetime.now(timezone.utc).isoformat()
    else:
        metadata.pop("retrain_in_progress", None)
        metadata.pop("retrain_started_at", None)
    _save_metadata(metadata)


def _retrain_worker() -> None:
    """Wraps train_model() so the flag is always cleared, even on crash."""
    try:
        from pipeline.glucose_model import train_model
        train_model()
    finally:
        _set_retrain_flag(active=False)


def _should_retrain() -> bool:
    """Returns True only if a retrain is needed and none is currently running."""
    metadata = _load_metadata()
    if metadata.get("retrain_in_progress"):
        if not _is_retrain_stale(metadata):
            return False  # healthy retrain in progress — skip
        # stale (crashed daemon) — reset flag and fall through
        _set_retrain_flag(active=False)
    return _model_should_retrain()


# ---------------------------------------------------------------------------
# Per-call helpers
# ---------------------------------------------------------------------------

def _get_pre_meal_state(meal: dict) -> tuple[int, str]:
    """Return (pre_meal_glucose_mgdl, pre_meal_trend) for a meal.

    Priority:
    1. CGM store via get_pre_meal_glucose()
    2. Meal-level fields (pre_meal_glucose_mgdl, pre_meal_trend)
    3. First reading in cgm_window.readings
    """
    cgm = get_pre_meal_glucose(meal["timestamp"])
    if cgm:
        return int(cgm["glucose_mgdl"]), cgm["trend"]

    if meal.get("pre_meal_glucose_mgdl"):
        return int(meal["pre_meal_glucose_mgdl"]), meal.get("pre_meal_trend", "flat")

    readings = meal.get("cgm_window", {}).get("readings", [])
    if readings:
        return int(readings[0]["glucose_mgdl"]), meal.get("pre_meal_trend", "flat")

    raise MissingCGMDataError(f"No pre-meal glucose available for {meal['meal_id']}")


def _extract_macros(meal: dict) -> dict:
    """Sum per-dish macros into canonical total_* keys.

    Uses dish-level macros (matching glucose_overlay's approach) to avoid
    the top-level field inconsistency in meal_logs.json.
    """
    dishes = meal.get("dishes", [])
    return {
        "total_carbs_g":   float(meal.get("total_carbs_g", 0)),
        "total_fiber_g":   sum(float(d["macros"].get("fiber_g",   0)) for d in dishes),
        "total_fat_g":     sum(float(d["macros"].get("fat_g",     0)) for d in dishes),
        "total_protein_g": sum(float(d["macros"].get("protein_g", 0)) for d in dishes),
    }


def _model_confidence() -> str:
    """Derive confidence tier from meals_used in training_metadata.json."""
    meals_used = _load_metadata().get("meals_used", 0)
    if meals_used >= 50:
        return "high"
    if meals_used >= 20:
        return "medium"
    return "low"


def _build_prediction(macros: dict, pre_glucose: int, pre_trend: str) -> dict:
    """Shared curve/outcome assembly used by both the logged and preview paths (API-010)."""
    curve = predict_glucose_curve(macros, pre_glucose, pre_trend)
    peak_point = max(curve, key=lambda p: p["predicted_bg"])
    model_confidence = _model_confidence()
    outcome = classify_glucose_outcome(curve, pre_glucose)
    outcome["confidence"] = model_confidence
    return {
        "curve": curve,
        "predicted_peak_bg": float(peak_point["predicted_bg"]),
        "predicted_time_to_peak_minutes": int(peak_point["minutes"]),
        "model_confidence": model_confidence,
        "outcome": outcome,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_glucose(meal_id: str) -> dict:
    """Full glucose analysis for a meal.

    Ties together meal log, CGM window, glucose prediction, and overlay chart
    into a single JSON-serializable dict.

    Args:
        meal_id: ID of the meal to analyze.

    Returns:
        {
            "meal_id": str,
            "meal_timestamp": str,
            "dishes": list,
            "total_carbs_g": float,
            "pre_meal_glucose": int,
            "pre_meal_trend": str,
            "prediction": {
                "curve": [{"minutes", "predicted_bg", "confidence_lower",
                           "confidence_upper"}],
                "predicted_peak_bg": float,
                "predicted_time_to_peak_minutes": int,
                "model_confidence": "high" | "medium" | "low"
            },
            "actuals": None | {
                "curve": [{"minutes", "glucose_mgdl", "timestamp"}],
                "actual_peak_bg": float,
                "time_to_peak_minutes": int,
                "tir_ratio": float,   # 0.0–1.0
                "mard": float,
                "chart_path": str
            },
            "retrain_triggered": bool
        }

    Raises:
        ValueError: meal_id not found, or no pre-meal glucose source available.
        RuntimeError: model has not been trained yet.
    """
    # --- Load meal ---
    meals = get_meal_logs()
    meal = next((m for m in meals if m["meal_id"] == meal_id), None)
    if meal is None:
        raise MealNotFoundError(f"meal_id '{meal_id}' not found in meal_logs.json")

    # --- Cold-start guard ---
    if not _MODEL_PATH.exists():
        raise RuntimeError(
            "Glucose model has not been trained yet. "
            "Collect at least 20 complete meal-CGM pairs and run train_model()."
        )

    # --- Background retrain (non-blocking) ---
    retrain_triggered = _should_retrain()
    if retrain_triggered:
        _set_retrain_flag(active=True)
        threading.Thread(target=_retrain_worker, daemon=True).start()

    # --- Refresh CGM window if not yet complete ---
    if meal.get("cgm_window", {}).get("status") != "complete":
        meal = attach_cgm_window(meal_id)
    window = meal.get("cgm_window", {})

    # --- Pre-meal state ---
    pre_glucose, pre_trend = _get_pre_meal_state(meal)

    # --- Prediction ---
    macros = _extract_macros(meal)
    prediction = _build_prediction(macros, pre_glucose, pre_trend)

    # --- Actuals (only when CGM window is complete) ---
    actuals = None
    if window.get("status") == "complete":
        overlay = generate_overlay(meal_id)
        actuals = {
            "curve": [
                {
                    "minutes": int(r["minutes_post_meal"]),
                    "glucose_mgdl": int(r["glucose_mgdl"]),
                    "timestamp": r["timestamp"],
                }
                for r in window["readings"]
            ],
            "actual_peak_bg": float(window["peak_glucose"]),
            "time_to_peak_minutes": int(window["time_to_peak_minutes"]),
            "tir_ratio": float(overlay["tir_actual"]),
            "mard": float(overlay["mard"]),
            "chart_path": overlay["chart_path"],
        }

    dishes = [
        {"name": d["dish_name"], "carbs_g": float(d["macros"].get("carbs_g", 0.0))}
        for d in meal["dishes"]
    ]

    return {
        "meal_id": meal_id,
        "meal_timestamp": meal["timestamp"],
        "dishes": dishes,
        "total_carbs_g": float(meal["total_carbs_g"]),
        "pre_meal_glucose": pre_glucose,
        "pre_meal_trend": pre_trend,
        "prediction": prediction,
        "actuals": actuals,
        "retrain_triggered": retrain_triggered,
    }


def analyze_glucose_preview(meal_id: str) -> dict:
    """Glucose prediction for a meal that has been analyzed but not yet logged (API-010).

    Reads `data/job_status/{meal_id}.json` directly — never touches
    `meal_logs.json`. Anchored to `job_status.created_at` (scan time), not a
    log timestamp, since the meal isn't logged yet. Always returns
    `actuals: None` (no CGM window is attached or persisted pre-log) and
    `retrain_triggered: False` (retraining only draws from confirmed logged
    data). Same return shape as `analyze_glucose()`.

    Raises:
        MealNotFoundError: no job_status record, or analysis not complete yet.
        MissingCGMDataError: no CGM reading (real or manual, API-011) within
            15 minutes of scan time.
        RuntimeError: glucose model has not been trained yet.
    """
    status_path = _JOB_STATUS_DIR / f"{meal_id}.json"
    if not status_path.exists():
        raise MealNotFoundError(f"meal_id '{meal_id}' not found in job_status")

    job = json.loads(status_path.read_text())
    if job.get("status") != "complete" or not job.get("result"):
        raise MealNotFoundError(f"meal_id '{meal_id}' analysis not complete")

    if not _MODEL_PATH.exists():
        raise RuntimeError(
            "Glucose model has not been trained yet. "
            "Collect at least 20 complete meal-CGM pairs and run train_model()."
        )

    meal_timestamp = job["created_at"]
    result_dishes = job["result"].get("dishes", [])

    # DishResult (job_status shape) carries no fiber_g field — total_fiber_g
    # is 0.0 here same as the logged path effectively always is too, since
    # /log-meal's macros dict has never included fiber_g either. Not a new
    # gap introduced by the preview path; see plans/API-010-plan.md.
    macros = {
        "total_carbs_g":   float(job["result"].get("total_carbs_g", 0.0)),
        "total_fiber_g":   0.0,
        "total_fat_g":     sum(float(d.get("fat_g", 0.0)) for d in result_dishes),
        "total_protein_g": sum(float(d.get("protein_g", 0.0)) for d in result_dishes),
    }

    cgm = get_pre_meal_glucose(meal_timestamp)
    if not cgm:
        raise MissingCGMDataError(f"No pre-meal glucose available for {meal_id}")
    pre_glucose, pre_trend = int(cgm["glucose_mgdl"]), cgm["trend"]

    prediction = _build_prediction(macros, pre_glucose, pre_trend)

    dishes = [
        {"name": d["name"], "carbs_g": float(d.get("carbs_g", 0.0))}
        for d in result_dishes
    ]

    return {
        "meal_id": meal_id,
        "meal_timestamp": meal_timestamp,
        "dishes": dishes,
        "total_carbs_g": macros["total_carbs_g"],
        "pre_meal_glucose": pre_glucose,
        "pre_meal_trend": pre_trend,
        "prediction": prediction,
        "actuals": None,
        "retrain_triggered": False,
    }
