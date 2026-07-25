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
