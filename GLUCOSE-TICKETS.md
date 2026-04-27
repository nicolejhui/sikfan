# GlycoLens — Blood Glucose Pipeline Engineering Tickets

> These tickets cover the personal glucose response model and post-meal tracking overlay.
> They are intentionally scoped to work WITHOUT live Dexcom API integration at this stage.
> CGM data is imported via CSV export from Dexcom Clarity (manual import at MVP).
> Build order: Epic 6 → Epic 7
> Gate: FOOD-015 (analyze_meal) must be complete before starting these tickets.

---

## Epic 6: Personal Glucose Response Model

### GLUC-001 — Design CGM data ingestion and storage schema

**Goal**
Define and implement the data schema for storing CGM readings and meal logs so the
glucose response model has clean, structured input to train on.

**Acceptance Criteria**
- [ ] CGM readings stored as structured JSON with all required fields
- [ ] Meal logs linked to CGM readings by timestamp
- [ ] Schema handles missing readings (CGM gaps are common) gracefully
- [ ] Data stored at `data/glucose/cgm_readings.json` and `data/glucose/meal_logs.json`
- [ ] Schema is validated on write — malformed entries are rejected with a clear error

**Implementation Technical Spec**

**Files to create:**
- `pipeline/glucose_store.py` — read/write functions
- `data/glucose/cgm_readings.json` — initialized as empty list `[]`
- `data/glucose/meal_logs.json` — initialized as empty list `[]`

**CGM Reading Schema:**
```python
{
    "timestamp": "2026-03-07T12:34:00",  # ISO 8601, always UTC
    "glucose_mgdl": 142,                  # integer, mg/dL
    "trend": "flat",                      # flat | rising | rising_rapidly | falling | falling_rapidly
    "source": "dexcom_csv"               # dexcom_csv | manual
}
```

**Meal Log Schema:**
```python
{
    "meal_id": "meal_20260307_123400",    # generated: meal_{YYYYMMDD}_{HHMMSS}
    "timestamp": "2026-03-07T12:34:00",  # ISO 8601, time of meal
    "dishes": [                           # output from analyze_meal() FOOD-015
        {
            "dish_name": "mapo_tofu",
            "portion_size": "medium",
            "macros": {
                "calories": 280,
                "carbs_g": 18,
                "protein_g": 14,
                "fat_g": 16,
                "fiber_g": 2
            }
        }
    ],
    "total_carbs_g": 18,                 # sum of all dish carbs
    "pre_meal_glucose_mgdl": 112,        # CGM reading closest to meal timestamp
    "pre_meal_trend": "flat",            # trend at meal time
    "cgm_window": []                     # populated post-meal by GLUC-004
}
```

**Function Signatures:**
```python
# pipeline/glucose_store.py

def save_cgm_reading(reading: dict) -> None:
    """Validate and append a CGM reading to cgm_readings.json"""

def save_meal_log(meal: dict) -> None:
    """Validate and append a meal log to meal_logs.json"""

def get_cgm_window(start_time: str, end_time: str) -> list[dict]:
    """Return all CGM readings between start_time and end_time (ISO 8601 strings)"""

def get_pre_meal_glucose(meal_timestamp: str, window_minutes: int = 15) -> dict:
    """Return the CGM reading closest to meal_timestamp within window_minutes"""

def get_meal_logs(last_n: int = None) -> list[dict]:
    """Return all meal logs, optionally limited to last_n entries"""
```

**Verification Commands:**
```bash
# 1. Initialize schema files
python -c "
from pipeline.glucose_store import save_cgm_reading, save_meal_log
import json

# Write a test CGM reading
save_cgm_reading({
    'timestamp': '2026-03-07T12:00:00',
    'glucose_mgdl': 120,
    'trend': 'flat',
    'source': 'manual'
})
print('CGM write: OK')

# Verify it was saved
with open('data/glucose/cgm_readings.json') as f:
    data = json.load(f)
print(f'CGM readings stored: {len(data)}')
"

# 2. Test validation rejects malformed entry
python -c "
from pipeline.glucose_store import save_cgm_reading
try:
    save_cgm_reading({'timestamp': 'bad-date', 'glucose_mgdl': 'not-a-number'})
    print('FAIL: should have raised error')
except ValueError as e:
    print(f'Validation working: {e}')
"

# 3. Test window query
python -c "
from pipeline.glucose_store import get_cgm_window
readings = get_cgm_window('2026-03-07T11:00:00', '2026-03-07T13:00:00')
print(f'Window query returned {len(readings)} readings')
"
```

