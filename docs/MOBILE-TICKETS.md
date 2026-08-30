# SikFan — Epic 9: Mobile App (React Native MVP)

> Build the MVP mobile app shown in the SikFan MVP design.
> Screen flow: Home → Camera → Analyzing → Results → Meal Log + Post-meal Tracking.
> All architecture decisions locked in `data/MOBILE_DECISIONS.md`.
> Gate: Epic 8 API layer must be running before MOB-003 can be tested end-to-end.
>
> **5 core screens** (from app-epics.md): Capture, Results (BG chart merged in per design),
> Meal History, Post-meal Tracking, Confirm/correct UI (stub in Epic 9, wired in Epic 10).
> All 5 must render correctly before wiring to the live API.
>
> **MVP mode:** `SIKFAN_MVP = true` hides live CGM card, pre-bolus card, and CGM tab.
> Build for MVP mode first. Post-MVP features are explicitly marked below.
>
> **Architecture decisions (locked — see `data/MOBILE_DECISIONS.md`):**
> Zustand (centralized store), Victory Native (charting), polling every 5 min (CGM refresh).

---

## Epic 9: Mobile App

### MOB-001 — Project scaffold, navigation shell, and design tokens

**Goal**
Bootstrap the React Native project with Expo managed workflow, React Navigation stack/tab structure, and the design token system extracted from the MVP design (colors, typography, spacing). Every subsequent ticket drops screens into this shell.

**Acceptance Criteria**
- [ ] Expo project initialised in `mobile/` using `expo init` with TypeScript template
- [ ] React Navigation installed: `@react-navigation/native`, `@react-navigation/bottom-tabs`, `@react-navigation/stack`
- [ ] Tab bar renders four tabs: Home, Log, Trends, About (MVP mode); camera FAB in the center triggers the camera stack
- [ ] Screen stubs exist for: HomeScreen, CameraScreen, AnalyzingScreen, ResultsScreen, MealLogScreen
- [ ] `theme.ts` exports the three palettes (sunrise, matcha, mist) matching the design token names in `theme.jsx`; sunrise is default
- [ ] `fonts.ts` registers Plus Jakarta Sans and Fraunces via `expo-font`; Modern variant (Plus Jakarta Sans) is default
- [ ] `mobile/` runs on iOS Simulator with `npx expo start` without errors

**Implementation Notes**
- Use Expo managed workflow — do not eject
- Tab bar: Home tab (stack: Home → Results), Log tab (stack: MealLog → Results), camera FAB opens CameraScreen as a full-screen modal stack, Trends and About are placeholder screens for MVP
- Mirror the `verdictColor` helper from `theme.jsx`: spike → warn palette, steady → good palette, drop → low palette
- The center camera FAB floats above the tab bar (negative `marginTop`) as shown in the design — this is a custom `tabBarButton` on the middle tab, not a real tab

**Dependencies:** None — this is the starting point.

---

### MOB-002 — Zustand store scaffolding (mealStore, glucoseStore, historyStore)

**Goal**
Wire up the three Zustand store slices that all screens read from and write to. No business logic yet — just the shape, actions, and TypeScript types.

**Acceptance Criteria**
- [ ] `store/mealStore.ts` with state: `status` (`idle | uploading | analyzing | done | error`), `mealId`, `mealTimestamp`, `dishes` (full `DishResult[]` from `MealResult`), `dishName`, `confidence`, `portion` (`number | null`), `portionBucket` (`string | null`), `macros`; actions: `startScan`, `setResult`, `reset`
- [ ] `store/glucoseStore.ts` with state: `preMealGlucose`, `preMealTrend`, `prediction` (`GlucosePrediction | null`), `verdict` (`'spike' | 'steady' | 'drop' | null`), `readings` (array of `{minutes, glucose_mgdl}`), `actuals` (`GlucoseActuals | null` — the full actuals object from `GlucoseResponse.actuals`, needed for MOB-011 summary cards), `pollCount` (number, tracks intervals fired), `pollingHandle` (interval ID or null), `pollingActive`; actions: `setPrediction(prediction, preMealGlucose, preMealTrend)`, `startPolling(mealId)`, `stopPolling`, `appendReading`, `reset`
- [ ] `store/historyStore.ts` with state: `meals` (array of `LoggedMeal` — a combined shape that must include: all fields from `MealResult` (`meal_id`, `dishes`, `total_carbs_g`, `image_url`) + `meal_timestamp: string` from `POST /log-meal` response + `verdict: "spike" | "steady" | "drop" | null` from `GlucoseResponse.prediction.outcome.label` once glucose is fetched); actions: `addMeal`, `updateVerdict(mealId, verdict)`, `clearHistory`
- [ ] All three stores typed with TypeScript interfaces that mirror the frozen API schemas in `CLAUDE.md`
- [ ] Each store exported from `store/index.ts`
- [ ] A simple `__tests__/store.test.ts` verifies `startScan` changes status to `uploading` and `reset` returns to `idle`

**Implementation Notes**
- Use `zustand` with `immer` middleware for nested state mutations: `npm install zustand immer`
- `pollingHandle` in glucoseStore stores the return value of `setInterval` — it must survive re-renders because it lives in the store, not in a component
- `mealStore` keeps `dishes: DishResult[]` for the full multi-dish list (required by Results screen macros card). `dishName`/`confidence`/`macros` are the primary/top dish fields used for the AnalyzingScreen chip. Do not nest `prediction` inside individual dish entries — the glucose prediction comes from a separate `GET /glucose/{meal_id}` call and lives at the top level of the store.
- `dishNative` (native script) is dropped for MVP — display English names only. `DishResult.name` is always the normalized ASCII slug (e.g. `"braised_beef_noodle"`); no client-side lookup table needed.
- `portion_g` and `portion_bucket` are `number | null` and `string | null` — the pipeline may not estimate portion for all crops. Use `formatPortion(portionBucket, portion)` from `store/types.ts` to render the display string; omit the field if the result is empty.

**Dependencies:** MOB-001

---

### MOB-003 — API client (Epic 8 endpoints)

**Goal**
Build a typed fetch wrapper around the Epic 8 FastAPI endpoints so screens can call the full async meal analysis flow, glucose prediction, meal logging, and image retrieval without caring about HTTP details.

