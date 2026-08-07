"""
pipeline/glucose_model.py — Personal glucose response model (GLUC-003).

Trains a Gaussian Process Regressor to predict a personalized BG curve
(0–180 min post-meal at 5-min intervals) from meal macros + pre-meal glucose state.

Feature indices (used in both build_training_data and predict_glucose_curve):
  0: total_carbs_g
  1: total_fiber_g
  2: total_fat_g
  3: total_protein_g
  4: pre_meal_glucose    (mg/dL)
  5: pre_meal_trend      (encoded: -2=falling_rapidly, -1=falling, 0=flat, 1=rising, 2=rising_rapidly)
  6: hour_of_day         (0–23)
  7: carb_to_fiber_ratio (total_carbs_g / max(total_fiber_g, 0.1))
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler

from pipeline.glucose_store import get_meal_logs, get_pre_meal_glucose

_MODEL_DIR = Path(__file__).parent.parent / "data" / "models"
_MODEL_PATH = _MODEL_DIR / "glucose_model.joblib"
_META_PATH = _MODEL_DIR / "training_metadata.json"

_TREND_MAP = {
    "falling_rapidly": -2,
    "falling": -1,
    "flat": 0,
    "rising": 1,
    "rising_rapidly": 2,
}
_TIME_GRID = list(range(0, 185, 5))  # [0, 5, 10, ..., 180] — 37 points

# Canonical key aliases — accept both short-form and prefixed keys from mobile app
_KEY_ALIASES = {
    "carbs_g": "total_carbs_g",
    "fiber_g": "total_fiber_g",
    "fat_g": "total_fat_g",
    "protein_g": "total_protein_g",
}


def _normalize_macros(meal_macros: dict) -> dict:
    """Accept both short-form (carbs_g) and canonical (total_carbs_g) keys."""
    return {_KEY_ALIASES.get(k, k): v for k, v in meal_macros.items()}


def _get_pre_meal_state(meal: dict) -> tuple[int, str]:
    """Return (pre_meal_glucose_mgdl, pre_meal_trend) for a meal log.

    Priority:
    1. cgm_readings.json via get_pre_meal_glucose() — has validated trend from GLUC-002
    2. cgm_window.readings[0] as fallback, trend derived from rate of change
    """
    cgm = get_pre_meal_glucose(meal["timestamp"])
    if cgm:
        return cgm["glucose_mgdl"], cgm["trend"]

    readings = meal.get("cgm_window", {}).get("readings", [])
    if not readings:
        raise ValueError(f"No pre-meal glucose available for {meal['meal_id']}")

    glucose = readings[0]["glucose_mgdl"]

    if len(readings) >= 2:
        rate = (readings[1]["glucose_mgdl"] - readings[0]["glucose_mgdl"]) / 5.0
        if rate > 3:
            trend = "rising_rapidly"
        elif rate > 1:
            trend = "rising"
        elif rate < -3:
            trend = "falling_rapidly"
        elif rate < -1:
            trend = "falling"
        else:
            trend = "flat"
    else:
        trend = "flat"

    return glucose, trend


def _interpolate_to_grid(readings: list[dict]) -> np.ndarray:
    """Interpolate cgm_window readings onto the regular 5-min grid (37 points).

    Uses np.interp which handles CGM gaps gracefully via linear interpolation.
    """
    minutes = np.array([r["minutes_post_meal"] for r in readings], dtype=float)
    glucose = np.array([r["glucose_mgdl"] for r in readings], dtype=float)
    return np.interp(_TIME_GRID, minutes, glucose)


def _build_feature_vector(macros: dict, pre_meal_glucose: float, pre_meal_trend: str) -> list[float]:
    """Build the 8-element feature vector from normalized macros + pre-meal state."""
    carbs = float(macros.get("total_carbs_g", 0))
    fiber = float(macros.get("total_fiber_g", 0))
    fat = float(macros.get("total_fat_g", 0))
    protein = float(macros.get("total_protein_g", 0))
    trend_encoded = float(_TREND_MAP.get(pre_meal_trend, 0))
    hour = float(datetime.now().hour)  # used only in predict path; overridden in build_training_data
    carb_fiber_ratio = carbs / max(fiber, 0.1)

    return [carbs, fiber, fat, protein, float(pre_meal_glucose), trend_encoded, hour, carb_fiber_ratio]


def build_training_data() -> tuple[np.ndarray, np.ndarray]:
    """Load meal logs + CGM windows, return (X_features, y_curves).

    Returns:
        X: shape (n_meals, 8)  — feature matrix
        y: shape (n_meals, 37) — BG curve at 5-min intervals for 180 min
    """
    meals = get_meal_logs()
    complete = [m for m in meals if m.get("cgm_window", {}).get("status") == "complete"]

    X_rows = []
    y_rows = []

    for meal in complete:
        try:
            pre_glucose, pre_trend = _get_pre_meal_state(meal)
        except ValueError:
            continue

        carbs = float(meal.get("total_carbs_g", 0))
        fiber = float(meal.get("total_fiber_g", 0))
        fat = float(meal.get("total_fat_g", 0))
        protein = float(meal.get("total_protein_g", 0))
        trend_encoded = float(_TREND_MAP.get(pre_trend, 0))
        hour = float(datetime.fromisoformat(meal["timestamp"]).hour)
        carb_fiber_ratio = carbs / max(fiber, 0.1)

        features = [carbs, fiber, fat, protein, float(pre_glucose), trend_encoded, hour, carb_fiber_ratio]

        readings = meal["cgm_window"]["readings"]
        curve = _interpolate_to_grid(readings)

        X_rows.append(features)
        y_rows.append(curve)

    return np.array(X_rows, dtype=float), np.array(y_rows, dtype=float)


def compute_mard(predicted: list, actual: list) -> float:
    """Compute Mean Absolute Relative Difference between predicted and actual curves.

    Accepts lists of floats or list-of-dicts with 'predicted_bg'/'glucose_mgdl' keys.
    """
    def _to_array(lst):
        if not lst:
            return np.array([])
        if isinstance(lst[0], dict):
            key = "predicted_bg" if "predicted_bg" in lst[0] else "glucose_mgdl"
            return np.array([x[key] for x in lst], dtype=float)
        return np.array(lst, dtype=float)

    pred = _to_array(predicted)
    act = _to_array(actual)
    # Avoid division by zero — skip zero-valued actual readings
    mask = act != 0
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs(pred[mask] - act[mask]) / act[mask]))


def train_model(min_meals: int = 20) -> dict:
    """Train GPR on available meal data, save to data/models/glucose_model.joblib.

    Returns: {'meals_used': int, 'mard': float, 'baseline_mard': float, 'model_path': str}
    """
    X, y = build_training_data()
    n = X.shape[0]

    if n < min_meals:
        print(f"WARNING: Only {n} complete meal-CGM pairs available (minimum {min_meals}). "
              f"Collect more meal data before training.")
        return {"meals_used": n, "mard": None, "baseline_mard": None, "model_path": None}

    kernel = Matern(nu=1.5) + WhiteKernel()
    gpr = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=2)
    base_model = MultiOutputRegressor(gpr)

    # Fit final model on all data
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    base_model.fit(X_scaled, y)

    # Leave-one-out MARD (only when N > min_meals to avoid degenerate folds)
    if n > min_meals:
        loo_preds = []
        loo_actuals = []
        baseline_preds = []
        for i in range(n):
            print(f"LOO fold {i + 1}/{n}...")
            idx_train = [j for j in range(n) if j != i]
            X_tr, y_tr = X[idx_train], y[idx_train]
            X_te, y_te = X[[i]], y[[i]]

            fold_scaler = StandardScaler()
            X_tr_s = fold_scaler.fit_transform(X_tr)
            X_te_s = fold_scaler.transform(X_te)

            fold_model = MultiOutputRegressor(
                GaussianProcessRegressor(kernel=Matern(nu=1.5) + WhiteKernel(),
                                         normalize_y=True, n_restarts_optimizer=2)
            )
            fold_model.fit(X_tr_s, y_tr)
            pred = fold_model.predict(X_te_s)[0]

            loo_preds.append(pred)
            loo_actuals.append(y_te[0])
            # Persistence baseline: flat at pre_meal_glucose (feature index 4)
            baseline_preds.append(np.full(len(_TIME_GRID), X[i, 4]))

        model_mard = float(np.mean([
            compute_mard(p.tolist(), a.tolist())
            for p, a in zip(loo_preds, loo_actuals)
        ]))
        baseline_mard = float(np.mean([
            compute_mard(b.tolist(), a.tolist())
            for b, a in zip(baseline_preds, loo_actuals)
        ]))
    else:
        # In-sample MARD at exactly min_meals
        y_pred = base_model.predict(X_scaled)
        model_mard = float(np.mean([
            compute_mard(y_pred[i].tolist(), y[i].tolist()) for i in range(n)
        ]))
        baseline_preds = [np.full(len(_TIME_GRID), X[i, 4]) for i in range(n)]
        baseline_mard = float(np.mean([
            compute_mard(b.tolist(), y[i].tolist())
            for i, b in enumerate(baseline_preds)
        ]))

    print(f"\nTraining complete — {n} meals")
    print(f"  Model MARD:    {model_mard:.1%}")
    print(f"  Baseline MARD: {baseline_mard:.1%}   ← flat-line persistence baseline")

    # Save model bundle
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"scaler": scaler, "model": base_model}, _MODEL_PATH)

    # Identify last meal by timestamp
    meals = get_meal_logs()
    complete_meals = [m for m in meals if m.get("cgm_window", {}).get("status") == "complete"]
    last_meal = max(complete_meals, key=lambda m: m["timestamp"])

    meta = {
        "trained_at": datetime.now().isoformat(),
        "meals_used": n,
        "last_meal_id": last_meal["meal_id"],
        "last_meal_timestamp": last_meal["timestamp"],
    }
    with open(_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    return {"meals_used": n, "mard": model_mard, "baseline_mard": baseline_mard,
            "model_path": str(_MODEL_PATH)}


def predict_glucose_curve(meal_macros: dict, pre_meal_glucose: int,
                          pre_meal_trend: str) -> list[dict]:
    """Predict BG curve for a meal.

    Args:
        meal_macros: Dict with keys total_carbs_g, total_fiber_g, total_fat_g, total_protein_g
                     (short-form aliases carbs_g, fiber_g, fat_g, protein_g also accepted)
        pre_meal_glucose: Blood glucose in mg/dL at meal time
        pre_meal_trend:   One of flat | rising | rising_rapidly | falling | falling_rapidly

    Returns:
        List of 37 dicts at 5-min intervals:
        {'minutes': int, 'predicted_bg': float, 'confidence_lower': float, 'confidence_upper': float}
    """
    if not _MODEL_PATH.exists():
        raise RuntimeError("Model not yet trained — run train_model() first")

    bundle = joblib.load(_MODEL_PATH)
    scaler = bundle["scaler"]
    model = bundle["model"]

    macros = _normalize_macros(meal_macros)

    carbs = float(macros.get("total_carbs_g", 0))
    fiber = float(macros.get("total_fiber_g", 0))
    fat = float(macros.get("total_fat_g", 0))
    protein = float(macros.get("total_protein_g", 0))
    trend_encoded = float(_TREND_MAP.get(pre_meal_trend, 0))
    hour = float(datetime.now().hour)
    carb_fiber_ratio = carbs / max(fiber, 0.1)

    raw_features = [carbs, fiber, fat, protein, float(pre_meal_glucose),
                    trend_encoded, hour, carb_fiber_ratio]
    feature_names = [
        "total_carbs_g", "total_fiber_g", "total_fat_g", "total_protein_g",
        "pre_meal_glucose", "pre_meal_trend", "hour_of_day", "carb_to_fiber_ratio"
    ]

    for name, val in zip(feature_names, raw_features):
        if not np.isfinite(val):
            raise ValueError(f"Feature '{name}' is NaN or inf — check input: {meal_macros}")

    X = np.array([raw_features], dtype=float)
    X_scaled = scaler.transform(X)

    means = []
    stds = []
    for estimator in model.estimators_:
        mean, std = estimator.predict(X_scaled, return_std=True)
        means.append(float(mean[0]))
        stds.append(float(std[0]))

    return [
        {
            "minutes": t,
            "predicted_bg": means[i],
            "confidence_lower": means[i] - 1.96 * stds[i],
            "confidence_upper": means[i] + 1.96 * stds[i],
        }
        for i, t in enumerate(_TIME_GRID)
    ]


def classify_glucose_outcome(curve: list[dict], pre_meal_glucose: int) -> dict:
    """
    Translate a GPR-predicted BG curve into a UI classification label.

    Args:
        curve: list of {'minutes': int, 'predicted_bg': float, ...} dicts
               (output of predict_glucose_curve())
        pre_meal_glucose: pre-meal BG reading in mg/dL

    Returns:
    {
        "label": "spike" | "steady" | "drop",
        "predicted_peak_bg": float,
        "delta_from_baseline": float    # + for spike, - for drop
    }

    Thresholds (from config.yaml):
        spike: predicted_peak > pre_meal_glucose + spike_threshold_mgdl
        drop:  predicted_peak < pre_meal_glucose - drop_threshold_mgdl
        steady: everything in between
    """
    import yaml
    _config_path = Path(__file__).parent.parent / "config.yaml"
    with open(_config_path) as f:
        cfg = yaml.safe_load(f)
    thresholds = cfg["glucose_classification"]
    spike_threshold = thresholds["spike_threshold_mgdl"]
    drop_threshold = thresholds["drop_threshold_mgdl"]

    peak_bg = max(curve, key=lambda p: abs(p["predicted_bg"] - pre_meal_glucose))["predicted_bg"]
    delta = peak_bg - pre_meal_glucose

    if delta > spike_threshold:
        label = "spike"
    elif delta < -drop_threshold:
        label = "drop"
    else:
        label = "steady"

    return {
        "label": label,
        "predicted_peak_bg": round(peak_bg, 1),
        "delta_from_baseline": round(delta, 1),
    }


def should_retrain() -> bool:
    """Return True if 10+ new confirmed meals have been added since last training."""
    if not _META_PATH.exists():
        return False

    with open(_META_PATH) as f:
        meta = json.load(f)

    last_trained_ts = meta.get("last_meal_timestamp")
    if not last_trained_ts:
        return False

    meals = get_meal_logs()
    new_complete = sum(
        1 for m in meals
        # cgm_window is normally a dict ({"status": ..., "readings": [...]});
        # guard against legacy/malformed entries where it's still a bare []
        # (pre-fix log_meal wrote this) rather than crashing on .get().
        if isinstance(m.get("cgm_window"), dict)
        and m["cgm_window"].get("status") == "complete"
        and m["timestamp"] > last_trained_ts  # compare by timestamp, not insertion order
    )
    return new_complete >= 10
