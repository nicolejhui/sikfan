import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { ActualReading, GlucoseActuals, GlucosePrediction, Verdict } from './types';

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

    startPolling: (_mealId: string) => {
      // MOB-009 wires up MVP_MODE guard and real interval
      set((s) => { s.pollingActive = true; });
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
        s.pollCount += 1;
      }),

    reset: () => set(() => ({ ...initial })),
  }))
);
