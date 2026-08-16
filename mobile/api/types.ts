import type {
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
}
