import json
import os
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(__file__).parent.parent / "data" / "glucose"
_CGM_FILE = _DATA_DIR / "cgm_readings.json"
_MEAL_FILE = _DATA_DIR / "meal_logs.json"

_VALID_TRENDS = {"flat", "rising", "rising_rapidly", "falling", "falling_rapidly"}
_VALID_SOURCES = {"dexcom_csv", "manual"}


def _ensure_files():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _CGM_FILE.exists():
        _CGM_FILE.write_text("[]")
    if not _MEAL_FILE.exists():
        _MEAL_FILE.write_text("[]")


def _load(path: Path) -> list:
    _ensure_files()
    with open(path) as f:
        return json.load(f)


def _save(path: Path, data: list) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _parse_iso(ts: str) -> datetime:
    """Parse ISO 8601 string; raise ValueError if malformed."""
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid ISO 8601 timestamp '{ts}': {e}")


def _validate_cgm(reading: dict) -> None:
    required = {"timestamp", "glucose_mgdl", "trend", "source"}
    missing = required - reading.keys()
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    _parse_iso(reading["timestamp"])

    if not isinstance(reading["glucose_mgdl"], int):
        raise ValueError(
            f"glucose_mgdl must be an integer, got {type(reading['glucose_mgdl']).__name__}"
        )
    if reading["glucose_mgdl"] < 20 or reading["glucose_mgdl"] > 600:
        raise ValueError(f"glucose_mgdl {reading['glucose_mgdl']} out of range [20, 600]")

    if reading["trend"] not in _VALID_TRENDS:
        raise ValueError(f"trend must be one of {_VALID_TRENDS}, got '{reading['trend']}'")

    if reading["source"] not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES}, got '{reading['source']}'")


def _validate_meal(meal: dict) -> None:
    required = {"meal_id", "timestamp", "dishes", "total_carbs_g"}
    missing = required - meal.keys()
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    _parse_iso(meal["timestamp"])

    if not isinstance(meal["dishes"], list):
        raise ValueError("dishes must be a list")

    if not isinstance(meal["total_carbs_g"], (int, float)):
        raise ValueError("total_carbs_g must be a number")


def save_cgm_reading(reading: dict) -> None:
    """Validate and append a CGM reading to cgm_readings.json."""
    _validate_cgm(reading)
    readings = _load(_CGM_FILE)
    readings.append(reading)
    _save(_CGM_FILE, readings)


def save_meal_log(meal: dict) -> None:
    """Validate and append a meal log to meal_logs.json."""
    _validate_meal(meal)
    meals = _load(_MEAL_FILE)
    meals.append(meal)
    _save(_MEAL_FILE, meals)


def get_cgm_window(start_time: str, end_time: str) -> list[dict]:
    """Return all CGM readings between start_time and end_time (ISO 8601 strings)."""
    start = _parse_iso(start_time)
    end = _parse_iso(end_time)
    readings = _load(_CGM_FILE)
    return [
        r for r in readings
        if start <= _parse_iso(r["timestamp"]) <= end
    ]


def get_pre_meal_glucose(meal_timestamp: str, window_minutes: int = 15) -> dict | None:
    """Return the CGM reading closest to meal_timestamp within window_minutes."""
    meal_dt = _parse_iso(meal_timestamp)
    readings = _load(_CGM_FILE)

    candidates = []
    for r in readings:
        rdt = _parse_iso(r["timestamp"])
        diff = abs((meal_dt - rdt).total_seconds() / 60)
        if diff <= window_minutes:
            candidates.append((diff, r))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def get_meal_logs(last_n: int = None) -> list[dict]:
    """Return all meal logs, optionally limited to last_n entries."""
    meals = _load(_MEAL_FILE)
    if last_n is not None:
        return meals[-last_n:]
    return meals
