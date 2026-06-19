# SikFan — Food Detection Model Engineering Tickets (v2)

> All tickets follow the FastSAM → CLIP → ChromaDB pipeline.
> No hardcoded class lists anywhere. Dishes are added dynamically.
> Build order: Epic 1 → Epic 2 → Epic 3 → Epic 4 → Epic 5
> Gate: FOOD-015 must pass before any mobile app work begins.
>
> **v2 changes:** Added FOOD-005b (mixed-component bowl decomposition).
> Updated FOOD-014 and FOOD-015 to reflect component-level schema.

---

## Epic 1: Data Collection & Labeling

### FOOD-001 — Bootstrap personal meal image dataset

**User Story**
As a developer, I want a structured folder of labeled meal photos so the embedding store has a cold-start seed of dishes I actually eat.

**Acceptance Criteria**
- >= 30 dishes represented (selected from CNFOOD-241, prioritized by
  dishes you personally eat)
- Each dish has >= 10 photos (sourced from CNFOOD-241 train600x600/ 
  for now; personal photos added incrementally via FOOD-010)

**Implementation Notes**
- Primary source is `data/CNFOOD-241/train600x600/` — do NOT need personal photos to complete this ticket
- Select 30-50 classes from class_name.xls that match dishes you actually eat; ignore the rest for now
- Copy selected class folders from CNFOOD-241 into 
`data/dishes/{dish_name}/` using the English name from class_name.xls as the folder name (not the numeric ID)
- Personal photos will be added later via the FOOD-010 confirmation loop — this ticket just needs a working seed
 
**Dependencies:** None — this is the starting point.

---

### FOOD-002 — Build data augmentation pipeline for food images

## Goal
Create a robust augmentation script using `albumentations` to synthetically expand the
CNFOOD-241 seed dataset so CLIP embeddings are more robust to real-world phone photo
variations (bad lighting, angles, partial plates, camera blur) before personal meal
photos are collected via FOOD-010.

---

## Acceptance Criteria
- [ ] Script processes all images in `data/dishes/{dish_name}/`
- [ ] Generates a 5x multiplier for every original image
- [ ] Augmented files saved as `image_NNN_aug_M.jpg` alongside originals
- [ ] Original source images remain byte-for-byte unchanged after script runs
- [ ] All output images are >= 224x224px
- [ ] Dish folders with < 3 source images are skipped with a printed warning
- [ ] Script is idempotent — re-running does not re-augment already-augmented images

---

## Implementation Technical Spec

**File to create:** `scripts/augment_data.py`

**Library:** `albumentations` — install with:
```bash
conda run -n carb_counter pip install albumentations
```

**CLI Interface:**
```bash
python scripts/augment_data.py --input data/dishes/ --multiplier 5
```

**Required Augmentation Pipeline:**
```python
import albumentations as A

transform = A.Compose([
    A.RandomBrightnessContrast(
        brightness_limit=0.3,
        contrast_limit=0.3,
        p=0.5
    ),
    A.HueSaturationValue(
        hue_shift_limit=10,
        sat_shift_limit=30,
        val_shift_limit=10,
        p=0.5
    ),
    A.HorizontalFlip(p=0.5),
    A.RandomResizedCrop(
        height=224,
        width=224,
        scale=(0.8, 1.0),
        interpolation=1,   # cv2.INTER_LINEAR — do not change
        p=1.0
    ),
    A.GaussianBlur(
        blur_limit=(3, 7),
        p=0.2
    ),
])
```

**Core Logic:**
```python
for dish_folder in data/dishes/*/:
    images = [f for f in dish_folder if not '_aug_' in f]  # skip existing augments
    
    if len(images) < 3:
        print(f"WARNING: skipping {dish_folder.name} — only {len(images)} source images")
        continue
    
    for image in images:
        for m in range(multiplier):
            augmented = transform(image=img_array)["image"]
            save as image_NNN_aug_M.jpg
```

**Strict Constraints:**
- **DO NOT** use `VerticalFlip` — upside-down food is not a valid training example
- **DO NOT** use rotations greater than 15 degrees — `ShiftScaleRotate` max `rotate_limit=15` if added later
- **DO NOT** augment files that already contain `_aug_` in the filename — prevents recursive augmentation
- **DO NOT** overwrite or modify original images under any circumstances

---

