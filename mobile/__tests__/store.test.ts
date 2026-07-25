jest.mock('../api/meals', () => ({
  submitMeal: jest.fn(() => new Promise(() => {})),
  pollMealStatus: jest.fn(),
}));

import { useMealStore } from '../store/mealStore';
import { useGlucoseStore } from '../store/glucoseStore';
import { useHistoryStore } from '../store/historyStore';
import type { GlucosePrediction, LoggedMeal } from '../store/types';

beforeEach(() => {
  useMealStore.getState().reset();
  useGlucoseStore.getState().reset();
  useHistoryStore.getState().clearHistory();
});

// mealStore
test('startScan sets status to uploading', () => {
  useMealStore.getState().startScan('file://test.jpg');
  expect(useMealStore.getState().status).toBe('uploading');
});

test('reset returns mealStore to idle', () => {
  useMealStore.getState().startScan('file://test.jpg');
  useMealStore.getState().reset();
  expect(useMealStore.getState().status).toBe('idle');
});

test('setResult stores portion fields (null when not estimated)', () => {
  useMealStore.getState().setResult({ portion: 480, portionBucket: 'large' });
  expect(useMealStore.getState().portion).toBe(480);
  expect(useMealStore.getState().portionBucket).toBe('large');

  useMealStore.getState().reset();
  expect(useMealStore.getState().portion).toBeNull();
  expect(useMealStore.getState().portionBucket).toBeNull();
});

// glucoseStore
const mockPrediction: GlucosePrediction = {
  curve: [{ minutes: 0, predicted_bg: 110, confidence_lower: 100, confidence_upper: 120 }],
  predicted_peak_bg: 145,
  predicted_time_to_peak_minutes: 45,
  model_confidence: 'high',
  outcome: { label: 'spike', confidence: 'high', predicted_peak_bg: 145, delta_from_baseline: 35 },
};

test('setPrediction stores prediction and verdict', () => {
  useGlucoseStore.getState().setPrediction(mockPrediction, 110, 'flat');
  const s = useGlucoseStore.getState();
  expect(s.verdict).toBe('spike');
  expect(s.preMealGlucose).toBe(110);
  expect(s.prediction?.predicted_peak_bg).toBe(145);
});

test('reset clears glucoseStore', () => {
  useGlucoseStore.getState().setPrediction(mockPrediction, 110, 'flat');
  useGlucoseStore.getState().reset();
  expect(useGlucoseStore.getState().verdict).toBeNull();
});

// historyStore
const meal: LoggedMeal = {
  meal_id: 'meal_001',
  dishes: [],
  total_carbs_g: 65,
  image_url: null,
  meal_timestamp: '2026-07-25T12:00:00Z',
  verdict: null,
};

test('addMeal prepends to history', () => {
  useHistoryStore.getState().addMeal(meal);
  useHistoryStore.getState().addMeal({ ...meal, meal_id: 'meal_002' });
  const meals = useHistoryStore.getState().meals;
  expect(meals).toHaveLength(2);
  expect(meals[0].meal_id).toBe('meal_002');
});

test('updateVerdict sets verdict on correct meal', () => {
  useHistoryStore.getState().addMeal(meal);
  useHistoryStore.getState().updateVerdict('meal_001', 'steady');
  expect(useHistoryStore.getState().meals[0].verdict).toBe('steady');
});

test('updateVerdict ignores unknown meal_id', () => {
  useHistoryStore.getState().addMeal(meal);
  useHistoryStore.getState().updateVerdict('does_not_exist', 'spike');
  expect(useHistoryStore.getState().meals[0].verdict).toBeNull();
});

test('clearHistory empties the list', () => {
  useHistoryStore.getState().addMeal(meal);
  useHistoryStore.getState().clearHistory();
  expect(useHistoryStore.getState().meals).toHaveLength(0);
});
