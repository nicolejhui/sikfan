import { apiFetchJson } from './client';
import type { GlucoseResponse } from './types';

export async function analyzeGlucose(mealId: string): Promise<GlucoseResponse> {
  return apiFetchJson(`/glucose/${mealId}`);
}
