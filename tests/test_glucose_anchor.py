"""SC-19 ... SC-20 — glucose anchoring (pipeline/glucose_store.py)."""

from __future__ import annotations

import pytest

import pipeline.glucose_store as glucose_store


def _reading(minutes_before_meal, meal_time, glucose_mgdl=110, source="dexcom_csv", trend="flat"):
    from datetime import timedelta
    ts = (meal_time - timedelta(minutes=minutes_before_meal)).isoformat().replace("+00:00", "Z")
    return {"timestamp": ts, "glucose_mgdl": glucose_mgdl, "trend": trend, "source": source}


@pytest.mark.safety
class TestSC19NoFakeBaselineEver:
    """As a user without a recent CGM reading, I want to be asked for my
    glucose - not shown an impact prediction built on a number nobody
    measured.

    Why this test exists: confirmed user story #4 (CLAUDE.md) - there is no
    default baseline glucose value.
    """

    def test_reading_20_minutes_before_meal_returns_none(self):
        from datetime import datetime, timezone
        meal_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        glucose_store.save_cgm_reading(_reading(20, meal_time))

        result = glucose_store.get_pre_meal_glucose(meal_time.isoformat())
        assert result is None

    def test_closest_of_two_readings_wins(self):
        from datetime import datetime, timezone
        meal_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        glucose_store.save_cgm_reading(_reading(10, meal_time, glucose_mgdl=140))
        glucose_store.save_cgm_reading(_reading(5, meal_time, glucose_mgdl=120))

        result = glucose_store.get_pre_meal_glucose(meal_time.isoformat())
        assert result["glucose_mgdl"] == 120

    def test_exactly_15_minutes_is_inclusive(self):
        from datetime import datetime, timezone
        meal_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        glucose_store.save_cgm_reading(_reading(15, meal_time, glucose_mgdl=130))

        result = glucose_store.get_pre_meal_glucose(meal_time.isoformat())
        assert result is not None
        assert result["glucose_mgdl"] == 130

    def test_just_over_15_minutes_is_excluded(self):
        from datetime import datetime, timezone
        meal_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        glucose_store.save_cgm_reading(_reading(15.01, meal_time, glucose_mgdl=130))

        result = glucose_store.get_pre_meal_glucose(meal_time.isoformat())
        assert result is None


class TestSC20ManualEntryIsAFirstClassReading:
    """As a user typing my glucose in by hand, I want the same prediction
    quality as a CGM user."""

    def test_manual_source_accepted_and_served(self):
        from datetime import datetime, timezone
        meal_time = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
        glucose_store.save_cgm_reading(_reading(5, meal_time, glucose_mgdl=115, source="manual"))

        result = glucose_store.get_pre_meal_glucose(meal_time.isoformat())
        assert result["source"] == "manual"
        assert result["glucose_mgdl"] == 115

    def test_guess_source_rejected(self):
        with pytest.raises(ValueError):
            glucose_store.save_cgm_reading({
                "timestamp": "2026-09-05T12:00:00Z", "glucose_mgdl": 110,
                "trend": "flat", "source": "guess",
            })

    @pytest.mark.parametrize("bad_value", [19, 601, 95.0, "95"])
    def test_out_of_range_or_wrong_type_glucose_rejected(self, bad_value):
        with pytest.raises(ValueError):
            glucose_store.save_cgm_reading({
                "timestamp": "2026-09-05T12:00:00Z", "glucose_mgdl": bad_value,
                "trend": "flat", "source": "manual",
            })

    def test_unknown_trend_rejected(self):
        with pytest.raises(ValueError):
            glucose_store.save_cgm_reading({
                "timestamp": "2026-09-05T12:00:00Z", "glucose_mgdl": 110,
                "trend": "sideways", "source": "manual",
            })

    def test_rejection_appends_nothing_to_file(self):
        bad_readings = [
            {"timestamp": "2026-09-05T12:00:00Z", "glucose_mgdl": 19, "trend": "flat", "source": "manual"},
            {"timestamp": "2026-09-05T12:00:00Z", "glucose_mgdl": 110, "trend": "flat", "source": "guess"},
        ]
        for bad in bad_readings:
            with pytest.raises(ValueError):
                glucose_store.save_cgm_reading(bad)
        assert glucose_store.get_meal_logs() == []
        assert glucose_store._load(glucose_store._CGM_FILE) == []

    def test_naive_timestamp_read_as_utc(self):
        glucose_store.save_cgm_reading({
            "timestamp": "2026-09-05T12:00:00", "glucose_mgdl": 110,
            "trend": "flat", "source": "manual",
        })
        window = glucose_store.get_cgm_window(
            "2026-09-05T11:55:00+00:00", "2026-09-05T12:05:00+00:00"
        )
        assert len(window) == 1

    def test_offset_timestamp_converted_to_utc(self):
        # +08:00 noon == 04:00 UTC
        glucose_store.save_cgm_reading({
            "timestamp": "2026-09-05T12:00:00+08:00", "glucose_mgdl": 110,
            "trend": "flat", "source": "manual",
        })
        window = glucose_store.get_cgm_window(
            "2026-09-05T03:55:00+00:00", "2026-09-05T04:05:00+00:00"
        )
        assert len(window) == 1

    def test_window_boundaries_inclusive(self):
        glucose_store.save_cgm_reading({
            "timestamp": "2026-09-05T12:00:00+00:00", "glucose_mgdl": 110,
            "trend": "flat", "source": "manual",
        })
        window = glucose_store.get_cgm_window(
            "2026-09-05T12:00:00+00:00", "2026-09-05T12:00:00+00:00"
        )
        assert len(window) == 1