## Expected Output Structure
```
data/dishes/mapo_tofu/
├── image_000.jpg          ← original (unchanged)
├── image_000_aug_0.jpg    ← augmented copy 1
├── image_000_aug_1.jpg    ← augmented copy 2
├── image_000_aug_2.jpg    ← augmented copy 3
├── image_000_aug_3.jpg    ← augmented copy 4
├── image_000_aug_4.jpg    ← augmented copy 5
├── image_001.jpg          ← original (unchanged)
├── image_001_aug_0.jpg
...
```

---

## Verification Commands

**1. Run the script:**
```bash
python scripts/augment_data.py --input data/dishes/ --multiplier 5
```

**2. Count augmented images created:**
```bash
python -c "
import os
aug_files = [f for r,d,files in os.walk('data/dishes') for f in files if '_aug_' in f]
orig_files = [f for r,d,files in os.walk('data/dishes') for f in files if '_aug_' not in f and f.endswith('.jpg')]
print(f'Originals: {len(orig_files)}')
print(f'Augmented: {len(aug_files)}')
print(f'Expected augmented: {len(orig_files) * 5}')
print(f'Match: {len(aug_files) == len(orig_files) * 5}')
"
```

**3. Verify originals are unchanged (spot check):**
```bash
python -c "
import os, hashlib
# Hash a sample of originals before and after — should be identical
dish = next(os.scandir('data/dishes')).path
originals = [f for f in os.listdir(dish) if '_aug_' not in f and f.endswith('.jpg')]
print(f'Spot checking {dish}: {len(originals)} originals intact')
"
```

**4. Verify minimum resolution:**
```bash
python -c "
from PIL import Image
import os
failures = []
for root, dirs, files in os.walk('data/dishes'):
    for f in files:
        if f.endswith('.jpg'):
            img = Image.open(os.path.join(root, f))
            if img.size[0] < 224 or img.size[1] < 224:
                failures.append(f)
print(f'Resolution failures: {len(failures)}')
if failures: print(failures[:5])
"
```

---

## Dependencies
- FOOD-001 (data/dishes/ must be populated before this runs)
- `albumentations` installed in `carb_counter` conda env

## Why This Matters
CNFOOD-241 seed images are clean, professional food photos. Real phone photos will have
worse lighting, odd angles, motion blur, and partial visibility. Augmentation closes
that gap synthetically so CLIP embeddings built in FOOD-008 are robust to real-world
conditions from day one — before personal meal photos are collected via FOOD-010.

---

### FOOD-003 — Build a personal labeling CLI for confirming new dish photos

**User Story**
As a developer, I want a lightweight local CLI to quickly confirm or rename dish predictions so I can label new photos without leaving the terminal.

**Acceptance Criteria**
- Script displays each unlabeled image in sequence
- User can type a dish name to confirm, or press Enter to accept a suggested name
- Labeled images are moved to `data/dishes/{dish_name}/`
- A session log records what was labeled and when

**Implementation Notes**
- Build as a simple Python CLI using `matplotlib` inline display (no need for Label Studio)
- Read from `data/unlabeled/`, write to `data/dishes/{dish_name}/`
- This script doubles as the human-in-the-loop step in the continual learning pipeline (see FOOD-010)
- Keep it simple — this is a personal tool, not a production UI

**Dependencies:** FOOD-001

---

## Epic 2: FastSAM Segmentation

### FOOD-004 — Implement FastSAM blob detection on a single meal photo

**User Story**
As a developer, I want FastSAM to segment a meal photo into individual food region crops so each item can be classified independently.

**Acceptance Criteria**
- Given any meal JPEG, function returns a list of PIL Image crops — one per detected food region
- Regions smaller than 2% of total image area are filtered out
- Each crop preserves original RGB colors without BGR inversion artifacts
- Function runs in <= 3 seconds on MPS for a 1024px image

**Implementation Notes**
- Use `FastSAM-s.pt` (already in project root) for development; swap to `FastSAM-x.pt` for production
- CRITICAL: load image with `cv2.imread()` then immediately convert BGR→RGB before any PIL operations — this was the source of color inversion bugs in `jan26-2026.ipynb`
- Baseline params: `retina_masks=True, imgsz=1024, conf=0.4, iou=0.9`
- Return crops as a list of dicts: `{crop: PIL.Image, bbox: (xmin,ymin,xmax,ymax), mask_pixels: int}`
- Reference `jan26-2026.ipynb` Cell 15 for the working mask extraction pattern

