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

## Epic 6: Macro Lookup Follow-ups (Post-MVP)

> Surfaced 2026-07-31 while debugging zero-macro results in the mobile app
> (MOB-010 testing). Both tickets touch `pipeline/macro_lookup.py`.

### FOOD-016 — Replace `_PINYIN_FALLBACK` hardcoded table with confirmation-time USDA pinning

**User Story**
As a developer, I want new dishes to get correct USDA macro matches automatically as they're confirmed, instead of only the ~24 dishes someone thought to hand-list in a fallback table, so macro lookup scales the same way the dish taxonomy itself scales.

**Why this exists / the problem**
`macro_lookup.py`'s `_PINYIN_FALLBACK` dict is a hardcoded `dict[str, str]` of dish-name → alternate-search-string overrides, used when the primary USDA FNDDS search returns 0 results (e.g. `"mapo tofu"` → `"mapo tofu spicy"`). It violates this project's own architecture rule (`CLAUDE.md`: "No fixed class lists. Dishes are added dynamically via user confirmation") — any dish not explicitly listed gets no fallback, no matter how the pipeline recognizes it. `boiled_chicken` (not in the table) and, prior to this fix, many pinyin-derived names all silently fail the same way.

**Acceptance Criteria**
- `POST /confirm-dish` (`CONFIRM` / `CORRECT` / `ADD_NEW`) resolves and stores a USDA `fdc_id` on the dish's ChromaDB metadata at confirmation time, not at lookup time
- `lookup_macros()` checks for a pinned `fdc_id` on the confirmed dish first; if present, fetches that exact USDA record directly (no search, no fallback table)
- `_PINYIN_FALLBACK` dict is deleted once the pinning path is in place
- Dishes with no pinned `fdc_id` yet (not-yet-confirmed / legacy) fall through to the existing search-then-no_results path, so nothing regresses for the transition period
- Existing `data/macro_cache/*.json` entries remain valid — this only changes how new pins are established
- **A `CORRECT` action (dish name changed, not just confirmed) re-runs macro lookup for the *new* label** — the old dish's cached macros must not silently persist under the corrected name. `POST /confirm-dish` re-resolves `fdc_id`/`carbs_g`/etc. for `corrected_label` before writing the ChromaDB update, exactly as if it were a fresh `ADD_NEW`
- `ConfirmDishResponse` gains a field indicating whether the meal's `total_carbs_g` changed as a result of the correction (e.g. `macros_changed: bool`) — this is what the mobile client uses to decide whether it needs to re-fetch glucose (see mobile-side note below)
- If the corrected macro total differs meaningfully from what the glucose prediction was computed against, flag the meal's existing prediction as stale rather than silently leaving a mismatched chart on screen — exact staleness threshold and recompute-vs-flag behavior TBD at implementation time, but "do nothing" is not acceptable here since a stale curve is actively misleading, same failure mode as FOOD-017

**Implementation Notes**
- The natural point to resolve the USDA match is inside the `POST /confirm-dish` route (`api.py`) — a human is already confirming/naming the dish at that exact moment
- Store `fdc_id` alongside the existing ChromaDB dish metadata (`dish_name`, `cuisine_type`, `date_added`, `confirmed_count` from FOOD-007)
- If the confirming user needs to pick among ambiguous USDA candidates, reuse `lookup_macros()`'s existing top-3 `candidates` list rather than building new UI
- This supersedes the "FOOD-016 LLM fallback" idea mentioned in `CLAUDE.md`'s FOOD-012 notes — an LLM-generated query expansion is still a viable fallback for dishes where no reasonable USDA match exists at all, but should be scoped as a separate ticket if still wanted, not conflated with this one
- **Mobile-side implication (Epic 10, not this ticket):** once `POST /confirm-dish` is real, `ConfirmDishSheet`'s `handleSaveCorrection` must not stop at a local cosmetic name update (what the Epic 9 stub does) — on a successful response it should re-fetch `GET /meal-status/{meal_id}` (updated macros) and, if `macros_changed` is true, `GET /glucose/{meal_id}` (updated prediction), rather than trusting the pre-correction values already in `mealStore`/`glucoseStore`

