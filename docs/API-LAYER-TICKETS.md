# Epic 8 — API Layer Tickets

## Context

FOOD-015 and GLUC-009 are complete. `analyze_meal()` and `analyze_glucose()` schemas are
frozen. Epic 8 wraps them in a FastAPI service deployed to Fly.io so the iOS app (Epic 9)
has stable HTTP endpoints to call from anywhere — not just on home WiFi.

**Locked decisions:**
- FastAPI on Fly.io from day one. Dev runs locally but all tickets assume cloud deployment.
- Auth: static `X-API-Key` header checked in a FastAPI dependency. HTTPS free via Fly.io.
- Images uploaded as multipart form (not base64).
- `POST /analyze-meal` is async: returns `meal_id` immediately. Results polled via `GET /meal-status/{meal_id}`.
- `POST /log-meal` is sync (fast write, no polling needed).

**Gate:** All endpoints pass curl smoke tests against the live Fly.io URL.

---

## Error Handling Matrix

**Standard error envelope** (all error responses across all endpoints):
```json
{
  "detail": {
    "code": "snake_case_error_code",
    "message": "Human-readable string shown to user or logged by app"
  }
}
```
FastAPI raises `HTTPException(status_code=..., detail={"code": ..., "message": ...})`.
The mobile app switches on `detail.code` for logic and displays `detail.message` for UX.

### 400 Bad Request — invalid input, client error

| Endpoint | `code` | `message` |
|---|---|---|
| `POST /analyze-meal` | `invalid_file_type` | `"Only JPEG and PNG images are accepted."` |
| `POST /log-meal` | `no_dishes` | `"confirmed_dishes cannot be empty."` |
| `POST /confirm-dish` | `missing_corrected_label` | `"corrected_label is required for CORRECT and ADD_NEW actions."` |
| `POST /confirm-dish` | _(Pydantic 422, not custom)_ | Invalid `action` value rejected automatically by `Literal` type before route logic runs |

### 401 Unauthorized — missing or wrong API key

| `code` | `message` |
|---|---|
| `invalid_api_key` | `"Invalid or missing API key."` |

### 404 Not Found — resource does not exist

| Endpoint | `code` | `message` |
|---|---|---|
| `GET /meal-status/{meal_id}` | `meal_not_found` | `"No analysis job found for this meal ID."` |
| `POST /log-meal` | `meal_not_found` | `"No analysis job found for this meal ID. Run /analyze-meal first."` |
| `GET /glucose/{meal_id}` | `meal_not_found` | `"No logged meal found with this ID."` |
| `POST /confirm-dish` | `crop_not_found` | `"No crop found with this crop ID for the given meal."` |

### 409 Conflict — duplicate or premature operation

| Endpoint | `code` | `message` |
|---|---|---|
| `POST /log-meal` | `analysis_not_complete` | `"Meal analysis is still in progress. Wait for status: complete before logging."` |
| `POST /log-meal` | `already_logged` | `"This meal has already been logged."` |

### 422 Unprocessable Entity — data precondition not met (not a server error)

| Endpoint | `code` | `message` |
|---|---|---|
| `GET /glucose/{meal_id}` | `no_pre_meal_glucose` | `"No CGM reading found near this meal time. Enter a manual pre-meal BG to enable glucose prediction."` |

### 503 Service Unavailable — model or runtime failure

| Endpoint | `code` | `message` |
|---|---|---|
| `GET /health` | `models_not_loaded` | `"One or more models failed to load. Check server logs."` |
| `GET /glucose/{meal_id}` | `glucose_model_error` | `"Glucose prediction failed due to a server error. Try again shortly."` |

Note: `POST /analyze-meal` failures surface via the job status file (`status: "failed"`,
`error: "<message>"`), not as a direct HTTP error code, because the work runs in the background.

---

## API-001 — Project scaffold: FastAPI app skeleton, Pydantic models, auth, GET /health

### Goal
Stand up the FastAPI application structure with shared auth dependency, all Pydantic
request/response models, and a working `GET /health` endpoint that confirms models are loaded.
No business logic yet — just the skeleton every other ticket builds on.

### Acceptance Criteria
- [ ] `api.py` exists at project root with a FastAPI app instance
- [ ] `GET /health` returns `{"status": "ok", "models_loaded": true, "chromadb_ready": true}` with HTTP 200
- [ ] `GET /health` returns HTTP 503 if FastSAM/CLIP fail to load on startup
- [ ] `GET /health` returns HTTP 503 if ChromaDB fails to connect on startup (`chromadb_ready: false`)
- [ ] Both `models_loaded` and `chromadb_ready` are set once at startup — not re-probed on every request
- [ ] All requests without a valid `X-API-Key` header return HTTP 401
- [ ] Auth dependency accepts either `API_KEY` or `API_KEY_PREV` (if set) — both valid during rotation
- [ ] `API_KEY_PREV` is optional; if unset, only `API_KEY` is checked
- [ ] `GET /health` is exempt from auth (Fly.io health checks hit it without headers)
- [ ] `requirements-api.txt` lists all FastAPI dependencies (fastapi, uvicorn[standard], python-multipart)
- [ ] All Pydantic models for every endpoint are defined in `api.py` even if their routes aren't implemented yet

### Files to create/modify
- `api.py` — main FastAPI app (create)
- `requirements-api.txt` — API-specific dependencies (create)

### Pydantic models (define all upfront)

```python
from pydantic import BaseModel
from typing import Literal

# GET /health
class HealthResponse(BaseModel):
    status: str          # "ok" | "degraded"
    models_loaded: bool
    chromadb_ready: bool

# POST /analyze-meal
class AnalyzeMealResponse(BaseModel):
    meal_id: str
    status: str          # always "pending" at submission time

# GET /meal-status/{meal_id}
class DishResult(BaseModel):
    crop_id: str         # synthetic key assigned by API-002: "crop_0", "crop_1", ...
    name: str
    confidence: float
    status: str          # "CONFIDENT" | "UNCERTAIN" | "UNKNOWN"
    carbs_g: float
    protein_g: float
    fat_g: float
    calories: float

class MealResult(BaseModel):
    meal_id: str
    dishes: list[DishResult]
    total_carbs_g: float

class JobStatusResponse(BaseModel):
    meal_id: str
    status: str          # "pending" | "processing" | "complete" | "failed"
    result: MealResult | None = None
    error: str | None = None

# POST /log-meal
class LogMealRequest(BaseModel):
    meal_id: str
    confirmed_dishes: list[str]

class LogMealResponse(BaseModel):
    meal_id: str
    logged: bool
    meal_timestamp: str

# GET /glucose/{meal_id}
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
    label: str           # "spike" | "steady" | "drop"
    confidence: str      # "high" | "medium" | "low"
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

# Stub — confirm exact field names from analyze_glucose() output in API-005 before finalising
class GlucoseDishEntry(BaseModel):
    name: str
    carbs_g: float

class GlucoseResponse(BaseModel):
    meal_id: str
    meal_timestamp: str
    dishes: list[GlucoseDishEntry]   # separate model — DishResult requires crop_id which glucose dishes don't have
    total_carbs_g: float
    pre_meal_glucose: int
    pre_meal_trend: str
    prediction: GlucosePrediction
    actuals: GlucoseActuals | None
    retrain_triggered: bool

# POST /confirm-dish
class ConfirmDishRequest(BaseModel):
    meal_id: str
    crop_id: str
    action: Literal["CONFIRM", "CORRECT", "ADD_NEW"]   # invalid values rejected at Pydantic layer
    corrected_label: str | None = None

class ConfirmDishResponse(BaseModel):
    crop_id: str
    action: Literal["CONFIRM", "CORRECT", "ADD_NEW"]
    updated_label: str
    chromadb_updated: bool
```

### Auth dependency implementation note

```python
import os
from fastapi import Header, HTTPException

def verify_api_key(x_api_key: str = Header(...)) -> None:
    valid_keys = {k for k in [os.getenv("API_KEY"), os.getenv("API_KEY_PREV")] if k}
    if x_api_key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_api_key", "message": "Invalid or missing API key."}
        )
```
`API_KEY_PREV` being absent (or `None`) is safe — the set comprehension filters out falsy values,
so an unset `API_KEY_PREV` does not create an empty-string backdoor.