**Dependencies:** `ultralytics>=8.0`, `FastSAM-s.pt` in project root

---

### FOOD-005 — Handle multi-dish plate segmentation edge cases

**User Story**
As a developer, I want the segmentation step to handle hot pot, bento boxes, and shared plates without producing hundreds of micro-blobs or missing large items.

**Acceptance Criteria**
- On a hot pot test image, produces between 3–15 meaningful crops (not 50+ noise blobs)
- Overlapping blobs with > 70% IoU are merged into a single crop
- Bowl/plate boundaries are not returned as standalone crops
- Maximum of 20 crops returned per image regardless of detection count

**Implementation Notes**
- Add NMS step after raw mask extraction using `torchvision.ops.box_iou` to merge overlapping detections
- Add shape heuristic: discard masks with aspect ratio > 4:1 (likely a table edge or chopstick)
- Test against `data/hot_pot_christmas.jpeg` and `data/assorted_breakfast.jpeg`
- Tune the `min_pixels` threshold as a fraction of total image area, not as an absolute pixel value

**Dependencies:** FOOD-004

---

### FOOD-005b — Detect and decompose mixed-component bowl crops ⭐ NEW

**User Story**
As a user, I want the app to recognize that a bowl contains multiple ingredients (protein, grain base, vegetables) rather than treating it as a single unidentifiable dish, so my carb and macro estimates are accurate even for layered or mixed meals.

**Acceptance Criteria**
- [ ] Pipeline detects when a crop likely contains multiple components using a visual complexity heuristic
- [ ] For mixed-component crops, returns a `components` list instead of a single `dish_name`
- [ ] Components are classified into semantic roles: `base` (grain/carb), `protein`, `vegetable`, `sauce`
- [ ] The `base` component always triggers an explicit user confirmation prompt — it is never silently assumed
- [ ] Single-dish crops (kimchi jar, orange slices) continue to return a single `dish_name` as before
- [ ] Output schema is backwards compatible — single-dish crops still return the existing format

**Implementation Notes**

*Complexity detection heuristic:*
Don't try to train a classifier for this. Use two cheap signals:
- **Color cluster count**: run k-means on the crop's pixel colors. If 3+ distinct clusters exist, flag as potentially mixed. Start with `k=4` (tunable via `config.yaml` — a typical mixed bowl has roughly 3-4 visually distinct color regions; k=3 risks merging two components, k=5+ risks splitting one). Treat this as a starting point and tune against real photos.
- **Crop size**: if `mask_pixels / total_image_pixels > 0.25` (a large region), assume it's a bowl and flag for decomposition

If either signal fires, treat the crop as mixed.

*Component classification:*
Run CLIP on the full crop with a small set of role-specific text prompts — this is the one place where text prompts are acceptable because you're asking about food roles, not specific dish names:
```python
role_prompts = {
    "base": ["white rice", "brown rice", "purple rice", "noodles", "congee", "no grain visible"],
    "protein": ["beef", "pork", "chicken", "tofu", "fish", "egg", "no protein visible"],
    "vegetable": ["bok choy", "spinach", "broccoli", "mixed vegetables", "no vegetables visible"],
}
```
For each role, return the top-1 CLIP match + similarity score. If top-1 score < 0.5, mark that role as `UNCERTAIN` and add to `items_needing_review`.

*The base confirmation rule:*
Regardless of CLIP confidence, always surface the grain/base to the user for confirmation before macro lookup. The base is the primary glycemic driver — a wrong assumption here (white rice vs. purple rice vs. noodles) causes the largest glucose prediction error. Never silently accept a base classification.

*Output schema for mixed crops:*
```python
{
    "crop_type": "mixed_bowl",        # vs. "single_dish"
    "components": [
        {
            "role": "base",
            "dish_name": "purple_rice",
            "confidence": 0.71,
            "status": "UNCERTAIN",    # always surfaces for confirmation regardless of score
            "confirmed": False
        },
        {
            "role": "protein",
            "dish_name": "braised_beef",
            "confidence": 0.84,
            "status": "CONFIDENT",
            "confirmed": False
        },
        {
            "role": "vegetable",
            "dish_name": "bok_choy",
            "confidence": 0.79,
            "status": "CONFIDENT",
            "confirmed": False
        }
    ],
    "macros": None    # populated after all components confirmed
}
```

