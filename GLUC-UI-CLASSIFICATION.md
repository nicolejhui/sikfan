# SikFan — Glucose UI Classification Layer

> **Context:** These changes are required before starting App Epics (Epic 8+).
> They add a UI-facing classification ("spike" / "steady" / "drop") on top of
> the existing Gaussian Process Regressor, without touching the model itself.
>
> **Gate:** All three tickets must be complete and GLUC-006 verification
> re-run before the `analyze_glucose()` schema is frozen for Epic 8.
>
> **Prerequisites:** FOOD-015 ✅, GLUC-001 through GLUC-006 ✅

---

## GLUC-007 — Add glucose outcome classification to model pipeline

**Goal**
Add a `classify_glucose_outcome()` function that translates the GPR-predicted BG
curve into a simple three-state UI label: `spike`, `steady`, or `drop`. The GPR
is untouched — this is a pure post-processing step.

**Acceptance Criteria**
- [ ] `classify_glucose_outcome(curve, pre_meal_glucose)` added to `pipeline/glucose_model.py`
- [ ] Returns `label` of exactly `"spike"`, `"steady"`, or `"drop"` — no other values
- [ ] Thresholds are read from `config.yaml`, not hardcoded
- [ ] `delta_from_baseline` is positive for spike, negative for drop
- [ ] All three labels are covered by unit tests with a mocked curve

**Implementation Technical Spec**

**File to modify:** `pipeline/glucose_model.py`

**Config to add** (`config.yaml`):
```yaml
glucose_classification:
  spike_threshold_mgdl: 40    # predicted peak delta above pre-meal BG → spike
  drop_threshold_mgdl: 20     # predicted peak delta below pre-meal BG → drop
```

**Function Signature:**
```python
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
```

**Verification Commands:**
```bash
# 1. Smoke test all three labels
python -c "
from pipeline.glucose_model import classify_glucose_outcome

base = 110

spike_curve = [{'minutes': i*5, 'predicted_bg': 110 + i*4, 'confidence_lower': 0, 'confidence_upper': 0} for i in range(37)]
drop_curve  = [{'minutes': i*5, 'predicted_bg': 110 - i*2, 'confidence_lower': 0, 'confidence_upper': 0} for i in range(37)]
flat_curve  = [{'minutes': i*5, 'predicted_bg': 115,        'confidence_lower': 0, 'confidence_upper': 0} for i in range(37)]

for label, curve in [('spike', spike_curve), ('drop', drop_curve), ('steady', flat_curve)]:
    result = classify_glucose_outcome(curve, base)
    status = '✅' if result['label'] == label else '❌'
    print(f'{status} Expected {label}, got: {result}')
"

# 2. Verify thresholds load from config
python -c "
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f)
keys = cfg.get('glucose_classification', {})
assert 'spike_threshold_mgdl' in keys, 'Missing spike_threshold_mgdl'
assert 'drop_threshold_mgdl'  in keys, 'Missing drop_threshold_mgdl'
print('config.yaml thresholds: OK')
print(f'  spike >= +{keys[\"spike_threshold_mgdl\"]} mg/dL')
print(f'  drop  <= -{keys[\"drop_threshold_mgdl\"]} mg/dL')
"
```

**Dependencies:** GLUC-003
---

## GLUC-008 — Add outcome field to analyze_glucose() response schema

**Goal**
Embed the UI classification from GLUC-007 into the `analyze_glucose()` return
dict so the API layer (Epic 8) surfaces it without any additional calls.

**Acceptance Criteria**
- [ ] `analyze_glucose()` in `glucose_analysis.py` calls `classify_glucose_outcome()` internally
- [ ] Return dict includes `prediction.outcome` with `label`, `predicted_peak_bg`, `delta_from_baseline`
- [ ] `model_confidence` from GPR is passed through into `prediction.outcome.confidence`
- [ ] Output remains fully JSON-serializable (existing serialization check still passes)
- [ ] Pipeline runtime still completes in <= 3 seconds

**Implementation Technical Spec**

**File to modify:** `glucose_analysis.py`

**Updated return schema** (additions marked `← NEW`):
```python
{
    "meal_id": str,
    "meal_timestamp": str,
    "dishes": list,
    "total_carbs_g": float,
    "pre_meal_glucose": int,
    "pre_meal_trend": str,
    "prediction": {
        "curve": [...],
        "predicted_peak_bg": float,
        "predicted_time_to_peak_minutes": int,
        "model_confidence": str,
        "outcome": {                        # ← NEW
            "label": "spike" | "steady" | "drop",
            "confidence": str,              # passes through model_confidence
            "predicted_peak_bg": float,
            "delta_from_baseline": float
        }
    },
    "actuals": { ... } | None,
    "retrain_triggered": bool
}
```