### Verification Blueprint

**Case 1 — health check (no auth)**
```bash
curl -s https://<app>.fly.dev/health
```
Expected HTTP status: `200`
Expected response schema:
```json
{"status": "ok", "models_loaded": true, "chromadb_ready": true}
```
Side-effects: none.

**Case 2 — auth rejection**
```bash
curl -s -o /dev/null -w "%{http_code}" https://<app>.fly.dev/glucose/test-id
```
Expected HTTP status: `401`
Expected response schema:
```json
{"detail": {"code": "invalid_api_key", "message": "Invalid or missing API key."}}
```
Side-effects: none.

**Case 3 — health check degraded (models failed)**
Trigger by temporarily renaming `FastSAM-s.pt`, then starting the server.
Expected HTTP status: `503`
Expected response schema:
```json
{"status": "degraded", "models_loaded": false, "chromadb_ready": true}
```
Side-effects: none.

**Case 4 — health check degraded (ChromaDB failed)**
Trigger by temporarily renaming `data/embeddings/` to `data/embeddings_bak/`, then starting the server.
Expected HTTP status: `503`
Expected response schema:
```json
{"status": "degraded", "models_loaded": true, "chromadb_ready": false}
```
Side-effects: none. Rename `data/embeddings_bak/` back after testing.

### Fly.io notes
- `GET /health` must be unauthenticated — Fly.io's built-in health check hits it without headers
- `API_KEY` will be set as a Fly.io secret (`fly secrets set API_KEY=...`) in API-007

---

## API-002 — POST /analyze-meal (async multipart upload)

### Goal
Accept a JPEG/PNG meal image via multipart form upload. Immediately return a `meal_id`
and `"pending"` status. Run `analyze_meal()` in a background thread. Write job status
to disk so `GET /meal-status` can poll it.

### Acceptance Criteria
- [ ] `POST /analyze-meal` accepts `file` field (multipart/form-data, JPEG or PNG)
- [ ] Returns `{"meal_id": "<uuid>", "status": "pending"}` within 200ms of receiving the file
- [ ] `analyze_meal()` runs in a background thread (not blocking the event loop)
- [ ] Job status written to `data/job_status/{meal_id}.json` at each state transition: `pending` → `processing` → `complete` | `failed`
- [ ] Uploaded image saved to `data/uploads/{meal_id}.jpg` for `analyze_meal()` to consume
- [ ] HTTP 400 if uploaded file is not image/jpeg or image/png
- [ ] HTTP 401 if `X-API-Key` missing or wrong

### Files to create/modify
- `api.py` — add `POST /analyze-meal` route
- `data/job_status/` — created on first run (`mkdir exist_ok`)
- `data/uploads/` — created on first run

### Status file schema (`data/job_status/{meal_id}.json`)
```json
{
  "meal_id": "uuid",
  "status": "pending | processing | complete | failed",
  "logged": false,
  "created_at": "ISO8601",
  "completed_at": "ISO8601 | null",
  "result": { "...MealResult..." },
  "error": "string | null"
}
```

### Implementation notes
- Use `fastapi.BackgroundTasks` to schedule the job after returning the response
- Wrap `analyze_meal()` in `asyncio.get_event_loop().run_in_executor(None, ...)` to avoid blocking the event loop (FastSAM + CLIP are CPU/MPS heavy)
- `meal_id` = `str(uuid.uuid4())`
- Write status file to disk (not in-memory) so state survives server restarts
- `analyze_meal()` does not return per-crop IDs. When building `MealResult`, assign synthetic `crop_id` values by index: `f"crop_{i}"` for each entry in `detected_items`. These IDs are written into the job_status file and are the handle API-006 uses to look up which dish to confirm.

### State Lifecycle

**`data/uploads/{meal_id}.jpg`**

| Action | Who | When |
|---|---|---|
| Create | Route handler | Immediately, before returning 200 |
| Read | Background task (`analyze_meal()`) | Once, during analysis |
| Delete | Background task | After `analyze_meal()` completes (success or failure) — in a `finally` block |

- Only one writer and one reader per file (distinct `meal_id`). No concurrent access risk.

**`data/job_status/{meal_id}.json`**

| Action | Who | When |
|---|---|---|
| Create | Route handler | Before returning 200 (`status: "pending"`, `logged: false`) |
| Update → "processing" | Background task | Immediately when task starts |
| Update → "complete"/"failed" | Background task | When `analyze_meal()` finishes |
| Read | `GET /meal-status`, `POST /log-meal` | On request |
| Update `logged: true` | `POST /log-meal` | On successful log |
| Delete | Never | Keep as audit trail (~1KB/meal) |

**Race condition: concurrent `POST /log-meal` for same `meal_id`**
Mitigation: write job_status updates atomically — write to `{meal_id}.tmp`, then `os.replace(tmp, target)`.
`os.replace` is atomic on POSIX.

**Race condition: background task writes while `POST /log-meal` reads**
Mitigation: `POST /log-meal` validates `status == "complete"` before proceeding.
The background task never touches the file again once `status: "complete"` is set.

### Verification Blueprint

**Case 1 — successful submission**
```bash
curl -s -X POST https://<app>.fly.dev/analyze-meal \
  -H "X-API-Key: $API_KEY" \
  -F "file=@data/assorted_breakfast.jpeg"
```
Expected HTTP status: `200`
Expected response schema:
```json
{"meal_id": "<uuid-v4>", "status": "pending"}
```
Side-effects to verify:
- `data/uploads/{meal_id}.jpg` exists immediately after the response
- `data/job_status/{meal_id}.json` exists with `{"status": "pending", "logged": false}`

**Case 2 — invalid file type**
```bash
curl -s -X POST https://<app>.fly.dev/analyze-meal \
  -H "X-API-Key: $API_KEY" \
  -F "file=@README.md"
```
Expected HTTP status: `400`
Expected response schema:
```json
{"detail": {"code": "invalid_file_type", "message": "Only JPEG and PNG images are accepted."}}
```
Side-effects: no files created in `data/uploads/` or `data/job_status/`.

### Fly.io notes
- `data/uploads/` and `data/job_status/` are transient — they do not need to survive deploys.
- `meal_logs.json` and `correction_log.jsonl` are **not transient** — they are the glucose pipeline's source of truth and the feedback record respectively. Both are protected by the Fly Volume mounted in API-007.

---

## API-003 — GET /meal-status/{meal_id} (polling endpoint)

### Goal
Allow the mobile app to poll for the result of an async `analyze-meal` job.
Returns current status and the full result once complete.

### Acceptance Criteria
- [ ] `GET /meal-status/{meal_id}` reads `data/job_status/{meal_id}.json`
- [ ] Returns `JobStatusResponse` with `status` of `pending | processing | complete | failed`
- [ ] When `status == "complete"`, `result` contains the full `MealResult`
- [ ] When `status == "failed"`, `error` contains a human-readable error string
- [ ] HTTP 404 if `meal_id` not found (no status file exists)
- [ ] HTTP 401 if auth missing
- [ ] Response time < 50ms (pure disk read, no model inference)

### Files to create/modify
- `api.py` — add `GET /meal-status/{meal_id}` route

### State Lifecycle

**`data/job_status/{meal_id}.json`**

| Action | Who | When |
|---|---|---|
| Read | `GET /meal-status/{meal_id}` | On every poll |

Pure reader. Concurrent polls for the same `meal_id` are safe. If file does not exist, return HTTP 404 immediately.

### Polling contract (for mobile app)
```
Poll interval: every 2 seconds
Timeout: stop polling after 60 seconds, show error
Terminal states: "complete" or "failed" — stop polling
```

### Verification Blueprint

**Case 1 — pending state**
```bash
curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/meal-status/$MEAL_ID
```
Expected HTTP status: `200`
Expected response schema:
```json
{"meal_id": "<uuid>", "status": "pending", "result": null, "error": null}
```
Side-effects: none (read-only).