**Acceptance Criteria**
- [ ] `api/client.ts` reads base URL from `EXPO_PUBLIC_API_URL` env var (fallback: `http://localhost:8000`); attaches `X-API-Key` header to every request from `EXPO_PUBLIC_API_KEY` env var
- [ ] `api/meals.ts` exports:
  - `submitMeal(imageUri: string): Promise<{meal_id: string, status: string}>` — POSTs the image as `multipart/form-data` to `POST /analyze-meal`; returns `{meal_id, status: "pending"}` immediately (does NOT wait for analysis)
  - `pollMealStatus(mealId: string): Promise<JobStatusResponse>` — GETs `GET /meal-status/{meal_id}`; returns current job status and full `MealResult` once complete
  - `logMeal(mealId: string, confirmedDishes: string[]): Promise<LogMealResponse>` — POSTs `POST /log-meal` with `{meal_id, confirmed_dishes}`; call after `pollMealStatus` returns `status: "complete"`
  - `getMealImage(mealId: string): Promise<string>` — fetches `GET /meal-image/{meal_id}` with the `X-API-Key` header, writes the response bytes to a temp file via `expo-file-system` (`FileSystem.writeAsStringAsync`), and returns the local `file://` URI for use as `<Image source={{uri}}>`. Do not pass the raw endpoint URL directly to `<Image>` — the endpoint requires auth and React Native's `<Image>` does not support custom headers (`URL.createObjectURL` is a browser API and is not available on native)
- [ ] `api/glucose.ts` exports `analyzeGlucose(mealId: string): Promise<GlucoseResponse>` — GETs `GET /glucose/{meal_id}`, returns typed response
- [ ] All functions throw a typed `ApiError` (with `status` and `message`) on non-2xx responses
- [ ] Response types in `api/types.ts` match the frozen schemas in `CLAUDE.md` exactly — no extra fields, no missing fields. Note: `GlucoseResponse.dishes` is `GlucoseDishEntry[]` (`{name, carbs_g}`) — a separate, reduced type from `DishResult`; do not reuse `DishResult` here
- [ ] `__tests__/api.test.ts` mocks `fetch` and verifies: correct URL construction, correct method, `ApiError` thrown on 401/404/422

**Async meal flow (important — analyze-meal is not a single round-trip):**
```
1. submitMeal(imageUri)          → POST /analyze-meal  → {meal_id, status: "pending"}
2. poll pollMealStatus(meal_id)  → GET /meal-status/{meal_id} every 2s, timeout at 60s
3. on status == "complete"       → result contains full MealResult; stop polling
4. logMeal(meal_id, dishes)      → POST /log-meal      → {meal_id, logged: true, meal_timestamp}
5. analyzeGlucose(meal_id)       → GET /glucose/{meal_id} → GlucoseResponse (called by polling loop MOB-009)
```
`AnalyzingScreen` (MOB-006) owns the polling loop — it calls `submitMeal` on mount, polls `pollMealStatus` every 2s, then dispatches `logMeal` before navigating to Results.

**Implementation Notes**
- Use `fetch` (built into React Native) — do not add axios
- Image upload: construct `FormData`, append the image file with key `file`, set no explicit Content-Type (let fetch set the multipart boundary automatically)
- `getMealImage` fetches the blob with `fetch` + `X-API-Key` header, writes it to a temp file via `FileSystem.downloadAsync` or `FileSystem.writeAsStringAsync` (base64), and returns the local `file://` URI — do NOT pass the raw endpoint URL to `<Image>` directly since `<Image>` does not support custom headers
- The `meal_id` from `submitMeal` is stored in `mealStore` and reused by `logMeal`, `analyzeGlucose`, and `getMealImage`

**Dependencies:** MOB-002

---

### MOB-004 — Home screen

**Goal**
Implement the Home screen as shown in the design: greeting, scan CTA, and a recent meals list that reads from `historyStore`.

**Acceptance Criteria**
- [x] Top bar: greeting "Good [morning/afternoon/evening], [name]" based on local time; SikFan wordmark with brand-coloured "Fan"; avatar circle with user initial
- [x] Scan CTA button: taps navigate to CameraScreen (modal); shows camera icon, "Scan a meal" title, subtitle "See its blood-sugar impact before you eat"
- [x] Recent meals list (from `historyStore.meals`, most recent first): each row shows food placeholder thumbnail, dish name (English), verdict chip (colour-coded), relative timestamp, sparkline
- [x] "Meal log" section header taps navigate to Log tab
- [x] "See all" link navigates to Log tab
- [x] Empty state: when `historyStore.meals` is empty, show a muted prompt to scan a first meal
- [x] Live CGM card is NOT rendered in MVP mode (controlled by a `MVP_MODE` constant in `constants.ts`)
- [x] TabBar renders with Home tab active

**Implementation Notes**
- `MVP_MODE = true` in `constants/config.ts` for this sprint — flip to `false` to unlock CGM card post-MVP
- Verdict chip colours come from the `verdictColor` helper in `theme.ts`: spike → warn, steady → good, drop → low
- Sparkline is a small static path (not Victory Native) — use `react-native-svg` `Polyline` on a 56×28 canvas; plot the last 8 glucose readings from the meal's stored curve, or a flat line if not yet available
- Greeting name is hardcoded to "Mina" for MVP; will be replaced with user profile in a later epic

**Dependencies:** MOB-002

---

### MOB-005 — Camera screen

**Goal**
Implement the camera viewfinder with shutter, auto-detect badge, flash toggle, and gallery fallback as shown in the design.

**Acceptance Criteria**
- [ ] Full-screen dark viewfinder using `expo-camera` (`Camera` component)
- [ ] Top bar: close button (dismisses modal, returns to previous screen), "Auto-detect on" pill badge with sparkles icon, flash toggle button
- [ ] Corner-bracket focus reticle centered on the viewfinder (four 34×34 corner pieces, 3px white border, 10px radius)
- [ ] "Center your plate" label below reticle
- [ ] Bottom controls: gallery picker button (left), shutter button (centre, 76×76 outer ring + white fill), placeholder icon (right, non-functional in MVP)
- [ ] Tapping shutter: captures photo, stores URI in `mealStore` via `startScan`, navigates to AnalyzingScreen
- [ ] Gallery picker: opens `expo-image-picker`, on selection stores URI and navigates to AnalyzingScreen (same path as shutter)
- [ ] Flash state toggles between on/off; updates `Camera` flash prop

**Implementation Notes**
- `expo-camera` and `expo-image-picker` are already in Expo managed workflow — add to `app.json` permissions: `["CAMERA", "MEDIA_LIBRARY"]`
- The shutter button's outer ring is `border: 5px solid rgba(255,255,255,0.9)` with a white filled inner circle — implement with two nested `View`s using `borderRadius: 999`
- On capture, write the image URI into `mealStore` with `startScan(uri)` before navigating — AnalyzingScreen will read from the store

**Dependencies:** MOB-001, MOB-002

---

### MOB-006 — Analyzing screen

**Goal**
Implement the 4-step analysis loading screen with animated scan beam, step-by-step progress bar, and detected dish chip — and trigger the actual API call in the background.

