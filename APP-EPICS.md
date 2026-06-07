# GlycoLens — App & Integration Epics (Phase 3)

> These epics cover building the mobile app and integrating the food detection
> and glucose models built in Phase 1 (FOOD-001–015) and Phase 2 (GLUC-001–006).
>
> **Prerequisites before starting any epic here:**
> - FOOD-015 passing: `analyze_meal()` returns clean, stable output
> - GLUC-006 passing: `analyze_glucose()` returns clean, stable output
> - Both function output schemas are frozen — changes after this point
>   require API versioning
>
> Detailed tickets for each epic should be written once FOOD-015 and GLUC-006
> are both passing, so ticket specs can reference real, stable function signatures.

---

## Epic 8 — Local API Layer

**Goal**
Wrap `analyze_meal()` and `analyze_glucose()` into a lightweight local REST API
so the mobile app has clean, stable endpoints to call. The API runs on your Mac
during development; moves to a cloud host when you want the app to work away
from home.

**Why FastAPI**
FastAPI is the right choice here — automatic OpenAPI docs, async support, and
Pydantic validation means your request/response schemas are enforced and
documented for free. It also hot-reloads during development which matters a lot
when you're iterating on model outputs.

**Key Endpoints (to be specced in tickets):**
- `POST /analyze-meal` — accepts image, returns macro breakdown + dish list
- `POST /log-meal` — saves a confirmed meal log, triggers CGM window monitoring
- `GET /glucose/{meal_id}` — returns glucose prediction + actuals if available
- `POST /confirm-dish` — user confirms or corrects a dish prediction, updates ChromaDB
- `GET /health` — confirms API + models are loaded and ready

**Key Decisions to make before writing tickets:**
- Local-only vs. cloud-hosted API (affects auth, HTTPS, and data privacy design)
- Sync vs. async meal logging (does the app wait for macro lookup or fire and forget?)
- How images are passed — base64 in JSON body vs. multipart form upload

**Rough ticket count:** 6–8 tickets
**Gate:** All endpoints return correct responses against Postman/curl tests before
any mobile app work begins.

---

## Epic 9 — Mobile App (iOS)

**Goal**
Build the iOS app that is the user-facing layer of GlycoLens. Camera capture,
macro display, BG prediction curve, and the confirmation/correction feedback UI.

**Recommended Stack: React Native**
React Native is the better choice over native Swift for this project for a few
reasons. You're building solo, so cross-platform code reuse matters even if
Android is post-MVP. The charting libraries for BG curve display
(Victory Native, Recharts) are more mature in the React Native ecosystem than
SwiftUI equivalents. And the feedback loop UI (confirm/correct dish predictions)
is form-heavy work that React Native handles faster to build.

If you have strong Swift preference that's valid too — the API layer (Epic 8)
means the app is just a UI skin over HTTP calls regardless of which you choose.

**Core Screens:**
- **Capture screen** — camera viewfinder with capture button, gallery fallback
- **Results screen** — dish list with macros, confidence badges, confirm/correct UI
- **BG prediction screen** — predicted glucose curve with confidence band,
  pre-meal BG context pulled from CGM
- **Meal history screen** — past meals with predicted vs. actual overlay thumbnails
- **Post-meal tracking screen** — live BG trace overlaid against prediction
  as the meal digests (updates every 5 min from CGM data)

**Key Decisions to make before writing tickets:**
- React Native vs. Swift
- State management approach (Redux, Zustand, or Context API)
- Charting library for BG curves
- How CGM data refreshes in the app (polling the API vs. push from Dexcom)

**Rough ticket count:** 12–16 tickets (one per screen + navigation + API integration)
**Gate:** All 5 core screens render correctly with mocked API responses before
wiring to live API.

---

## Epic 10 — Model Integration & Feedback Sync

**Goal**
Close the feedback loop between the mobile app and your local models. Every
user interaction in the app — confirming a dish, correcting a prediction,
logging a meal — must flow back to update ChromaDB and the glucose store so
the models improve over time.