**Case 2 — complete state**
Expected HTTP status: `200`
Expected response schema:
```json
{
  "meal_id": "<uuid>",
  "status": "complete",
  "result": {
    "meal_id": "<uuid>",
    "dishes": [
      {"name": "<string>", "confidence": "<float>", "status": "<string>",
       "carbs_g": "<float>", "protein_g": "<float>", "fat_g": "<float>", "calories": "<float>"}
    ],
    "total_carbs_g": "<float>"
  },
  "error": null
}
```
Side-effects to verify:
- `data/job_status/{meal_id}.json` has `status: "complete"`
- `data/uploads/{meal_id}.jpg` is **deleted** (cleaned up by background task)

**Case 3 — not found**
```bash
curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/meal-status/does-not-exist
```
Expected HTTP status: `404`
Expected response schema:
```json
{"detail": {"code": "meal_not_found", "message": "No analysis job found for this meal ID."}}
```
Side-effects: none.

---

## API-004 — POST /log-meal (sync meal logging)

### Goal
Record a confirmed meal to `meal_logs.json` so the glucose pipeline can attach a
post-meal CGM window to it. Sync operation — mobile app waits for the write to confirm.

### Acceptance Criteria
- [ ] `POST /log-meal` accepts `LogMealRequest` JSON body
- [ ] Appends entry to `meal_logs.json` following the existing GLUC-001 schema (append-only, never scanned)
- [ ] Returns `{"meal_id": ..., "logged": true, "meal_timestamp": "<ISO8601>"}` on success
- [ ] HTTP 404 if `data/job_status/{meal_id}.json` does not exist
- [ ] HTTP 409 (`analysis_not_complete`) if job status is `"pending"` or `"processing"`
- [ ] HTTP 409 (`already_logged`) if job status file has `"logged": true`
- [ ] HTTP 400 if `confirmed_dishes` is empty
- [ ] Completes in < 200ms (no model calls, pure I/O)

### Duplicate detection strategy
**Never scan `meal_logs.json`** — that is O(n). Use the job_status file instead:
- API-002 writes `data/job_status/{meal_id}.json` with `"logged": false`
- `POST /log-meal` reads that single file to check for 404 and 409 — O(1)
- On success: append to `meal_logs.json`, then atomically flip `logged: true` in job_status
- `meal_logs.json` is append-only; the API never reads it

### State Lifecycle

**`data/job_status/{meal_id}.json`**

| Action | Who | When |
|---|---|---|
| Read | `POST /log-meal` | Validate: file exists, `status == "complete"`, `logged == false` |
| Atomic write | `POST /log-meal` | Flip `logged: true` via `os.replace(tmp, target)` after appending to `meal_logs.json` |

**`meal_logs.json`**

| Action | Who | When |
|---|---|---|
| Append | `POST /log-meal` | After all validation passes, before flipping `logged: true` |
| Read | Never by the API | Only consumed by `analyze_glucose()` and pipeline scripts |

Sequence of operations on success: (1) read job_status, (2) validate, (3) append to `meal_logs.json`, (4) atomic write job_status with `logged: true`. If step 4 fails after step 3, the meal is in `meal_logs.json` but `logged` is still `false` — a retry attempts another append; log this as a warning and return 200.

### Files to create/modify
- `api.py` — add `POST /log-meal` route; also update API-002's job_status write to include `"logged": false`
- `meal_logs.json` — appended to (existing file, GLUC-001 schema)

### Verification Blueprint

**Case 1 — successful log**
```bash
curl -s -X POST https://<app>.fly.dev/log-meal \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<uuid>","confirmed_dishes":["braised_beef_noodle","rice"]}'
```
Expected HTTP status: `200`
Expected response schema:
```json
{"meal_id": "<uuid>", "logged": true, "meal_timestamp": "<ISO8601>"}
```
Side-effects to verify:
- `meal_logs.json` last entry has `meal_id` matching submitted UUID
- `data/job_status/{meal_id}.json` has `"logged": true`

**Case 2 — duplicate log**
```bash
curl -s -X POST https://<app>.fly.dev/log-meal \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<same-uuid>","confirmed_dishes":["braised_beef_noodle"]}'
```
Expected HTTP status: `409`
Expected response schema:
```json
{"detail": {"code": "already_logged", "message": "This meal has already been logged."}}
```
Side-effects: `meal_logs.json` unchanged.

**Case 3 — analysis still in progress**
Expected HTTP status: `409`
Expected response schema:
```json
{"detail": {"code": "analysis_not_complete", "message": "Meal analysis is still in progress. Wait for status: complete before logging."}}
```
Side-effects: `meal_logs.json` unchanged.

---

## API-005 — GET /glucose/{meal_id}

### Goal
Expose the full `analyze_glucose()` output via HTTP. Returns the frozen GLUC-009
schema including prediction curve, outcome label, and actuals if available.

### Acceptance Criteria
- [ ] `GET /glucose/{meal_id}` calls `analyze_glucose(meal_id)` and returns `GlucoseResponse`
- [ ] Response exactly matches the frozen GLUC-009 schema (validated by Pydantic)
- [ ] `actuals` field is `null` when no post-meal CGM data exists yet — valid 200, not an error
- [ ] HTTP 404 if `analyze_glucose()` raises `MealNotFoundError` (or equivalent) — do not pre-read `meal_logs.json`; let `analyze_glucose()` signal the miss via exception
- [ ] HTTP 422 (`no_pre_meal_glucose`) if `analyze_glucose()` raises a missing-CGM-data error — data precondition failure, not a server error
- [ ] HTTP 503 (`glucose_model_error`) if `analyze_glucose()` raises any other unexpected exception
- [ ] HTTP 401 if auth missing

### Error state taxonomy
All non-200 outcomes are driven by exceptions raised by `analyze_glucose()` — the route never reads
`meal_logs.json` directly. Map exceptions to HTTP codes as follows:

| Scenario | Exception from `analyze_glucose()` | HTTP | Mobile app response |
|---|---|---|---|
| meal_id unknown | `MealNotFoundError` (or equivalent) | 404 | Should not happen if app submits valid IDs |
| No pre-meal BG reading | missing-CGM-data error | 422 (`no_pre_meal_glucose`) | Prompt user to enter manual BG |
| `actuals` is null | _(no exception — normal return)_ | 200 | Post-meal window not attached yet |
| Model/runtime crash | any other exception | 503 | Show generic error, retry later |

Before implementing, read `glucose_analysis.py` to confirm the actual exception type(s) raised for
the 404 and 422 cases, and update this table with the real class names.

### Files to create/modify
- `api.py` — add `GET /glucose/{meal_id}` route

### Implementation note on `dishes` field
The frozen GLUC-009 schema in CLAUDE.md types `dishes` as bare `list`. Before implementing,
read `glucose_analysis.py` to confirm the actual element shape returned by `analyze_glucose()`.
`DishResult` cannot be reused here — it requires `crop_id`, which glucose dishes don't have
(they come from logged meal data, not live inference crops). `GlucoseDishEntry` is stubbed in
API-001 with `name` and `carbs_g`; add or remove fields to match the actual output.

### Verification Blueprint

**Case 1 — successful response with actuals=null**
```bash
curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/<meal_id>
```
Expected HTTP status: `200`
Expected response schema (abbreviated):
```json
{
  "meal_id": "<uuid>",
  "meal_timestamp": "<ISO8601>",
  "dishes": [{"name": "<string>", "carbs_g": "<float>"}],
  "total_carbs_g": "<float>",
  "pre_meal_glucose": "<int>",
  "pre_meal_trend": "<string>",
  "prediction": {
    "curve": [{"minutes": "<int>", "predicted_bg": "<float>",
               "confidence_lower": "<float>", "confidence_upper": "<float>"}],
    "predicted_peak_bg": "<float>",
    "predicted_time_to_peak_minutes": "<int>",
    "model_confidence": "high|medium|low",
    "outcome": {
      "label": "spike|steady|drop",
      "confidence": "high|medium|low",
      "predicted_peak_bg": "<float>",
      "delta_from_baseline": "<float>"
    }
  },
  "actuals": null,
  "retrain_triggered": false
}
```
Side-effects: none (read-only).