*Macro aggregation:*
Once components are confirmed, look up macros for each component individually via FOOD-012 and sum them. Portion size for each component is estimated from its visual fraction of the total crop area — bok choy covering 30% of the bowl gets 30% of the bowl's total estimated portion weight.

*config.yaml additions:*
```yaml
component_detection:
  kmeans_k: 4                    # color clusters for complexity heuristic — tune against real photos
  kmeans_min_clusters: 3         # threshold: >= this many clusters = mixed bowl candidate
  large_crop_threshold: 0.25     # mask_pixels / total_image_pixels threshold for bowl assumption
  role_confidence_threshold: 0.5 # below this = UNCERTAIN for any role
```

**What this does NOT do**
- Does not attempt to detect occluded ingredients (rice hidden under beef). Relies on the base confirmation prompt to catch this — the user knows what's underneath even if the model can't see it
- Does not decompose single-dish crops (a plate of dumplings stays as one item)
- Does not handle liquid-based dishes (soups, congee) differently at this stage — that's a follow-on ticket

**Updates required in other tickets**
- **FOOD-014**: Portion estimation must handle component-level fractions within a mixed crop, not just whole-crop sizing (see updated FOOD-014 below)
- **FOOD-015**: `analyze_meal()` output schema needs `crop_type` and `components` fields; `total_macros` aggregation must sum across components (see updated FOOD-015 below)

**Verification:**
```bash
python -c "
from pipeline.segmentation import segment_meal
from pipeline.component_classifier import classify_components

crops = segment_meal('data/IMG_4488.jpeg')
for crop in crops:
    result = classify_components(crop)
    print(f'Crop type: {result[\"crop_type\"]}')
    if result['crop_type'] == 'mixed_bowl':
        for c in result['components']:
            print(f'  {c[\"role\"]}: {c[\"dish_name\"]} ({c[\"confidence\"]:.2f}, {c[\"status\"]})')
"
```

Expected output for a mixed bowl meal photo:
```
Crop type: mixed_bowl
  base: purple_rice (0.71, UNCERTAIN)
  protein: braised_beef (0.84, CONFIDENT)
  vegetable: bok_choy (0.79, CONFIDENT)
Crop type: single_dish
  dish_name: orange_segments (0.91, CONFIDENT)
Crop type: single_dish
  dish_name: kimchi (0.88, CONFIDENT)
```

**Dependencies:** FOOD-005, FOOD-006, FOOD-012

**Position in pipeline:** Sits between FOOD-005 (segmentation) and FOOD-006 (CLIP classification). Complete at end of Sprint 2 before starting Sprint 3.

---

## Epic 3: CLIP Classification & Embedding Store

### FOOD-006 — Build open-vocabulary CLIP classifier (no hardcoded class list)

**User Story**
As a developer, I want CLIP to classify a food crop by comparing it against my personal embedding store rather than a fixed list of class names, so the model works on dishes it has never been explicitly trained on.

**Acceptance Criteria**
- Given a PIL image crop, function returns top-3 dish name matches with cosine similarity scores
- If embedding store is empty, returns an `unknown` result rather than erroring
- Does NOT import or reference `label_dict` or any hardcoded class list anywhere in the classification path
- Runs in <= 500ms per crop on MPS

**Implementation Notes**
- Load CLIP ViT-B/32 via the `openai/clip` library
- Query flow: encode crop image → compute cosine similarity against all ChromaDB embeddings → return top-3
- REMOVE the `clip.tokenize(all_food_names)` pattern from `jan26-2026.ipynb` Cell 19 entirely — that pattern is the source of the class-list dependency this ticket eliminates
- The embedding store (FOOD-007) must exist before this ticket can be tested end-to-end

**Dependencies:** FOOD-004, FOOD-007

---

### FOOD-007 — Implement ChromaDB embedding store with persistence

**User Story**
As a developer, I want a persistent local vector store so that dish embeddings I add today are still available next time I run the app, without needing to re-encode anything.

**Acceptance Criteria**
- ChromaDB collection persists to disk at `data/embeddings/`
- On restart, all previously stored embeddings are immediately queryable
- Store supports: `add_dish(name, image)`, `query_dish(image, top_k)`, `delete_dish(name)`, `list_dishes()`
- Adding a duplicate dish name updates the existing entry rather than creating a second

