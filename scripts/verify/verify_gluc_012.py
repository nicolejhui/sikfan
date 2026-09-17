"""
GLUC-012 verification: macros_incomplete propagation through POST /log-meal,
and exclusion from pipeline.glucose_model.build_training_data().

Follows scripts/verify_food013.py's check()/_patch_dirs() convention: all
storage paths this test touches are monkeypatched into a tempdir (and the
process chdir's into it for the duration) so the real
data/glucose/meal_logs.json, data/glucose/cgm_readings.json, and
data/job_status/ are never opened, let alone written.

Run from project root: python scripts/verify_gluc_012.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import api
import pipeline.glucose_store as glucose_store
import pipeline.glucose_model as glucose_model


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")
    sys.exit(1)


def check(msg: str, cond: bool) -> None:
    ok(msg) if cond else fail(msg)


def _patch_dirs(tmp: str) -> Path:
    """Redirect every module-level storage path this test touches into tmp.

    api.py's job_status paths are built inline as Path("data/job_status")/...
    on every call (not a module constant) — those can't be monkeypatched
    directly, so the caller must also chdir into tmp for the duration of the
    test. glucose_store's paths ARE module constants (anchored to __file__,
    unaffected by chdir), so they're patched here explicitly.

    Fails loudly if any expected attribute is missing — silently creating a
    new attribute that nothing reads would let a write fall through to the
    real data files, which is exactly what this patch exists to prevent.
    """
    tmp_path = Path(tmp)
    (tmp_path / "data" / "job_status").mkdir(parents=True)
    (tmp_path / "data" / "glucose").mkdir(parents=True)

    assert hasattr(api, "_MEAL_LOGS_PATH"), "api._MEAL_LOGS_PATH not found — verify script is out of sync with api.py"
    api._MEAL_LOGS_PATH = tmp_path / "data" / "glucose" / "meal_logs.json"

    assert hasattr(glucose_store, "_DATA_DIR"), "glucose_store._DATA_DIR not found — verify script is out of sync"
    glucose_store._DATA_DIR = tmp_path / "data" / "glucose"
    assert hasattr(glucose_store, "_CGM_FILE"), "glucose_store._CGM_FILE not found — verify script is out of sync"
    glucose_store._CGM_FILE = tmp_path / "data" / "glucose" / "cgm_readings.json"
    assert hasattr(glucose_store, "_MEAL_FILE"), "glucose_store._MEAL_FILE not found — verify script is out of sync"
    glucose_store._MEAL_FILE = tmp_path / "data" / "glucose" / "meal_logs.json"

    return tmp_path


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = _patch_dirs(tmp)
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            meal_id = "verify-gluc-012"
            unresolved_name = "unresolvable_dish_xyz"

            # Fabricate a completed analysis job with one dish flagged
            # needs_macro_entry — no live USDA call, fully deterministic.
            status_path = Path("data/job_status") / f"{meal_id}.json"
            status_path.write_text(json.dumps({
                "meal_id": meal_id,
                "status": "complete",
                "logged": False,
                "created_at": "2026-08-16T12:00:00+00:00",
                "completed_at": "2026-08-16T12:00:05+00:00",
                "result": {
                    "meal_id": meal_id,
                    "dishes": [{
                        "crop_id": "crop_0",
                        "name": unresolved_name,
                        "confidence": 0.9,
                        "status": "CONFIDENT",
                        "carbs_g": 0.0,
                        "protein_g": 0.0,
                        "fat_g": 0.0,
                        "calories": 0.0,
                        "needs_macro_entry": True,
                    }],
                    "total_carbs_g": 0.0,
                },
                "error": None,
            }))

            resp = api.log_meal(api.LogMealRequest(
                meal_id=meal_id, confirmed_dishes=[unresolved_name],
            ))
            check("response.macros_incomplete is True", resp.macros_incomplete is True)
            check("response.unresolved_dishes == [dish]", resp.unresolved_dishes == [unresolved_name])

            logs = json.loads(api._MEAL_LOGS_PATH.read_text())
            check("meal_logs.json row has macros_incomplete: True", logs[-1]["macros_incomplete"] is True)
            check("meal_logs.json row has unresolved_dishes", logs[-1]["unresolved_dishes"] == [unresolved_name])

            # Give the row a complete CGM window so it's otherwise trainable,
            # then confirm build_training_data() still excludes it.
            logs[-1]["cgm_window"] = {
                "status": "complete",
                "readings": [{"minutes_post_meal": m, "glucose_mgdl": 100} for m in range(0, 185, 5)],
                "peak_glucose": 100,
                "time_to_peak_minutes": 0,
                "return_to_baseline_minutes": 0,
                "area_under_curve": 0,
            }
            api._MEAL_LOGS_PATH.write_text(json.dumps(logs))

            X, y = glucose_model.build_training_data()
            check("flagged row excluded from build_training_data()", X.shape[0] == 0)
        finally:
            os.chdir(cwd)

    print("\nAll GLUC-012 checks passed.")


if __name__ == "__main__":
    main()