**Case 2 — no pre-meal BG reading**
```bash
curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/<meal_id_no_cgm>
```
Expected HTTP status: `422`
Expected response schema:
```json
{"detail": {"code": "no_pre_meal_glucose", "message": "No CGM reading found near this meal time. Enter a manual pre-meal BG to enable glucose prediction."}}
```
Side-effects: none.

**Case 3 — unknown meal_id**
```bash
curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/bad-id
```
Expected HTTP status: `404`
Expected response schema:
```json
{"detail": {"code": "meal_not_found", "message": "No logged meal found with this ID."}}
```
Side-effects: none.

---

## API-006 — POST /confirm-dish

### Goal
Let the mobile app send a dish confirmation or correction back to the server.
Updates ChromaDB centroid (reuses FOOD-010 logic) and appends to `correction_log.jsonl`.

### Acceptance Criteria
- [ ] `POST /confirm-dish` accepts `ConfirmDishRequest` JSON body
- [ ] `action: "CONFIRM"` — increments confirmation count for the crop's predicted label in ChromaDB
- [ ] `action: "CORRECT"` — requires `corrected_label`; moves crop embedding to correct label
- [ ] `action: "ADD_NEW"` — requires `corrected_label`; creates new dish entry in ChromaDB
- [ ] Appends entry to `correction_log.jsonl` for all three actions (existing FOOD-010 format)
- [ ] Returns `ConfirmDishResponse` with `chromadb_updated: true` on success
- [ ] HTTP 400 if `action` is `"CORRECT"` or `"ADD_NEW"` and `corrected_label` is missing
- [ ] HTTP 404 if `crop_id` not found
- [ ] HTTP 401 if auth missing

### Concurrency notes — why no `fcntl` file locking is needed

**`correction_log.jsonl`:** This is append-only JSONL, not a single JSON object. Each line is
an independent record. On POSIX, `open(..., 'a')` + a single `write()` call is atomic for
payloads under `PIPE_BUF` (4096 bytes on Linux). A JSONL confirmation entry is ~150–200 bytes —
well under this limit. Interleaved bytes between two concurrent writes are not physically possible
at this size. Additionally, the API never reads `correction_log.jsonl` — only offline pipeline
scripts on the Mac do, so there is no concurrent read-during-write exposure at the API layer.

**ChromaDB:** `PersistentClient` uses SQLite as its backend. SQLite serializes concurrent writers
internally via its own WAL-mode locking. Adding `fcntl` on top of SQLite's managed files would be
redundant and could interfere with SQLite's own lock management.

**Practical concurrency:** This is a single-user personal app with one iOS client. Simultaneous
`POST /confirm-dish` requests from the same user are not a realistic scenario. The added complexity
of `fcntl` (or `portalocker` for cross-platform support) plus the deadlock risk if an exception
skips the unlock is not justified. Revisit if this ever becomes a multi-user service.

### crop_id lookup
`crop_id` values (`crop_0`, `crop_1`, ...) are synthetic keys written by API-002 into
`data/job_status/{meal_id}.json`. To resolve a `crop_id` → `dish_name`:
1. Read `data/job_status/{meal_id}.json`
2. Find the `DishResult` entry whose `crop_id` matches the request
3. Use its `dish_name` as the ChromaDB label to confirm/correct

HTTP 404 (`crop_not_found`) if no entry in the job_status file matches the given `crop_id`.

### Files to create/modify
- `api.py` — add `POST /confirm-dish` route
- Reuses existing ChromaDB update functions from `pipeline/` — do not duplicate logic

### Verification Blueprint

**Case 1 — CONFIRM action**
```bash
curl -s -X POST https://<app>.fly.dev/confirm-dish \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<uuid>","crop_id":"<crop_id>","action":"CONFIRM"}'
```
Expected HTTP status: `200`
Expected response schema:
```json
{"crop_id": "<crop_id>", "action": "CONFIRM", "updated_label": "<dish_name>", "chromadb_updated": true}
```
Side-effects to verify:
- `correction_log.jsonl` last line contains `"action": "CONFIRM"` and matching `crop_id`
- ChromaDB collection count unchanged (CONFIRM increments weight, not count)

**Case 2 — CORRECT action**
```bash
curl -s -X POST https://<app>.fly.dev/confirm-dish \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<uuid>","crop_id":"<crop_id>","action":"CORRECT","corrected_label":"mapo_tofu"}'
```
Expected HTTP status: `200`
Expected response schema:
```json
{"crop_id": "<crop_id>", "action": "CORRECT", "updated_label": "mapo_tofu", "chromadb_updated": true}
```
Side-effects to verify:
- `correction_log.jsonl` last line contains `"action": "CORRECT"` and `"corrected_label": "mapo_tofu"`
- ChromaDB entry for `crop_id` now associated with `mapo_tofu` centroid

**Case 3 — CORRECT with missing corrected_label**
```bash
curl -s -X POST https://<app>.fly.dev/confirm-dish \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<uuid>","crop_id":"<crop_id>","action":"CORRECT"}'
```
Expected HTTP status: `400`
Expected response schema:
```json
{"detail": {"code": "missing_corrected_label", "message": "corrected_label is required for CORRECT and ADD_NEW actions."}}
```
Side-effects: `correction_log.jsonl` unchanged, ChromaDB unchanged.

---

## API-007 — Fly.io configuration (Dockerfile, fly.toml, scripts)

### Goal
Containerize the FastAPI app and configure Fly.io for deployment. Produce all deployment
config files and the two ChromaDB management scripts. No actual deploy yet — that is API-008.

### Acceptance Criteria
- [ ] `Dockerfile` builds successfully (`docker build -t sikfan-api .`)
- [ ] `Dockerfile` uses `python:3.11-slim`, installs `requirements-api.txt`, copies project files
- [ ] `fly.toml` configures: app name, region sjc, internal port 8080, health check on `/health`
- [ ] `fly.toml` has `[[mounts]]` section active, mounting `sikfan_data` volume at `/app/data`
- [ ] `fly.toml` sets `min_machines_running = 1` (keep warm — model load is slow)
- [ ] `API_KEY` secret documented as required (set via `fly secrets set API_KEY=<value>`)
- [ ] Local docker run works: `docker run -p 8000:8080 -e API_KEY=testkey sikfan-api`
- [ ] `GET /health` returns 200 inside the container
- [ ] `scripts/predeploy.sh` exists, prints ChromaDB count, writes `deploy_snapshot/embeddings/`, exits non-zero if `data/embeddings/` is absent
- [ ] `scripts/sync_from_fly.sh` exists, pulls live container ChromaDB, verifies count > 0 before swapping, exits non-zero if pull is empty

### Files to create/modify
- `Dockerfile` — create
- `fly.toml` — create
- `.dockerignore` — create
- `scripts/predeploy.sh` — create
- `scripts/sync_from_fly.sh` — create

### Dockerfile

> **First-time setup note:** `deploy_snapshot/` is gitignored and must be created by running
> `bash scripts/predeploy.sh` before the first `docker build`. If it is absent, the
> `COPY deploy_snapshot/embeddings` line will fail with a cryptic Docker path error.
> Never run `docker build` directly — always go through `scripts/predeploy.sh && fly deploy`
> (or the full sync workflow). This dependency is intentional: it forces a conscious snapshot
> before every image build.

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY . .
# deploy_snapshot/embeddings is copied here — see .dockerignore.
# If this COPY fails, run: bash scripts/predeploy.sh
# deploy_snapshot/ is gitignored; it must be created locally before building.
EXPOSE 8080
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
```

### fly.toml
```toml
app = "sikfan-api"
primary_region = "sjc"

[build]

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  min_machines_running = 1

  [[http_service.checks]]
    path = "/health"
    interval = "30s"
    timeout = "5s"

[[mounts]]
  source = "sikfan_data"
  destination = "/app/data"

[env]
  ENVIRONMENT = "production"
