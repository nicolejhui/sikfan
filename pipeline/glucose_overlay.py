"""pipeline/glucose_overlay.py — Predicted vs Actual BG overlay charts (GLUC-005).

Generates matplotlib charts comparing the model's predicted glucose curve against
the actual CGM trace for completed meal windows.
"""

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be set before pyplot import
import matplotlib.pyplot as plt
import numpy as np

from pipeline.glucose_model import predict_glucose_curve, compute_mard
from pipeline.glucose_store import _load, _MEAL_FILE
from pipeline.meal_tracker import get_time_in_range

_OVERLAYS_DIR = Path(__file__).parent.parent / "data" / "glucose" / "overlays"
_REPORT_PATH = Path(__file__).parent.parent / "data" / "glucose" / "overlay_report.csv"


def _get_meal(meal_id: str) -> dict:
    meals = _load(_MEAL_FILE)
    meal = next((m for m in meals if m["meal_id"] == meal_id), None)
    if meal is None:
        raise ValueError(f"meal_id '{meal_id}' not found in meal_logs.json")
    return meal


def _extract_macros(meal: dict) -> dict:
    """Sum per-dish macros into the canonical total_* keys expected by predict_glucose_curve."""
    dishes = meal.get("dishes", [])
    return {
        "total_carbs_g": float(meal.get("total_carbs_g", 0)),
        "total_fiber_g": sum(float(d["macros"].get("fiber_g", 0)) for d in dishes),
        "total_fat_g":   sum(float(d["macros"].get("fat_g",   0)) for d in dishes),
        "total_protein_g": sum(float(d["macros"].get("protein_g", 0)) for d in dishes),
    }


