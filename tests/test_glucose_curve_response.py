"""GLUC-013 — regression tests for the frozen-curve bug.

The bug: predict_glucose_curve() computed carb_to_fiber_ratio against a
fiber value that both serving paths always hardcoded to 0.0, sending every
inference query ~10sigma outside the Matern kernel's training support. The
GPR then fell back to its prior mean and returned the same curve regardless
of carbs — a correction on the Results screen looked applied (the macro
card updated) but the glucose projection never moved. See
plans/GLUC-013-plan.md.

These tests train a small synthetic GPR bundle (matching the new 6-feature
schema) rather than depending on the real data/models/glucose_model.joblib
— that file is gitignored, locally generated, and would make these tests
depend on machine state instead of the code under test.
"""

from __future__ import annotations

import json

import joblib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import StandardScaler

import pipeline.glucose_model as glucose_model
from pipeline.glucose_model import _FEATURE_NAMES, _TIME_GRID, predict_glucose_curve


def _synthetic_training_rows() -> list[tuple[list[float], float]]:
    """(features, peak_bg) pairs spanning a realistic range for each feature,
    with peak_bg genuinely responsive to carbs so the fitted GPR has a real
    carb signal to recover — unlike the production bug, where the ratio
    feature made every row look identical to the model."""
    rows = []
    for carbs in (10, 25, 40, 55, 70, 85):
        for fiber in (0, 3, 6):
            fat, protein, pre_bg, trend = 15.0, 20.0, 110.0, 0.0
            peak = 100.0 + 1.1 * carbs - 0.5 * fiber
            rows.append(([carbs, fiber, fat, protein, pre_bg, trend], peak))
    return rows


def _make_bundle(feature_names=None) -> dict:
    rows = _synthetic_training_rows()
    X = np.array([r[0] for r in rows], dtype=float)
    peaks = np.array([r[1] for r in rows], dtype=float)
    # Broadcast each scalar peak across the 37-point time grid so the
    # MultiOutputRegressor has the right output shape; only the peak value
    # (the max) is exercised by these tests.
    y = np.tile(peaks[:, None], (1, len(_TIME_GRID)))

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    kernel = Matern(nu=1.5) + WhiteKernel()
    model = MultiOutputRegressor(GaussianProcessRegressor(kernel=kernel, normalize_y=True))
    model.fit(X_scaled, y)

    names = feature_names if feature_names is not None else _FEATURE_NAMES
    feature_ranges = {
        name: [float(X[:, i].min()), float(X[:, i].max())]
        for i, name in enumerate(_FEATURE_NAMES)
    }
    return {
        "scaler": scaler,
        "model": model,
        "feature_names": names,
        "feature_ranges": feature_ranges,
    }


@pytest.fixture
def trained_bundle(tmp_path, monkeypatch):
    """Point pipeline.glucose_model at a freshly-trained synthetic bundle."""
    bundle_path = tmp_path / "glucose_model.joblib"
    joblib.dump(_make_bundle(), bundle_path)
    monkeypatch.setattr(glucose_model, "_MODEL_PATH", bundle_path)
    return bundle_path


def _peak(macros, pre_bg=110, trend="flat"):
    result = predict_glucose_curve(macros, pre_bg, trend)
    return max(p["predicted_bg"] for p in result["curve"])


class TestCarbResponseMagnitude:
    """The property GLUC-013 actually broke: a correction-sized carb change
    must visibly move the predicted peak. Not a monotonicity test (D5) —
    with 21 real training meals the fitted curve isn't strictly monotone;
    magnitude is what the user's complaint was actually about."""

    @pytest.mark.parametrize("fiber", [0.0, 5.0])
    def test_correction_sized_carb_change_moves_peak(self, trained_bundle, fiber):
        macros_common = {"total_fiber_g": fiber, "total_fat_g": 16.0, "total_protein_g": 26.0}

        # 24g / 40g / 67g == the ×0.60 / ×1.667 ends of pipeline.portion._FACTORS
        # applied to a 40g baseline (FOOD-020's too_high/too_low "lot" corrections).
        peak_low = _peak({**macros_common, "total_carbs_g": 24})
        peak_mid = _peak({**macros_common, "total_carbs_g": 40})
        peak_high = _peak({**macros_common, "total_carbs_g": 67})

        assert peak_high - peak_low >= 3.0, (
            f"a too_high/too_low 'lot' correction (24g vs 67g) moved the peak by only "
            f"{peak_high - peak_low:.2f} mg/dL at fiber={fiber} — this is the exact "
            f"frozen-curve failure GLUC-013 fixes"
        )
        # mid-point isn't required to sit between the ends (D5) — only that
        # the overall correction-sized swing has real magnitude, checked above.
        assert peak_mid != peak_low or peak_mid != peak_high