```

### ChromaDB snapshot strategy

**Problem:** Baking `data/embeddings/` directly into the image resets ChromaDB to build-time
state on every `fly deploy`, silently discarding any confirmations collected since the last build.

**Solution:** The Dockerfile copies from `deploy_snapshot/embeddings/` (not `data/embeddings/`).
This directory is only updated when you explicitly run `scripts/predeploy.sh`. If it is absent,
the Docker build fails with a clear error.

```dockerfile
# In Dockerfile — copy snapshot, not live dir.
# NOTE: In Fly.io production, the [[mounts]] volume overlays /app/data at runtime,
# so this COPY is shadowed and has no effect on the live container. It exists solely
# for local `docker run` testing where no volume is present.
COPY deploy_snapshot/embeddings /app/data/embeddings
```

```
# .dockerignore
data/embeddings/          # exclude live, mutable ChromaDB — never baked directly into the image
!deploy_snapshot/         # include explicitly exported snapshot (created by scripts/predeploy.sh)
__pycache__/
.git/
data/CNFOOD-241/
```

> Note: `deploy_snapshot/` itself should be in `.gitignore` (build artifact, not source).
> This means it will be absent in fresh clones. The `!deploy_snapshot/` line above keeps it
> out of Docker's ignore list once it exists locally — it does not create it.

### scripts/predeploy.sh
```bash
#!/usr/bin/env bash
set -e

SNAPSHOT_DIR="deploy_snapshot/embeddings"
SOURCE_DIR="data/embeddings"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "ERROR: $SOURCE_DIR not found — cannot create snapshot."
  exit 1
fi

echo "=== Pre-deploy ChromaDB snapshot ==="
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='$SOURCE_DIR')
col = client.get_or_create_collection('dishes')
print(f'Current ChromaDB: {col.count()} embeddings')
"

rm -rf "$SNAPSHOT_DIR"
cp -r "$SOURCE_DIR" "$SNAPSHOT_DIR"
echo "Snapshot written to $SNAPSHOT_DIR"
echo "Run 'fly deploy' now to ship this snapshot."
```

### scripts/sync_from_fly.sh
```bash
#!/usr/bin/env bash
set -e

echo "=== Syncing ChromaDB from live Fly.io container ==="

# Step 1: Pull to temp dir — leave local embeddings untouched until verified
rm -rf data/embeddings_fly_latest
fly ssh sftp get /app/data/embeddings data/embeddings_fly_latest