**Acceptance Criteria**
- [ ] Dark full-screen layout with ambient green radial gradient
- [ ] "Analyzing meal" pill badge at top (sparkles icon)
- [ ] Food image placeholder (248×248, border-radius 26) with animated scan beam sweeping top-to-bottom on loop
- [ ] Corner reticle (green, 30×30, 14px radius) on the image frame
- [ ] 4 steps advance automatically at ~1-second intervals: Scan → Identify → Portion → Model glucose response (MVP label: "Predicting carbs, macros & impact")
- [ ] Progress bar fills as steps advance (25% / 50% / 75% / 100%)
- [ ] Step labels below progress bar (Scan / Identify / Portion / Model), active steps brighter
- [ ] After ~1.15 s, detected dish chip animates in at the bottom of the image: dish name (English), confidence %; uses check icon in green circle
- [ ] When `mealStore.status === 'done'`, navigate to ResultsScreen automatically
- [ ] When `mealStore.status === 'error'`, navigate back to CameraScreen with an error toast
- [ ] The actual `submitMeal(imageUri)` API call is dispatched on mount via `mealStore.startScan`; this screen is purely the loading state

**Implementation Notes**
- Animated beam: use React Native `Animated.loop` + `Animated.timing` on a `translateY` from 0 to (248 - 64) with duration 1900ms, `easing: Easing.linear`
- The dish chip shows the value from `mealStore.dishName` once status is no longer `uploading` — if the API is fast, it may appear before the 1.15 s timer; use `Math.max(actualArrival, 1150ms)` to always honour the animation beat
- Do not implement real image recognition in this ticket — `mealStore.startScan` calls `submitMeal` from the API client (MOB-003); the chip shows whatever the API returns

**Dependencies:** MOB-003, MOB-005

---

### MOB-007 — Results screen

**Goal**
Implement the Results screen: dish header, verdict banner, Victory Native CGM prediction chart with confidence band, stat strip, macros card, and "Log this meal" action.

**Acceptance Criteria**
- [ ] Back button in top bar navigates to the originating screen (Home or Log)
- [ ] DishHeader: food placeholder thumbnail (56×56, radius 14), dish name (English), confidence badge with sparkles icon, portion label (rendered via `formatPortion(portionBucket, portion)` — omitted if null), edit button (non-functional in MVP)
- [ ] VerdictBanner: coloured tint background, VerdictMark icon (arrow-up / wave / arrow-down), verdict word ("Spikes" / "Stabilizes" / "Drops"), peak-rise delta (e.g. "+48")
- [ ] CGM chart using Victory Native: x-axis = minutes (0 to 180), y-axis = mg/dL; `VictoryLine` for predicted curve, `VictoryArea` for confidence band (upper/lower from GPR), `VictoryAxis` for both axes; chart height 186
- [ ] Stat strip below chart: Peak (`prediction.predicted_peak_bg` mg/dL), Peak at (`prediction.predicted_time_to_peak_minutes` min), Settles (derived: `prediction.curve[prediction.curve.length - 1].predicted_bg` mg/dL — the last point on the 180-min curve; no explicit "settles" field exists in the API)
- [ ] Macros card: horizontal bar chart (carbs / protein / fat / calories) — summed across all `dishes[]` from `MealResult`; no fiber or sugar labels (those fields are not returned by the API)
- [ ] "Log this meal" primary button: calls `historyStore.addMeal`, shows "Logged to your day" toast (1.3 s), navigates to Meal Log
- [ ] Edit button on DishHeader opens the confirm/correct bottom sheet (MOB-010 stub)
- [ ] Left icon button in action row (bolt icon) is non-functional in MVP
- [ ] Pre-bolus / InsulinCard is NOT rendered in MVP mode
- [ ] Meal data (dishes, macros, portion, image) read from `mealStore`; prediction, verdict, and curve read from `glucoseStore` — no prop drilling

**Implementation Notes**
- Install: `npm install victory-native react-native-svg` (Victory Native requires SVG peer dep)
- `VictoryArea` for confidence band: map `prediction.curve` to `[{x: minutes, y: confidence_upper, y0: confidence_lower}]`; fill with verdict colour at 20% opacity
- `VictoryLine` for predicted curve: map `prediction.curve` to `[{x: minutes, y: predicted_bg}]`; stroke = verdict colour
- The chart is prediction-only on first view; actual CGM overlay (`VictoryScatter` + second `VictoryLine`) is added in MOB-009 when polling delivers actuals
- The tinted background gradient behind the top bar uses the verdict colour at low opacity — `LinearGradient` from `expo-linear-gradient`

**Dependencies:** MOB-002, MOB-006

---

### MOB-008 — Meal Log screen

**Goal**
Implement the Meal Log screen: two-column grid of past meals, grouped by Today / Yesterday / Earlier, with verdict chip overlay and a "NEW" badge on the most recently logged entry.

**Acceptance Criteria**
- [ ] Screen title "Meal log" with meal count and week summary line below
- [ ] Legend row: stabilizes / spikes / drops colour dots
- [ ] Meals grouped by relative day (Today, Yesterday, Earlier), each group has a date header
- [ ] Grid: 2 columns, 11px gap, each card is 1:1 aspect-ratio photo placeholder with:
  - Verdict chip (top-left): icon + verdict word, white glass background
  - "NEW" badge (top-right) on the most recent entry (`historyStore.meals[0]`)
  - Caption scrim at bottom: dish name (English), time
- [ ] Tapping any card navigates to ResultsScreen, which loads that meal's data from the store
- [ ] TabBar renders with Log tab active
- [ ] Camera FAB in tab bar navigates to CameraScreen (modal)
- [ ] When `historyStore.meals` is empty: show a full-width empty state prompt

**Implementation Notes**
- `groupByDay(meals)` helper: compare `meal_timestamp` to today's date (local timezone) to assign "Today", "Yesterday", or a formatted day label for earlier
- Each card's `borderRadius 18` with `overflow: hidden` clips the photo placeholder and scrim
- The caption scrim is `LinearGradient` from transparent (top) to `rgba(20,18,16,0.82)` (bottom), positioned absolutely over the bottom of the card

**Dependencies:** MOB-002, MOB-007

---

### MOB-009 — CGM polling loop in glucoseStore

**Goal**
Implement the 5-minute polling loop that calls `analyzeGlucose` after a meal is logged, appends actuals to `glucoseStore`, and overlays them on the Results chart. Polling runs for 180 minutes then stops automatically.

