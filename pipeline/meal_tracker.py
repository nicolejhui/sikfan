from datetime import datetime, timezone, timedelta

from pipeline.glucose_store import (
    _MEAL_FILE,
    _CGM_FILE,
    _load,
    _save,
    _parse_iso,
)

_WINDOW_MINUTES = 180
_INTERVAL_MINUTES = 5
_COMPLETE_THRESHOLD = 30
_NUM_SLOTS = _WINDOW_MINUTES // _INTERVAL_MINUTES + 1  # 37


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _update_meal_in_place(updated_meal: dict) -> None:
    meals = _load(_MEAL_FILE)
    for i, m in enumerate(meals):
        if m["meal_id"] == updated_meal["meal_id"]:
            meals[i] = updated_meal
            _save(_MEAL_FILE, meals)
            return
    raise ValueError(f"meal_id '{updated_meal['meal_id']}' not found in meal_logs.json")


def _interpolate_to_grid(raw_readings: list[dict], meal_dt: datetime) -> list[dict]:
    """
    Return a clean 37-point vector at t=0,5,...,180 min.
    Snaps raw readings to nearest 5-min slot; linearly interpolates gaps.
    """
    # Build slot map: slot (int) -> (glucose, timestamp_str), keeping reading closest to slot
    slot_map: dict[int, tuple[float, str, float]] = {}  # slot -> (glucose, ts, abs_delta_min)
    for r in raw_readings:
        rdt = _to_utc(_parse_iso(r["timestamp"]))
        elapsed = (rdt - meal_dt).total_seconds() / 60.0
        if elapsed < 0 or elapsed > _WINDOW_MINUTES:
            continue
        slot = round(elapsed / _INTERVAL_MINUTES) * _INTERVAL_MINUTES
        slot = max(0, min(_WINDOW_MINUTES, slot))
        delta = abs(elapsed - slot)
        if slot not in slot_map or delta < slot_map[slot][2]:
            slot_map[slot] = (r["glucose_mgdl"], r["timestamp"], delta)

    known = sorted(slot_map.keys())
    all_slots = list(range(0, _WINDOW_MINUTES + _INTERVAL_MINUTES, _INTERVAL_MINUTES))

    result = []
    for slot in all_slots:
        if slot in slot_map:
            glucose, ts_str, _ = slot_map[slot]
        else:
            before = [s for s in known if s < slot]
            after = [s for s in known if s > slot]
            if before and after:
                s0, s1 = before[-1], after[0]
                g0 = slot_map[s0][0]
                g1 = slot_map[s1][0]
                glucose = g0 + (g1 - g0) * (slot - s0) / (s1 - s0)
            elif before:
                glucose = slot_map[before[-1]][0]
            elif after:
                glucose = slot_map[after[0]][0]
            else:
                break  # no data at all
            ts_str = (meal_dt + timedelta(minutes=slot)).strftime("%Y-%m-%dT%H:%M:%S")

        result.append({
            "minutes_post_meal": slot,
            "glucose_mgdl": round(glucose) if isinstance(glucose, float) else int(glucose),
            "timestamp": ts_str,
        })

    return result


def compute_window_stats(readings: list[dict]) -> dict:
    """Compute peak, time_to_peak, return_to_baseline, area_under_curve."""
    if not readings:
        return {
            "peak_glucose": None,
            "time_to_peak_minutes": None,
            "return_to_baseline_minutes": None,
            "area_under_curve": None,
        }

    baseline = readings[0]["glucose_mgdl"]
    peak_glucose = max(r["glucose_mgdl"] for r in readings)
    peak_reading = next(r for r in readings if r["glucose_mgdl"] == peak_glucose)
    time_to_peak = peak_reading["minutes_post_meal"]

    return_to_baseline = None
    past_peak = False
    for r in readings:
        if r["minutes_post_meal"] >= time_to_peak:
            past_peak = True
        if past_peak and r["glucose_mgdl"] <= baseline:
            return_to_baseline = r["minutes_post_meal"]
            break

    # Trapezoidal AUC (mg/dL * minutes)
    auc = 0.0
    for i in range(1, len(readings)):
        dt = readings[i]["minutes_post_meal"] - readings[i - 1]["minutes_post_meal"]
        avg_g = (readings[i]["glucose_mgdl"] + readings[i - 1]["glucose_mgdl"]) / 2.0
        auc += avg_g * dt

    return {
        "peak_glucose": peak_glucose,
        "time_to_peak_minutes": time_to_peak,
        "return_to_baseline_minutes": return_to_baseline,
        "area_under_curve": round(auc, 1),
    }


def attach_cgm_window(meal_id: str) -> dict:
    """Find CGM readings for 0-180 min post meal, attach to meal log.
    Returns updated meal log dict."""
    meals = _load(_MEAL_FILE)
    meal = next((m for m in meals if m["meal_id"] == meal_id), None)
    if meal is None:
        raise ValueError(f"meal_id '{meal_id}' not found")

    meal_dt = _to_utc(_parse_iso(meal["timestamp"]))
    end_dt = meal_dt + timedelta(minutes=_WINDOW_MINUTES)

    cgm_data = _load(_CGM_FILE)
    raw_readings = [
        r for r in cgm_data
        if meal_dt <= _to_utc(_parse_iso(r["timestamp"])) <= end_dt
    ]

    if not raw_readings:
        meal["cgm_window"] = {
            "status": "pending",
            "readings": [],
            "peak_glucose": None,
            "time_to_peak_minutes": None,
            "return_to_baseline_minutes": None,
            "area_under_curve": None,
        }
        _update_meal_in_place(meal)
        return meal

    interpolated = _interpolate_to_grid(raw_readings, meal_dt)
    status = "complete" if len(raw_readings) >= _COMPLETE_THRESHOLD else "incomplete"
    stats = compute_window_stats(interpolated)

    meal["cgm_window"] = {"status": status, "readings": interpolated, **stats}
    _update_meal_in_place(meal)
    return meal


def backfill_cgm_windows() -> dict:
    """Process all historical meal logs missing cgm_window.
    Returns {'updated': int, 'incomplete': int, 'missing_data': int}"""
    meals = _load(_MEAL_FILE)
    updated = 0
    incomplete = 0
    missing_data = 0

    for meal in meals:
        existing = meal.get("cgm_window")
        if isinstance(existing, dict) and existing.get("status") == "complete":
            continue

        meal = attach_cgm_window(meal["meal_id"])
        status = meal.get("cgm_window", {}).get("status", "pending")

        if status == "complete":
            updated += 1
        elif status == "incomplete":
            incomplete += 1
        else:
            missing_data += 1

    return {"updated": updated, "incomplete": incomplete, "missing_data": missing_data}


def get_time_in_range(meal_id: str, low: int = 70, high: int = 180) -> float:
    """Return TIR percentage for a specific meal's CGM window."""
    meals = _load(_MEAL_FILE)
    meal = next((m for m in meals if m["meal_id"] == meal_id), None)
    if meal is None:
        raise ValueError(f"meal_id '{meal_id}' not found")

    window = meal.get("cgm_window")
    if not window or not window.get("readings"):
        return 0.0

    readings = window["readings"]
    in_range = sum(1 for r in readings if low <= r["glucose_mgdl"] <= high)
    return in_range / len(readings)
