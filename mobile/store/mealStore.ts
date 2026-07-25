import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import type { DishResult } from './types';

type Status = 'idle' | 'uploading' | 'analyzing' | 'done' | 'error';

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
};

export const useMealStore = create<MealState & MealActions>()(
  immer((set) => ({
    ...initial,
    startScan: (_imageUri: string) => set((s) => { s.status = 'uploading'; }),
    setResult: (result) => set((s) => { Object.assign(s, result); }),
    reset: () => set(() => ({ ...initial })),
  }))
);