class TestFeatureSchemaGuard:
    def test_stale_bundle_raises_clear_retrain_error(self, tmp_path, monkeypatch):
        stale_bundle_path = tmp_path / "stale_glucose_model.joblib"
        # Old 8-feature schema (pre-GLUC-013), same shape the production
        # bundle had when carb_to_fiber_ratio/hour_of_day were still features.
        stale_names = _FEATURE_NAMES + ["hour_of_day", "carb_to_fiber_ratio"]
        joblib.dump(_make_bundle(feature_names=stale_names), stale_bundle_path)
        monkeypatch.setattr(glucose_model, "_MODEL_PATH", stale_bundle_path)

        with pytest.raises(RuntimeError, match="different feature set"):
            predict_glucose_curve(
                {"total_carbs_g": 40, "total_fiber_g": 0, "total_fat_g": 16, "total_protein_g": 26},
                110, "flat",
            )

    def test_matching_bundle_does_not_raise(self, trained_bundle):
        predict_glucose_curve(
            {"total_carbs_g": 40, "total_fiber_g": 0, "total_fat_g": 16, "total_protein_g": 26},
            110, "flat",
        )


class TestCorrectionMovesTheCurveEndToEnd:
    """The user's actual complaint, expressed as a test: a macro correction
    on a scanned-but-not-yet-logged meal must change what GET /glucose
    returns. Exercised through the real FastAPI route (api.app), not just
    the pipeline function, so the API boundary (fiber_g plumbing, the
    preview path's macro extraction) is covered too.

    Deliberately drives the job_status file directly rather than going
    through POST /correct-macros: that route's macro recomputation
    (_recompute_dish_macros -> resolve_composite_macros) can call out to an
    LLM-backed decomposition, which has no place in an offline unit test.
    The job_status mutation below is exactly what that route would write —
    same file, same fields — so GET /glucose is exercised identically.
    """

    def test_get_glucose_reflects_a_macro_correction(self, tmp_path, monkeypatch, trained_bundle):
        import api
        import glucose_analysis

        job_status_dir = tmp_path / "data" / "job_status"
        job_status_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)  # api.py's routes use the relative "data/job_status" path
        monkeypatch.setattr(glucose_analysis, "_JOB_STATUS_DIR", job_status_dir)
        monkeypatch.setattr(glucose_analysis, "_MODEL_PATH", trained_bundle)
        monkeypatch.setattr(glucose_analysis, "_METADATA_FILE", tmp_path / "training_metadata.json")
        monkeypatch.setenv("API_KEY", "test-key")

        from pipeline.glucose_store import save_cgm_reading

        meal_id = "test-meal-gluc013"
        created_at = "2026-09-06T12:00:00+00:00"
        save_cgm_reading({
            "timestamp": created_at, "glucose_mgdl": 110, "trend": "flat", "source": "manual",
        })

        def _write_job(carbs_g: float) -> None:
            job = {
                "status": "complete",
                "created_at": created_at,
                "result": {
                    "meal_id": meal_id,
                    "total_carbs_g": carbs_g,
                    "dishes": [{
                        "crop_id": "crop_0", "name": "test_dish", "confidence": 0.9,
                        "status": "CONFIDENT", "carbs_g": carbs_g, "protein_g": 26.0,
                        "fat_g": 16.0, "calories": 400.0, "fiber_g": 5.0,
                        "portion_g": 200.0, "portion_bucket": "medium",
                        "needs_macro_entry": False,
                    }],
                },
            }
            (job_status_dir / f"{meal_id}.json").write_text(json.dumps(job))

        client = TestClient(api.app)
        headers = {"x-api-key": "test-key"}

        _write_job(24.0)  # too_high · a lot applied to a 40g baseline
        before = client.get(f"/glucose/{meal_id}", headers=headers)
        assert before.status_code == 200, before.json()
        peak_before = before.json()["prediction"]["predicted_peak_bg"]

        _write_job(67.0)  # too_low · a lot applied to the same 40g baseline
        after = client.get(f"/glucose/{meal_id}", headers=headers)
        assert after.status_code == 200, after.json()
        peak_after = after.json()["prediction"]["predicted_peak_bg"]

        assert abs(peak_after - peak_before) > 3.0, (
            f"correcting carbs from 24g to 67g moved the predicted peak by only "
            f"{abs(peak_after - peak_before):.2f} mg/dL — GET /glucose is still frozen"
        )


