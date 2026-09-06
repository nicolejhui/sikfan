"""SC-21 ... SC-22 — learning a dish from confirmations (pipeline/feedback.py),
using the fake_store fixture instead of ChromaDB/CLIP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.feedback import FeedbackAction, record_feedback


class TestSC21TheSeedImageSurvivesTheFirstTwoConfirmations:
    """As a user confirming a dish SikFan already knows, I want it to get
    better at recognizing it - without one bad photo degrading it."""

    def test_confirmations_0_and_1_increment_count_only(self, fake_store, crop):
        fake_store.seed("mapo_tofu", confirmed_count=0)

        record_feedback(crop, "mapo_tofu", "mapo_tofu", 0.9, FeedbackAction.CONFIRM, fake_store)
        assert fake_store._col._metas["mapo_tofu"]["confirmed_count"] == 1
        assert fake_store.update_centroid_calls == []

        record_feedback(crop, "mapo_tofu", "mapo_tofu", 0.9, FeedbackAction.CONFIRM, fake_store)
        assert fake_store._col._metas["mapo_tofu"]["confirmed_count"] == 2
        assert fake_store.update_centroid_calls == []

    def test_third_confirmation_calls_update_centroid(self, fake_store, crop):
        fake_store.seed("mapo_tofu", confirmed_count=2)

        record_feedback(crop, "mapo_tofu", "mapo_tofu", 0.9, FeedbackAction.CONFIRM, fake_store)

        assert fake_store.update_centroid_calls == ["mapo_tofu"]


class TestSC22ConfirmationsAndCorrectionsDontLoseHistory:
    def test_confirm_with_mismatched_label_raises(self, fake_store, crop):
        fake_store.seed("mapo_tofu", confirmed_count=0)
        with pytest.raises(ValueError):
            record_feedback(crop, "mapo_tofu", "kung_pao_chicken", 0.9, FeedbackAction.CONFIRM, fake_store)

    def test_add_new_for_existing_dish_goes_through_gate_not_add_dish(self, fake_store, crop):
        fake_store.seed("mapo_tofu", confirmed_count=5)

        result = record_feedback(crop, "unknown", "mapo_tofu", 0.4, FeedbackAction.ADD_NEW, fake_store)

        assert fake_store.add_dish_calls == []  # history preserved, not reset
        assert result["confirmed_count"] == 6

    def test_add_new_for_truly_new_dish_calls_add_dish(self, fake_store, crop):
        result = record_feedback(crop, "unknown", "brand_new_dish", 0.4, FeedbackAction.ADD_NEW, fake_store)

        assert fake_store.add_dish_calls == ["brand_new_dish"]
        assert result["confirmed_count"] == 0

    def test_crop_save_failure_degrades_to_none_without_raising(self, fake_store, crop, tmp_path):
        fake_store.seed("mapo_tofu", confirmed_count=0)
        # dishes_dir points at a path that is a FILE, not a directory, so
        # Path.mkdir() inside _save_crop raises and is caught.
        blocked_dir = tmp_path / "blocked_dishes_dir"
        blocked_dir.write_text("not a directory")

        result = record_feedback(
            crop, "mapo_tofu", "mapo_tofu", 0.9, FeedbackAction.CONFIRM, fake_store,
            dishes_dir=str(blocked_dir),
        )

        assert result["crop_saved_path"] is None

    def test_log_write_failure_degrades_to_false_without_raising(self, fake_store, crop, tmp_path):
        fake_store.seed("mapo_tofu", confirmed_count=0)
        blocked_log_dir = tmp_path / "blocked_log_dir"
        blocked_log_dir.write_text("not a directory")
        log_path = str(blocked_log_dir / "correction_log.jsonl")

        result = record_feedback(
            crop, "mapo_tofu", "mapo_tofu", 0.9, FeedbackAction.CONFIRM, fake_store,
            dishes_dir=str(tmp_path / "dishes"), log_path=log_path,
        )

        assert result["logged"] is False