**Dependencies:** FOOD-010 (confirmation loop), FOOD-012 (macro lookup)

---

### FOOD-017 — Fix macro lookup `None` → `0.0` coercion masking no-match results — CLOSED

**Resolution note:** Superseded by the `needs_macro_entry` flag pattern
(`DishResult.needs_macro_entry`, shipped alongside this ticket) rather than
this ticket's originally-specified `carbs_g: float | None` null-on-the-wire
approach. `carbs_g` stays `0.0` for an unresolved dish; `needs_macro_entry`
is the signal a client checks instead. This was deliberate — nulling
`carbs_g` would have been a breaking change to the frozen `DishResult`/
`analyze_meal` schema, and a boolean flag alongside an unchanged numeric type
is additive. GLUC-012 (`docs/GLUCOSE-TICKETS.md`) extends the same pattern
into the training corpus: `POST /log-meal` now propagates
`needs_macro_entry` into `meal_logs.json` as `macros_incomplete` +
`unresolved_dishes`, and `train_model()` excludes flagged rows.

**User Story**
As a developer, I want a genuine "no macro data found" result to be visibly distinguishable from "this dish has zero carbs/protein/fat/calories," so the app doesn't silently show wrong nutrition info as if it were real.

**Why this exists / the problem**
`macro_lookup._build_result()` returns `calories=None, carbs_g=None, ...` when USDA search finds nothing (`source: "no_results"`). Somewhere between that and the `/analyze-meal` response, those `None`s become explicit `0.0`s — confirmed via `GET /meal-status/{meal_id}` returning `"carbs_g": 0.0` for `boiled_chicken`/`dumplings`, both of which had no successful USDA match. A `0.0` reads as "this food has no carbs," not "we don't know" — meaning `total_carbs_g` and the mobile Macros card can silently understate a meal's real carb load.

