# SikFan

A mobile app used to predict blood glucose impact based on a picture of a meal with models trained on east asian cusine and my personal glucose and meal data. SikFan predicts glucose impact of a dish with a classic Gaussian Process Regression (GPR) model layered on top of a vision model comprised of FastSAM/CLIP with an LLM fallback. 

## Model Architecture

```
FastSAM (segmentation) → CLIP ViT-B/32 (embedding) → ChromaDB (embedding store) 

```
There is no hardcoded dish names or any rules. SikFan utilizes an embedding store to update centroids with each logged dish to improve food recognition and portion estimation over time. 

Two primary functions, one for each model:
- `analyze_meal.py` — `analyze_meal(image_path) -> dict`: segmentation →
  embedding → dish match/decomposition → macro lookup → portion estimate.
- `glucose_analysis.py` — `analyze_glucose(meal_id) -> dict`: pre/post-meal
  CGM anchoring → trained model using personal meal and glucose data → predicted glucose curve, with automatic
  retraining as new logged meals + actuals come in.

`api.py` (FastAPI) exposes both over HTTP for the mobile app.

## Requirements

- Conda env
- Apple Silicon (MPS backend only — never CUDA)
- `.claude/hooks.json` auto-activates the conda env before shell commands run
  under Claude Code

## Project structure

```
pipeline/       core model pipeline modules (segmentation, embedding, macro
                lookup, portion estimation, glucose model)
scripts/        one-off utility scripts (data prep, labeling, dataset tools)
scripts/verify/ per-ticket verification scripts (archived after ship)
tests/          pytest suite
mobile/         React Native app (Expo, bare workflow — native ios/ is
                hand-managed and committed)
docs/           ticket specs, architecture notes, decision records
data/           datasets, embeddings store, macro cache, glucose model
                (mostly gitignored; see CLAUDE.md for layout)
analyze_meal.py, api.py, glucose_analysis.py   top-level entry points
```

## Data flow

1. Photo → FastSAM segments the plate into components.
2. Each component is embedded (CLIP) and matched against ChromaDB, or
   decomposed into sub-components for a composite dish.
3. Matched dish → USDA FoodData Central macro lookup, portion-size estimate
   from pixel area, corrected by a learned per-dish prior from past user
   corrections.
4. If a CGM reading is available (or entered manually), `analyze_glucose`
   predicts a post-meal glucose curve from total carbs + fiber + historical
   response, retraining periodically as new actuals come in.

## Development

Run tests with `pytest`. Mobile-specific verification: see
`.claude/skills/mobile_ticket_check/SKILL.md`.