**Acceptance Criteria**
- [ ] `glucoseStore.startPolling(mealId)` sets a `setInterval` at 300 000 ms, stores the handle in `pollingHandle`, sets `pollingActive = true`
- [ ] Each poll calls `analyzeGlucose(mealId)` from the API client; if `actuals` is non-null: (a) appends new readings from `actuals.curve` to `glucoseStore.readings` (deduplicates by `minutes`), and (b) writes the full `actuals` object to `glucoseStore.actuals` (overwrites each poll — MOB-011 summary cards read from here)
- [ ] Polling stops automatically after 180 minutes (36 intervals) — the interval is cleared and `pollingActive` is set to `false`
- [ ] `glucoseStore.stopPolling()` clears the interval immediately regardless of elapsed time
- [ ] `startPolling` is called automatically when a meal is logged — MOB-009 modifies `historyStore.addMeal` to call `useGlucoseStore.getState().startPolling(meal.meal_id)` after appending; no screen needs to call it directly
- [ ] ResultsScreen's CGM chart adds a second `VictoryLine` (solid, slightly thicker) for actual CGM readings when `glucoseStore.readings.length > 0`, and `VictoryScatter` dots for each reading point
- [ ] Polling is NOT started in MVP mode (`MVP_MODE = true`) — the polling call is guarded by the same constant used elsewhere

**Implementation Notes**
- `setInterval` in Zustand: store the numeric interval ID directly in state (`pollingHandle: number | null`). Calling `clearInterval(state.pollingHandle)` from inside a Zustand action is safe.
- Deduplication: on each poll result, merge new readings by checking if `readings.some(r => r.minutes === newReading.minutes)` before pushing
- The 180-minute cutoff: track `pollCount` in the store; at `pollCount >= 36`, call `stopPolling()`
- Actual CGM overlay on the chart: `glucoseStore.readings` is already in `[{minutes, glucose_mgdl}]` shape — map to `[{x: minutes, y: glucose_mgdl}]` for Victory Native

**Dependencies:** MOB-003, MOB-007, MOB-008

---

### MOB-010 — Confirm/correct dish UI stub (Epic 9 stub; wired in Epic 10)

**Goal**
Build the confirm/correct bottom sheet that appears when the user taps the edit button on the Results screen. In Epic 9 the sheet renders and accepts input but calls a no-op. Epic 10 wires it to `POST /confirm-dish` and ChromaDB.

**Acceptance Criteria**
- [ ] Tapping the edit button on DishHeader opens a modal bottom sheet
- [ ] Bottom sheet shows: detected dish name (English), confidence badge, two primary actions ("Looks right ✓" and "Correct it"), and a dismiss handle
- [ ] "Looks right" taps close the sheet and show a brief toast "Confirmed" — no API call in MVP
- [ ] "Correct it" reveals a text input pre-filled with the dish name and a "Save correction" button — tapping Save shows "Saved" toast and closes the sheet — no API call in MVP
- [ ] Both actions log to console (`console.log('[MOB-010 stub] confirm/correct:')`) so Epic 10 can grep for the integration point
- [ ] Bottom sheet is dismissible by swiping down or tapping the backdrop

**Implementation Notes**
- Use `@gorhom/bottom-sheet` (`npm install @gorhom/bottom-sheet`) — it handles keyboard avoidance and swipe-to-dismiss out of the box
- The stub's `console.log` should output the full correction payload shape that `POST /confirm-dish` will eventually receive: `{meal_id, crop_id, action: "CONFIRM" | "CORRECT" | "ADD_NEW", corrected_label}` — makes Epic 10 integration a straight substitution. Note: the identifier is `crop_id` (e.g. `"crop_0"`), not `dish_name`; `corrected_label` is the field name for the new dish name on CORRECT/ADD_NEW actions
- Do not import the API client in this ticket; keep the stub self-contained
- Implementation shipped a client-side-only optimistic name update (`mealStore.updateDishName` / `historyStore.updateDishName`, wired via `ConfirmDishSheet`'s `onCorrected` prop) so the corrected name is visible in `DishHeader` immediately for testing — this is cosmetic only, does not touch macros/glucose, and is explicitly superseded by the Epic 10 note below

**Epic 10 wiring note (do not implement in MOB-010, tracked for the real integration):**
A correction changes the dish's macros (see `FOOD-016`'s updated acceptance criteria — `CORRECT` re-runs USDA lookup for the new label server-side) and can invalidate the glucose prediction, which was computed against the *pre-correction* `total_carbs_g`. When `POST /confirm-dish` becomes real, `handleSaveCorrection` must not stop at the local cosmetic update this ticket ships — on a successful response it should re-fetch `GET /meal-status/{meal_id}` for updated macros, and if the response's `macros_changed` flag is true, re-fetch `GET /glucose/{meal_id}` for an updated prediction, rather than leaving the pre-correction values in `mealStore`/`glucoseStore` on screen.

**Dependencies:** MOB-007

---

### MOB-011 — Post-meal tracking screen

**Goal**
Build the dedicated post-meal tracking screen that renders the live BG trace overlaid against the prediction curve as the meal digests. This is the 5th core screen from app-epics.md. It becomes active once a meal is logged and the CGM polling loop (MOB-009) is running.

**Acceptance Criteria**
- [ ] Screen is navigable from the Results screen via a "Track this meal" link that appears after "Log this meal" is tapped
- [ ] Header shows dish name, log timestamp, and elapsed time since meal (e.g. "32 min ago")
- [ ] Victory Native chart: same axes as Results (0–180 min, mg/dL); shows prediction curve + confidence band from `glucoseStore.prediction`; overlays actual CGM readings from `glucoseStore.readings` as a solid `VictoryLine` + `VictoryScatter` dots
- [ ] Live status chip below chart: "Tracking · updates every 5 min" while polling is active; "Tracking complete" when `glucoseStore.pollingActive === false`
- [ ] Summary cards below chart (visible once readings arrive): actual peak bg (`glucoseStore.actuals.actual_peak_bg`), time to peak (`glucoseStore.actuals.time_to_peak_minutes`), TIR ratio (`glucoseStore.actuals.tir_ratio`) — all from `glucoseStore.actuals` (`GlucoseActuals`) which is populated by MOB-009 when `GlucoseResponse.actuals` is non-null
- [ ] "Done" button navigates to Home; triggers `glucoseStore.stopPolling()` if still active
- [ ] Screen is NOT rendered in MVP mode (`MVP_MODE = true` skips the navigation entry point on Results) — guarded by the same `MVP_MODE` constant

**Implementation Notes**
- Navigation: add a stack route `PostMealTracking` to the Home stack; Results screen pushes to it after logging when `MVP_MODE === false`
- The chart auto-refreshes by reading from `glucoseStore.readings` — no manual refresh needed; Zustand subscription causes a re-render each time `appendReading` fires
- Elapsed time: compute from `mealStore.mealTimestamp` (ISO string from API response) to `Date.now()` with `setInterval(1000 * 60)` local to this screen — update every minute
- The actuals summary cards should only render when `glucoseStore.readings.length >= 3` to avoid showing incomplete data mid-curve

