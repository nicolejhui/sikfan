import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { LoggedMeal } from './types';
import { useGlucoseStore } from './glucoseStore';
import { MVP_MODE } from '../constants/config';

interface HistoryState {
  meals: LoggedMeal[];
}

interface HistoryActions {
  addMeal: (meal: LoggedMeal) => void;
  updateVerdict: (mealId: string, verdict: LoggedMeal['verdict']) => void;
  updateDishName: (mealId: string, cropId: string, name: string) => void;
  clearHistory: () => void;
}

export const useHistoryStore = create<HistoryState & HistoryActions>()(
  immer((set) => ({
    meals: [],

    addMeal: (meal) => {
      set((s) => { s.meals.unshift(meal); });
      if (!MVP_MODE) {
        useGlucoseStore.getState().startPolling(meal.meal_id);
      }
    },

    updateVerdict: (mealId, verdict) =>
      set((s) => {
        const m = s.meals.find((x) => x.meal_id === mealId);
        if (m) m.verdict = verdict;
      }),

    updateDishName: (mealId, cropId, name) =>
      set((s) => {
        const meal = s.meals.find((m) => m.meal_id === mealId);
        const dish = meal?.dishes.find((d) => d.crop_id === cropId);
        if (dish) dish.name = name;
      }),

    clearHistory: () =>
      set((s) => { s.meals = []; }),
  }))
);
