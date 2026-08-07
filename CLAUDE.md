# SikFan - Carb Counter

## Environment
<!-- - Conda env: carb_counter
- Always activate with `conda activate carb_counter` before running anything
- Python: run scripts with `python`, notebooks with Jupyter -->

## Environment
- Conda env: carb_counter
- Hook auto-activates env before every bash command via .claude/hooks.json
- Device: Apple Silicon MPS (never use CUDA)
- If a script still runs in wrong env, check hook path matches `which python` output
```

**Your new folder structure:**
```
CARB-COUNTER/
├── .claude/
│   └── hooks.json    ← new
├── CLAUDE.md
├── TICKETS-v2.md
├── GLUCOSE-TICKETS.md
...

## Project Structure
- `data/CNFOOD-241/` — train600x600/ and val600x600/ folders
- `data/assorted_breakfast.jpeg`, `hot_pot_christmas.jpeg`, `fungus_dessert_soup.jpeg` — test images
- `FastSAM-s.pt` — segmentation model (use this, not FastSAM-x)
- `jan26-2026.ipynb` — existing exploration notebook, use as reference

## Code Structure
- `pipeline/` — core model pipeline modules
- `scripts/` — one-off utility scripts (augmentation, labeling)
- `analyze_meal.py` — end-to-end integration function (FOOD-015 gate)
- `jan26-2026.ipynb` — reference only, do not modify

## Data Directory Note
Current images live directly in `data/` 
New pipeline data goes in:
- `data/dishes/{dish_name}/` — labeled images
- `data/unlabeled/` — images awaiting labeling
- `data/embeddings/` — ChromaDB persistent store
- `data/macro_cache/` — cached USDA API results

## Key Technical Constraints
- MPS backend only: always use `device = "mps"` not "cuda"
- BGR->RGB conversion required after any cv2.imread() call
- Do NOT use ImageFolder or hardcoded class lists anywhere
- Embedding store persists to `data/embeddings/` via ChromaDB

## Architecture
FastSAM (segmentation) → CLIP ViT-B/32 (embedding) → ChromaDB (vector store)
No fixed class lists. Dishes are added dynamically via user confirmation.

## Planning
Before implementing any ticket, save the plan to plans/{TICKET-ID}-plan.md
Always add a decision log and include proposed approaches, what was rejected and why.

## Completed
- Sprint 1: FOOD-001, FOOD-002, FOOD-003
- Sprint 2: FOOD-004, FOOD-005, FOOD-005b
- Sprint 3: FOOD-007, FOOD-008, FOOD-006
- Sprint 4: FOOD-009, FOOD-010
- Sprint 5: Nutrition (FOOD-012, FOOD-013, FOOD-014)
- Sprint 6: QA and E2E pipeline (FOOD-011, FOOD-015)

## Current Sprint
Epic 9 — Mobile App (React Native MVP). MOB-001–MOB-004 done, see docs/MOBILE-TICKETS.md.

## Mobile Ticket Verification (required)
Run the `/mobile_ticket_check` skill after implementing any MOB-* ticket, before marking it complete — do not rely on `tsc` alone. It runs `expo-doctor`, `tsc --noEmit`, and a clean-cache Metro restart with bundling verification. MOB-005 shipped two dependency-drift breakages (`viewManagersMetadata of null`, then a `babel-preset-expo` hoisting failure) that `tsc` never caught and cost a full debugging session — see `.claude/skills/mobile_ticket_check/SKILL.md` for details.

## Mobile Design System — Source of Truth
- Upstream: Claude Design project "SikFan" (project id `23216dca-776e-4021-ae6d-814c5407e7e2`), file `theme.jsx`. This is the canonical definition of every color token, palette (sunrise/matcha/mist), and verdict color group.
- In-repo mirror: `mobile/constants/theme.ts` — ported field-for-field from `theme.jsx` (same palette names, hex values, and `{fg, deep, tint, ring}` verdict-group shape). Keep it in sync if `theme.jsx` changes.
- Rule for every mobile ticket: import colors from `constants/theme.ts` (`defaultPalette.canvas/surface/ink/inkSoft/inkFaint/brand/hair/shadow`, `verdictColor()`) — never hardcode a hex value in a screen. MOB-001's original `theme.ts` was built without checking `theme.jsx` and used invented dark/neon colors that didn't match the design at all; that was corrected during MOB-004.
- If a new screen needs a color/spacing token not yet in `theme.ts`, pull it from `theme.jsx` (via the Design MCP or by asking Nicole) rather than guessing.

## Frozen API Schemas (do not change without versioning)

### analyze_meal(image_path) → dict
Stable since FOOD-015. See docs/TICKETS-v2.md for full schema.

### analyze_glucose(meal_id) → dict
Stable since GLUC-009. Full shape:
```
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
        "model_confidence": "high" | "medium" | "low",
        "outcome": {
            "label": "spike" | "steady" | "drop",
            "confidence": "high" | "medium" | "low",
            "predicted_peak_bg": float,
            "delta_from_baseline": float
        }
    },
    "actuals": None | {
        "curve": [{"minutes": int, "glucose_mgdl": int, "timestamp": str}],
        "actual_peak_bg": float,
        "time_to_peak_minutes": int,
        "tir_ratio": float,
        "mard": float,
        "chart_path": str
    },
    "retrain_triggered": bool
}
```
Schema changes after GLUC-009 require incrementing the API version in Epic 8.