**Dependencies:** MOB-007, MOB-008, MOB-009

---

### MOB-012 — Pre-meal manual blood glucose input

**Goal**
Let the user anchor the glucose prediction to their actual current reading when no CGM is connected — the expected case for most MVP users. The entered value is submitted to the backend (`API-011`) and a **real prediction is fetched for it** — there is no client-side curve math. Until an anchor exists (real CGM, found automatically per `API-010`, or manual, via this ticket), the Results screen shows no glucose-impact UI at all; macro breakdown and dish name are never gated by this and always show immediately regardless of anchor status.

**Revision note (2026-08-06):** this ticket originally planned a purely
client-side "shift the curve by `delta`" design assuming a baseline curve
always exists to offset. That assumption doesn't hold — per three confirmed
user stories, when there's no CGM reading, no glucose-impact UI should show
at all (nothing to shift), and once a manual value is submitted it should
produce a real, freshly-computed prediction, not an approximation. Rewritten
below to submit-then-refetch. See `plans/API-011-plan.md`'s decision log for
the full reasoning.

**Acceptance Criteria**

*api/client.ts — bugfix, prerequisite for this ticket*
- [ ] `extractErrorMessage`/`ApiError` currently does `data?.detail ?? data?.message`, but this backend's error bodies are `{"detail": {"code": "...", "message": "..."}}` — `detail` is an object, not a string, so `ApiError` cannot reliably expose the error `code`. Fix `ApiError` to carry a `code: string | undefined` field parsed from `detail.code`, and `message` from `detail.message` (falling back to the old flat-string handling if `detail` isn't an object, for resilience against other endpoints' error shapes)

*api/glucose.ts*
- [ ] Add `submitManualGlucose(mealId: string, glucoseMgdl: number): Promise<void>` — `POST /manual-glucose` with `{meal_id: mealId, glucose_mgdl: glucoseMgdl}`

*glucoseStore*
- [ ] `preMealGlucose`/`preMealTrend` (defined in MOB-002) are now populated **only from a successful `GlucoseResponse`** (real CGM via API-010, or the re-fetch after manual submission) — never set directly from raw user keypad input
- [ ] Add `predictionStatus: 'idle' | 'loading' | 'ready' | 'needs_manual_entry' | 'error'` to `glucoseStore`, driven by fetching `GET /glucose/{meal_id}`: `loading` while in flight, `ready` on 200 (`setPrediction` called as today), `needs_manual_entry` specifically on a `no_pre_meal_glucose` `ApiError.code`, `error` on any other failure (503 model error, etc. — entering a BG will not fix these, so the UI must not offer manual entry for them)
- [ ] Add `submitManualGlucose(mealId: string, glucoseMgdl: number): Promise<void>` action — calls `api/glucose.ts`'s `submitManualGlucose`, then re-fetches `GET /glucose/{meal_id}` and updates `prediction`/`preMealGlucose`/`preMealTrend`/`predictionStatus` from the real response, exactly the same code path a successful automatic (CGM-found) fetch already uses
- [ ] `mealStore.reset()` resets `predictionStatus` to `idle` so each new scan starts clean

*GlucosePad component (`components/GlucosePad.tsx`)*
- [ ] Bottom-sheet numeric keypad; accepts `initial: number | null`, `last: number` (most recent stored reading, default 0 if none), `onSet(value: number): void`, `onClose(): void` — `onSet` now triggers `glucoseStore.submitManualGlucose`, not a local state write
- [ ] Large number display (50 sp font) with "mg/dL" label; placeholder "– – –" when empty
- [ ] Real-time range label below the number: `In range` (70–180, good colour), `High` (> 180, warn colour), `Low` (< 70, low colour), `too low to log` (< 40), `out of range` (> 400)
- [ ] "Last reading {last}" quick-fill pill button (clock icon); hidden when `last` is 0
- [ ] 3-column keypad: digits 1–9 on rows 1–3, empty / 0 / backspace on row 4; max 3 digits entered
- [ ] Submit button: label `Anchor projection` on first entry, `Update reading` when editing; disabled and greyed when value outside 40–400; shows a brief loading state while `submitManualGlucose` is in flight
- [ ] Tapping the backdrop calls `onClose`; swipe-down on the sheet handle also closes
- [ ] Implemented with `@gorhom/bottom-sheet` (already a dependency from MOB-010)

*Analyzing screen (extends MOB-006)*
- [ ] A prompt card is rendered at the bottom of the screen, above the progress section, while the scan runs
- [ ] **Unset state**: dark glass card with drop icon (good-colour ring), "Add your blood sugar" title, "Enter it now while we analyze" subtitle, dashed `– – –` mg/dL placeholder on the right; tapping opens `GlucosePad`
- [ ] **Set state**: card shows entered value (large font), "Blood sugar anchored" label, colour-coded range badge (in-range / high / low), edit icon button to re-open pad
- [ ] Submitting here requires `mealStore.mealId` to already exist (set once `submitMeal` resolves, early in the scan) — `POST /manual-glucose` needs a `job_status` record to look up its `created_at` anchor; disable/hide the prompt card until `mealId` is non-null rather than allowing a submit with nothing to attach it to
- [ ] Navigation hold: if the pad is open when the scan's 5.2 s timer fires, navigation to Results is deferred until the pad is dismissed or the value is confirmed (matches design's `padOpenRef` / `wantDone` logic)

*Results screen (extends MOB-007)*
- [ ] Macro breakdown (`macrosCard`) and dish header (`DishHeader`) render immediately once analysis completes, regardless of `predictionStatus` — never gated by glucose-anchor state
- [ ] While `predictionStatus === 'loading'`: no glucose UI shown yet (avoid a flash of the manual-entry CTA before the automatic CGM check has even resolved)
- [ ] `predictionStatus === 'needs_manual_entry'`: render `BloodSugarCard`'s CTA in place of `VerdictBanner`/chart/stat strip entirely — brand-coloured card, "Add your blood sugar" / "Anchor this projection to your current reading", drop icon, dashed `– – –` mg/dL placeholder; tapping opens `GlucosePad`. Nothing dimmed or partially shown — there is no curve to show yet
- [ ] `predictionStatus === 'ready'`: `VerdictBanner`, CGM chart, stat strip render normally (existing MOB-007 behavior); a small surface card shows the anchor value (24 sp font), "Anchored to your reading" label, colour-coded range badge, edit button (re-opens `GlucosePad`, calling `submitManualGlucose` again on change — only relevant when the anchor was manual; if it came from real CGM, still editable as a manual override for this view)
- [ ] `predictionStatus === 'error'`: generic failure state (e.g. "Couldn't load glucose prediction, try again") — do not offer manual entry, since a model/server error isn't fixed by a BG value

