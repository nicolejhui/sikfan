# Skill: Run Full GlycoLens Pipeline

Execute the full image processing and metabolic analysis pipeline on an unlabeled meal photo.

## Description
This skill runs a raw meal image through the complete pipeline using pipeline/run_full_pipeline.py to extract nutritional metadata, log the meal to generate a unique tracking ID, evaluate glycemic impact based on current glucose metrics, and save an annotated visualization with labeled bounding boxes.

## Prerequisites
Before running this skill, ensure the following modules are completely operational and their tests are passing:
- `analyze_meal` (Meal macro and bounding box estimation)
- `analyze_glucose` (Metabolic and glycemic impact analysis)
- `pipeline.glucose_store` (Data persistence layer)

External dependencies required:
- `opencv-python`
- `numpy`
- `Pillow`

## Expected Outputs
The pipeline returns a structured JSON-like dictionary containing:
1. `labeled_image_path`: Path to the annotated image (`data/pipeline_runs/{meal_id}_labeled.jpg`).
2. `analyze_meal_output`: Full nested dictionary of nutritional metrics and bounding boxes.
3. `analyze_glucose_output`: Full nested dictionary of simulated or calculated glycemic variations.

---

## Claude Code Prompt Example

To trigger this skill, instruct Claude using a variation of this prompt:

```text
Run the full pipeline on the image "data/sample_meal.jpg" with a pre-meal glucose of 105 and a "flat" trend.