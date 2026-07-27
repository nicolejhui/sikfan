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
Let the user anchor the glucose projection to their actual current reading before they see results. No CGM in MVP mode — the user types in their reading on the Analyzing screen (while the scan runs) or on the Results screen (before committing). The entered value shifts the entire predicted curve; the Results screen prediction section is gated behind it.

**Acceptance Criteria**

*glucoseStore*
- [ ] Add `setPreMealGlucose(value: number | null)` action to `glucoseStore`; it writes to the existing `preMealGlucose` state field (defined in MOB-002) and sets `preMealTrend` to `null` (manual entry has no trend arrow)
- [ ] `mealStore.reset()` calls `glucoseStore.setPreMealGlucose(null)` so each new scan starts clean

*GlucosePad component (`components/GlucosePad.tsx`)*
- [ ] Bottom-sheet numeric keypad; accepts `initial: number | null`, `last: number` (most recent stored reading, default 0 if none), `onSet(value: number): void`, `onClose(): void`
- [ ] Large number display (50 sp font) with "mg/dL" label; placeholder "– – –" when empty
- [ ] Real-time range label below the number: `In range` (70–180, good colour), `High` (> 180, warn colour), `Low` (< 70, low colour), `too low to log` (< 40), `out of range` (> 400)
- [ ] "Last reading {last}" quick-fill pill button (clock icon); hidden when `last` is 0
- [ ] 3-column keypad: digits 1–9 on rows 1–3, empty / 0 / backspace on row 4; max 3 digits entered
- [ ] Submit button: label `Anchor projection` on first entry, `Update reading` when editing; disabled and greyed when value outside 40–400
- [ ] Tapping the backdrop calls `onClose`; swipe-down on the sheet handle also closes
- [ ] Implemented with `@gorhom/bottom-sheet` (already a dependency from MOB-010)

*Analyzing screen (extends MOB-006)*
- [ ] A prompt card is rendered at the bottom of the screen, above the progress section, while the scan runs
- [ ] **Unset state**: dark glass card with drop icon (good-colour ring), "Add your blood sugar" title, "Enter it now while we analyze" subtitle, dashed `– – –` mg/dL placeholder on the right; tapping opens `GlucosePad`
- [ ] **Set state**: card shows entered value (large font), "Blood sugar anchored" label, colour-coded range badge (in-range / high / low), edit icon button to re-open pad
- [ ] Navigation hold: if the pad is open when the scan's 5.2 s timer fires, navigation to Results is deferred until the pad is dismissed or the value is confirmed (matches design's `padOpenRef` / `wantDone` logic)

*Results screen (extends MOB-007)*
- [ ] `BloodSugarCard` is rendered immediately below `DishHeader`, before the prediction content, in all three layouts (result / curve / minimal)
- [ ] **Unset state**: brand-coloured CTA card, "Add your blood sugar" / "Anchor this projection to your current reading"; drop icon; dashed `– – –` mg/dL placeholder; tapping opens `GlucosePad`
- [ ] **Set state**: surface card showing entered value (24 sp font), "Anchored to your reading" label, colour-coded range badge, edit button
- [ ] **Prediction gate**: `VerdictBanner`, CGM chart, stat strip, macros card, and actions row are rendered at `opacity: 0.4` with `pointerEvents: none` and a subtle desaturation while `preMealGlucose === null`; they animate to full opacity/interaction once a value is entered (transition 350 ms)
- [ ] **Curve shift**: when `preMealGlucose` is set, the projected peak, settle, and every point on the glucose curve are offset by `delta = preMealGlucose - baselineStartGlucose` so the chart reflects the user's actual baseline; `baselineStartGlucose` is a constant derived from the API's `pre_meal_glucose` field in `GlucoseResponse`

**Implementation Notes**
- `GlucosePad` is shared between `AnalyzingScreen` and `ResultsScreen` — export it from `components/GlucosePad.tsx`
- `preMealGlucose` lives in `glucoseStore`; both screens read it via `useGlucoseStore(s => s.preMealGlucose)` — no prop-drilling, no duplicate local state
- The curve shift on Results is purely presentational: derive the shifted meal object locally in the screen (`const shift = preMealGlucose - baselineStartGlucose; const shiftedMeal = {...meal, peak: meal.peak + shift, ...}`) — do not mutate the store or the API response
- `last` prop to `GlucosePad`: pass `glucoseStore.preMealGlucose ?? 0`; if non-zero this shows the quick-fill button pre-populated with the previous reading
- The `MVP_MODE` flag does not affect this feature — manual BG entry is the primary (and only) input method in MVP mode

**Dependencies:** MOB-002, MOB-006, MOB-007, MOB-010

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
