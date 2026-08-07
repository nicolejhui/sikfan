import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { ActualReading, GlucoseActuals, GlucosePrediction, Verdict } from './types';
import { analyzeGlucose, submitManualGlucose as apiSubmitManualGlucose } from '../api/glucose';
import { ApiError } from '../api/client';

const POLL_INTERVAL_MS = 300_000;
const MAX_POLLS = 36; // 36 * 5 min = 180 min

export type PredictionStatus = 'idle' | 'loading' | 'ready' | 'needs_manual_entry' | 'error';

interface GlucoseState {
  preMealGlucose: number | null;
  preMealTrend: string | null;
  prediction: GlucosePrediction | null;
  verdict: Verdict | null;
  readings: ActualReading[];
  actuals: GlucoseActuals | null;
  pollCount: number;
  pollingHandle: ReturnType<typeof setInterval> | null;
  pollingActive: boolean;
  predictionStatus: PredictionStatus;
}

interface GlucoseActions {
  setPrediction: (
    prediction: GlucosePrediction,
    preMealGlucose: number,
    preMealTrend: string
  ) => void;
  startPolling: (mealId: string) => void;
  stopPolling: () => void;
  appendReading: (readings: ActualReading[], actuals: GlucoseActuals) => void;
  // MOB-012: fetches GET /glucose/{meal_id} and sets predictionStatus from
  // the result — 'ready' on success, 'needs_manual_entry' specifically on a
  // no_pre_meal_glucose ApiError (only that error offers manual entry; any
  // other failure is a real error a BG value won't fix), 'error' otherwise.
  fetchPrediction: (mealId: string) => Promise<void>;
  // No client-side curve math (see docs/MOBILE-TICKETS.md MOB-012 revision
  // note) — submits the value via API-011, then re-fetches a real prediction
  // through the same fetchPrediction path a successful automatic fetch uses.
  submitManualGlucose: (mealId: string, glucoseMgdl: number) => Promise<void>;
  reset: () => void;
}

const initial: GlucoseState = {
  preMealGlucose: null,
  preMealTrend: null,
  prediction: null,
  verdict: null,
  readings: [],
  actuals: null,
  pollCount: 0,
  pollingHandle: null,
  pollingActive: false,
  predictionStatus: 'idle',
};

export const useGlucoseStore = create<GlucoseState & GlucoseActions>()(
  immer((set, get) => ({
    ...initial,

    setPrediction: (prediction, preMealGlucose, preMealTrend) =>
      set((s) => {
        s.prediction = prediction;
        s.verdict = prediction.outcome.label;
        s.preMealGlucose = preMealGlucose;
        s.preMealTrend = preMealTrend;
      }),

    startPolling: (mealId: string) => {
      const existing = get().pollingHandle;
      if (existing !== null) clearInterval(existing);

      set((s) => { s.pollingActive = true; s.pollCount = 0; });

      const tick = async () => {
        try {
          const res = await analyzeGlucose(mealId);
          if (res.actuals) {
            get().appendReading(res.actuals.curve, res.actuals);
          }
        } catch {
          // a single failed poll shouldn't stop tracking
        }
        set((s) => { s.pollCount += 1; });
        if (get().pollCount >= MAX_POLLS) {
          get().stopPolling();
        }
      };

      const handle = setInterval(tick, POLL_INTERVAL_MS);
      set((s) => { s.pollingHandle = handle; });
    },

    stopPolling: () => {
      const handle = get().pollingHandle;
      if (handle !== null) clearInterval(handle);
      set((s) => { s.pollingHandle = null; s.pollingActive = false; });
    },

    appendReading: (newReadings, actuals) =>
      set((s) => {
        for (const r of newReadings) {
          if (!s.readings.some((x) => x.minutes === r.minutes)) {
            s.readings.push(r);
          }
        }
        s.actuals = actuals;
      }),

    fetchPrediction: async (mealId: string) => {
      set((s) => { s.predictionStatus = 'loading'; });
      try {
        const res = await analyzeGlucose(mealId);
        get().setPrediction(res.prediction, res.pre_meal_glucose, res.pre_meal_trend);
        set((s) => { s.predictionStatus = 'ready'; });
      } catch (err) {
        if (err instanceof ApiError && err.code === 'no_pre_meal_glucose') {
          set((s) => { s.predictionStatus = 'needs_manual_entry'; });
        } else {
          set((s) => { s.predictionStatus = 'error'; });
        }
      }
    },

    submitManualGlucose: async (mealId: string, glucoseMgdl: number) => {
      await apiSubmitManualGlucose(mealId, glucoseMgdl);
      await get().fetchPrediction(mealId);
    },

    reset: () => {
      // Clear any live MOB-009 polling interval before wiping state — a
      // plain state replacement would drop the only reference to
      // pollingHandle, leaking an orphaned setInterval that keeps firing
      // (and can never be cancelled again) for whatever meal it was
      // tracking. Not previously reachable: nothing called reset() from a
      // live code path before MOB-012 wired mealStore.startScan/reset() to
      // it — only exercised in unit tests, where a leaked interval doesn't
      // outlive the test.
      const handle = get().pollingHandle;
      if (handle !== null) clearInterval(handle);
      set(() => ({ ...initial }));
    },
  }))
);