class TestBaselinePrediction:
    """API-014 — the pre-correction glucose curve (`baseline_prediction`)."""

    def test_every_macro_feature_has_a_baseline_default(self):
        """§1 as an invariant: a future GPR feature added to _FEATURE_NAMES
        cannot silently escape _BASELINE_DEFAULTS the way fiber_g did."""
        import api

        macro_features = [
            n for n in _FEATURE_NAMES if n.startswith("total_") and n.endswith("_g")
        ]
        for feature in macro_features:
            short_key = feature[len("total_"):]  # total_carbs_g -> carbs_g
            assert short_key in api._BASELINE_DEFAULTS, (
                f"{feature} is a live GPR feature but {short_key!r} is missing from "
                f"_BASELINE_DEFAULTS — it will not be captured on correction or "
                f"restored on reset (this is exactly the GLUC-013 fiber_g bug)"
            )

    def _setup(self, tmp_path, monkeypatch, trained_bundle):
        import api
        import glucose_analysis

        job_status_dir = tmp_path / "data" / "job_status"
        job_status_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(glucose_analysis, "_JOB_STATUS_DIR", job_status_dir)
        monkeypatch.setattr(glucose_analysis, "_MODEL_PATH", trained_bundle)
        monkeypatch.setattr(glucose_analysis, "_METADATA_FILE", tmp_path / "training_metadata.json")
        monkeypatch.setenv("API_KEY", "test-key")

        from pipeline.glucose_store import save_cgm_reading

        meal_id = "test-meal-api014"
        created_at = "2026-09-07T12:00:00+00:00"
        save_cgm_reading({
            "timestamp": created_at, "glucose_mgdl": 110, "trend": "flat", "source": "manual",
        })
        return api, job_status_dir, meal_id, created_at

    def _write_job(self, job_status_dir, meal_id, created_at, *, carbs_g, baseline=None):
        dish = {
            "crop_id": "crop_0", "name": "test_dish", "confidence": 0.9,
            "status": "CONFIDENT", "carbs_g": carbs_g, "protein_g": 26.0,
            "fat_g": 16.0, "calories": 400.0, "fiber_g": 5.0,
            "portion_g": 200.0, "portion_bucket": "medium",
            "needs_macro_entry": False,
        }
        if baseline is not None:
            dish["_baseline"] = baseline
        job = {
            "status": "complete",
            "created_at": created_at,
            "result": {
                "meal_id": meal_id,
                "total_carbs_g": carbs_g,
                "dishes": [dish],
            },
        }
        (job_status_dir / f"{meal_id}.json").write_text(json.dumps(job))

    def test_undo_restores_fiber(self, tmp_path, monkeypatch, trained_bundle):
        """§3: snapshot, correct, reset — fiber_g must return to its original
        value alongside carbs_g (the GLUC-013 regression §1 fixes)."""
        import api

        dish_entry = {
            "crop_id": "crop_0", "name": "test_dish", "carbs_g": 40.0,
            "protein_g": 26.0, "fat_g": 16.0, "calories": 400.0, "fiber_g": 5.0,
            "portion_g": 200.0,
        }
        api._snapshot_baseline(dish_entry)
        assert dish_entry["_baseline"]["fiber_g"] == 5.0

        # Simulate a correction mutating fiber (composition-changing edit).
        dish_entry["carbs_g"] = 67.0
        dish_entry["fiber_g"] = 0.5

        baseline = dish_entry["_baseline"]
        for key in api._BASELINE_DEFAULTS:
            dish_entry[key] = baseline[key]

        assert dish_entry["fiber_g"] == 5.0
        assert dish_entry["carbs_g"] == 40.0

    def test_no_ghost_when_inputs_unchanged(self, tmp_path, monkeypatch, trained_bundle):
        """§3: a dish-confirm / carb-neutral correction snapshots _baseline but
        doesn't change the macros — baseline_prediction must be None (D3)."""
        api, job_status_dir, meal_id, created_at = self._setup(tmp_path, monkeypatch, trained_bundle)

        baseline = {
            "carbs_g": 40.0, "fiber_g": 5.0, "fat_g": 16.0, "protein_g": 26.0,
        }
        self._write_job(job_status_dir, meal_id, created_at, carbs_g=40.0, baseline=baseline)

        client = TestClient(api.app)
        resp = client.get(f"/glucose/{meal_id}", headers={"x-api-key": "test-key"})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["baseline_prediction"] is None

    def test_ghost_when_carbs_move(self, tmp_path, monkeypatch, trained_bundle):
        """§3: a real correction produces a present baseline_prediction whose
        predicted_peak_bg differs from the live one."""
        api, job_status_dir, meal_id, created_at = self._setup(tmp_path, monkeypatch, trained_bundle)

        baseline = {
            "carbs_g": 67.0, "fiber_g": 5.0, "fat_g": 16.0, "protein_g": 26.0,
        }
        self._write_job(job_status_dir, meal_id, created_at, carbs_g=24.0, baseline=baseline)

        client = TestClient(api.app)
        resp = client.get(f"/glucose/{meal_id}", headers={"x-api-key": "test-key"})
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["baseline_prediction"] is not None
        assert body["baseline_prediction"]["predicted_peak_bg"] != body["prediction"]["predicted_peak_bg"]
