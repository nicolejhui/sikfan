# GlycoLens - Carb Counter

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
├── TICKETS.md
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

## Current Sprint
Sprint 3 — Embedding Store (FOOD-007, FOOD-008, FOOD-006)

## Completed
- Sprint 1: FOOD-001, FOOD-002, FOOD-003
- Sprint 2: FOOD-004, FOOD-005

## Up Next After Sprint 3
- FOOD-005b (component detection skeleton — Part 1 only, no CLIP)
  - Build complexity detection heuristic (k-means color clustering)
  - Build large crop threshold check
  - Output schema with empty components list (dish_name: None, status: pending_clip)
  - Do NOT wire to ChromaDB or CLIP yet

## FOOD-005 Notes
- NMS IoU tuned to 0.4
- Center-bias scoring added
- steamed_bun_stuffed correctly returns 16 crops (genuine separate items)
- Remaining noise handled by CLIP semantic filter in FOOD-006