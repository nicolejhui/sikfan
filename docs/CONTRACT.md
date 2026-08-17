# SikFan API Contract — Epic 8

Base URL: `http://localhost:8000` (or `EXPO_PUBLIC_API_URL`)
Auth: `X-API-Key: <key>` header required on all routes except `/health`
Docs: `http://localhost:8000/docs`

---

## Async meal flow (important)

```
1. POST /analyze-meal        → {meal_id, status: "pending"}
2. GET  /meal-status/{meal_id}  (poll every 2s, timeout at 60s)
3. on status == "complete"   → result contains full MealResult; stop polling
4. POST /log-meal            → {meal_id, logged: true, meal_timestamp}
5. GET  /glucose/{meal_id}   → GlucoseResponse (called by 5-min polling loop)
```

---

## Endpoints

### GET /health
No auth required.

**Response 200**
```json
{
  "status": "ok",
  "models_loaded": true,
  "chromadb_ready": true
}
```

**Response 503** — one or both subsystems failed to load; body is the same shape with `"status": "degraded"`.

---

### POST /analyze-meal
Upload a meal image. Returns immediately; analysis runs in the background.

**Request** — `multipart/form-data`
- `file` (required): JPEG or PNG image; do NOT set `Content-Type` manually — let fetch set the multipart boundary

**Response 200**
```json
{
  "meal_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending"
}
```

**Errors**
| Code | Body `code` | Meaning |
|------|-------------|---------|
| 400 | `invalid_file_type` | File is not JPEG or PNG |
| 401 | `invalid_api_key` | Missing or invalid `X-API-Key` |

---

### GET /meal-status/{meal_id}
Poll for the result of an analyze-meal job. Pure disk read — safe to call frequently.

**Response 200**
```json
{
  "meal_id": "550e8400-...",
  "status": "complete",
  "result": {
    "meal_id": "550e8400-...",
    "dishes": [
      {
        "crop_id": "crop_0",
        "name": "braised_beef_noodle",
        "confidence": 0.93,
        "status": "CONFIDENT",
        "carbs_g": 68.0,
        "protein_g": 24.0,
        "fat_g": 12.0,
        "calories": 480.0
      }
    ],
    "total_carbs_g": 68.0,
    "image_url": "/meal-image/550e8400-..."
  },
  "error": null
}
```

`status` values: `"pending"` | `"processing"` | `"complete"` | `"failed"`
`result` is `null` until `status == "complete"`.
`error` is `null` on success; string message on `"failed"`.

**DishResult fields**
| Field | Type | Notes |
|-------|------|-------|
| `crop_id` | string | `"crop_0"`, `"crop_1"`, ... — use this for `/confirm-dish` |
| `name` | string | Normalized dish name |
| `confidence` | float | 0.0–1.0 |
| `status` | string | `"CONFIDENT"` \| `"UNCERTAIN"` \| `"UNKNOWN"` |
| `carbs_g` | float | |
| `protein_g` | float | |
| `fat_g` | float | |
| `calories` | float | |

**mixed_bowl crops** — components inside a bowl are given IDs like `"crop_1_0"`, `"crop_1_1"`. The parent segment image (`crop_1.jpg`) is shared; do not delete it until all components are confirmed.

**Errors**
| Code | Body `code` | Meaning |
|------|-------------|---------|
| 404 | `meal_not_found` | `meal_id` not recognized |

---

### POST /log-meal
Call after `pollMealStatus` returns `status: "complete"`. Records the meal for the glucose pipeline.

**Request body**
```json
{
  "meal_id": "550e8400-...",
  "confirmed_dishes": ["braised_beef_noodle"]
}
```
`confirmed_dishes` must be non-empty and match dish `name` values from `MealResult.dishes`.

**Response 200**
```json
{
  "meal_id": "550e8400-...",
  "logged": true,
  "meal_timestamp": "2026-06-20T14:32:00+00:00",
  "macros_incomplete": false,
  "unresolved_dishes": []
}
```
`macros_incomplete` (GLUC-012) is `true` when one or more `confirmed_dishes`
had `needs_macro_entry: true` in the analysis result — i.e. USDA had no match
and that dish's macros are `0.0`, not verified-zero. `unresolved_dishes` lists
those dish names. The meal is still logged either way; flagged rows are
excluded from `train_model()`'s training corpus so a missing macro match
can't silently teach the glucose model that a high-carb meal caused no rise.

**Errors**
| Code | Body `code` | Meaning |
|------|-------------|---------|
| 400 | `no_dishes` | `confirmed_dishes` is empty |
| 404 | `meal_not_found` | `meal_id` not recognized |
| 409 | `analysis_not_complete` | Job still pending or processing |
| 409 | `already_logged` | Meal was already logged |

---

### GET /glucose/{meal_id}
Returns the full glucose prediction (and actuals once CGM data arrives) for a logged meal.