**Implementation Notes**
- Initialize with `chromadb.PersistentClient(path='data/embeddings')`
- Use a single collection named `dishes`
- Metadata fields to store: `dish_name`, `cuisine_type`, `date_added`, `confirmed_count`
- For deduplication: query by `dish_name` metadata filter before inserting
- Do NOT store raw image bytes in ChromaDB — store CLIP embeddings only; keep images in `data/dishes/`
- Install: `pip install chromadb`

**Dependencies:** `chromadb>=0.4`

---

### FOOD-008 — Seed embedding store from existing labeled dataset

**User Story**
As a developer, I want a one-time seeding script that encodes all my existing labeled photos and loads them into ChromaDB so the model has a useful starting point before I collect any new data.

**Acceptance Criteria**
- Script reads all images from `data/dishes/*/*.jpg`, encodes via CLIP, and upserts into ChromaDB
- Script is idempotent — safe to re-run without creating duplicate entries
- Prints per-dish summary on completion (dish name + embedding count)
- Completes seeding of 500 images in <= 10 minutes on MPS

**Implementation Notes**
- Process images in batches of 32 for MPS efficiency
- Average the CLIP embeddings of all images for a given dish into a single centroid embedding — more robust than one embedding per image
- Store centroid + source image count as metadata so future confirmations can update it via rolling average (see FOOD-010)
- This script should also be able to seed from `data/CNFOOD-241/train600x600/` for any classes you want to pre-load

**Dependencies:** FOOD-001, FOOD-007

---

## Epic 4: Feedback & Continual Learning Loop

### FOOD-009 — Implement per-prediction confidence thresholding and unknown detection

**User Story**
As a developer, I want the pipeline to distinguish between confident matches, uncertain matches, and genuinely unknown dishes so I know when to trust the output vs. when to intervene.

**Acceptance Criteria**
- Each prediction returns a `status` field: `CONFIDENT` (similarity >= 0.82), `UNCERTAIN` (0.65–0.82), or `UNKNOWN` (< 0.65)
- `UNCERTAIN` results show top-3 candidates for user selection
- `UNKNOWN` results prompt user to provide a name
- Thresholds are configurable via `config.yaml`, not hardcoded

**Implementation Notes**
- CLIP cosine similarity scores are not calibrated probabilities — 0.82 is a starting threshold, expect to tune against your personal dataset
- Log all `UNCERTAIN` and `UNKNOWN` predictions to `data/review_queue.jsonl` for retrospective analysis
- The 0.65/0.82 defaults are based on published CLIP food classification benchmarks
- Create `config.yaml` in project root with a `thresholds` section for easy tuning

**Dependencies:** FOOD-006, FOOD-007

---

### FOOD-010 — Build confirmation and correction feedback loop

**User Story**
As a user, I want to confirm or correct the model's dish prediction so that my correction immediately improves future predictions for that dish without any retraining.

**Acceptance Criteria**
- After each prediction, user can: (1) confirm correct, (2) select from top-3 alternatives, or (3) type a new dish name
- All three actions update ChromaDB immediately
- After 3+ confirmations, the embedding centroid is updated via rolling average of confirmed image embeddings
- Changes are queryable on the very next prediction

**Implementation Notes**
- Rolling average update: `new_centroid = (old_centroid * n + new_embedding) / (n + 1)` where `n` is `confirmed_count`
- This IS the continual learning — no retraining, no gradient updates, just centroid refinement
- Save the confirmed crop image to `data/dishes/{dish_name}/` to enrich the dataset for FOOD-002
- Log all corrections to `data/correction_log.jsonl` with: timestamp, original prediction, corrected label, confidence score

**Dependencies:** FOOD-006, FOOD-007, FOOD-009

---

### FOOD-011 — Implement periodic embedding store quality review

**User Story**
As a developer, I want a diagnostic script that surfaces dishes with poor embedding quality so I know where to collect more photos.

**Acceptance Criteria**
- Script outputs dishes ranked by: (1) lowest `confirmed_count`, (2) highest correction rate, (3) highest inter-dish cosine similarity (frequent confusions)
- Report saved to `data/embedding_health_report.md`
- Any dish pair with similarity > 0.88 is flagged as a likely confusion pair

