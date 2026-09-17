"""
Verification script for GLUC-001 and GLUC-002.
Run from project root: python scripts/verify_gluc_001_002.py
"""
import json
import os
import sys
import tempfile
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.glucose_store import (
    save_cgm_reading,
    save_meal_log,
    get_cgm_window,
    get_pre_meal_glucose,
    get_meal_logs,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    return condition


# ── GLUC-001 ──────────────────────────────────────────────────────────────────
print("\n=== GLUC-001: Storage ===")

# 1. Write a CGM reading
save_cgm_reading({
    "timestamp": "2026-03-07T12:00:00",
    "glucose_mgdl": 120,
    "trend": "flat",
    "source": "manual",
})
with open("data/glucose/cgm_readings.json") as f:
    data = json.load(f)
check("CGM write + persist", any(r["timestamp"] == "2026-03-07T12:00:00" for r in data))

# 2. Validation rejects malformed entry
try:
    save_cgm_reading({"timestamp": "bad-date", "glucose_mgdl": "not-a-number", "trend": "flat", "source": "manual"})
    check("Validation rejects bad timestamp", False)
except ValueError as e:
    check("Validation rejects bad timestamp", True, str(e))

try:
    save_cgm_reading({"timestamp": "2026-03-07T12:00:00", "glucose_mgdl": 120.5, "trend": "flat", "source": "manual"})
    check("Validation rejects float glucose", False)
except ValueError as e:
    check("Validation rejects float glucose", True, str(e))

try:
    save_cgm_reading({"timestamp": "2026-03-07T12:00:00", "glucose_mgdl": 120, "trend": "zooming", "source": "manual"})
    check("Validation rejects unknown trend", False)
except ValueError as e:
    check("Validation rejects unknown trend", True, str(e))

# 3. Window query
# Add a second reading for range testing
save_cgm_reading({
    "timestamp": "2026-03-07T12:10:00",
    "glucose_mgdl": 125,
    "trend": "rising",
    "source": "manual",
})
window = get_cgm_window("2026-03-07T11:00:00", "2026-03-07T13:00:00")
check("get_cgm_window returns readings in range", len(window) >= 2, f"{len(window)} readings")

outside = get_cgm_window("2026-03-07T14:00:00", "2026-03-07T15:00:00")
check("get_cgm_window returns empty outside range", len(outside) == 0)

# 4. Pre-meal glucose lookup
pre = get_pre_meal_glucose("2026-03-07T12:02:00", window_minutes=15)
check("get_pre_meal_glucose finds closest reading", pre is not None and pre["glucose_mgdl"] in (120, 125))

no_pre = get_pre_meal_glucose("2026-03-07T20:00:00", window_minutes=5)
check("get_pre_meal_glucose returns None when no match", no_pre is None)

# 5. Meal log write
save_meal_log({
    "meal_id": "meal_20260307_120000",
    "timestamp": "2026-03-07T12:00:00",
    "dishes": [{"dish_name": "mapo_tofu", "portion_size": "medium", "macros": {"calories": 280, "carbs_g": 18, "protein_g": 14, "fat_g": 16, "fiber_g": 2}}],
    "total_carbs_g": 18,
    "pre_meal_glucose_mgdl": 112,
    "pre_meal_trend": "flat",
    "cgm_window": [],
})
with open("data/glucose/meal_logs.json") as f:
    meals = json.load(f)
check("Meal log write + persist", any(m["meal_id"] == "meal_20260307_120000" for m in meals))

logs = get_meal_logs(last_n=1)
check("get_meal_logs last_n=1", len(logs) == 1)

# ── GLUC-002 ──────────────────────────────────────────────────────────────────
print("\n=== GLUC-002: CSV Importer ===")

from scripts.import_dexcom_csv import parse_dexcom_csv, import_to_store, derive_trend

# 6. Trend derivation
check("derive_trend rising_rapidly (rate>3)", derive_trend(130, 100, 5) == "rising_rapidly", f"rate={(130-100)/5}")
check("derive_trend rising (rate 1-3)", derive_trend(115, 100, 10) == "rising", f"rate={(115-100)/10}")
check("derive_trend flat", derive_trend(100, 99, 5) == "flat")
check("derive_trend falling", derive_trend(85, 100, 10) == "falling")
check("derive_trend falling_rapidly", derive_trend(70, 100, 5) == "falling_rapidly")

# 7. Parse synthetic CSV
SAMPLE_CSV = """\
Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL),Insulin Value (u),Carb Value (grams),Duration (hh:mm:ss),Evidence Review Board Result,Advisory Message,Notes
1,2026-01-01T08:00:00,,,,,,142,,,,,,
2,2026-01-01T08:05:00,,,,,,145,,,,,,
3,2026-01-01T08:10:00,,,,,,138,,,,,,
4,2026-01-01T08:15:00,,,,,,Low,,,,,,
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
    tmp.write(SAMPLE_CSV)
    tmp_path = tmp.name

try:
    parsed = parse_dexcom_csv(tmp_path)
    check("CSV parses valid rows", len(parsed) == 3, f"{len(parsed)} readings parsed")
    check("CSV skips non-numeric glucose ('Low')", len(parsed) == 3)
    check("First reading trend is flat (no prev)", parsed[0]["trend"] == "flat")
    check("Readings have correct source", all(r["source"] == "dexcom_csv" for r in parsed))

    # 8. Idempotency: import twice
    # Reset store to known state for clean idempotency test
    GLUC_FILE = Path("data/glucose/cgm_readings.json")
    before_count = len(json.loads(GLUC_FILE.read_text()))

    summary1 = import_to_store(parsed)
    after_first = len(json.loads(GLUC_FILE.read_text()))
    check("First import adds new readings", summary1["imported"] == 3, f"imported={summary1['imported']}")

    summary2 = import_to_store(parsed)
    after_second = len(json.loads(GLUC_FILE.read_text()))
    check("Re-import is idempotent (no duplicates)", after_first == after_second,
          f"before={after_first}, after={after_second}")
    check("Re-import reports duplicates=3", summary2["duplicates"] == 3, f"duplicates={summary2['duplicates']}")
    check("Re-import imported=0", summary2["imported"] == 0)
finally:
    os.unlink(tmp_path)

print("\nDone.")