**Acceptance Criteria**
- Trace where `MacroResult.carbs_g == None` gets converted to `0.0` (likely in `analyze_meal.py`'s crop-to-`DishResult` mapping)
- `DishResult` fields for a dish with `source: "no_results"` surface as `None`/null on the wire (matching the existing `portion_g`/`portion_bucket` null convention), not `0.0`
- `total_carbs_g` aggregation either excludes unresolved dishes or the response otherwise flags that the total is incomplete — silently summing `None`-as-`0` into a total the user trusts is the actual bug impact, not just a display nit
- Add a regression test with a dish name guaranteed to 0-result USDA search, asserting `carbs_g is None` end-to-end through `/analyze-meal`

**Implementation Notes**
- This is independent of FOOD-016 — fix this regardless of whether/when the pinning replacement lands, since it's a correctness bug, not a coverage gap
- Mobile-side (`MOB-007` Results screen) already renders `Macros` unconditionally once `macros` is non-null; once this returns `None` per-field, that screen will need a small follow-up to show "—" instead of `0` for unresolved dishes — file as a MOB-* ticket once this lands, not bundled in here

**Dependencies:** FOOD-012

---

## Epic 7: Embedding Quality Follow-ups (Post-MVP)

> Surfaced 2026-07-31 while reviewing a live scan: the noodle/beef components of
> a `braised_beef_noodle` bowl matched at confidence 0.27 / 0.23 — well below
> even the `UNCERTAIN` band's usual range — while a visually similar
> `marinated_egg` crop from the same photo matched confidently. Prompted the
> question of whether CLIP's frozen embeddings need adaptation for this dish
> vocabulary, as an axis separate from FOOD-010's per-dish confirmation loop.

### FOOD-018 — Train a lightweight projection head on frozen CLIP embeddings

**User Story**
As a developer, I want the embeddings driving dish matching to better separate my specific dish vocabulary, so low-confidence/confused matches (like `beef` at 0.228) become rarer without needing a full model fine-tune.

**Why this exists / the problem**
The pipeline currently does zero-shot CLIP: embeddings come straight from the frozen `openai/clip` ViT-B/32 encoder, and the only adaptation mechanism is FOOD-010's rolling-average centroid update per dish (`new_centroid = (old*n + new)/(n+1)`) — which improves *where a dish's centroid sits* but not *how separable the underlying embedding space is* for dishes CLIP wasn't trained to distinguish well (e.g. visually similar Chinese dishes, per the confusion-pair note already in FOOD-011). A live scan surfaced this concretely: the actual noodle+beef bowl in a `braised_beef_noodle` photo matched at 0.27/0.23 confidence, while a separately-segmented `marinated_egg` crop from the same photo matched confidently at 0.83.

**Acceptance Criteria**
- A small trainable projection layer (linear or shallow MLP) sits on top of the frozen CLIP image encoder's output
- Projection head is trained with a metric-learning loss (e.g. triplet loss or ArcFace) using confirmed dish photos as positive/negative pairs — dishes confirmed via FOOD-010's loop are the training signal, no new labeling process required
- ChromaDB stores the *projected* embedding (post-projection-head), not the raw CLIP embedding — this is a storage-format change, so existing embeddings need re-encoding, not just appending
- Training runs on MPS in a reasonable time for the current dataset size (tens of dishes, ~10+ photos each)
- A before/after comparison on the existing confusion pairs flagged by FOOD-011 (similarity > 0.88) shows measurable separation improvement
- CLIP itself remains frozen — this ticket does not fine-tune or LoRA-adapt CLIP's own weights (see Implementation Notes for why, and where that heavier option is deferred to)

**Implementation Notes**
- Deliberately the lighter of two options considered: full CLIP fine-tuning (or a LoRA adapter on CLIP's vision tower) was the alternative, rejected for now because it needs meaningfully more data per class than this project currently has, requires a real train/validation split, and risks overfitting or forgetting CLIP's general visual knowledge on a small personal dataset. A frozen-CLIP + trained-projection-head approach needs far fewer examples per dish and can't damage CLIP's base representation. Revisit full fine-tuning only if the projection head plateaus and confusion pairs persist.
- This is complementary to, not a replacement for, FOOD-010's confirmation loop — centroids still matter for per-dish positioning; this ticket changes the space they live in
- Re-encoding existing ChromaDB embeddings through the new projection head is a one-time migration step — needs a script, not just a training run
- Natural trigger for retraining: after every N new confirmations (mirrors FOOD-011's "every 50 new meal logs" QA cadence) rather than on every single confirmation

**Dependencies:** FOOD-006 (CLIP classifier), FOOD-007 (ChromaDB), FOOD-010 (confirmation loop — training data source), FOOD-011 (confusion-pair detection — defines what "better" means here)

---

### FOOD-019 — Composite dish decomposition for macro lookup — IMPLEMENTED

**Status note (2026-08-28):** Backend implemented — `pipeline/dish_decompose.py`,
wired into `api.py`'s `_recompute_dish_macros()` (API-012), `pipeline/glucose_model.py`'s
`is_trainable()` gate, `config.yaml`. Verified via `scripts/verify_food019.py`
(mocked LLM boundary, no credits required) and `scripts/verify_gluc_012.py`
(no regression). The mobile-side breakdown UI (MOB-014) and a live smoke
test against a real Sonnet call (`scripts/verify_food019.py` currently mocks
the Anthropic client entirely — see the `_call_llm_decompose` docstring) are
still open. Full design and decision log: `plans/FOOD-019-plan.md`.

**User Story**
As a user, I want a composite meal name like "Japanese curry chicken katsu
with white rice" to show real macro info, so a dish that's obviously a
combination of foods doesn't silently fall back to "no macro info" just
because USDA has no single entry matching the whole phrase.

**Why this exists / the problem**
`lookup_macros()` (`pipeline/macro_lookup.py`) queries USDA FNDDS with the
full dish name as one string. FNDDS has entries for individual prepared
foods, not arbitrary user-typed combinations, so a composite name returns
`source: "no_results"` and `_recompute_dish_macros()` (`api.py`) returns hard
`0.0` macros with `needs_macro_entry: True` — this was, in practice, the main
coverage ceiling once real meals started getting logged through the app.

**Acceptance Criteria**
- `pipeline.dish_decompose.decompose_dish(dish_name)` parses a dish name into
  component foods via one Anthropic call, caches the result, and returns a
  one-component passthrough for a simple dish name (no behavior change) —
  see Implementation Notes for why this is LLM-only, not rule-based
- The decomposition cache self-invalidates on a prompt edit or a
  `config.yaml` model swap (`schema_version`/`model` stamped on every entry)
  — no manual cache-clear step required for normal drift
- A failed decomposition call (no key, network, malformed output) fails open
  to the passthrough shape and is **never cached** — one bad call cannot
  permanently pin a dish to "does not decompose"
- `resolve_composite_macros(dish_name)` folds resolved/estimated components
  into a single per-100g profile shaped exactly like a
  `pipeline.nutrition` cache entry (`source: "composite"`), so
  `pipeline.portion`/`pipeline.nutrition` need zero code changes to consume it
- Two coverage numbers are computed and persisted: `macro_coverage`
  (mass-weighted) drives UI transparency; `carb_coverage` (carb-weighted, the
  metric that actually matters for glucose training) drives the
  `glucose_training.min_carb_coverage` gate in `pipeline.glucose_model`
- `needs_macro_entry` on a composite dish is `True` only when *no* component
  resolved via USDA at all — a partially-estimated composite still reports a
  real (if partly estimated) carb total, never a hard zero
- `scripts/clear_decompositions.py` provides manual recourse (`--dish`,
  `--stale`, `--all`) for a bad split that needs re-deriving outside the
  automatic self-invalidation path

**Implementation Notes**
- **No rule-based parsing anywhere** — no connective splitter ("with" /
  "over" / "and"), no name→grams table. A splitter needs two hardcoded
  tables, not one (a connective list *and* a per-food gram table, since USDA
  is per-100g), which is the same brittleness this file's own `FOOD-012
  Notes` section already flags in `_PINYIN_FALLBACK`. `_PINYIN_FALLBACK`
  itself is left alone, not extended or removed — that's FOOD-016's job.
- The decomposer supplies relative composition (proportions) only, never
  absolute grams — portion size stays owned by `pipeline.portion`'s
  pixel-based estimate, which is real signal an LLM without the photo
  cannot supply better than a guess
- Default model is `claude-sonnet-5`, read from `config.yaml`'s
  `llm.decompose_model` rather than hardcoded, so quality can be escalated
  by editing config, not code
- Full decision log (10 entries — engine choice, proportions-not-grams,
  cache design, coverage-metric split, the training-gate threshold and its
  corpus measurement, model choice, cache self-invalidation, additive schema):
  `plans/FOOD-019-plan.md`
- This is the ticket `docs/TICKETS-v2.md`'s own FOOD-012 Notes section
  anticipated ("pairs naturally with FOOD-016 LLM fallback") and that
  FOOD-016 (`docs/TICKETS-v2.md`) explicitly scoped out as a separate ticket
  rather than conflating with USDA-pinning — this is that separate ticket
- **GLUC-012 interaction:** extends that ticket's `macros_incomplete` binary
  exclusion with a second, additive exclusion clause on `carb_coverage` —
  recorded as an amendment in `docs/GLUCOSE-TICKETS.md` since GLUC-012's own
  D3 is marked locked

**Dependencies:** FOOD-012 (macro lookup), FOOD-014 (portion estimation —
consumes the composite cache entry unchanged), GLUC-012 (training exclusion
— gains the `carb_coverage` clause)

---

## Macro Correction — Pipeline Support (Post-MVP)

> Surfaced 2026-08-29 from the macro-correction design (Claude Design project
> `23216dca-776e-4021-ae6d-814c5407e7e2`, `results.jsx`). `POST /confirm-dish`
> lets a user correct *what a dish is*; nothing lets them correct *how much of
> it there is*, even though that number drives the glucose projection, the
> macro card, and the training set. These two tickets supply the pipeline
> capabilities `API-013` needs; they are independent of each other and can be
> built in either order or in parallel.

### FOOD-020 — Durable per-dish portion priors

**User Story**
As a user, when I tell the app the carbs are too high for a dish, I want that
judgement to change *future* scans of the same dish — not just the meal in
front of me — so I'm not re-correcting the same bowl of noodles every week.

**Why this exists / the problem**
The read path already exists and has never been wired to anything.
`pipeline/portion.py`'s `_read_multiplier()` (L106) is consulted by
`estimate_portion()` on every scan (L177) and by both mixed-bowl paths
(L235, L278) — `portion_g = base_g * multiplier`. FOOD-014 also shipped the
writer, `apply_multiplier()` (L299), but it is called from **nowhere in
production code**, only from `scripts/verify_food014.py`. There is no way for
a correction to persist a learned prior.

Two defects block reusing what's there. `apply_multiplier()` writes into
`data/macro_cache/{slug}.json` for the common case, and that file is
full-replaced by `api.py`'s `_write_macro_cache_entry` on composite resolution
and deleted outright by `clear_cache()` / `clear_decomposition()` — so a prior
written there survives only until the next correction touches the same dish,
which is the normal path, not a rare race. Separately, `_read_multiplier()`
returns as soon as an override *file* exists (L108–113), so a macro-only
override like the real `data/overrides/mapo_tofu.json` — which has no
`portion_multiplier` key — yields `1.0` and never falls through.

**Acceptance Criteria**
- [ ] `save_portion_prior(dish_name, factor, reason, direction)` writes
      `data/portion_priors/{slug}.json` atomically via
      `pipeline.nutrition._atomic_write`, slugged through `_slug()` (=
      `feedback.normalize_dish_name()`) like every other per-dish key
- [ ] **A `leftover` correction never produces a `portion_multiplier`**, and an
      unrecognized reason raises `ValueError` — enforced inside
      `pipeline/portion.py` via `PERSISTED_REASONS`/`COUNTED_REASONS`, not by
      the calling route
- [ ] A prior **activates on the second** correction in the same direction, not
      the first; a conflicting correction replaces the evidence and resets the
      count; `looks_right` clears pending evidence and spares an active prior
- [ ] `looks_right` reaches `save_portion_prior()` rather than being short-circuited
      by the route, and is accepted with `reason=None` — validation is
      direction-aware, not a flat reason-set check (D8)
- [ ] `too_low` factors are reciprocals of the `too_high` ones (1/0.85, 1/0.60),
      so a correction followed by its opposite returns the multiplier to
      exactly 1.0
- [ ] Multiplier clamped to `[0.5, 2.0]` and rounded to 2dp — an arbitrary
      float is accepted, not quantized to a grid (see Implementation Notes)
- [ ] A prior whose `last_scanned` is older than 180 days stops being applied
      but survives as evidence; `touch_portion_prior()` (called from `api.py`'s
      scan-completion path, never from `_read_multiplier()`) revives it
- [ ] The record stores the *evidence*, not just a float: `n_corrections` and
      a per-`reason` count, both accumulating across repeated corrections —
      including for `leftover`, which is counted but never acted on
- [ ] `_read_multiplier()` reads priors > overrides > macro_cache, falling
      through any layer whose file lacks a `portion_multiplier` key rather
      than any layer whose file is absent (fixes the early-return bug)
- [ ] A prior survives a `data/macro_cache/{slug}.json` overwrite,
      `clear_cache()`, and `clear_decomposition()` — durable by construction,
      since no existing cache-clearing path can reach the new directory
- [ ] `estimate_portion()` and both mixed-bowl paths pick the prior up through
      the existing `base_g * multiplier` lines, with no edit to them
- [ ] `apply_multiplier()` and `_VALID_MULTIPLIERS` left untouched;
      `scripts/verify_food014.py` still passes unmodified
- [ ] `scripts/verify_food020.py` passes (temp `data/` root — never touches
      real user data)

**Implementation Notes**
- **A new store, not an existing one.** `data/overrides/` means "macros the
  user typed by hand" (`nutrition.set_manual_override()`, validated by
  `_validate_macros()`); `apply_multiplier()` already has to bypass that
  validation to write there. Piling a second meaning onto the file makes
  `reset_override()` ambiguous — it would discard the learned prior along with
  the hand-typed macros.
- **Continuous float, not a widened `_VALID_MULTIPLIERS`.** The factors
  *compound* (0.85 corrected again by 0.85 needs 0.7225). Quantizing to a grid
  means a second correction snaps back to the same point and appears to do
  nothing — the exact failure the correction UI exists to fix.
- **Three guards against a permanently wrong number** (added 2026-08-29 after
  review; full reasoning in `plans/FOOD-020-plan.md` D4–D7). Each addresses a
  way a correction could silently under-count a dish forever, which in a T1D app
  means chronically under-dosed insulin:
  1. *The store owns the reason rule.* A route-level `if` keeping `leftover`
     out is correct today and one refactor from being wrong, with no visible
     symptom when it breaks.
  2. *Two corrections before learning.* One atypical photo shouldn't bias a
     dish permanently.
  3. *Reciprocal factors + a `[0.5, 2.0]` clamp.* The design's symmetric
     percentages aren't invertible — down-a-lot then up-a-lot lands at 0.84,
     not 1.0, so a user who over-corrects can never return to the original
     estimate and oscillation ratchets downward. **This diverges from
     `results.jsx:612` and should be flagged to the design project.**
- **Compounding itself is correct and stays.** The user judges the number in
  front of them: at a prior of 0.60 showing 36 g, "still too high, a little"
  means 36 × 0.85, so the prior becomes 0.51. Replacing it with 0.85 would jump
  the dish back to 51 g, undoing a correction they never asked to undo.
- **Staleness is counted in scans, not corrections.** A prior you never have to
  correct is a prior that's working — expiring on correction-silence would break
  exactly the right ones. Only a dish you've stopped scanning decays.
- The early-return fix is kept in this ticket rather than split out: adding a
  third layer to `_read_multiplier()` without fixing precedence would leave
  the new layer subject to the same bug.
- Full decision log: `plans/FOOD-020-plan.md`

**Dependencies:** FOOD-014 (`_read_multiplier`, the gram tables),
`pipeline/nutrition` (`_atomic_write`, `_slug`, `_load_json`). Blocks API-013.

---

### FOOD-021 — Component folding + LLM ingredient candidates

**User Story**
As a user, when the breakdown lists an ingredient that isn't in my dish — or
misses one that is — I want to swap it, remove it, or add the missing one and
have the macros re-fold correctly, so a wrong ingredient doesn't quietly
distort the carb total.

**Why this exists / the problem**
Two pipeline capabilities are missing for the ingredient-level fix flow.
First, `resolve_composite_macros()` (L430–524) inlines the `Decimal` weighted
fold over `_component_per_100g()` plus the `macro_coverage`/`carb_coverage`
computation — so that fold can't be reused on a *user-edited* component list,
which is exactly what `API-013` needs. Second, the design mock hardcodes
`item.alts` (plausible alternatives for a misread component) and
`MEAL.addable` (commonly-missed items) as literal tables in `theme.jsx`. Real
ones have to be generated.

**Acceptance Criteria**
- [ ] `fold_components(components)` extracted; `resolve_composite_macros()`
      delegates to it with byte-for-byte identical output for identical input
      (pure refactor, asserted in the verify script)
- [ ] `fold_components()` raises `ValueError` on an empty component list or one
      whose proportions sum to zero, rather than dividing by zero computing
      `macro_coverage` — it is a public pipeline function, so it validates its
      own input rather than trusting `API-013`'s `empty_dish` guard to run first
- [ ] `suggest_alternatives(dish_name, component_name)` and
      `suggest_additions(dish_name, present_components)` each make at most one
      Anthropic call, cache per dish, and fail closed to `[]`
- [ ] Every returned candidate carries real USDA-backed per-100g macros —
      resolved through `lookup_macros(name, suggest_query=...)` +
      `select_best_usda_candidate()`; an unbacked candidate is **dropped**,
      never returned zero-filled
- [ ] `suggest_additions()` excludes everything already in
      `present_components` — the exclusion happens at suggestion time, in the
      model's judgement, not in a client-side matcher
- [ ] No hardcoded food-name table, keyword set, or string-similarity
      threshold anywhere in the new code (CLAUDE.md rule)
- [ ] Candidate cache at `data/macro_cache/candidates/{slug}.json`, invalidated
      on `schema_version`/`model` mismatch and clearable via
      `scripts/clear_decompositions.py`
- [ ] A decomposition record carrying `user_edited: true` survives
      schema/model invalidation in `_load_decomposition()`; a normal one does
      not
- [ ] `scripts/verify_food021.py` passes; `scripts/verify_food019.py` still
      passes unmodified (no drift from the refactor)

**Implementation Notes**
- **Candidates are LLM-generated, not tabled.** A per-dish alternatives table
  is the brittleness CLAUDE.md records being removed from this path twice
  (`_PINYIN_FALLBACK`, deleted; `_MATCH_STOPWORDS`, added and rejected in the
  same session) — and worse, since it only ever covers the dishes someone
  thought to type, in an app whose whole premise is no fixed class list. The
  chosen shape matches `suggest_usda_query()` / `select_best_usda_candidate()`:
  one cached call, structured output, fails closed. An empty list is a valid
  answer the UI degrades to gracefully; a failure is never a wrong number.
- **USDA resolution before display.** An LLM can name a plausible ingredient it
  cannot cost. Showing a candidate whose macros we'd then have to guess would
  make a "correction" *less* trustworthy than the scan it replaced. Three real
  options beat five with two invented. This costs one USDA call per candidate
  on a cold cache, which is why the result is cached per dish.
- **`sameFood()` is not ported.** `results.jsx:319` dedupes the add-list with a
  word-set match, which fails silently in both directions on real data ("sauce,
  soy" vs "soy sauce" collapse; "chicken breast" vs "chicken, breast, fried"
  don't) and produces double-counted carbs. Exact-slug equality via
  `normalize_dish_name()` is the backstop — identity matching, not similarity.
- **`user_edited` survives invalidation** because once a user has hand-corrected
  a breakdown, discarding it on the next model bump throws away a human
  judgement in favour of a machine one.
- **Open decision — the `_SCHEMA_VERSION` bump.** The plan bumps it because the
  record gains an optional `user_edited` key, but that invalidates the entire
  existing `data/macro_cache/decompositions/` corpus (~60 dishes) and forces a
  full re-decomposition on next scan of each. Since the field is additive and a
  missing key already reads as `false` via `.get()`, not bumping is the cheaper
  and equally correct option. Settle before implementing.
- Full decision log: `plans/FOOD-021-plan.md`

**Dependencies:** FOOD-019 (`dish_decompose.py`, `resolve_composite_macros`,
`_component_per_100g`, `select_best_usda_candidate`), FOOD-012
(`lookup_macros`). Blocks API-013.

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