**Implementation Notes**
- Compute pairwise cosine similarity across all dish centroids
- Similarity > 0.88 between two dishes = likely confusion pair, especially relevant for visually similar Chinese dishes (e.g. different broths, similar noodle soups)
- Run after every 50 new meal logs as a lightweight QA step
- Output from this report directly feeds data collection priorities back into FOOD-001

**Dependencies:** FOOD-007, FOOD-010

---

## Epic 5: Macro Lookup & Nutrition API

### FOOD-012 — Integrate USDA FoodData Central API for macro lookup

**User Story**
As a user, I want the confirmed dish name to automatically trigger a macro lookup so I don't have to manually enter nutrition data.

**Acceptance Criteria**
- Given a confirmed dish name, function returns: calories, carbohydrates (total + fiber), protein, fat per 100g
- Returns top-3 USDA matches ranked by relevance for user to select
- Caches results locally so the same dish never makes a redundant API call
- Gracefully handles no-results by flagging for manual entry (FOOD-013)

**Implementation Notes**
- API: USDA FoodData Central (free, no rate limit for personal use)
- Endpoint: `GET https://api.nal.usda.gov/fdc/v1/foods/search?query={dish_name}&dataType=Survey%20(FNDDS)`
- FNDDS (Food and Nutrient Database for Dietary Studies) has better coverage of mixed dishes vs. raw ingredients
- Cache results as JSON: `data/macro_cache/{dish_name_slug}.json`
- For Chinese dishes: try English name first, then transliterated pinyin if no results
- Get free API key at: https://fdc.nal.usda.gov/api-guide.html

**Dependencies:** FOOD-010, USDA API key

---

### FOOD-013 — Build manual macro entry and override flow

**User Story**
As a user, I want to manually enter or override macro data for a dish when the API result is wrong or missing, and have that override persist for future logs of the same dish.

**Acceptance Criteria**
- User can input calories, carbs, protein, fat manually for any dish
- Manual entries are flagged as `source: user_override` in the cache
- On subsequent lookups, `user_override` entries take priority over API results
- User can reset an override back to API data
- Manual entry prompts for portion size in grams alongside macros

**Implementation Notes**
- Extend macro_cache JSON schema with a `source` field: values are `usda_api`, `user_override`, or `estimated`
- Store overrides in `data/macro_cache/` with same filename convention as API cache — lookup logic stays uniform
- This is especially important for home-cooked dishes the USDA won't have (e.g. a specific hong shao rou recipe)

**Dependencies:** FOOD-012

---

### FOOD-014 — Implement portion size estimation from crop dimensions *(updated for FOOD-005b)*

**User Story**
As a user, I want the app to estimate portion size from the photo so I don't have to weigh my food, with the option to manually adjust.

**Acceptance Criteria**
- Given a segmentation mask, pipeline outputs an estimated portion size (small/medium/large at MVP)
- Estimated macros are scaled proportionally to estimated portion
- User can adjust via portion multiplier: 0.5x, 1x, 1.5x, 2x
- Adjusted portion size persists as a personal default for that dish
- **[NEW]** For mixed-component crops, portion is estimated per component based on its visual fraction of the total crop area, not the whole crop

**Implementation Notes**
- Do NOT attempt CV-based weight estimation from a monocular image — this is an unsolved hard problem
- For **single-dish crops**, use relative mask area as proxy: `mask_pixels / total_image_pixels`
  - Small: < 15% of image
  - Medium: 15–35% of image
  - Large: > 35% of image
- For **mixed-component crops** (from FOOD-005b), estimate each component's portion as a fraction of the total crop area using its color cluster footprint from the k-means step. Example: bok choy occupying 30% of the bowl's pixels → gets 30% of the bowl's total estimated gram weight
- Default gram values per size bucket (example: rice → small=100g, medium=180g, large=280g)
- Store per-dish portion defaults in the macro_cache JSON so they personalize over time

**Dependencies:** FOOD-005, FOOD-005b, FOOD-012

---

### FOOD-015 — Build end-to-end pipeline integration test *(updated for FOOD-005b)*

**User Story**
As a developer, I want a single function that accepts a meal photo path and returns a complete structured meal log so I can verify the full pipeline works end-to-end before building the mobile app.

