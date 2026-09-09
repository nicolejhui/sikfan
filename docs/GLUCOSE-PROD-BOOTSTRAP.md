# Glucose Model — Production Bootstrap (open)

Status: **open, not scheduled.** Found 2026-09-07 while investigating 422s after the
API-015 deploy. Not caused by API-015 — glucose has almost certainly never worked in
production.

---

## Root cause (verified in source)

`analyze_glucose()` (`glucose_analysis.py:214`) checks `_MODEL_PATH.exists()`
(`data/models/glucose_model.joblib`) **before** looking at any CGM data, and raises
`RuntimeError` -> `422 {"code": "glucose_model_not_ready"}` when it is missing. Both the
preview path (`GET /glucose/{meal_id}` pre-log) and the logged path hit this same guard
first, which is why `POST /manual-glucose` returns 200 and every subsequent
`GET /glucose/{meal_id}` returns 422.

The model has never reached the Fly volume, and there is no path by which it could:

1. **`data/` is gitignored** (`.gitignore:2`), so the Dockerfile's `COPY . .` cannot
   carry `data/models/glucose_model.joblib`.
2. **`scripts/predeploy.sh` only snapshots `data/embeddings/`** — it never touches
   `data/models/`.
3. **Nothing on the server ever calls `train_model()`.** `should_retrain()` is only
   reached *after* the existence check passes, so there is no cold-start path.

### The bigger problem: the volume shadows the image

`fly.toml` mounts `sikfan_data` at `/app/data`. The Dockerfile's
`COPY deploy_snapshot/embeddings /app/data/embeddings` is therefore **hidden at runtime**
by the volume mounted over that path. The Dockerfile says so itself: *"For local docker
run only — shadowed by Fly.io volume mount in production."*

So this is not merely a missing line in `predeploy.sh`. **Nothing copied into `/app/data`
at build time has ever reached the running app**, embeddings included. Production
ChromaDB is whatever accumulated on the volume from live user confirmations, not the
local trained store. Worth verifying separately — it may explain recognition quality
previously attributed to the model.

Any fix must therefore write to a path **outside** `/app/data`, then seed into the
volume at startup.

---

## When to retrain — read this first, it changes the plan

**More app usage will not produce training data.** This is the important finding, and it
inverts the "address it when I get more usage" plan.

`is_trainable()` (`glucose_model.py:102`) requires `cgm_window.status == "complete"`, and
`attach_cgm_window()` (`meal_tracker.py:122`) sets `complete` only at
`_COMPLETE_THRESHOLD = 30` readings inside a 180-minute post-meal window — roughly one
reading every 5 minutes for 3 hours. That is a **streaming CGM feed**, not a pre-meal
number.

Production has manual entry only (`API-011`; `_VALID_SOURCES = {"dexcom_csv", "manual"}`).
A manual entry is a single pre-meal anchor, so a production meal can never reach
`complete`.

Current local state:

| | |
|---|---|
| meal logs | 49 — **21 complete**, 15 pending, 13 incomplete |
| CGM readings | 16,552 — 16,436 `dexcom_csv`, 116 `manual` |
| model trained | 2026-09-06, on **21 meals**, last meal `2026-04-26` |

Every trainable meal comes from the historical April Dexcom CSV import. The model is
already trained on all of it, and it sits barely above `train_model(min_meals=20)`.

**Consequences:**

- `should_retrain()` needs **10 new complete meals** (`glucose_model.py:535`). In
  production that counter will never move. The server cannot retrain itself, and could
  not bootstrap itself even if the guard allowed it.
- **Retraining before vs. after fixing the bootstrap makes no difference** — there is
  nothing new to train on either way. The sequencing question dissolves.
- Retrain only when a **fresh Dexcom CSV export** is imported, or when real CGM
  integration lands. Waiting on usage is waiting on nothing.
- Because the shipped model would be a thin 21-meal fit on 4-month-old data, check that
  `model_confidence` reports `low`/`medium` honestly rather than projecting false
  precision — this feeds insulin decisions.

---

## Options

### Option A — Seed from the image at startup *(recommended)*

`COPY deploy_snapshot/ /app/seed/` (outside the mount), then in `lifespan` copy any
missing artifact from `/app/seed` into `/app/data`. Extend `predeploy.sh` to snapshot
`data/models/` alongside `data/embeddings/`.

**Pros**
- Fixes glucose *and* the silently-shadowed embeddings with one pattern.
- No shell access needed — `fly ssh console` is blocked by the permission classifier.
- Deterministic: the artifact ships with the image, so a rollback rolls the model back too.
- Seed-only-when-missing never clobbers production data (live dish confirmations, or a
  future server-trained model).

**Cons**
- Requires **pinning `scikit-learn` and `numpy`**, currently unpinned in
  `requirements.txt`. The bundle was pickled with sklearn 1.8.0 / numpy 2.4.1; a
  container that builds a different version can warn or fail to unpickle — trading a 422
  for a load-time crash. This is a real prerequisite, not a nicety.
- Grows the image by the model + embeddings.
- Seed-when-missing means a stale seed is never refreshed; a deliberate refresh needs the
  volume path cleared, or an explicit override flag.

### Option B — Seed the model only, leave embeddings alone

Same mechanism, scoped to `data/models/`.

**Pros**
- Smallest change; unblocks glucose and nothing else.
- Same sklearn pinning caveat, but a smaller blast radius.

**Cons**
- Leaves the embeddings snapshot silently broken, so `predeploy.sh` keeps *looking* like
  it ships dish data while doing nothing. That mismatch is how this bug survived.

### Option C — Authenticated admin upload endpoint

`POST` the `.joblib` to the running server, written straight to the volume.

**Pros**
- No rebuild; works for future retrains without a deploy.
- Only option that can push a newly trained model without shipping an image.

**Cons**
- **An endpoint that accepts a pickle and writes it to disk is a remote-code-execution
  surface** — `joblib.load` executes on unpickle. Behind API-key auth it is still the
  most dangerous option here.
- Model provenance stops being tied to a deploy, so a rollback no longer rolls back the
  model.
- Given retraining is gated on a Dexcom import that happens on the laptop anyway, the
  "no rebuild" benefit is mostly theoretical.

**Recommendation:** A. Fold in B's scope only if the image size turns out to matter.
Avoid C unless real CGM integration later makes server-side retraining routine.

---

## Checklist when this gets picked up

- [ ] Pin `scikit-learn==1.8.0` and `numpy==2.4.1` (or whatever trained the shipped
      bundle) in `requirements.txt`.
- [ ] `COPY deploy_snapshot/ /app/seed/` in the Dockerfile; drop the shadowed
      `COPY ... /app/data/embeddings`.
- [ ] Seed missing artifacts in `lifespan`, log what was seeded vs. found, and surface it
      in `/health` (e.g. `glucose_model_ready`).
- [ ] Extend `scripts/predeploy.sh` to snapshot `data/models/`.
- [ ] Verify `GET /glucose/{meal_id}` returns 200 on a fresh volume.
- [ ] Confirm ChromaDB count on the running app matches the local store — that tells you
      whether the embeddings were ever really there.
- [ ] Re-check `model_confidence` honesty for a 21-meal model on stale data.

## Related

- `plans/API-015-plan.md`, `docs/API-015-POST-DEPLOY.md` — the memory work; unrelated
  cause, same deploy.
- CLAUDE.md "Glucose Prediction — Pre-log Anchor" — why manual entry is stored as a
  regular CGM reading, and why that still cannot produce a trainable window.
