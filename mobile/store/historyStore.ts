import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { LoggedMeal } from './types';

interface HistoryState {
  meals: LoggedMeal[];
}

interface HistoryActions {
  addMeal: (meal: LoggedMeal) => void;
  updateVerdict: (mealId: string, verdict: LoggedMeal['verdict']) => void;
  clearHistory: () => void;
}

export const useHistoryStore = create<HistoryState & HistoryActions>()(
  immer((set) => ({
    meals: [],

    addMeal: (meal) =>
      set((s) => { s.meals.unshift(meal); }),

    updateVerdict: (mealId, verdict) =>
      set((s) => {
        const m = s.meals.find((x) => x.meal_id === mealId);
        if (m) m.verdict = verdict;
      }),

    clearHistory: () =>
      set((s) => { s.meals = []; }),
  }))
);
