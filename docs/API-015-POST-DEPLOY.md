# API-015 — Post-Deploy Checklist

Follow-ups for the per-scan memory fix (`plans/API-015-plan.md`). Written 2026-09-07,
before the deploy that ships: pre-segmentation downscale (`max_long_edge=1536`),
`max_det=100`, bool masks, predictor-results release, single-worker analysis pool,
downscaled stored originals, `MALLOC_ARENA_MAX=2`.

---

## 1. Verify immediately after deploy

Do these in order. Items 1-3 are the ones that would show a bad deploy.

- [ ] **Health.** `curl https://sikfan-api.fly.dev/health` — expect
      `models_loaded: true` and `chromadb_ready: true`. A 503 here means FastSAM/CLIP
      or ChromaDB failed to load, which the `lifespan` block swallows silently.
- [ ] **One scan, end to end, from the real iPhone.** Confirm dish names, macros, and
      the glucose projection all still render. This is the first time the downscale
      path runs on a true 12 MP capture.
- [x] **Memory returns to baseline *between* scans**, not just survives one.
      **VERIFIED 2026-09-09** (API-016 `/health` instrumentation, six 12 MP iPhone
      scans on a single process, no restarts, no OOM):

      | after | `memory_current_mb` | `memory_peak_mb` |
      |---|---|---|
      | baseline (models loaded, no scan) | 1285.8 | 1948.8 |
      | 3 scans | 1450.5 — flat across 12 samples / 6 min | 1948.8 |
      | 4 scans | 1426.7 | 1948.8 |
      | 6 scans | 1457.2 | 1948.8 |

      `current` oscillates in a 1426-1457 MB band and never trends upward; the 4th
      scan ended *below* the idle level preceding it, and the final read was taken
      21s after the last scan. `peak` never moved off its boot-time value across all
      six scans. That is the no-ratchet signature — `predictor.results` release +
      `MALLOC_ARENA_MAX=2` are working. The ~165 MB above baseline is a reused
      working set, not accumulated residue.
- [ ] **`/analyze-meal` still returns immediately.** Fire two scans back to back. The
      second must return its `meal_id` right away, not block until the first finishes.
      This is what the `_io_executor` split fixed; if it regresses, the resize is back
      on the analysis pool.
- [ ] **`GET /meal-image/{meal_id}` looks right** in the app at 1536px — this deploy is
      the first to store a downscaled original.
- [x] **How to read memory without shell access.** `fly ssh console` is blocked by the
      permission classifier as production shell access, so `/proc/meminfo` is off the
      table. **Done (API-016):** `/health` now reports `memory_current_mb` and
      `memory_peak_mb` — peak via `resource.getrusage(RUSAGE_SELF).ru_maxrss` and
      current via `/proc/self/statm`, both stdlib, no new dependency. Peak gives the
      OOM ceiling; current tells you whether memory comes back down between scans,
      which machine-level graphs smear out. `memory_current_mb` is `null` on macOS
      (no `/proc`) — that's the degrade path working, not a bug. See
      `plans/API-016-plan.md`.

      **Protocol** (D9: Fly's ~70s idle auto-stop resets both fields on a cold start,
      so any before/after spanning a restart is meaningless — pin the process first):
      ```bash
      # terminal 1 — keepalive, bounded to ~20 min so it can't hold a 4 GB machine
      # up indefinitely
      for i in $(seq 40); do curl -s -o /dev/null https://sikfan-api.fly.dev/health; sleep 30; done

      # terminal 2 — baseline, one scan from the iPhone, then:
      curl -s https://sikfan-api.fly.dev/health | jq   # immediately after
      curl -s https://sikfan-api.fly.dev/health | jq   # ~30s later
      ```
      Repeat for three scans. Confirm `fly status -a sikfan-api` LAST UPDATED hasn't
      moved (a restart invalidates the run). Pass: `memory_current_mb` returns to near
      baseline each time. Fail (ratchet): it steps up scan over scan — see §4.

      Grafana at fly-metrics.net remains useful as machine-level corroboration, not
      the primary instrument (it smears current/peak together, per D9's sawtooth
      caveat above). Pair with `glucose_model_ready` from
      docs/GLUCOSE-PROD-BOOTSTRAP.md so one `/health` call answers both questions.

      **Still open:** this item's own ratchet check (below) hasn't been run against
      real 12 MP captures yet — the field exists now, but the measurement itself is
      unverified until the protocol above is executed post-deploy.