**Implementation Notes**
- `GlucosePad` is shared between `AnalyzingScreen` and `ResultsScreen` — export it from `components/GlucosePad.tsx`
- No client-side curve math anywhere — the prediction shown after manual entry is always the real `GET /glucose/{meal_id}` response fetched after `POST /manual-glucose` succeeds, identical in shape and trust level to the automatic-CGM case
- `last` prop to `GlucosePad`: pass `glucoseStore.preMealGlucose ?? 0`; if non-zero this shows the quick-fill button pre-populated with the previous reading
- The `MVP_MODE` flag does not affect this feature — manual BG entry is the primary fallback input method in MVP mode (automatic CGM detection, per API-010, is tried first and used silently when available)

**Dependencies:** MOB-002, MOB-006, MOB-007, MOB-010, API-010, API-011

---

### MOB-013 — Real meal photos in thumbnails

**Goal**
Replace the placeholder boxes used for meal thumbnails on Home, Results, and Meal Log with the actual captured/analyzed photo. `getMealImage` (MOB-003) already fetches the image and writes it to a local `file://` URI, but no screen currently calls it, and `LoggedMeal.image_url` is hardcoded to `null` when a meal is logged — this ticket closes that gap end-to-end.

**Acceptance Criteria**
- [ ] `mealStore` retains the captured photo's local URI (from `CameraScreen`'s `startScan(uri)`) for the duration of a scan, so `AnalyzingScreen` and `ResultsScreen` can render it immediately without a network round-trip
- [ ] `ResultsScreen.handleLog` passes a real value for `image_url` (the captured local URI, or the API's `image_url` from `MealResult` if present) instead of hardcoding `null` when calling `historyStore.addMeal`
- [ ] `HomeScreen`'s `MealRow` thumbnail renders the meal's actual photo via `<Image source={{uri: meal.image_url}}>`, falling back to the existing placeholder `View` when `image_url` is null (e.g. meals logged before this ticket shipped)
- [ ] `ResultsScreen`'s `DishHeader` thumbnail (56×56) renders the actual photo the same way, with the same placeholder fallback
- [ ] `MealLogScreen`'s grid tiles render the actual photo as the card background (replacing `cardPhoto`), with the same placeholder fallback; the verdict chip, NEW badge, and caption scrim continue to render on top
- [ ] For meals where only a remote `image_url` is available (no local URI — e.g. app was restarted since the meal was logged), the thumbnail lazily calls `getMealImage(meal_id)` and caches the resulting local URI so repeated renders don't re-fetch
- [ ] Broken/missing image fetches (404, network error) fall back to the placeholder `View` rather than a broken `<Image>` icon or crash

**Implementation Notes**
- `LoggedMeal.image_url` is already typed as `string | null` in `store/types.ts` — no type changes needed, just start populating it
- Prefer the locally captured `file://` URI over a `getMealImage` round-trip whenever it's still available (same session) — only fall back to the network fetch for older/reloaded state
- A small `useMealThumbnail(meal: LoggedMeal)` hook (in `hooks/` or colocated with `store/`) is a reasonable place to centralize the "local URI, else fetch-and-cache, else placeholder" logic so Home/Results/Log don't each reimplement it
- Cache fetched URIs in a plain in-memory `Map<mealId, uri>` (module-level, not in Zustand) — thumbnails are a rendering concern, not app state that needs to survive reloads

**Dependencies:** MOB-002, MOB-003, MOB-004, MOB-005, MOB-007, MOB-008

---

### MOB-014 — Composite dish breakdown on Results — IMPLEMENTED

**Status note (backfilled 2026-08-29):** shipped 2026-08-28 and verified then
(`expo-doctor` 20/22, `tsc --noEmit` clean, clean-cache Metro bundle of 2681
modules) — but the ticket itself was never written into this file, only
referenced in passing from the Known Issues section below and from
`docs/API-LAYER-TICKETS.md`'s API-012. Recorded here so the Epic 9 sequence has
no gap and so MOB-016 has something to declare a dependency on.

**Goal**
Surface FOOD-019's component decomposition in the Results screen's Macros card,
so a composite dish ("tomato egg rice with shredded chicken and bok choy") shows
what it was actually broken into rather than a single opaque carb number.

**What shipped** (`mobile/screens/ResultsScreen.tsx`)
- `compositeDishes` (~L214) filters `dishes` to those with a non-empty
  `.components`; `totalComponents` counts across them
- A collapsible "Breakdown · N items" toggle (~L433) reveals a row per
  component (~L448), with the dish name as a sub-heading when more than one
  dish is composite
- `componentGrams()` / `componentCarbs()` (~L47–56) derive each row's grams and
  carbs from `component.proportion * dishPortionG` and
  `per_100g.carbs_g * grams / 100` — display-side arithmetic over server
  values, no macro estimation on the client
- An estimated-macros notice (~L423) names any dish carrying
  LLM-`estimated` rather than USDA-backed components, so a partly-guessed
  total is never presented as measured

**Known follow-ups** (both filed separately, not defects in this ticket)
- MOB-015 — a *non-composite* second dish still has no rendering path at all
- MOB-017 — the macro bar colours in this card don't match `theme.jsx`

**Dependencies:** MOB-007, FOOD-019, API-012

---

### MOB-015 — Results screen only surfaces the primary dish; extra dishes are invisible

**Goal**
A meal with more than one detected dish (e.g. a rice bowl + a separate plate
of dumplings) only shows the *first* dish (`dishes[0]`) anywhere on the
Results screen — its name in the header, its `ConfirmDishSheet`, and (if
composite) its ingredient "Breakdown." Every other dish in `dishes[]` is
folded into the top Macros card's totals (`sumMacros` reduces over the full
array) with **zero UI representation**: no name, no card, no confirm/correct
affordance. Confirmed live 2026-08-29 — a scan returned `tomato_egg_rice_with_shredded_chicken_
and_bok_choy` (6.36g carbs, shown with its 5-item breakdown) and a second
dish `dumplings` (9.96g carbs, `components: null`) that never appeared
anywhere on screen; the Macros card correctly totalled 16.32g but nothing
explained where the extra ~10g came from.

**Acceptance Criteria**
- [ ] Every dish in `mealStore.dishes` (or `loggedMeal.dishes`) gets *some*
      visible representation on Results — at minimum a name + its own
      carbs_g, even for a non-composite dish with no `.components`