**Dependencies:** FOOD-015 (meal log uses analyze_meal output schema)

---

### GLUC-002 — Build Dexcom CSV importer

**Goal**
Parse a Dexcom Clarity CSV export and load all readings into the CGM store so the
glucose response model has historical data to train on without requiring live API
access at MVP.

**Acceptance Criteria**
- [ ] Script parses standard Dexcom Clarity CSV export format correctly
- [ ] Imports all readings with valid glucose values (skips sensor warm-up gaps)
- [ ] Deduplicates on timestamp — re-importing same CSV does not create duplicates
- [ ] Prints import summary: total rows, imported, skipped, duplicates
- [ ] Handles Dexcom's date format (`YYYY-MM-DD HH:MM:SS`) correctly

**Implementation Technical Spec**

**File to create:** `scripts/import_dexcom_csv.py`

**CLI Interface:**
```bash
python scripts/import_dexcom_csv.py --file path/to/dexcom_export.csv
```

**Dexcom CSV Format (standard Clarity export):**
```
Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,
Patient Info,Device Info,Source Device ID,Glucose Value (mg/dL),
Insulin Value (u),Carb Value (grams),Duration (hh:mm:ss),
Evidence Review Board Result,Advisory Message,Notes
1,2026-01-01T08:00:00,,,,,,142,,,,,,
2,2026-01-01T08:05:00,,,,,,145,,,,,,
```

**Trend Derivation Logic:**
```python
# Dexcom CSV does not include trend arrows — derive from rate of change
def derive_trend(current_glucose: int, prev_glucose: int, minutes_elapsed: float) -> str:
    rate = (current_glucose - prev_glucose) / minutes_elapsed  # mg/dL per minute
    if rate > 3:   return "rising_rapidly"
    if rate > 1:   return "rising"
    if rate < -3:  return "falling_rapidly"
    if rate < -1:  return "falling"
    return "flat"
```

**Function Signatures:**
```python
# scripts/import_dexcom_csv.py

def parse_dexcom_csv(filepath: str) -> list[dict]:
    """Parse Dexcom Clarity CSV and return list of CGM reading dicts"""

def import_to_store(readings: list[dict]) -> dict:
    """Import parsed readings into glucose_store, return summary dict:
    {'total': int, 'imported': int, 'skipped': int, 'duplicates': int}"""
```

**Verification Commands:**
```bash
# 1. Run import (use your actual Dexcom export)
python scripts/import_dexcom_csv.py --file path/to/your_dexcom_export.csv

# 2. Verify readings were stored
python -c "
from pipeline.glucose_store import get_cgm_window
import json
with open('data/glucose/cgm_readings.json') as f:
    data = json.load(f)
print(f'Total CGM readings in store: {len(data)}')
print(f'First reading: {data[0]}')
print(f'Last reading: {data[-1]}')
"

# 3. Test idempotency — re-import should not create duplicates
python -c "
import json, subprocess
with open('data/glucose/cgm_readings.json') as f:
    count_before = len(json.load(f))
subprocess.run(['python', 'scripts/import_dexcom_csv.py', '--file', 'path/to/your_dexcom_export.csv'])
with open('data/glucose/cgm_readings.json') as f:
    count_after = len(json.load(f))
print(f'Before: {count_before}, After: {count_after}')
print(f'Idempotent: {count_before == count_after}')
"
```

**Dependencies:** GLUC-001

---

### GLUC-003 — Build personal glucose response model (training pipeline)

**Goal**
Train a personalized model that predicts how a specific meal's macros will affect
the user's blood glucose over the 3 hours following that meal, based on their own
historical meal + CGM data.