## Glucose Prediction — Pre-log Anchor (confirmed 2026-08-06)
Four user stories confirmed with Nicole, driving `API-010`, `API-011`, and
the rewritten `MOB-012` (see `docs/API-LAYER-TICKETS.md` and
`docs/MOBILE-TICKETS.md` for full ticket detail, `plans/API-010-plan.md` /
`plans/API-011-plan.md` for decision logs):

1. **CGM connected, happy path:** As a user, given my CGM is connected to
   SikFan, when I take a picture of a meal, then I should see glucose
   impact, macro breakdown, and dish name — automatically, no manual step.
2. **CGM connected, recent reading:** As a user, given my CGM is connected
   but the last reading was 5–10 min ago, then my pre-meal glucose should be
   the most recent reading (so long as it's within 15 min), and I should
   still get macro breakdown, glucose impact, and dish name on scan.
3. **No CGM, gate the impact:** As a user, given I don't have my CGM
   connected, when I take a picture of a meal, I should see an option to
   manually enter my blood glucose — and only then see glucose impact, macro
   breakdown, and dish name. Macro breakdown and dish name are **never**
   gated behind a glucose anchor; only glucose-impact UI is.
4. **No fake baseline:** if there is no CGM value, do not show glucose
   impact at all (not even dimmed/placeholder) — show the manual-entry
   option instead. There is no default/fallback baseline glucose value.

**Resulting architecture decision:** manual glucose entry (`API-011`) is not
a special-case parameter on `GET /glucose/{meal_id}` — it's stored as a
regular CGM reading (`source: "manual"` in `data/glucose/cgm_readings.json`,
via the existing `save_cgm_reading()`), so a future real CGM integration
needs zero changes to the prediction path. `pipeline/glucose_store.py`'s
`_VALID_SOURCES = {"dexcom_csv", "manual"}` already anticipated this.

**Gating change to the frozen `analyze_glucose` contract:** `GET
/glucose/{meal_id}` (`API-010`) no longer requires the meal to be logged
first — it works as soon as `POST /analyze-meal` completes, anchored to
`job_status.created_at` (scan time) instead of requiring `POST /log-meal`'s
timestamp. The response *schema* is unchanged (still the frozen GLUC-009
shape below); only when it returns 200 vs 404 changes. `POST /log-meal` was
also changed to reuse `job_status.created_at` as `meal_timestamp` (not
`datetime.now()` at tap time), so a pre-log preview and the post-log tracked
prediction share the same anchor and produce a continuous curve.

## Feedback loop validated (2026-04-15)
- dried_tofu_sticks: ADD_NEW, UNCERTAIN → CONFIDENT after 3 confirmations
- braised_beef_noodle: CONFIRM, confidence 0.8519 → 0.9325 after 3 confirmations
- Both correction_log.jsonl and data/dishes/ enrichment working correctly

## FOOD-012 Notes
- _PINYIN_FALLBACK hardcoded lookup table in macro_lookup.py
- Brittle — only covers explicitly listed dishes
- Long term: replace with smarter query expansion or LLM-generated 
  search terms (pairs naturally with FOOD-016 LLM fallback)
  
## usage
Limit your reads to only CLAUDE.md and docs/MOBILE-TICKETS.md do not read anything else without asking me


<!-- ## Pipeline State (as of FOOD-005b)
- Top-1 accuracy: 73.7% on 38 dishes (FOOD-006 standalone test)
- Full pipeline accuracy lower due to mixed_bowl routing — 
  8-10 single dishes incorrectly flagged as mixed_bowl and 
  never reaching classify_crop()
- Known confusion pairs: red sauce meats, white dough items, 
  chili oil dishes — see FOOD-011 for systematic report
- hot_pot and japanese_curry centroids built from 1 image each 
  — weak embeddings, expect low confidence on these

## Known Pipeline Limitations (do not fix before FOOD-015)
- Mixed_bowl false positives on large single dishes 
  (mapo_tofu, rice, scrambled_egg_with_tomato etc.)
  → Accepted risk: safer than false negatives for T1D app
  → Will surface as component confirmation in mobile app
- Fragment crops on close-up professional photos
  → CLIP semantic filter in FOOD-009 will partially mitigate
- spicy_pot: all crops discarded by food/not-food filter
  → Needs more seed images or filter threshold adjustment

## FOOD-009 Context
- Start thresholds: CONFIDENT >= 0.82, UNCERTAIN 0.65-0.82, 
  UNKNOWN < 0.65 (defaults from ticket)
- Real data suggests CONFIDENT threshold may need to be 
  higher (~0.87) based on confusion pair scores
- Kimchi not in training data — will correctly surface as UNKNOWN
- All mixed_bowl components hardcoded to UNCERTAIN regardless 
  of score — this is intentional for T1D safety
- Use planning mode before implementing
- 1.7% fragment crops consistently returning false CONFIDENT
  → Systematic pattern observed across multiple real meal photos
  → Accepted for now, FOOD-016 LLM fallback long term fix -->