- [ ] `estimatedDishes`/`dishesMissingMacros` messaging (which already names
      dishes by iterating the full array) is the existing precedent to
      follow for how a second dish's name reaches the UI
- [ ] Decide (see plan) whether dish 2+ gets a full `DishHeader`-style card
      with its own edit/confirm affordance, or a lighter-weight
      name+macros row — `ConfirmDishSheet` is currently wired to exactly one
      `cropId` (`primaryDish.crop_id`), so multi-dish correction is a real
      scope question, not just a rendering one
- [ ] `formatDishName()` used for any newly-rendered dish name, per the
      existing repo-wide rule (`CLAUDE.md` "Mobile Dish-Name Display")

**Implementation Notes**
- Root cause is in `mobile/screens/ResultsScreen.tsx`: `primaryDish =
  (loggedMeal ? loggedMeal.dishes[0] : scanDishes[0])` (~line 100) drives
  the header/confirm sheet, and `compositeDishes = dishes.filter(d =>
  d.components && d.components.length > 0)` (~line 214) drives the
  Breakdown section — neither loop covers a simple, non-primary dish
  (`components: null`, not `dishes[0]`)
- `macros` (top Macros card totals) is already correct — `sumMacros` in
  `mobile/store/mealStore.ts` (`applyResult`) sums `carbs_g`/etc. across
  **all** dishes; this ticket is about visibility of the breakdown, not the
  totals math

**Dependencies:** MOB-007, MOB-010

---

### MOB-016 — Macro correction UI

**Goal**
Let the user say the carbs are wrong, and why, from the Results screen — then
see both the macro card and the glucose projection update in place. Ported from
`results.jsx` (`MacroCard`, `IngredientSheet`, `AddIngredientSheet`) in the
Claude Design project `23216dca-776e-4021-ae6d-814c5407e7e2`, which specifies
the whole interaction. Backed by API-013. Full plan and decision log:
`plans/MOB-016-plan.md`.

The design's own framing:
> The scan's carb estimate drives the whole projection, so the user can tell us
> it reads too low / too high instead of typing exact grams.
> Misidentified food is a different kind of answer — send them to the thing
> that's actually wrong instead of guessing a percentage.

**Acceptance Criteria**
- [ ] Correction is an **inline block inside the Macros card**, collapsed by
      default — positioned after the estimated-macros notice (~L423–431) and
      before the breakdown toggle (~L433), the design's own ordering
- [ ] Entry row (44pt min height) has both states: untouched → `surfaceSoft`
      icon square, "Estimate look off?"; corrected → `brand` square, "Edit your
      correction"
- [ ] Panel is driven by an explicit `correcting` flag (not by whether
      corrections exist) and has a close (X) button beside "Do the carbs look
      right?"
- [ ] Segmented control: **Too high · Looks right · Too low**
- [ ] Reason chips filtered by direction, so no nonsensical pair is offered —
      too high → *Portion was smaller* / *It's mostly broth* / *Not eating the
      full portion*; too low → *Portion was bigger* / *More food than it looks*
- [ ] Magnitude chips *A little* / *A lot*; picking a reason pre-selects
      "little" (`results.jsx:611`)
- [ ] Footer row "An ingredient is wrong or missing" always available inside
      the panel → opens fix mode; "Undo all" appears once anything is corrected
      → `POST /reset-corrections`
- [ ] "Undo all" reverts the carb correction **and** any ingredient
      swaps/removals/additions, including the saved breakdown used by future
      scans — but not a learned portion prior. Don't imply it resets everything;
      the response's `decomposition_reverted`/`prior_retained` say what actually
      happened (API-013)
- [ ] Fix mode on the breakdown: header becomes "Which ingredient is wrong?"
      with a **Done** button replacing the chevron; rows become buttons opening
      `IngredientSheet`; a dashed "Something's missing" row opens
      `AddIngredientSheet`; swapped/added rows carry "fixed"/"added" pills
- [ ] The last remaining component can't be removed: `IngredientSheet`'s "It's
      not in my dish" row is disabled (with a short reason) when removing it
      would empty the dish. The server refuses this with 422 `empty_dish`
      regardless — the client guard exists so the user never reaches a dead end,
      not as the enforcement. If the 422 does arrive, surface it inline like any
      other correction failure
- [ ] A dish with no `portion_g` gets no fix-mode affordance at all —
      `/correct-ingredients` 422s with `no_portion_estimate`, since proportions
      can't be converted to grams without one
- [ ] "Carbs updated" summary row at the top of the Macros card when the total
      has changed — old value struck through → `arrowR` → new value, with a
      `+Ng`/`−Ng` `brand` pill (`results.jsx:390–403`)
- [ ] The confirmation line's percentage is **derived from the factor the server
      applied**, not hardcoded to 15/40 — FOOD-020 D6 makes `too_low` factors
      reciprocals of `too_high` so corrections are reversible, so an up-a-lot
      correction reads "raised 67%" (1/0.60), not "raised 40%"
- [ ] Every correction **and** the reset call one shared
      `refreshAfterCorrection(mealId)` — macros and the glucose curve both
      update, through a single path
- [ ] No client-side macro or curve math anywhere
- [ ] `sameFood()` not ported (see Implementation Notes)
- [ ] Requests fire on selection (no submit button); controls disable in
      flight; a failure shows an **inline** error and reverts the selection —
      `GlucosePad`'s pattern (`GlucosePad.tsx:59-73`), not a toast, so the user
      keeps their context
- [ ] Candidates fetched lazily on first entry into fix mode, not on mount —
      it's an LLM-backed call and most meals are never corrected
- [ ] Hidden entirely for logged meals; gated on `!loggedMeal && mealId && cropId`
- [ ] All colours from `constants/theme.ts`; `formatDishName()` for any dish
      name rendered (CLAUDE.md rules)
- [ ] `/mobile_ticket_check` passes

**Implementation Notes**
- **Inline block, not a bottom sheet.** The first draft assumed a sheet, to
  match `ConfirmDishSheet`. The design doesn't: the correction is about the
  numbers directly above it, and a sheet would hide the macro bars at the exact
  moment the user is judging them. The two *ingredient* interactions stay
  bottom sheets, as the design has them — those are modal choices from a list.
- **Entry point is the Macros card, not the `dishHeader` pencil.** That pencil
  is identity-shaped: it opens `ConfirmDishSheet` to correct *what the dish is*.
  Putting a quantity correction there conflates the two questions the design
  deliberately separates.
- **No client-side scaling**, even though the design mock does it (`cf` in
  `ResultsScreen`). The glucose curve can't be scaled on the client — it comes
  from a trained model against `total_carbs_g` — so a local multiply would leave
  the two views disagreeing until a refetch. Same reasoning already recorded in
  `plans/API-011-plan.md` for rejecting a client-side curve shift.
