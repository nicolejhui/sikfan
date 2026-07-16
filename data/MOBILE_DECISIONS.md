# Epic 9 — Mobile App Architecture Decisions
 
> Decisions locked before ticket writing begins. Reference this document
> when writing Epic 9 tickets to avoid re-litigating these choices.
 
---
 
## Decision 1 — State Management: Zustand (centralized store)
 
**Choice:** Zustand, used as a single centralized global store.
 
**What this determines:**
Meal results, CGM readings, polling status, and glucose predictions all live
in one global Zustand store accessible by any screen. Changes in one place
propagate everywhere automatically — no prop drilling, no screen-local
polling loops.
 
**Why not Redux:**
Zustand gives the same centralized pattern with a fraction of the boilerplate.
For a solo project at this scale, the setup cost of Redux isn't justified.
 
**Why not Context API / local state:**
The CGM polling loop writes live BG data that must surface simultaneously on
the results screen, the BG prediction screen, and the post-meal tracking screen.
Local-first state can't share that cleanly without reintroducing the complexity
Zustand removes.
 
**Store slices to plan for:**
- `mealStore` — current meal analysis job, status, dish results
- `glucoseStore` — BG predictions, CGM readings, polling interval handle
- `historyStore` — past meal logs for the history screen
---
 
## Decision 2 — Charting Library: Victory Native
 
**Choice:** Victory Native
 
**What this determines:**
The BG prediction curve and post-meal CGM overlay are rendered using
Victory Native's `VictoryLine` + `VictoryArea` combo. The confidence band
shading (upper/lower bounds from the GPR) maps directly to `VictoryArea`.
 
**Why not Recharts:**
Recharts is a web library. Running it in React Native requires a WebView
bridge, which adds latency and complexity on a chart that updates every
5 minutes from live CGM data.
 
**Why not react-native-svg-charts:**
Less actively maintained. Confidence interval rendering would require
custom implementation.
 
**Key Victory Native components expected:**
- `VictoryChart` — chart container
- `VictoryLine` — predicted BG curve + actual CGM trace overlay
- `VictoryArea` — confidence band shading
- `VictoryScatter` — individual CGM data points
- `VictoryAxis` — time (x) and mg/dL (y) axes
---
 
## Decision 3 — CGM Refresh Strategy: Polling
 
**Choice:** Polling every 5 minutes
 
**What this determines:**
The app requests new CGM readings from the API on a fixed 5-minute interval.
No WebSocket connection, no push notification infrastructure required.
 
**Why polling is appropriate here:**
Dexcom itself only updates every 5 minutes. Polling at the same cadence loses
nothing in practice — a push architecture would deliver the same data on the
same schedule with significantly more infrastructure complexity.
 
**Implementation note:**
The polling loop lives in the Zustand `glucoseStore` as a `setInterval` handle,
started when a meal is logged and cleared when the post-meal tracking window
closes (180 minutes post-meal). The interval handle is stored in the store so
any screen can cancel it if needed.
 
---
 
## Summary Table
 
| Decision | Choice | Rejected Alternatives |
|---|---|---|
| State management | Zustand (centralized) | Redux, Context API / local state |
| Charting library | Victory Native | Recharts, react-native-svg-charts |
| CGM refresh | Polling every 5 min | WebSockets, APNs push notifications |