# Step 2: Verify non-empty BEFORE touching local embeddings
COUNT=$(python3 -c "
import chromadb
client = chromadb.PersistentClient(path='data/embeddings_fly_latest')
col = client.get_or_create_collection('dishes')
print(col.count())
")

if [ "$COUNT" -le 0 ]; then
  echo "ERROR: pulled ChromaDB has 0 embeddings — aborting swap to protect local data."
  rm -rf data/embeddings_fly_latest
  exit 1
fi

echo "Live container ChromaDB: $COUNT embeddings — looks good."

# Step 3: Safe to swap
rm -rf data/embeddings
mv data/embeddings_fly_latest data/embeddings
echo "Local data/embeddings replaced with live container state."
echo "Run 'bash scripts/predeploy.sh && fly deploy' to ship it."
```

### Deploy workflow
```bash
# First deploy — create the persistent volume before deploying
fly volumes create sikfan_data --region sjc --size 1
fly launch --name sikfan-api --region sjc --no-deploy
fly secrets set API_KEY=$(openssl rand -hex 32)
bash scripts/predeploy.sh
fly deploy

# Seed the volume with local embeddings (volume starts empty on first deploy)
fly sftp shell <<'EOF'
put -r deploy_snapshot/embeddings /app/data/embeddings
EOF

# Verify seed succeeded — chromadb_ready must be true before proceeding
curl -s https://sikfan-api.fly.dev/health | python3 -c "
import sys, json
h = json.load(sys.stdin)
assert h['chromadb_ready'], 'ChromaDB seed failed — check volume and retry sftp'
print('Volume seeded successfully:', h)
"

# Subsequent deploys — no remote confirmations since last deploy
bash scripts/predeploy.sh && fly deploy

# Subsequent deploys — remote confirmations exist (POST /confirm-dish was called on live URL)
bash scripts/sync_from_fly.sh && bash scripts/predeploy.sh && fly deploy
```

> **Why `predeploy.sh` / `sync_from_fly.sh` are still needed even with the volume:**
> The volume protects `meal_logs.json`, `correction_log.jsonl`, and `data/embeddings/` from
> being wiped on deploy. However, ChromaDB state on the volume and your local `data/embeddings/`
> can diverge when the live API collects confirmations — `sync_from_fly.sh` reconciles this so
> your local models stay current. `predeploy.sh` is now a safety check + local backup rather
> than a required seeding step.

### Fly.io notes
- `fly secrets set API_KEY=<value>` — run once after `fly launch`
- `fly volumes create sikfan_data --region sjc --size 1` — run before first deploy; creates the persistent volume
- Volume is mounted at `/app/data` — `meal_logs.json`, `correction_log.jsonl`, `data/embeddings/`, and `data/job_status/` all survive `fly deploy`
- After first deploy, seed the volume with embeddings via `fly ssh sftp put` (the Dockerfile COPY is shadowed by the mount in production)
- `deploy_snapshot/` should be in `.gitignore` (build artifact, not source)
- `FastSAM-s.pt` (~23MB): bake into image — `.dockerignore` must NOT exclude it

### Key rotation workflow (zero-downtime)
```bash
# Step 1: Set new key alongside old — machine restarts (~15s), both keys valid:
fly secrets set API_KEY_PREV=$OLD_KEY API_KEY=$(openssl rand -hex 32)

# Step 2: Update your iOS app / curl scripts to use the new key.

# Step 3: Retire the old key (no restart needed for unset):
fly secrets unset API_KEY_PREV
```
`API_KEY_PREV` is optional in the auth dependency. If unset, only `API_KEY` is checked.

### Verification Blueprint

**Case 1 — predeploy.sh**
```bash
bash scripts/predeploy.sh
```
Expected: prints ChromaDB count, confirms `deploy_snapshot/embeddings/` written.
Side-effects: `deploy_snapshot/embeddings/` exists and is non-empty. Script exits 1 if `data/embeddings/` absent.

**Case 2 — sync_from_fly.sh**
```bash
bash scripts/sync_from_fly.sh
```
Expected: prints live container count, confirms swap.
Side-effects: `data/embeddings/` replaced with Fly.io container state. `data/embeddings_fly_latest/` removed.

**Case 3 — Docker build and local health check**
```bash
docker build -t sikfan-api .
docker run -d -p 8000:8080 -e API_KEY=testkey --name sikfan-test sikfan-api
sleep 5
curl -s http://localhost:8000/health
docker stop sikfan-test && docker rm sikfan-test
```
Expected HTTP status: `200`
Expected response schema:
```json
{"status": "ok", "models_loaded": true}
```
Side-effects: none (container stopped and removed after test).

---

## API-008 — Fly.io deploy + end-to-end smoke test (Epic 8 gate)

### Goal
Deploy to Fly.io and run a full end-to-end smoke test against the live URL covering
every endpoint. This ticket is the Epic 8 gate — all prior tickets must pass first.

### Acceptance Criteria
- [ ] `fly deploy` completes without error
- [ ] `https://<app>.fly.dev/health` returns `{"status":"ok","models_loaded":true}`
- [ ] Full E2E smoke test passes (all 7 steps — see below)
- [ ] Auth rejection returns 401 on live URL
- [ ] Async meal analysis completes within 90 seconds on Fly.io hardware
- [ ] `fly logs` shows no errors during smoke test run
- [ ] `scripts/sync_from_fly.sh` verified: run `POST /confirm-dish` against live URL, then sync, confirm local count matches
- [ ] `scripts/predeploy.sh` verified: exits 1 clearly if `data/embeddings/` is absent

### Files to create/modify
- `scripts/smoke_test.sh` — create
- `scripts/sync_from_fly.sh` — created in API-007, verified here

### scripts/smoke_test.sh
```bash
#!/usr/bin/env bash
set -e
BASE_URL="${1:-https://sikfan-api.fly.dev}"
KEY="${API_KEY:?API_KEY env var required}"

echo "=== SikFan API Smoke Test ==="
echo "Target: $BASE_URL"

# 1. Health
echo "[1/7] GET /health"
curl -sf "$BASE_URL/health" | python3 -m json.tool
echo "PASS"

# 2. Auth rejection
echo "[2/7] Auth rejection"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/glucose/test")
[ "$STATUS" = "401" ] && echo "PASS" || (echo "FAIL: expected 401, got $STATUS"; exit 1)

# 3. Async meal analysis
echo "[3/7] POST /analyze-meal"
MEAL_ID=$(curl -sf -X POST "$BASE_URL/analyze-meal" \
  -H "X-API-Key: $KEY" \
  -F "file=@data/assorted_breakfast.jpeg" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['meal_id'])")
echo "meal_id: $MEAL_ID"

# 4. Poll until complete (max 90s)
echo "[4/7] GET /meal-status (polling)"
for i in $(seq 1 45); do
  JOB_STATUS=$(curl -sf -H "X-API-Key: $KEY" "$BASE_URL/meal-status/$MEAL_ID" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "  attempt $i: $JOB_STATUS"
  [ "$JOB_STATUS" = "complete" ] && break
  [ "$JOB_STATUS" = "failed" ] && (echo "FAIL: job failed"; exit 1)
  sleep 2
done
[ "$JOB_STATUS" = "complete" ] || (echo "FAIL: timed out"; exit 1)
echo "PASS"

# 5. Log the meal
echo "[5/7] POST /log-meal"
curl -sf -X POST "$BASE_URL/log-meal" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"meal_id\":\"$MEAL_ID\",\"confirmed_dishes\":[\"test_dish\"]}" | python3 -m json.tool
echo "PASS"

# 6. Glucose prediction
echo "[6/7] GET /glucose/{meal_id}"
curl -sf -H "X-API-Key: $KEY" "$BASE_URL/glucose/$MEAL_ID" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print('outcome:', r['prediction']['outcome']['label'])"
echo "PASS"

# 7. Confirm a dish (primary happy path — Epic 10 depends on this)
echo "[7/7] POST /confirm-dish"
CROP_ID=$(curl -sf -H "X-API-Key: $KEY" "$BASE_URL/meal-status/$MEAL_ID" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['result']['dishes'][0]['crop_id'])")
CONFIRM_RESULT=$(curl -sf -X POST "$BASE_URL/confirm-dish" \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"meal_id\":\"$MEAL_ID\",\"crop_id\":\"$CROP_ID\",\"action\":\"CONFIRM\"}")
echo "$CONFIRM_RESULT" | python3 -m json.tool
UPDATED=$(echo "$CONFIRM_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['chromadb_updated'])")
[ "$UPDATED" = "True" ] && echo "PASS" || (echo "FAIL: chromadb_updated was not True"; exit 1)

echo ""
echo "=== All 7 checks passed ==="
```

### Deploy commands
```bash
# First deploy
fly volumes create sikfan_data --region sjc --size 1
fly launch --name sikfan-api --region sjc --no-deploy
fly secrets set API_KEY=$(openssl rand -hex 32)
bash scripts/predeploy.sh
fly deploy

# Subsequent deploy — no remote confirmations since last deploy
bash scripts/predeploy.sh && fly deploy

# Subsequent deploy — remote confirmations exist
bash scripts/sync_from_fly.sh && bash scripts/predeploy.sh && fly deploy

# Run smoke test
bash scripts/smoke_test.sh https://sikfan-api.fly.dev
```

### Verification Blueprint

**Gate test**
```bash
bash scripts/smoke_test.sh https://sikfan-api.fly.dev
```
Expected output: `=== All 7 checks passed ===`
Side-effects to verify:
- `/health` → HTTP 200, `models_loaded: true`
- Auth rejection → HTTP 401, `code: invalid_api_key`
- `POST /analyze-meal` → job file created; `status: complete` within 90s
- `POST /log-meal` → appended to `meal_logs.json`; job file has `logged: true`
- `GET /glucose/{meal_id}` → `prediction.outcome.label` in `["spike","steady","drop"]`
- `POST /confirm-dish` → `chromadb_updated: true`; `correction_log.jsonl` has new CONFIRM entry
- `fly logs` → zero ERROR-level lines during test run

**sync_from_fly.sh gate**
```bash
# Confirm a dish via live API first
curl -s -X POST https://sikfan-api.fly.dev/confirm-dish \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"meal_id":"<uuid>","crop_id":"<crop_id>","action":"CONFIRM"}'

# Pull live ChromaDB back
bash scripts/sync_from_fly.sh

# Verify local count updated
python3 -c "
import chromadb
c = chromadb.PersistentClient('data/embeddings')
col = c.get_or_create_collection('dishes')
print('local count:', col.count())
"
```
Expected: local count matches or exceeds pre-test count.
Side-effects: `data/embeddings/` updated with live container state.

### Fly.io notes
- First deploy will be slow (~5min) due to model files in image. Subsequent deploys faster.
- `fly scale vm shared-cpu-2x` if model inference times out on default shared-cpu-1x.
- Check `fly logs --app sikfan-api` if health check fails after deploy.

---

## Ticket Summary

| Ticket  | Endpoint(s)                     | Key files                              | Blocks  |
|---------|---------------------------------|----------------------------------------|---------|
| API-001 | GET /health + auth scaffold     | `api.py`, `requirements-api.txt`       | API-002 |
| API-002 | POST /analyze-meal              | `api.py`, `data/uploads/`              | API-003 |
| API-003 | GET /meal-status/{meal_id}      | `api.py`, `data/job_status/`           | API-004 |
| API-004 | POST /log-meal                  | `api.py`, `meal_logs.json`             | API-005 |
| API-005 | GET /glucose/{meal_id}          | `api.py`                               | API-006 |
| API-006 | POST /confirm-dish              | `api.py`, `correction_log.jsonl`       | API-007 |
| API-007 | Fly.io config (no deploy)       | `Dockerfile`, `fly.toml`, `scripts/`   | API-008 |
| API-008 | Deploy + E2E smoke test (GATE)  | `scripts/smoke_test.sh`                | Epic 9  |

# API-009 Patch
# Append the ticket below to API-LAYER-TICKETS.md (before the Ticket Summary table),
# and add the summary row to the table.

---

## API-009 — GET /meal-image/{meal_id} (meal photo retrieval)

### Goal
Persist the original meal photo at upload time and expose it via a dedicated endpoint so the mobile app can lazy-load meal images in the meal log and analysis screens without adding payload to analysis responses.

### Acceptance Criteria
- [ ] `POST /analyze-meal` saves the original upload to `data/meals/{meal_id}/original.jpg` before returning 200
- [ ] `data/meals/` directory created on first run (`mkdir exist_ok`)
- [ ] `GET /meal-image/{meal_id}` returns the image as `FileResponse` with `media_type="image/jpeg"`
- [ ] HTTP 404 with `meal_not_found` if no image exists for that `meal_id`
- [ ] HTTP 401 if `X-API-Key` missing or wrong
- [ ] `image_url` field added to `MealResult` and `GlucoseResponse` Pydantic models, set to `"/meal-image/{meal_id}"`
- [ ] Original image persisted to volume (survives deploys) — `data/meals/` falls under existing Fly.io volume mount
- [ ] TTL sweep at startup cleans `data/meals/{meal_id}/` for meal_ids not in `meal_logs.json` older than 7 days

### Files to create/modify
- `api.py` — add `GET /meal-image/{meal_id}` route; modify `POST /analyze-meal` handler to persist original; add `image_url` to `MealResult` and `GlucoseResponse`
- `fly.toml` — confirm `data/meals/` falls under existing volume mount (no change needed if mount is `data/`)

### Pydantic model changes

```python
class MealResult(BaseModel):
    meal_id: str
    dishes: list[DishResult]
    total_carbs_g: float
    image_url: str           # "/meal-image/{meal_id}"

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
    image_url: str           # "/meal-image/{meal_id}"
```

### Implementation notes

**Saving the original (in `POST /analyze-meal` route handler, before returning 200):**
```python
meal_dir = Path(f"data/meals/{meal_id}")
meal_dir.mkdir(parents=True, exist_ok=True)
image_path = meal_dir / "original.jpg"
image_path.write_bytes(await file.read())
```
Save happens in the route handler (not the background task) so the image is available immediately. `data/uploads/{meal_id}.jpg` continues to exist for `analyze_meal()` — the original save is a separate write.

**Endpoint:**
```python
from fastapi.responses import FileResponse

@app.get("/meal-image/{meal_id}", dependencies=[Depends(verify_api_key)])
async def get_meal_image(meal_id: str):
    image_path = Path(f"data/meals/{meal_id}/original.jpg")
    if not image_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "meal_not_found", "message": "No image found for this meal ID."}
        )
    return FileResponse(image_path, media_type="image/jpeg")
```

**TTL sweep (add to startup lifespan):**
```python
def sweep_old_meal_images(meal_logs_path: Path, meals_dir: Path, max_age_days: int = 7):
    logged_ids = {entry["meal_id"] for entry in load_meal_logs(meal_logs_path)}
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    for meal_dir in meals_dir.iterdir():
        if meal_dir.is_dir() and meal_dir.name not in logged_ids:
            mtime = datetime.utcfromtimestamp(meal_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(meal_dir)
```
Runs once at startup alongside the existing crop TTL sweep. Keeps storage bounded — only logged meals with recent images are retained.

### State Lifecycle

**`data/meals/{meal_id}/original.jpg`**

| Action | Who | When |
|---|---|---|
| Create | Route handler (`POST /analyze-meal`) | Before returning 200 |
| Read | `GET /meal-image/{meal_id}` | On request |
| Delete | Startup TTL sweep | If meal_id not in `meal_logs.json` and older than 7 days |

### Error matrix addition

| Endpoint | `code` | `message` |
|---|---|---|
| `GET /meal-image/{meal_id}` | `meal_not_found` | `"No image found for this meal ID."` |

### Verification Blueprint

**Case 1 — successful image retrieval**
```bash
# Submit a meal and get meal_id
MEAL_ID=$(curl -sf -X POST https://<app>.fly.dev/analyze-meal \
  -H "X-API-Key: $API_KEY" \
  -F "file=@data/assorted_breakfast.jpeg" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['meal_id'])")

# Fetch the image immediately (no need to wait for analysis)
curl -sf -H "X-API-Key: $API_KEY" \
  https://<app>.fly.dev/meal-image/$MEAL_ID \
  --output /tmp/test_meal.jpg
file /tmp/test_meal.jpg
```
Expected HTTP status: `200`
Expected: `file` command reports JPEG image data.
Side-effects: `data/meals/{meal_id}/original.jpg` exists on server.

**Case 2 — unknown meal_id**
```bash
curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/meal-image/nonexistent-id
```
Expected HTTP status: `404`
Expected response:
```json
{"detail": {"code": "meal_not_found", "message": "No image found for this meal ID."}}
```

**Case 3 — image_url present in analysis response**
```bash
# After analysis completes
curl -sf -H "X-API-Key: $API_KEY" https://<app>.fly.dev/meal-status/$MEAL_ID \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['image_url'])"
```
Expected output: `/meal-image/<meal_id>`

**Case 4 — auth rejection**
```bash
curl -s https://<app>.fly.dev/meal-image/$MEAL_ID
```
Expected HTTP status: `401`

---

## API-010 — Glucose prediction before logging (scan-time anchor)

### Goal
`GET /glucose/{meal_id}` currently requires the meal to be logged
(`POST /log-meal`) first — it 404s (`meal_not_found`) on any meal that's only
been analyzed, not logged. Change it to return a prediction as soon as
`POST /analyze-meal` completes, anchored to scan time (`job_status.created_at`)
rather than log time, so the mobile Results screen can show glucose impact
*before* the user commits to logging. Full design + decision log:
`plans/API-010-plan.md`.

### Acceptance Criteria
- [ ] `GET /glucose/{meal_id}` returns 200 with a full prediction immediately
      after `POST /analyze-meal` completes, without requiring `/log-meal` first
- [ ] Preview response uses `job_status.created_at` as `meal_timestamp` and
      sums macros from `job_status.result.dishes` (not `meal_logs.json`)
- [ ] Preview response always has `actuals: null` and `retrain_triggered: false`
      — no CGM window is attached or persisted pre-log
- [ ] Once a meal is logged, `GET /glucose/{meal_id}` transitions to the
      existing logged path (CGM window attach, actuals, retrain) automatically
      — same URL, same schema, no separate preview endpoint or response flag
- [ ] `POST /log-meal`'s `meal_timestamp` is changed to equal the meal's
      `job_status.created_at`, not the time `/log-meal` was called — so the
      pre-log preview and the post-log prediction share the same CGM anchor
      and produce a continuous curve (no visible jump the moment a meal is logged)
- [ ] 422 `no_pre_meal_glucose` and 404 `meal_not_found` behavior unchanged
      for both the preview and logged paths
- [ ] Existing GLUC-009 `GlucoseResponse` schema fields unchanged — this is a
      gating/source-of-truth change, not a schema change; no Pydantic model edits

### Implementation Notes
- `analyze_glucose()`'s prediction assembly (`predict_glucose_curve` +
  `classify_glucose_outcome` + peak/confidence packaging) should be extracted
  into a shared helper reused by both the existing logged path and a new
  `analyze_glucose_preview(meal_id)` function — avoid duplicating that logic