**Response 200**
```json
{
  "meal_id": "550e8400-...",
  "meal_timestamp": "2026-06-20T14:32:00+00:00",
  "dishes": [
    { "name": "braised_beef_noodle", "carbs_g": 68.0 }
  ],
  "total_carbs_g": 68.0,
  "pre_meal_glucose": 94,
  "pre_meal_trend": "flat",
  "prediction": {
    "curve": [
      {
        "minutes": 0,
        "predicted_bg": 94.0,
        "confidence_lower": 88.0,
        "confidence_upper": 100.0
      }
    ],
    "predicted_peak_bg": 142.0,
    "predicted_time_to_peak_minutes": 45,
    "model_confidence": "high",
    "outcome": {
      "label": "spike",
      "confidence": "high",
      "predicted_peak_bg": 142.0,
      "delta_from_baseline": 48.0
    }
  },
  "actuals": null,
  "retrain_triggered": false,
  "image_url": "/meal-image/550e8400-..."
}
```

`actuals` is `null` until CGM readings arrive. Once populated:
```json
"actuals": {
  "curve": [
    { "minutes": 30, "glucose_mgdl": 128, "timestamp": "2026-06-20T15:02:00+00:00" }
  ],
  "actual_peak_bg": 138.0,
  "time_to_peak_minutes": 50,
  "tir_ratio": 0.87,
  "mard": 4.2,
  "chart_path": "data/glucose/charts/550e8400-....png"
}
```

`model_confidence` / `outcome.confidence`: `"high"` | `"medium"` | `"low"`
`outcome.label`: `"spike"` | `"steady"` | `"drop"`

**Errors**
| Code | Body `code` | Meaning |
|------|-------------|---------|
| 404 | `meal_not_found` | Meal not logged |
| 422 | `no_pre_meal_glucose` | No CGM reading near meal time |
| 422 | `glucose_model_not_ready` | Model not ready |
| 503 | `glucose_model_error` | Server-side prediction failure |

---

### POST /confirm-dish
Send user feedback for a detected crop. Updates ChromaDB centroid and appends to `correction_log.jsonl`.

**Request body**
```json
{
  "meal_id": "550e8400-...",
  "crop_id": "crop_0",
  "action": "CONFIRM",
  "corrected_label": null
}
```

`action` values:
- `"CONFIRM"` — user agrees with the detection; `corrected_label` can be omitted
- `"CORRECT"` — dish was wrong; `corrected_label` required
- `"ADD_NEW"` — dish is not yet in the store; `corrected_label` required

**Response 200**
```json
{
  "crop_id": "crop_0",
  "action": "CONFIRM",
  "updated_label": "braised_beef_noodle",
  "chromadb_updated": true
}
```

`chromadb_updated: false` if the crop file was missing (mixed_bowl component or server restart) — the correction is still logged.

**Errors**
| Code | Body `code` | Meaning |
|------|-------------|---------|
| 400 | `missing_corrected_label` | `corrected_label` required for CORRECT/ADD_NEW |
| 404 | `meal_not_found` | `meal_id` not recognized |
| 404 | `crop_not_found` | `crop_id` not found in that meal's result |

---

### GET /meal-image/{meal_id}
Returns the original meal JPEG. Requires auth — do not pass this URL directly to `<Image>` on native (no custom header support). Fetch it as a blob and use a local URI instead.

**Response 200** — `image/jpeg` binary
**Response 404** — `{ "code": "meal_not_found", "message": "..." }`

---

## Error envelope
All non-2xx responses return:
```json
{
  "detail": {
    "code": "snake_case_error_code",
    "message": "Human readable description."
  }
}
```
Exception: FastAPI's built-in 422 validation errors use its default envelope.

---

## Type reference

### MealResult
```typescript
{
  meal_id: string
  dishes: DishResult[]
  total_carbs_g: number
  image_url: string | null   // null for pre-API-009 status files
}
```

### DishResult
```typescript
{
  crop_id: string            // "crop_0", "crop_1_0", etc.
  name: string
  confidence: number
  status: "CONFIDENT" | "UNCERTAIN" | "UNKNOWN"
  carbs_g: number
  protein_g: number
  fat_g: number
  calories: number
}
```

### JobStatusResponse
```typescript
{
  meal_id: string
  status: "pending" | "processing" | "complete" | "failed"
  result: MealResult | null
  error: string | null
}
```

### GlucoseResponse
```typescript
{
  meal_id: string
  meal_timestamp: string     // ISO 8601
  dishes: { name: string; carbs_g: number }[]
  total_carbs_g: number
  pre_meal_glucose: number   // mg/dL
  pre_meal_trend: string
  prediction: GlucosePrediction
  actuals: GlucoseActuals | null
  retrain_triggered: boolean
  image_url: string
}
```

### GlucosePrediction
```typescript
{
  curve: { minutes: number; predicted_bg: number; confidence_lower: number; confidence_upper: number }[]
  predicted_peak_bg: number
  predicted_time_to_peak_minutes: number
  model_confidence: "high" | "medium" | "low"
  outcome: {
    label: "spike" | "steady" | "drop"
    confidence: "high" | "medium" | "low"
    predicted_peak_bg: number
    delta_from_baseline: number
  }
}
```

### GlucoseActuals
```typescript
{
  curve: { minutes: number; glucose_mgdl: number; timestamp: string }[]
  actual_peak_bg: number
  time_to_peak_minutes: number
  tir_ratio: number
  mard: number
  chart_path: string
}
```