**Acceptance Criteria**
- `analyze_meal(image_path)` returns a dict with:
  - `detected_items`: list of items per crop — each item has `crop_type`, and either `dish_name` (single-dish) or `components` (mixed bowl)
  - `total_macros`: summed calories/carbs/protein/fat across all items and all components
  - `items_needing_review`: list of UNKNOWN, UNCERTAIN, or unconfirmed `base` components
- Handles images between 480px and 4000px
- Full pipeline completes in <= 8 seconds on MPS for a typical meal photo

**Output Schema:**
```python
{
    "detected_items": [
        {
            # Single-dish crop (e.g. kimchi jar, orange slices)
            "crop_type": "single_dish",
            "dish_name": "kimchi",
            "confidence": 0.88,
            "status": "CONFIDENT",
            "macros": { "calories": 15, "carbs_g": 2, "protein_g": 1, "fat_g": 0, "fiber_g": 1 },
            "portion": "medium"
        },
        {
            # Mixed-component crop (e.g. rice bowl)
            "crop_type": "mixed_bowl",
            "components": [
                {
                    "role": "base",
                    "dish_name": "purple_rice",
                    "confidence": 0.71,
                    "status": "UNCERTAIN",
                    "confirmed": False,
                    "macros": { "calories": 220, "carbs_g": 46, "protein_g": 5, "fat_g": 2, "fiber_g": 2 },
                    "portion_fraction": 0.40
                },
                {
                    "role": "protein",
                    "dish_name": "braised_beef",
                    "confidence": 0.84,
                    "status": "CONFIDENT",
                    "confirmed": False,
                    "macros": { "calories": 280, "carbs_g": 4, "protein_g": 28, "fat_g": 16, "fiber_g": 0 },
                    "portion_fraction": 0.45
                },
                {
                    "role": "vegetable",
                    "dish_name": "bok_choy",
                    "confidence": 0.79,
                    "status": "CONFIDENT",
                    "confirmed": False,
                    "macros": { "calories": 25, "carbs_g": 3, "protein_g": 2, "fat_g": 0, "fiber_g": 1 },
                    "portion_fraction": 0.15
                }
            ],
            "macros": None    # populated after all components confirmed
        }
    ],
    "total_macros": {
        "calories": 540,
        "carbs_g": 55,
        "protein_g": 36,
        "fat_g": 18,
        "fiber_g": 4
    },
    "items_needing_review": [
        { "crop_type": "mixed_bowl", "role": "base", "dish_name": "purple_rice", "reason": "base_always_confirm" }
    ]
}
```

**Implementation Notes**
- This ticket wires together the full pipeline:
  `FOOD-004 (FastSAM) → FOOD-005 (edge cases) → FOOD-005b (component detection) → FOOD-006 (CLIP query) → FOOD-009 (confidence thresholding) → FOOD-012 (macro lookup)`
- Write pytest tests using `data/hot_pot_christmas.jpeg`, `data/assorted_breakfast.jpeg`, and `data/IMG_4488.jpeg` as fixtures — the last one specifically validates the mixed-bowl path
- This function is the single API boundary the mobile app will call — keep its output schema stable from this point forward

**Dependencies:** FOOD-004 through FOOD-014 (all prior tickets including FOOD-005b)

---

## Build Order Summary

| Sprint | Tickets | Goal |
|--------|---------|------|
| Sprint 1 | FOOD-001, FOOD-002, FOOD-003 | Data foundation |
| Sprint 2 | FOOD-004, FOOD-005, FOOD-005b | Segmentation + component detection |
| Sprint 3 | FOOD-007, FOOD-008, FOOD-006 | Embedding store + classifier |
| Sprint 4 | FOOD-009, FOOD-010 | Confidence + feedback loop |
| Sprint 5 | FOOD-012, FOOD-013, FOOD-014 | Nutrition data |
| Sprint 6 | FOOD-011, FOOD-015 | QA + integration gate |

> **FOOD-015 is the go/no-go gate.** Clean output from `analyze_meal()` = model layer complete = ready for mobile app.
>
> **Sprint 2 note:** FOOD-005b depends on FOOD-006 (CLIP) for its role-prompt classification, but FOOD-006 depends on FOOD-007 (ChromaDB) which is Sprint 3. Resolve this by building the role-prompt CLIP calls in FOOD-005b as a standalone function that loads CLIP directly — it does not query ChromaDB, so there's no circular dependency.