def generate_overlay(meal_id: str) -> dict:
    """Generate predicted vs actual BG overlay chart for a meal.

    Saves chart to data/glucose/overlays/{meal_id}.png.

    Returns:
        {
            'meal_id': str,
            'predicted_peak': float,
            'actual_peak': float,
            'mard': float,
            'tir_actual': float,
            'chart_path': str
        }
    """
    meal = _get_meal(meal_id)
    window = meal.get("cgm_window", {})
    actual_readings = window.get("readings", [])

    if not actual_readings:
        raise ValueError(
            f"No CGM readings for meal '{meal_id}' — run attach_cgm_window() first"
        )

    # Pre-meal state: prefer stored value, fall back to first reading
    pre_glucose = (
        meal.get("pre_meal_glucose_mgdl")
        or actual_readings[0]["glucose_mgdl"]
    )
    pre_trend = meal.get("pre_meal_trend", "flat")

    # --- Prediction ---
    macros = _extract_macros(meal)
    predicted_curve = predict_glucose_curve(macros, pre_glucose, pre_trend)

    # Arrays for plotting
    pred_minutes = np.array([p["minutes"] for p in predicted_curve])
    pred_bg      = np.array([p["predicted_bg"] for p in predicted_curve])
    pred_lower   = np.array([p["confidence_lower"] for p in predicted_curve])
    pred_upper   = np.array([p["confidence_upper"] for p in predicted_curve])

    actual_minutes = np.array([r["minutes_post_meal"] for r in actual_readings])
    actual_glucose = np.array([r["glucose_mgdl"] for r in actual_readings])

    # Peaks
    predicted_peak   = float(pred_bg.max())
    actual_peak      = float(actual_glucose.max())
    pred_peak_time   = int(pred_minutes[pred_bg.argmax()])
    actual_peak_time = int(actual_minutes[actual_glucose.argmax()])

    # MARD — interpolate actual onto the predicted grid for a fair point-wise comparison
    actual_on_grid = np.interp(pred_minutes, actual_minutes, actual_glucose)
    mard = compute_mard(pred_bg.tolist(), actual_on_grid.tolist())

    # TIR for this meal
    tir = get_time_in_range(meal_id)

    # Dish names for chart title
    dish_names = ", ".join(d["dish_name"] for d in meal.get("dishes", []))
    if len(dish_names) > 60:
        dish_names = dish_names[:57] + "..."

    # --- Build chart ---
    fig, ax = plt.subplots(figsize=(10, 5))

    # Confidence interval band
    ax.fill_between(pred_minutes, pred_lower, pred_upper,
                    alpha=0.25, color="steelblue", label="Confidence Interval")

    # Predicted curve (blue)
    ax.plot(pred_minutes, pred_bg,
            color="steelblue", linewidth=2, label="Predicted")

    # Actual CGM trace (red)
    ax.plot(actual_minutes, actual_glucose,
            color="crimson", linewidth=2, label="Actual CGM")

    # Target range lines (green dashed, single legend entry)
    ax.axhline(70,  color="green", linestyle="--", linewidth=1, label="Target Range (70–180)")
    ax.axhline(180, color="green", linestyle="--", linewidth=1)

    # Annotate predicted peak
    ax.axvline(pred_peak_time, color="steelblue", linestyle=":", linewidth=1, alpha=0.6)
    ax.annotate(
        f"Pred peak\n{predicted_peak:.0f} mg/dL\n@ {pred_peak_time} min",
        xy=(pred_peak_time, predicted_peak),
        xytext=(min(pred_peak_time + 8, 160), predicted_peak + 6),
        fontsize=7, color="steelblue",
        arrowprops=dict(arrowstyle="->", color="steelblue", lw=0.8),
    )

    # Annotate actual peak (offset slightly to avoid overlap when peaks coincide)
    ax.axvline(actual_peak_time, color="crimson", linestyle=":", linewidth=1, alpha=0.6)
    ax.annotate(
        f"Actual peak\n{actual_peak:.0f} mg/dL\n@ {actual_peak_time} min",
        xy=(actual_peak_time, actual_peak),
        xytext=(max(actual_peak_time - 40, 5), actual_peak - 18),
        fontsize=7, color="crimson",
        arrowprops=dict(arrowstyle="->", color="crimson", lw=0.8),
    )

    ax.set_xlabel("Minutes post-meal")
    ax.set_ylabel("Blood glucose (mg/dL)")
    ax.set_xlim(0, 180)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)

    # Title + subtitle
    ax.set_title(f"{dish_names} | Predicted vs Actual BG Response", fontsize=11, pad=14)
    fig.text(
        0.5, 0.93,
        f"MARD: {mard:.1%}  |  Predicted Peak: {predicted_peak:.0f}  |  Actual Peak: {actual_peak:.0f} mg/dL",
        ha="center", fontsize=9, style="italic", color="dimgray",
    )

    fig.tight_layout(rect=[0, 0, 1, 0.93])

    # Save
    _OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
    chart_path = str(_OVERLAYS_DIR / f"{meal_id}.png")
    fig.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Terminal summary
    print(f"Meal:           {meal_id}")
    print(f"Predicted peak: {predicted_peak:.0f} mg/dL  @ {pred_peak_time} min")
    print(f"Actual peak:    {actual_peak:.0f} mg/dL  @ {actual_peak_time} min")
    print(f"MARD:           {mard:.2%}")
    print(f"TIR:            {tir:.1%}")
    print(f"Chart saved:    {chart_path}")

    return {
        "meal_id": meal_id,
        "predicted_peak": predicted_peak,
        "actual_peak": actual_peak,
        "mard": mard,
        "tir_actual": tir,
        "chart_path": chart_path,
    }


def batch_overlay_report() -> None:
    """Generate overlays for all meals with complete CGM windows.

    Saves summary CSV to data/glucose/overlay_report.csv with columns:
    meal_id, dish_names, total_carbs, predicted_peak, actual_peak, mard, tir
    """
    meals = _load(_MEAL_FILE)
    complete = [m for m in meals if m.get("cgm_window", {}).get("status") == "complete"]

    if not complete:
        print("No complete meal CGM windows found — run backfill_cgm_windows() first.")
        return

    rows = []
    for meal in complete:
        try:
            summary = generate_overlay(meal["meal_id"])
        except Exception as e:
            print(f"SKIP {meal['meal_id']}: {e}")
            continue

        dish_names = ", ".join(d["dish_name"] for d in meal.get("dishes", []))
        rows.append({
            "meal_id":       summary["meal_id"],
            "dish_names":    dish_names,
            "total_carbs":   meal.get("total_carbs_g", ""),
            "predicted_peak": f"{summary['predicted_peak']:.1f}",
            "actual_peak":   f"{summary['actual_peak']:.1f}",
            "mard":          f"{summary['mard']:.4f}",
            "tir":           f"{summary['tir_actual']:.4f}",
        })

    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["meal_id", "dish_names", "total_carbs",
                        "predicted_peak", "actual_peak", "mard", "tir"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nBatch report: {len(rows)} meals → {_REPORT_PATH}")