- **`sameFood()` (`results.jsx:319`) is not ported** — a word-set match over
  food names is the string-similarity heuristic CLAUDE.md forbids. The server
  excludes present components at suggestion time instead (FOOD-021); the client
  renders the read-only "already counted" row for exact matches only and needs
  no matcher.
- **Extract `refreshAfterCorrection(mealId)`** from `handleCorrected`
  (L154–173): `pollMealStatus` → `refreshFromResult` → `fetchPrediction`. The
  name-reapply step at L164 stays in `handleCorrected` — it exists because
  `refreshFromResult` overwrites the display name with the server's normalized
  slug, which is specific to the name-correction path.
- **New files:** `components/CarbCorrection.tsx`, `components/IngredientSheet.tsx`,
  `components/AddIngredientSheet.tsx`; `api/meals.ts` gains `correctMacros`,
  `correctIngredients`, `getIngredientCandidates`, `resetCorrections` beside
  `confirmDish` (L27–33), via the existing `apiFetchJson`.
- **Icons (verified against `theme.jsx`, 2026-08-29):** the design's right-arrow
  is `Icon name="arrowR"`, defined in `theme.jsx`'s own SVG set. That set is a
  web-preview primitive and is **not** ported to the app — every RN screen uses
  `@expo/vector-icons` `Ionicons` instead (`CameraScreen`, `AnalyzingScreen`,
  `MealLogScreen`). So use `Ionicons name="arrow-forward"`, and `"sparkles"` for
  the confirmation line (already used at `CameraScreen.tsx:96`). Do **not** add an
  `arrowR` token to `theme.ts`.
- **`th.brandTint` does not exist — confirmed.** `results.jsx` references it with
  a `|| th.surfaceSoft` fallback, but `theme.jsx`'s palettes define only
  `canvas/canvas2/surface/surfaceSoft/ink/inkSoft/inkFaint/hair/shadow/brand` plus
  the `good`/`warn`/`low` groups. Use `surfaceSoft`; do not invent the token.
- **Multi-dish limitation:** this targets the primary dish's `crop_id`, so a
  multi-dish meal can only have its first dish corrected until MOB-015 lands.

**Dependencies:** API-013 (all four routes), MOB-007 (Results screen), MOB-010
(`ConfirmDishSheet`, `@gorhom/bottom-sheet`), MOB-012 (`GlucosePad`'s
inline-error pattern), MOB-014 (the breakdown UI this extends). Related:
MOB-015.

---

### MOB-017 — Macro bar colours don't match the design source of truth

**Goal**
`ResultsScreen.tsx`'s macro bars (~L418–421) colour carbs `brand`, protein
`good.fg`, fat `warn.fg`, calories `low.fg`. The design's `MacroBars`
(`results.jsx`, project `23216dca-776e-4021-ae6d-814c5407e7e2`) colours carbs
`warn.fg`, protein `good.fg`, fat `low.fg`, calories `brand`. Three of the four
are wrong, and carbs — the number this whole app is about — is the most visible
of them.

**Acceptance Criteria**
- [ ] Macro bar colours match `results.jsx`'s `MacroBars` exactly: carbs
      `warn.fg`, protein `good.fg`, fat `low.fg`, calories `brand`
- [ ] Colours read from `constants/theme.ts`, no hardcoded hex (CLAUDE.md rule)
- [ ] Checked side-by-side against the design before closing — if `theme.jsx`
      has drifted since, that is the authority, not this ticket's snapshot
- [ ] `/mobile_ticket_check` passes

**Implementation Notes**
- **Verified first-hand against `theme.jsx` (2026-08-29)**, not inferred.
  `MacroBars`'s `rows` array reads exactly: `carbs → th.warn.fg`,
  `protein → th.good.fg`, `fat → th.low.fg`, `calories → th.brand`.
- The same function carries bar-scaling maxima worth matching while you're in
  there: carbs 90, protein 50, fat 40, calories 800 — each bar's fill is
  `min(100, g / max * 100)%`. Calories renders with **no `g` suffix**
  (`unit: ''`); the other three append `g`.
- Surfaced 2026-08-29 while planning MOB-016 and deliberately kept out of it: a
  silent visual change bundled into a feature ticket is hard to review and
  harder to attribute later. It is a one-line-per-bar fix, but it changes the
  look of the primary screen, so it gets its own commit.
- Same class of drift as MOB-001's fabricated `theme.ts`, corrected during
  MOB-004 (see CLAUDE.md "Mobile Design System — Source of Truth").

**Dependencies:** MOB-007, MOB-014

---

## Known Issues (deferred, not blocking)

Surfaced 2026-08-28 by `npx expo-doctor` while verifying MOB-014 (composite
dish breakdown UI, FOOD-019). Two of the four findings that day (`eas-cli`
stray install, 6 packages a patch behind SDK 56) were fixed in the same pass
— see `mobile/package.json` — and re-verified clean via `expo-doctor` (20/22,
up from 18/22), `tsc --noEmit` (no new errors), and a clean-cache Metro
bundle (2681 modules, no errors). The two below were deliberately deferred:

- **Hermes V1 memory regression.** `expo@56.0.21`'s Hermes build
  (`250829098.0.10`) is affected by a known memory regression; the fix is
  upgrading to **Expo SDK 57** (`expo@^57.0.9`+, React Native 0.86.2+) — a
  major version jump, not a patch. Deferred because a major SDK upgrade is
  exactly the class of change that broke native module registration
  silently during MOB-005 (see `.claude/skills/mobile_ticket_check/SKILL.md`
  incident notes) and deserves its own dedicated pass with full
  re-verification, not a bundled fix alongside unrelated ticket work.
- ~~**Prebuild / app.json sync.**~~ **RESOLVED 2026-08-28.** `mobile/ios/` is
  tracked in git (`project.pbxproj`, `Podfile`, `AppDelegate.swift`, etc. —
  only build artifacts are gitignored) — this project is confirmed on the
  **bare workflow, not Prebuild/CNG**. `app.json`'s native fields
  (`orientation`, `icon`, `userInterfaceStyle`, `ios`, `android`, `plugins`)
  are inert for anything already reflected in `ios/`; native changes go
  directly into `mobile/ios/` from now on. Recorded as a standing rule in
  `CLAUDE.md`'s "Mobile Native Build" section — no code change needed, no
  ticket required, this was purely a documentation gap.

**Hermes/SDK 57 next step:** re-run `npx expo-doctor` to see current status;
it's a candidate for its own ticket if it starts causing real friction (a
memory-related crash, for instance) before then.
