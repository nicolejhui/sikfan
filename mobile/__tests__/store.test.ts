jest.mock('../api/meals', () => ({
  submitMeal: jest.fn(() => new Promise(() => {})),
  pollMealStatus: jest.fn(),
}));

const mockAnalyzeGlucose = jest.fn();
const mockSubmitManualGlucose = jest.fn();
jest.mock('../api/glucose', () => ({
  analyzeGlucose: (...args: unknown[]) => mockAnalyzeGlucose(...args),
  submitManualGlucose: (...args: unknown[]) => mockSubmitManualGlucose(...args),
}));

import { useMealStore } from '../store/mealStore';
import { useGlucoseStore } from '../store/glucoseStore';
import { useHistoryStore } from '../store/historyStore';
import { ApiError } from '../api/client';
import type { GlucoseActuals, GlucosePrediction, LoggedMeal } from '../store/types';

beforeEach(() => {
  useMealStore.getState().reset();
  useGlucoseStore.getState().stopPolling();
  useGlucoseStore.getState().reset();
  useHistoryStore.getState().clearHistory();
  mockAnalyzeGlucose.mockReset();
  mockSubmitManualGlucose.mockReset();
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

// glucoseStore.fetchPrediction / submitManualGlucose (MOB-012)
const mockGlucoseResponse = {
  meal_id: 'meal_001',
  meal_timestamp: '2026-08-06T12:00:00',
  dishes: [],
  total_carbs_g: 40,
  pre_meal_glucose: 110,
  pre_meal_trend: 'flat',
  prediction: mockPrediction,
  actuals: null,
  retrain_triggered: false,
  image_url: '/meal-image/meal_001',
};

test('fetchPrediction sets ready + prediction on success', async () => {
  mockAnalyzeGlucose.mockResolvedValue(mockGlucoseResponse);
  await useGlucoseStore.getState().fetchPrediction('meal_001');
  const s = useGlucoseStore.getState();
  expect(s.predictionStatus).toBe('ready');
  expect(s.preMealGlucose).toBe(110);
  expect(s.prediction?.predicted_peak_bg).toBe(145);
});

test('fetchPrediction sets needs_manual_entry on no_pre_meal_glucose', async () => {
  mockAnalyzeGlucose.mockRejectedValue(new ApiError(422, 'no CGM', 'no_pre_meal_glucose'));
  await useGlucoseStore.getState().fetchPrediction('meal_001');
  expect(useGlucoseStore.getState().predictionStatus).toBe('needs_manual_entry');
});

test('fetchPrediction sets error on any other failure', async () => {
  mockAnalyzeGlucose.mockRejectedValue(new ApiError(503, 'model down', 'glucose_model_error'));
  await useGlucoseStore.getState().fetchPrediction('meal_001');
  expect(useGlucoseStore.getState().predictionStatus).toBe('error');
});

test('submitManualGlucose posts the value then re-fetches a real prediction', async () => {
  mockSubmitManualGlucose.mockResolvedValue(undefined);
  mockAnalyzeGlucose.mockResolvedValue(mockGlucoseResponse);

  await useGlucoseStore.getState().submitManualGlucose('meal_001', 118);

  expect(mockSubmitManualGlucose).toHaveBeenCalledWith('meal_001', 118);
  expect(mockAnalyzeGlucose).toHaveBeenCalledWith('meal_001');
  expect(useGlucoseStore.getState().predictionStatus).toBe('ready');
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

// glucoseStore polling (MOB-009)
const mockActuals: GlucoseActuals = {
  curve: [{ minutes: 5, glucose_mgdl: 112, timestamp: '2026-07-25T12:05:00Z' }],
  actual_peak_bg: 150,
  time_to_peak_minutes: 45,
  tir_ratio: 0.9,
  mard: 5.1,
  chart_path: '',
};

describe('glucoseStore polling', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    useGlucoseStore.getState().stopPolling();
    jest.useRealTimers();
  });

  test('startPolling sets pollingActive and polls every 5 minutes', async () => {
    mockAnalyzeGlucose.mockResolvedValue({ actuals: null });
    useGlucoseStore.getState().startPolling('meal_001');
    expect(useGlucoseStore.getState().pollingActive).toBe(true);

    await jest.advanceTimersByTimeAsync(300_000);
    expect(mockAnalyzeGlucose).toHaveBeenCalledWith('meal_001');
    expect(mockAnalyzeGlucose).toHaveBeenCalledTimes(1);
    expect(useGlucoseStore.getState().pollCount).toBe(1);
  });

  test('appends and dedupes readings when actuals arrive', async () => {
    mockAnalyzeGlucose.mockResolvedValue({ actuals: mockActuals });
    useGlucoseStore.getState().startPolling('meal_001');

    await jest.advanceTimersByTimeAsync(300_000);
    await jest.advanceTimersByTimeAsync(300_000);

    const s = useGlucoseStore.getState();
    expect(s.readings).toHaveLength(1);
    expect(s.actuals?.actual_peak_bg).toBe(150);
  });

  test('stopPolling clears the interval immediately', async () => {
    mockAnalyzeGlucose.mockResolvedValue({ actuals: null });
    useGlucoseStore.getState().startPolling('meal_001');
    useGlucoseStore.getState().stopPolling();

    await jest.advanceTimersByTimeAsync(300_000);
    expect(mockAnalyzeGlucose).not.toHaveBeenCalled();
    expect(useGlucoseStore.getState().pollingActive).toBe(false);
  });

  test('reset also clears a live polling interval (no orphaned setInterval)', async () => {
    mockAnalyzeGlucose.mockResolvedValue({ actuals: null });
    useGlucoseStore.getState().startPolling('meal_001');
    useGlucoseStore.getState().reset();

    await jest.advanceTimersByTimeAsync(300_000);
    expect(mockAnalyzeGlucose).not.toHaveBeenCalled();
    expect(useGlucoseStore.getState().pollingHandle).toBeNull();
  });

  test('polling stops automatically after 36 intervals (180 minutes)', async () => {
    mockAnalyzeGlucose.mockResolvedValue({ actuals: null });
    useGlucoseStore.getState().startPolling('meal_001');

    await jest.advanceTimersByTimeAsync(300_000 * 36);
    expect(useGlucoseStore.getState().pollingActive).toBe(false);
    expect(mockAnalyzeGlucose).toHaveBeenCalledTimes(36);

    await jest.advanceTimersByTimeAsync(300_000);
    expect(mockAnalyzeGlucose).toHaveBeenCalledTimes(36);
  });
});

describe('historyStore.addMeal polling integration', () => {
  afterEach(() => {
    useGlucoseStore.getState().stopPolling();
  });

  test('does not start polling in MVP mode', () => {
    useHistoryStore.getState().addMeal(meal);
    expect(useGlucoseStore.getState().pollingActive).toBe(false);
  });
});