**Verification Commands:**
```bash
# 1. Confirm outcome key is present and valid
python -c "
import json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs

meals = get_meal_logs(last_n=1)
if meals:
    result = analyze_glucose(meals[-1]['meal_id'])
    outcome = result['prediction']['outcome']
    assert outcome['label'] in ('spike', 'steady', 'drop'), f'Invalid label: {outcome[\"label\"]}'
    assert 'confidence' in outcome
    assert 'predicted_peak_bg' in outcome
    assert 'delta_from_baseline' in outcome
    print(f'outcome.label:             {outcome[\"label\"]}')
    print(f'outcome.confidence:        {outcome[\"confidence\"]}')
    print(f'outcome.predicted_peak_bg: {outcome[\"predicted_peak_bg\"]}')
    print(f'outcome.delta_from_baseline: {outcome[\"delta_from_baseline\"]}')
    print('Schema: ✅')
else:
    print('No meals logged — add a meal log first')
"

# 2. JSON serialization check (regression — must still pass)
python -c "
import json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs
meals = get_meal_logs(last_n=1)
if meals:
    result = analyze_glucose(meals[-1]['meal_id'])
    s = json.dumps(result)
    print(f'JSON serializable: ✅ ({len(s)} chars)')
"

# 3. Performance check (regression — must still pass)
python -c "
import time
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs
meals = get_meal_logs(last_n=1)
if meals:
    start = time.time()
    analyze_glucose(meals[-1]['meal_id'])
    elapsed = time.time() - start
    status = '✅' if elapsed <= 3.0 else '❌'
    print(f'{status} Pipeline time: {elapsed:.2f}s (limit: 3.0s)')
"
```

**Dependencies:** GLUC-007

---

## GLUC-009 — Freeze analyze_glucose() schema and update CLAUDE.md

**Goal**
Formally document the final frozen schema, update `CLAUDE.md` to reflect the
current sprint, and confirm the gate for Epic 8 is clear.

**Acceptance Criteria**
- [ ] `CLAUDE.md` updated: current sprint set to Epic 8 (Local API Layer)
- [ ] `CLAUDE.md` documents the frozen `analyze_glucose()` output schema (outcome field included)
- [ ] `CLAUDE.md` documents the frozen `analyze_meal()` output schema (already stable)
- [ ] A note added that schema changes after this point require API versioning
- [ ] Final GLUC-006 end-to-end verification re-run and confirmed passing with outcome field present

**Files to modify:**
- `CLAUDE.md` — sprint status + frozen schemas section

**CLAUDE.md additions:**
```markdown
## Current Sprint
Epic 8 — Local API Layer (FastAPI wrapper around analyze_meal + analyze_glucose)

## Frozen API Schemas (do not change without versioning)

### analyze_meal(image_path) → dict
Stable since FOOD-015. See TICKETS.md for full schema.

### analyze_glucose(meal_id) → dict
Stable since GLUC-009. Key fields:
- prediction.outcome.label: "spike" | "steady" | "drop"
- prediction.outcome.confidence: "high" | "medium" | "low"
- prediction.outcome.predicted_peak_bg: float
- prediction.outcome.delta_from_baseline: float
- prediction.curve: full GPR curve at 5-min intervals (kept for detail screens)

Schema changes after this point require incrementing the API version in Epic 8.
```

**Verification Commands:**
```bash
# Final gate check — run before marking GLUC-009 done
python -c "
import json
from glucose_analysis import analyze_glucose
from pipeline.glucose_store import get_meal_logs

meals = get_meal_logs(last_n=1)
assert meals, 'No meal logs found'

result = analyze_glucose(meals[-1]['meal_id'])

# Gate checks
assert 'prediction' in result
assert 'outcome' in result['prediction']
assert result['prediction']['outcome']['label'] in ('spike', 'steady', 'drop')
assert 'curve' in result['prediction']           # GPR curve still present
assert 'actuals' in result                        # actuals key still present
json.dumps(result)                                # serializable

print('✅ GLUC-009 gate passed — schema frozen, Epic 8 can begin')
print(f'   outcome.label:      {result[\"prediction\"][\"outcome\"][\"label\"]}')
print(f'   outcome.confidence: {result[\"prediction\"][\"outcome\"][\"confidence\"]}')
"
```

**Dependencies:** GLUC-008
---

## Build Order

| Ticket | File(s) Changed | Blocking |
|--------|----------------|---------|
| GLUC-007 | `pipeline/glucose_model.py`, `config.yaml` | GLUC-008 |
| GLUC-008 | `glucose_analysis.py` | GLUC-009 |
| GLUC-009 | `CLAUDE.md` | Epic 8 start |

> **After GLUC-009 passes:** `analyze_glucose()` schema is frozen.
> Epic 8 ticket specs can now reference `prediction.outcome.label` directly
> as the primary UI signal. The full GPR curve remains in `prediction.curve`
> for the BG detail screen.
