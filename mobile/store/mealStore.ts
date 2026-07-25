import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { DishResult, MealResult } from './types';
import { submitMeal, pollMealStatus } from '../api/meals';

type Status = 'idle' | 'uploading' | 'analyzing' | 'done' | 'error';

const POLL_INTERVAL_MS = 2000;

interface MealState {
  status: Status;
  mealId: string | null;
  mealTimestamp: string | null;
  dishes: DishResult[];
  dishName: string | null;
  confidence: number | null;
  portion: number | null;
  portionBucket: string | null;
  macros: { carbs_g: number; protein_g: number; fat_g: number; calories: number } | null;
  error: string | null;
}

interface MealActions {
  startScan: (imageUri: string) => void;
  setResult: (result: Partial<MealState>) => void;
  reset: () => void;
}

const initial: MealState = {
  status: 'idle',
  mealId: null,
  mealTimestamp: null,
  dishes: [],
  dishName: null,
  confidence: null,
  portion: null,
  portionBucket: null,
  macros: null,
  error: null,
};

type StoreApi = { getState: () => MealState & MealActions };

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function applyResult(result: MealResult, set: (fn: (s: MealState) => void) => void) {
  const primary = result.dishes[0] as DishResult | undefined;
  const macros = result.dishes.reduce(
    (acc, d) => ({
      carbs_g: acc.carbs_g + d.carbs_g,
      protein_g: acc.protein_g + d.protein_g,
      fat_g: acc.fat_g + d.fat_g,
      calories: acc.calories + d.calories,
    }),
    { carbs_g: 0, protein_g: 0, fat_g: 0, calories: 0 }
  );

  set((s) => {
    s.status = 'done';
    s.dishes = result.dishes;
    s.dishName = primary?.name ?? null;
    s.confidence = primary?.confidence ?? null;
    s.portion = primary?.portion_g ?? null;
    s.portionBucket = primary?.portion_bucket ?? null;
    s.macros = macros;
  });
}

async function runScan(
  imageUri: string,
  set: (fn: (s: MealState) => void) => void,
  get: StoreApi['getState']
) {
  try {
    const { meal_id } = await submitMeal(imageUri);
    set((s) => { s.mealId = meal_id; s.status = 'analyzing'; });

    while (get().status === 'analyzing') {
      const job = await pollMealStatus(meal_id);
      if (job.status === 'complete' && job.result) {
        applyResult(job.result, set);
        return;
      }
      if (job.status === 'error') {
        set((s) => { s.status = 'error'; s.error = job.error ?? 'Analysis failed.'; });
        return;
      }
      await sleep(POLL_INTERVAL_MS);
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Analysis failed.';
    set((s) => { s.status = 'error'; s.error = message; });
  }
}

export const useMealStore = create<MealState & MealActions>()(
  immer((set, get) => ({
    ...initial,
    startScan: (imageUri: string) => {
      set((s) => { Object.assign(s, initial); s.status = 'uploading'; });
      void runScan(imageUri, set, get);
    },
    setResult: (result) => set((s) => { Object.assign(s, result); }),
    reset: () => set(() => ({ ...initial })),
  }))
);