- `attach_cgm_window()` (`pipeline/meal_tracker.py`) requires the meal to
  already exist in `meal_logs.json` and writes back into it — it cannot run
  against a `job_status` file. The preview path must not call it; `actuals`
  stays `null` until the meal is actually logged and tracked
- Do NOT write a provisional entry to `meal_logs.json` at scan/preview time —
  that file is the training corpus (`_should_retrain()`/`train_model()`
  iterate it), so unlogged previews (retaken/discarded photos) would need to
  be filtered out of every training consumer. Read `job_status` directly for
  the preview instead; a preview that's never logged leaves no trace
- `job_status`'s `result.dishes` (`DishResult`: `crop_id, name, confidence,
  status, carbs_g, protein_g, fat_g, calories, portion_g, portion_bucket`) is
  a different shape from `meal_logs.json`'s `dishes` (`{dish_name,
  portion_size, macros: {...}}`) — map explicitly, don't assume interchangeable
- Aside, not introduced by this ticket: `/log-meal`'s macros dict never
  included `fiber_g`, so `total_fiber_g` has always been `0` for every logged
  meal today. The preview path (also missing `fiber_g` on `DishResult`) is no
  worse than the existing status quo — not in scope to fix here

### Files to create/modify
- `glucose_analysis.py` — add `analyze_glucose_preview()`, extract shared
  prediction-assembly helper
- `api.py` — `GET /glucose/{meal_id}` route falls back to the preview path on
  `MealNotFoundError`; `POST /log-meal` reuses `job_status.created_at`

### Verification Blueprint

**Case 1 — preview before logging**
```bash
# After /analyze-meal completes but before /log-meal is called
curl -s -w "\nHTTP %{http_code}\n" -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID
```
Expected HTTP status: `200`, `actuals: null`, `retrain_triggered: false`,
`meal_timestamp` equal to the job's `created_at`.

**Case 2 — continuity after logging**
```bash
PREVIEW=$(curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID)
curl -sf -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"meal_id\":\"$MEAL_ID\",\"confirmed_dishes\":[...]}" https://<app>.fly.dev/log-meal
LOGGED=$(curl -s -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID)
```
Expected: `PREVIEW.prediction` and `LOGGED.prediction` match (same curve) —
only `actuals` differs (still `null` until CGM window completes).

**Case 3 — no CGM data near scan time**
```bash
curl -s -w "\nHTTP %{http_code}\n" -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID_NO_CGM
```
Expected HTTP status: `422`, `{"code": "no_pre_meal_glucose", ...}` — unchanged.

### Dependencies
GLUC-006 (`analyze_glucose`), API-002 (`job_status` + `created_at`), API-004
(`/log-meal`), API-005 (`GET /glucose/{meal_id}`)

---

## API-011 — POST /manual-glucose (no-CGM fallback, forward-compatible with real CGM)

### Goal
When `GET /glucose/{meal_id}` 422s with `no_pre_meal_glucose` (no real CGM
reading near scan time — the expected case for most MVP users, who have no
CGM connected), let the user manually enter their current blood glucose on
the Results screen (`MOB-012`) and get a real prediction from it. Designed so
a future live CGM integration requires **zero changes** to this endpoint or
to `GET /glucose/{meal_id}` — full design + decision log: `plans/API-011-plan.md`.

### Acceptance Criteria
- [ ] `POST /manual-glucose` accepts `{meal_id: str, glucose_mgdl: int}` and
      writes a reading into `data/glucose/cgm_readings.json` via the existing
      `save_cgm_reading()` — `timestamp: now()`, `trend: "flat"` (no trend
      arrow is inferable from a single manual point), `source: "manual"`
- [ ] Does **not** call `predict_glucose_curve` directly and does not compute
      or return a prediction itself — it only writes the reading. The client
      re-fetches `GET /glucose/{meal_id}` afterward, which resolves normally
      because `get_pre_meal_glucose()` now finds the just-written reading —
      completely unchanged code path, no override parameter, no branching on
      "was this manual"
- [ ] `glucose_mgdl` validated same as any CGM reading: integer, 20–600
      range (existing `_validate_cgm` in `pipeline/glucose_store.py`, reused
      as-is — no new validation logic)
- [ ] 400 if `glucose_mgdl` out of range or non-integer
- [ ] 404 if `meal_id` has no `job_status` record
- [ ] 401 if auth missing

### Implementation Notes
- **This is the whole point of the ticket:** manual entries are stored
  exactly the way a real CGM feed's readings would be — same file, same
  shape, same `save_cgm_reading()` call, differing only in `source`. When
  real CGM integration ships later, live readings land in the same store the
  same way; the manual-entry code path and the future-live-CGM code path are
  unified by construction. Do not build this as a special case on `GET
  /glucose/{meal_id}` (e.g. a `?pre_meal_glucose=` override param) — that
  would need to be torn out again once real CGM exists. `pipeline/
  glucose_store.py`'s `_VALID_SOURCES = {"dexcom_csv", "manual"}` already
  anticipated this exact duality
- `meal_id` in the request body is used only to look up `job_status.created_at`
  as the reading's `timestamp` anchor (so it lands within the 15-minute
  window `get_pre_meal_glucose` searches) — it is not stored on the CGM
  reading itself; `cgm_readings.json` has no concept of "which meal" a
  reading belongs to, same as real CGM data wouldn't
- `trend: "flat"` is a deliberate default, not a guess at the user's real
  trend — `MOB-012`'s mobile-side `preMealTrend` is explicitly `null` on
  manual entry ("no trend arrow"), but the CGM store's `_validate_cgm`
  requires one of the five valid trend strings. `"flat"` is the safest
  neutral default for a single-point manual reading

### Files to create/modify
- `api.py` — add `POST /manual-glucose` route, calling
  `pipeline.glucose_store.save_cgm_reading()`

### Verification Blueprint

**Case 1 — manual entry unblocks a previously-422ing preview**
```bash
curl -s -w "\nHTTP %{http_code}\n" -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID_NO_CGM
# 422 no_pre_meal_glucose
curl -s -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"meal_id\":\"$MEAL_ID_NO_CGM\",\"glucose_mgdl\":118}" https://<app>.fly.dev/manual-glucose
curl -s -w "\nHTTP %{http_code}\n" -H "X-API-Key: $API_KEY" https://<app>.fly.dev/glucose/$MEAL_ID_NO_CGM
```
Expected: first call `422`, third call `200` with `pre_meal_glucose: 118`,
`pre_meal_trend: "flat"`, and a real prediction curve.

**Case 2 — out-of-range value**
```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d "{\"meal_id\":\"$MEAL_ID\",\"glucose_mgdl\":900}" https://<app>.fly.dev/manual-glucose
```
Expected HTTP status: `400`.

### Dependencies
API-002 (`job_status` + `created_at`), API-010 (`GET /glucose/{meal_id}`
preview path this feeds into), `pipeline/glucose_store.save_cgm_reading`

---

## API-012 — Composite dish decomposition in the correction path — IMPLEMENTED

### Goal
Wire FOOD-019's `pipeline.dish_decompose.resolve_composite_macros()` into
`_recompute_dish_macros()` so a corrected label like "japanese curry chicken
katsu with white rice" — which has no single USDA match — gets real macros
via component decomposition, instead of the hard `0.0` / `needs_macro_entry:
True` `_recompute_dish_macros()` previously returned for any `no_results`.
Full design + decision log: `plans/FOOD-019-plan.md`.

### Status note (2026-08-28)
Implemented and verified via `scripts/verify_food019.py` (mocked LLM
boundary — no Anthropic credits required to run it) plus a regression run of
`scripts/verify_gluc_012.py` (no drift). A live smoke test against a real
Sonnet call is still open, pending confirmation of the exact
`output_config`/`format` structured-output sub-schema against a billed
account (flagged in `pipeline/dish_decompose.py`'s `_call_llm_decompose`
docstring) — any request-shape issue there fails open to pre-FOOD-019
behavior rather than erroring, so this is a quality follow-up, not a
blocker.

### What changed
- `_recompute_dish_macros(corrected_label, portion_g)` (`api.py`) now tries
  `resolve_composite_macros(corrected_label)` first. `None` (dish didn't
  actually decompose) falls through to the pre-existing `lookup_macros()`
  path, byte-for-byte unchanged.
- When it resolves, the composite per-100g profile is persisted to
  `data/macro_cache/{slug}.json` (`source: "composite"`) via the same
  `pipeline.nutrition._atomic_write` helper `pipeline.nutrition` itself uses
  — so a future scan of the same corrected label hits the cache through the
  existing `get_macros()` → `pipeline.portion.estimate_portion()` path with
  **zero changes** to `pipeline/portion.py` or `analyze_meal.py`.
- `DishResult` gains three additive fields — `components: list[dict] | None
  = None`, `macro_coverage: float = 1.0`, `carb_coverage: float = 1.0` — so
  no existing consumer of the frozen `analyze_meal`/`DishResult` schema
  breaks; a dish that never went through composite decomposition (the vast
  majority, including a freshly-scanned dish that transparently hits an
  already-cached composite entry — see the note below) keeps the defaults.
- `LogMealResponse` and the `meal_logs.json` row gain `carb_coverage`
  (meal-level, carb-weighted across confirmed dishes, via the new
  `_aggregate_carb_coverage()` helper) — this is the field
  `pipeline.glucose_model.is_trainable()`'s FOOD-019 D6 threshold reads.
  Persisted starting now regardless of whether the training gate currently
  excludes anything, since coverage can't be reconstructed retroactively.

### Known scope boundary (deliberate, not an oversight)
`pipeline/portion.py`'s `scale_macros()` only ever returns the 5 numeric
macro fields — it does not pass `macro_coverage`/`carb_coverage`/`components`
through. So a dish resolved purely by the **initial scan** hitting an
already-cached composite entry (via `get_macros()`) will show correct
`carbs_g`/etc. but **not** the coverage/component breakdown — that metadata
only surfaces via `_recompute_dish_macros()`'s own return value, i.e. after a
CORRECT/ADD_NEW correction. `plans/FOOD-019-plan.md` puts
`pipeline/portion.py` and `analyze_meal.py` in "Unchanged on purpose"
deliberately; plumbing coverage all the way through the scan pipeline is a
possible follow-up if MOB-014 (mobile breakdown UI) needs it for the
first-scan case, not bundled into this ticket.

### Files modified
- `api.py` — `_recompute_dish_macros()`, `DishResult`, `LogMealResponse`,
  `log_meal()`, new `_aggregate_carb_coverage()` helper
- `pipeline/dish_decompose.py` (new, FOOD-019) — `resolve_composite_macros()`
  is the only entry point this ticket calls into

### Dependencies
FOOD-019 (`pipeline/dish_decompose.py`), API-006 (`POST /confirm-dish`, the
route `_recompute_dish_macros()` serves), API-004 (`POST /log-meal`, the
`carb_coverage` persistence point)

---

## Ticket Summary — updated row to add

Replace the existing Ticket Summary table footer with:

| API-008 | Deploy + E2E smoke test (GATE)  | `scripts/smoke_test.sh`                | Epic 9  |
| API-009 | GET /meal-image/{meal_id}       | `api.py`, `data/meals/`                | Epic 9  |