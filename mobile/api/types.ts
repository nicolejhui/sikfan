import type {
  DishResult,
  MealResult,
  GlucoseResponse,
  GlucoseDishEntry,
  GlucosePrediction,
  GlucoseActuals,
} from '../store/types';

export type { MealResult, GlucoseResponse, GlucoseDishEntry, GlucosePrediction, GlucoseActuals };

export type JobStatus = 'pending' | 'processing' | 'complete' | 'error';

export interface JobStatusResponse {
  meal_id: string;
  status: JobStatus;
  result: MealResult | null;
  error: string | null;
}

export interface LogMealResponse {
  meal_id: string;
  logged: boolean;
  meal_timestamp: string;
}

// Matches ConfirmDishRequest in api.py: corrected_label is omitted (not sent
// as null) for CONFIRM, which is the frozen convention per
// data/MOBILE_DECISIONS.md Decision 4.
export type ConfirmDishRequest =
  | { meal_id: string; crop_id: string; action: 'CONFIRM' }
  | { meal_id: string; crop_id: string; action: 'CORRECT' | 'ADD_NEW'; corrected_label: string };

export interface ConfirmDishResponse {
  crop_id: string;
  action: 'CONFIRM' | 'CORRECT' | 'ADD_NEW';
  updated_label: string;
  chromadb_updated: boolean;
  macros_changed: boolean;  // true if CORRECT/ADD_NEW's carbs_g differs from the pre-correction value
}

// MOB-016 / API-013 — macro & ingredient correction. Matches api.py's
// CorrectMacrosRequest/Response, CorrectIngredientsRequest/Response,
// IngredientCandidatesResponse, ResetCorrectionsRequest/Response exactly.

export type CorrectionDirection = 'too_high' | 'too_low' | 'looks_right';
export type CorrectionReason = 'portion' | 'broth' | 'hidden' | 'leftover';
export type CorrectionMagnitude = 'little' | 'lot';

export interface CorrectMacrosRequest {
  meal_id: string;
  crop_id: string;
  direction: CorrectionDirection;
  reason?: CorrectionReason;
  magnitude?: CorrectionMagnitude;
}

export interface CorrectMacrosResponse {
  crop_id: string;
  direction: CorrectionDirection;
  old_portion_g: number | null;
  new_portion_g: number | null;
  old_carbs_g: number;
  new_carbs_g: number;
  multiplier_persisted: number | null;
  prior_state: string;
  dish: DishResult;
}

export type IngredientEditAction = 'remove' | 'swap' | 'add';

export interface IngredientEdit {
  action: IngredientEditAction;
  component_name: string;
  replacement_name?: string;  // required for "swap"
  grams?: number;             // required for "add" — positive only
}

export interface CorrectIngredientsRequest {
  meal_id: string;
  crop_id: string;
  edits: IngredientEdit[];
}

export interface CorrectIngredientsResponse {
  crop_id: string;
  old_portion_g: number;
  new_portion_g: number;
  old_carbs_g: number;
  new_carbs_g: number;
  dish: DishResult;
}

export interface IngredientCandidate {
  name: string;
  grams_hint: number | null;
  per_100g: {
    calories: number;
    carbs_g: number;
    fiber_g: number;
    protein_g: number;
    fat_g: number;
  };
  carbs_g: number;
}

export interface IngredientCandidates {
  alts: Record<string, IngredientCandidate[]>;
  addable: IngredientCandidate[];
}

export interface ResetCorrectionsRequest {
  meal_id: string;
  crop_id: string;
}

export interface ResetCorrectionsResponse {
  crop_id: string;
  decomposition_reverted: boolean;
  prior_retained: boolean;
  dish: DishResult;
}