**What this epic wires together:**
- Dish confirmation in the app → ChromaDB centroid update (FOOD-010 logic)
- Meal log from the app → `meal_logs.json` update (GLUC-001 schema)
- Post-meal CGM window → attached to meal log (GLUC-004 logic)
- Model retraining trigger → fires automatically when `should_retrain()` is True
- Embedding health report → surfaces confusion pairs for your review (FOOD-011)

**The core challenge here**
Your models live on your Mac. Your phone is the client. During development this
is fine — phone and Mac on the same WiFi, API on localhost. When you want the
app to work away from home you'll need to decide: move the API to a cloud host,
or implement local model sync (e.g. export updated ChromaDB to the phone).
For personal use, a simple cloud VM (a small AWS EC2 or Fly.io instance) is
probably the path of least resistance.

**Key Decisions to make before writing tickets:**
- Local-only vs. cloud-hosted model (affects where ChromaDB lives long-term)
- Retraining cadence — on-device trigger vs. scheduled nightly job on Mac
- How model updates are versioned (important once you have a working model
  you don't want to accidentally overwrite with a bad retrain)

**Rough ticket count:** 6–8 tickets
**Gate:** A full end-to-end test where a photo taken on the phone results in
an updated ChromaDB embedding on the Mac within 30 seconds.

---

## Epic 11 — Live Dexcom CGM Integration (Post-MVP)

**Goal**
Replace the manual Dexcom CSV import flow with a live Dexcom API integration
so CGM readings stream into the app in real time — enabling live pre-meal BG
context, automatic post-meal window capture, and real-time BG overlay as a
meal digests.

**Why this is post-MVP**
The Dexcom developer API requires a partnership application that takes 3–6
months to be approved. Starting that application now (even before this epic
is ready) is worth doing. Until approval comes through, the CSV import from
GLUC-002 keeps everything functional.

**What changes vs. the CSV flow:**
- Pre-meal BG context is live (current reading at photo capture time)
- Post-meal CGM window captures automatically — no manual CSV export needed
- Post-meal tracking screen updates in real time as glucose rises and falls
- Automatic meal log triggers when a rapid post-meal glucose rise is detected
  (spike detection as a meal logging prompt)

**Key Decisions to make before writing tickets:**
- Dexcom API OAuth flow implementation (requires approved developer account)
- Polling interval for live readings (Dexcom API updates every 5 minutes)
- Background refresh strategy on iOS (background fetch vs. push notifications)
- Fallback behavior if Dexcom API is unavailable (fall back to manual entry)

**Rough ticket count:** 8–10 tickets
**Action now:** Submit Dexcom developer partnership application at
https://developer.dexcom.com — do this before you need it.

---

## Phase 3 Build Order

```
Epic 8 (API Layer)
    ↓
Epic 9 (Mobile App) ←→ Epic 10 (Model Sync)
    ↓
Epic 11 (Live CGM) — post-MVP
```

Epics 9 and 10 are developed in parallel — build a screen in Epic 9,
immediately wire its feedback actions in Epic 10, then move to the next screen.
Don't build all of Epic 9 before starting Epic 10 or you'll have a large
integration gap to close at the end.

---

## Key Decisions Summary

Before writing detailed tickets for any epic above, these decisions need answers.
Each one affects multiple tickets and is easier to decide now than to change later:

| Decision | Affects | Recommendation |
|----------|---------|----------------|
| React Native vs. Swift | Epic 9 entirely | React Native for solo dev speed |
| Local vs. cloud API | Epics 8, 10, 11 | Local for MVP, Fly.io for v1 |
| Image upload format | Epic 8, 9 | Multipart form (better for large images) |
| CGM refresh strategy | Epics 9, 11 | Polling every 5min matches Dexcom update rate |
| Retraining cadence | Epic 10 | Trigger-based (should_retrain()) not scheduled |
| Model versioning | Epic 10 | Timestamp-named model files + rollback support |