- [x] **Peak memory headroom.** **The pre-deploy estimate was pessimistic by a wide
      margin — measured 2026-09-09, and both of its inputs were wrong:**
      - Estimated: ~2.4 GB added per scan over a ~1 GB baseline = ~3.4 GB / 4 GB.
      - Measured: steady-state baseline is **~1.29 GB**, and six scans never pushed
        RSS above the **1948.8 MB** high-water mark set during *startup*. Per-scan add
        is therefore bounded by ~663 MB and is probably well under it.
      - **Model loading, not scanning, is the largest single allocation this process
        makes** — boot transiently peaks ~660 MB above steady state.
      - Real headroom: **~2.1 GB free**, not the ~600 MB feared.

      Caveat on the bound: because the boot peak (1948.8) exceeds anything the scans
      reached, it *masks* the per-scan peak. We know scans stay under 1.95 GB; we
      cannot tell from `ru_maxrss` alone whether a scan peaks at 1.9 GB or 1.4 GB.
      Narrowing that needs per-scan instrumentation, which API-016 deliberately did
      not build.

      **Consequence for §2 and §3:** the memory pressure that motivated the 1536 cap
      and `max_det=100` tuning is much less acute than assumed. This does *not* license
      lowering `max_det` (§2's accuracy finding is independent and still binding), but
      it does mean **raising** `max_long_edge` to 2048 is affordable if §3's accuracy
      sweep favours it — which was previously treated as a memory-constrained tradeoff.

## 2. Do not do these

- **Do not lower `max_det` below 100.** Measured on all 9 corpus images (plan D9):
  100 is byte-identical to the old default of 300, but 32 loses *whole dishes* —
  `8A88401B` went `dried_tofu_sticks x2` -> nothing, `E292AC74` lost
  `dried_tofu_sticks`, `assorted_breakfast` lost `steamed_bun_stuffed`. `max_det`
  truncates by model confidence **before** the area filter runs. It cannot buy memory.
- **Do not lower `memory = "4gb"`** until §1's headroom check has run against real
  12 MP photos for a few days.
- **Do not lower `max_long_edge` below 1536** without the §3 accuracy gate. The
  pipeline is measurably resolution-sensitive (plan D10).

## 3. Open — the resolution accuracy gate

Still unsatisfied. `max_long_edge=1536` is unvalidated on real data.

- [ ] Shoot ~5 meal photos on the **actual iPhone**, AirDrop to the Mac.
      **This deploy downscales stored originals to 1536, so the Fly volume will never
      hold full-res photos — they must come off the phone directly.**
- [ ] `python scripts/resolution_sweep.py --source-dir <dir>` and compare dish names
      and confidence scores at 4032 / 2048 / 1536 / 1024.
- [ ] If 1536 shows drift, raise to 2048 and re-check headroom. If 1024 is clean, take
      it — it drops the per-scan add from ~2.4 GB to ~1.3 GB.

Notes on the tooling, so the first sweep's mistake isn't repeated:
- The script now **warns** when inputs are at or below the smallest cap being swept.
  The original sweep was invalid because every input was already ≤1024px, so all four
  resolutions processed identical pixels.
- `--device` defaults to `cpu` to mirror Fly. On MPS the float32 mask tensor sits in
  GPU memory and never enters `ru_maxrss`, understating production ~4x.
- `--synthetic` is valid for **memory only**. Upscaling invents no detail, so it
  cannot judge accuracy.

## 4. Deferred — drop `retina_masks` (the real fix)

`retina_masks=True` forces masks to the *original* photo's dimensions. The pipeline
extracts only a bounding box and a pixel count from them, so that precision is
allocated and discarded. Turning it off puts masks at the 1024 letterbox (~2.25x
smaller than a 1536 cap) while `results[0].boxes.xyxy` is **already** in original
coordinates, so crop bounds lose nothing.

Deliberately **not** bundled with this deploy — it lands in the portion -> carbs ->
insulin path.

**The hazard:** without retina, mask pixels are counted in letterbox space *including
the gray padding bars*, while `image_pixels` is original-space. That inflates the
denominator in `_pixel_bucket()`'s `mask_pixels / image_pixels`, making every dish look
like a smaller fraction of the plate -> smaller portion bucket -> fewer grams ->
**under-counted carbs -> under-dosed insulin**.

Requirements for that ticket:
- Handle the letterbox padding explicitly in the ratio; do not assume it away.
- Validate on `portion_g` before/after across the corpus, **not** just dish names —
  the current sweep script compares names and would not catch this.
- Only then re-measure memory.

## 5. Smaller open items

- [ ] **`4E98C01F` classifies zero dishes** at every `max_det` including the 300
      baseline. Pre-existing, not caused by API-015, but it is a real miss sitting in
      the corpus. Worth a look at what that image is.
- [ ] **Upload latency.** `CameraScreen.tsx:58` `takePictureAsync()` takes no options
      and the gallery path uses `quality: 1`, so the phone uploads ~12 MP over
      cellular. Server memory no longer cares, but the user waits. A capture-time
      resize needs `expo-image-manipulator` (a new native dep — run
      `/mobile_ticket_check`, and see CLAUDE.md on MOB-005 dependency drift).
      Track as a mobile ticket; the server must keep its own downscale regardless.
- [ ] **`outputs/synthetic_sweep/`** holds upscaled test images generated during
      benchmarking. Safe to delete.

---

## 6. Found after deploying (2026-09-07)

- **Glucose 422s are unrelated to API-015.** `GET /glucose/{meal_id}` fails a cold-start
  guard because the trained model has never reached the Fly volume, and no path exists to
  put it there. Full write-up and options in **docs/GLUCOSE-PROD-BOOTSTRAP.md**.
- **The volume shadows the image**, so `scripts/predeploy.sh` is a no-op in production —
  the embeddings snapshot has never reached the running app either. Same doc.
- **Retraining is not usage-gated.** Manual glucose entry cannot produce a trainable CGM
  window, so accumulating production meals will not improve the model. See that doc's
  "When to retrain" section before planning around it.