**Acceptance Criteria**
- [ ] Model trains on meal logs that have at least 3 hours of post-meal CGM data
- [ ] Requires minimum 20 meal-CGM pairs to train (prints warning if below threshold)
- [ ] Model saved to `data/models/glucose_model.pkl` after training
- [ ] Outputs a predicted BG curve: list of (minutes_post_meal, predicted_bg) tuples at 5-minute intervals over 180 minutes
- [ ] Prediction MARD (Mean Absolute Relative Difference) printed after training
- [ ] Model retrained automatically when 10 new confirmed meal logs are added

**Implementation Technical Spec**

**Files to create:**
- `pipeline/glucose_model.py` — model class and training logic
- `data/models/` — directory for saved models

**Model Architecture:**
Use a Gaussian Process Regressor (GPR) as the MVP model. GPR is ideal here because:
- Works well with small datasets (you won't have hundreds of meals initially)
- Outputs uncertainty estimates alongside predictions (critical for medical context)
- No GPU required — runs fine on CPU
- Interpretable — you can see which features drive predictions

```python
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel
```

**Input Features per meal:**
```python
features = {
    "total_carbs_g": float,        # primary glycemic driver
    "fiber_g": float,              # slows glucose absorption
    "fat_g": float,                # slows gastric emptying
    "protein_g": float,            # minor glucose effect
    "pre_meal_glucose": float,     # starting BG level
    "pre_meal_trend": float,       # encoded: -2/-1/0/1/2 for trend arrows
    "hour_of_day": float,          # dawn phenomenon affects response
    "carb_to_fiber_ratio": float,  # derived feature
}
```

**Target:**
```python
# For each meal, extract the CGM readings for 0-180 minutes post-meal
# Target is the actual BG curve: list of glucose values at t=0,5,10,...,180 minutes
cgm_window = get_cgm_window(meal_timestamp, meal_timestamp + 3hrs)
```

**Function Signatures:**
```python
# pipeline/glucose_model.py

def build_training_data() -> tuple[np.ndarray, np.ndarray]:
    """Load meal logs + CGM windows, return (X_features, y_curves)"""

def train_model(min_meals: int = 20) -> dict:
    """Train GPR on available meal data, save to data/models/glucose_model.pkl
    Returns: {'meals_used': int, 'mard': float, 'model_path': str}"""

def predict_glucose_curve(meal_macros: dict, pre_meal_glucose: int,
                          pre_meal_trend: str) -> list[dict]:
    """Predict BG curve for a meal.
    Returns list of {'minutes': int, 'predicted_bg': float,
                     'confidence_lower': float, 'confidence_upper': float}
    at 5-minute intervals for 180 minutes"""

def should_retrain() -> bool:
    """Return True if 10+ new confirmed meals since last training"""

def compute_mard(predicted: list, actual: list) -> float:
    """Compute Mean Absolute Relative Difference between predicted and actual curves"""
```

**Verification Commands:**
```bash
# 1. Build training data (requires GLUC-001, GLUC-002 complete with real data)
python -c "
from pipeline.glucose_model import build_training_data
X, y = build_training_data()
print(f'Training samples: {X.shape[0]}')
print(f'Feature dimensions: {X.shape[1]}')
print(f'Ready to train: {X.shape[0] >= 20}')
"

# 2. Train model
python -c "
from pipeline.glucose_model import train_model
result = train_model(min_meals=20)
print(f'Meals used: {result[\"meals_used\"]}')
print(f'MARD: {result[\"mard\"]:.2%}')
print(f'Model saved to: {result[\"model_path\"]}')
"

# 3. Run a test prediction
python -c "
from pipeline.glucose_model import predict_glucose_curve
curve = predict_glucose_curve(
    meal_macros={'total_carbs_g': 65, 'fiber_g': 3, 'fat_g': 8, 'protein_g': 12},
    pre_meal_glucose=112,
    pre_meal_trend='flat'
)
print(f'Prediction points: {len(curve)}')
print(f'Peak predicted BG: {max(p[\"predicted_bg\"] for p in curve):.0f} mg/dL')
print(f'Time to peak: {max(curve, key=lambda x: x[\"predicted_bg\"])[\"minutes\"]} min')
"

# 4. Verify model file exists
python -c "
import os
exists = os.path.exists('data/models/glucose_model.pkl')
print(f'Model file exists: {exists}')
"
```

**Dependencies:** GLUC-001, GLUC-002
**Note:** You need at least 20 meal logs with 3 hours of post-meal CGM data before
training is meaningful. Use your Dexcom CSV history (GLUC-002) paired with any
historical meal records you have. The model improves significantly with each
additional confirmed meal.

---

## Epic 7: Post-Meal Real-Time Tracking & Overlay

### GLUC-004 — Implement post-meal CGM window capture

**Goal**
After a meal is logged, automatically capture the 3-hour post-meal CGM window and
attach it to the meal log so the model has ground truth to learn from.

**Acceptance Criteria**
- [ ] After logging a meal, system monitors for CGM readings over the next 3 hours
- [ ] CGM window is attached to the meal log once complete (>= 30 readings in 3hrs)
- [ ] Partial windows (< 30 readings) are flagged as `incomplete` but still stored
- [ ] Meal logs updated in-place in `meal_logs.json` with `cgm_window` populated
- [ ] Window capture works from CSV import (historical) and manual entry (current)

**Implementation Technical Spec**

**File to create:** `pipeline/meal_tracker.py`

**CGM Window Format (added to meal log):**
```python
"cgm_window": {
    "status": "complete",          # complete | incomplete | pending
    "readings": [
        {
            "minutes_post_meal": 0,
            "glucose_mgdl": 112,
            "timestamp": "2026-03-07T12:34:00"
        },
        {
            "minutes_post_meal": 5,
            "glucose_mgdl": 115,
            "timestamp": "2026-03-07T12:39:00"
        },
        # ... continues every 5 min for 180 min
    ],
    "peak_glucose": 178,
    "time_to_peak_minutes": 45,
    "return_to_baseline_minutes": 135,   # null if never returned
    "area_under_curve": 4820.0           # mg/dL * minutes (glycemic load proxy)
}
```

**Function Signatures:**
```python
# pipeline/meal_tracker.py

def attach_cgm_window(meal_id: str) -> dict:
    """Find CGM readings for 0-180 min post meal, attach to meal log.
    Returns updated meal log dict."""

def compute_window_stats(readings: list[dict]) -> dict:
    """Compute peak, time_to_peak, return_to_baseline, area_under_curve
    from a list of CGM readings. Returns stats dict."""

def backfill_cgm_windows() -> dict:
    """Process all historical meal logs missing cgm_window.
    Returns {'updated': int, 'incomplete': int, 'missing_data': int}"""

def get_time_in_range(meal_id: str, low: int = 70, high: int = 180) -> float:
    """Return TIR percentage for a specific meal's CGM window"""
```

**Verification Commands:**
```bash
# 1. Backfill windows for all historical meals (run after GLUC-002 import)
python -c "
from pipeline.meal_tracker import backfill_cgm_windows
result = backfill_cgm_windows()
print(f'Windows attached: {result[\"updated\"]}')
print(f'Incomplete (CGM gaps): {result[\"incomplete\"]}')
print(f'No CGM data found: {result[\"missing_data\"]}')
"

# 2. Verify a meal log has window attached
python -c "
import json
with open('data/glucose/meal_logs.json') as f:
    meals = json.load(f)
meal = next((m for m in meals if m.get('cgm_window')), None)
if meal:
    w = meal['cgm_window']
    print(f'Meal: {meal[\"meal_id\"]}')
    print(f'Window status: {w[\"status\"]}')
    print(f'Peak glucose: {w[\"peak_glucose\"]} mg/dL')
    print(f'Time to peak: {w[\"time_to_peak_minutes\"]} min')
    print(f'Readings captured: {len(w[\"readings\"])}')
else:
    print('No meals with CGM windows found yet')
"

# 3. Test TIR computation
python -c "
import json
from pipeline.meal_tracker import get_time_in_range
with open('data/glucose/meal_logs.json') as f:
    meals = json.load(f)
meal = next((m for m in meals if m.get('cgm_window', {}).get('status') == 'complete'), None)
if meal:
    tir = get_time_in_range(meal['meal_id'])
    print(f'TIR for {meal[\"meal_id\"]}: {tir:.1%}')
"
```

**Dependencies:** GLUC-001, GLUC-002

---

### GLUC-005 — Build predicted vs. actual BG overlay

**Goal**
After a meal's CGM window is complete, compare the model's pre-meal prediction
against the actual CGM trace so you can visually evaluate model accuracy and
identify which dishes are hardest to predict.

**Acceptance Criteria**
- [ ] Given a meal_id, generates a matplotlib chart showing predicted curve vs. actual CGM trace
- [ ] Chart shows confidence interval band around prediction
- [ ] Annotates peak predicted vs. peak actual on the chart
- [ ] MARD for that specific meal displayed on chart
- [ ] Chart saved to `data/glucose/overlays/{meal_id}.png`
- [ ] Summary printed to terminal: predicted peak, actual peak, MARD, TIR

**Implementation Technical Spec**

**File to create:** `pipeline/glucose_overlay.py`

**Function Signatures:**
```python
# pipeline/glucose_overlay.py

def generate_overlay(meal_id: str) -> dict:
    """Generate predicted vs actual BG overlay chart for a meal.
    Saves chart to data/glucose/overlays/{meal_id}.png
    Returns summary dict:
    {
        'meal_id': str,
        'predicted_peak': float,
        'actual_peak': float,
        'mard': float,
        'tir_actual': float,
        'chart_path': str
    }"""

def batch_overlay_report() -> None:
    """Generate overlays for all meals with complete CGM windows.
    Saves summary CSV to data/glucose/overlay_report.csv with columns:
    meal_id, dish_names, total_carbs, predicted_peak, actual_peak, mard, tir"""
```

**Chart Specification:**
```python
import matplotlib.pyplot as plt

# Chart must include:
# - X axis: minutes post-meal (0 to 180)
# - Y axis: blood glucose mg/dL
# - Blue line: predicted BG curve
# - Blue shaded band: confidence interval (lower to upper bounds)
# - Red line: actual CGM readings
# - Horizontal green dashed lines: target range (70 mg/dL and 180 mg/dL)
# - Vertical annotation: predicted peak (blue) and actual peak (red)
# - Title: f"{dish_names} | Predicted vs Actual BG Response"
# - Subtitle: f"MARD: {mard:.1%} | Predicted Peak: {pred_peak} | Actual Peak: {actual_peak} mg/dL"
# - Legend: Predicted, Confidence Interval, Actual CGM, Target Range
```

**Verification Commands:**
```bash
# 1. Generate overlay for most recent complete meal
python -c "
import json
from pipeline.glucose_overlay import generate_overlay

with open('data/glucose/meal_logs.json') as f:
    meals = json.load(f)

complete = [m for m in meals if m.get('cgm_window', {}).get('status') == 'complete']
if complete:
    result = generate_overlay(complete[-1]['meal_id'])
    print(f'Meal: {result[\"meal_id\"]}')
    print(f'Predicted peak: {result[\"predicted_peak\"]:.0f} mg/dL')
    print(f'Actual peak: {result[\"actual_peak\"]:.0f} mg/dL')
    print(f'MARD: {result[\"mard\"]:.2%}')
    print(f'TIR: {result[\"tir_actual\"]:.1%}')
    print(f'Chart saved: {result[\"chart_path\"]}')
else:
    print('No complete meal windows yet — run GLUC-004 first')
"

# 2. Generate batch report for all complete meals
python -c "
from pipeline.glucose_overlay import batch_overlay_report
batch_overlay_report()
import os
print(f'Report exists: {os.path.exists(\"data/glucose/overlay_report.csv\")}')
"

# 3. Verify chart file was created
python -c "
import os
overlays = os.listdir('data/glucose/overlays/') if os.path.exists('data/glucose/overlays/') else []
print(f'Overlay charts generated: {len(overlays)}')
"
```

**Dependencies:** GLUC-003, GLUC-004

---

### GLUC-006 — Build end-to-end glucose pipeline integration test

**Goal**
A single function that ties the full glucose pipeline together — from meal log
to prediction to post-meal tracking — so the glucose layer is verified end-to-end
before mobile app integration.

**Acceptance Criteria**
- [ ] `analyze_glucose(meal_id)` returns complete pre + post meal glucose analysis
- [ ] Works on historical meals (with real CGM data) and new meals (prediction only)
- [ ] Triggers model retraining if `should_retrain()` returns True
- [ ] All outputs are JSON-serializable (mobile app ready)
- [ ] Full pipeline completes in <= 3 seconds for a single meal

**Implementation Technical Spec**

**File to create:** `glucose_analysis.py` (project root, alongside `analyze_meal.py`)

**Function Signature:**
```python
def analyze_glucose(meal_id: str) -> dict:
    """
    Full glucose analysis for a meal.
    Returns:
    {
        "meal_id": str,
        "meal_timestamp": str,
        "dishes": list,
        "total_carbs_g": float,
        "pre_meal_glucose": int,
        "pre_meal_trend": str,
        "prediction": {
            "curve": [{"minutes": int, "predicted_bg": float,
                       "confidence_lower": float, "confidence_upper": float}],
            "predicted_peak_bg": float,
            "predicted_time_to_peak_minutes": int,
            "model_confidence": str   # high | medium | low based on training data size
        },
        "actuals": {                  # null if CGM window not yet complete
            "curve": [...],
            "actual_peak_bg": float,
            "time_to_peak_minutes": int,
            "tir_ratio": float,           # 0.0–1.0 (ratio, not percentage)
            "mard": float,
            "chart_path": str
        },
        "retrain_triggered": bool
    }
    """
```

**Verification Commands:**
```bash
# 1. Run full pipeline on most recent meal
python -c "
import json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs

meals = get_meal_logs(last_n=1)
if meals:
    result = analyze_glucose(meals[-1]['meal_id'])
    print(json.dumps({
        'meal_id': result['meal_id'],
        'total_carbs': result['total_carbs_g'],
        'predicted_peak': result['prediction']['predicted_peak_bg'],
        'has_actuals': result['actuals'] is not None,
        'retrain_triggered': result['retrain_triggered']
    }, indent=2))
else:
    print('No meals logged yet')
"

# 2. Verify JSON serializable
python -c "
import json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs
meals = get_meal_logs(last_n=1)
if meals:
    result = analyze_glucose(meals[-1]['meal_id'])
    json_str = json.dumps(result)   # will raise if not serializable
    print(f'JSON serializable: True ({len(json_str)} chars)')
"

# 3. Performance check
python -c "
import time, json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs
meals = get_meal_logs(last_n=1)
if meals:
    start = time.time()
    result = analyze_glucose(meals[-1]['meal_id'])
    elapsed = time.time() - start
    print(f'Pipeline time: {elapsed:.2f}s')
    print(f'Within 3s limit: {elapsed <= 3.0}')
"
```

**Dependencies:** GLUC-001 through GLUC-005 (all prior glucose tickets)

---

## Build Order Summary

| Sprint | Tickets | Goal |
|--------|---------|------|
| Sprint 7 | GLUC-001, GLUC-002 | Data ingestion & CGM store |
| Sprint 8 | GLUC-003 | Personal response model training |
| Sprint 9 | GLUC-004, GLUC-005 | Post-meal tracking & overlay |
| Sprint 10 | GLUC-006 | Integration gate |

> **Prerequisites before starting Sprint 7:**
> - FOOD-015 must be passing (analyze_meal returns clean output)
> - You need a Dexcom Clarity CSV export with at least 30 days of CGM history
> - You need at least 20 historical meals you can manually log to pair with CGM data
>
> **GLUC-006 is the go/no-go gate** for glucose features in the mobile app.
> Clean output from `analyze_glucose()` = glucose layer complete.

---

## A Note on Training Data Bootstrap

Before GLUC-003 is useful, you need paired meal + CGM data. Here's the fastest
way to bootstrap:

1. Export your Dexcom Clarity history as CSV (Settings → Export Data in the app)
2. Import via GLUC-002
3. Manually log 20-30 past meals you remember eating using `save_meal_log()` —
   approximate timestamps and macros are fine to start
4. Run `backfill_cgm_windows()` (GLUC-004) to automatically pair them with your
   CGM history
5. Train the model (GLUC-003) — even imperfect historical meal data gives the
   model a meaningful starting point

The model improves significantly with each new meal logged through the full
pipeline going forward.
