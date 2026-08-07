import { apiFetchJson } from './client';
import type { GlucoseResponse } from './types';

export async function analyzeGlucose(mealId: string): Promise<GlucoseResponse> {
  return apiFetchJson(`/glucose/${mealId}`);
}

// No-CGM fallback (API-011): stores the reading server-side the same way a
// real CGM feed would; the caller re-fetches analyzeGlucose() afterward for
// the actual prediction — this does not return one itself.
export async function submitManualGlucose(mealId: string, glucoseMgdl: number): Promise<void> {
  await apiFetchJson(`/manual-glucose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meal_id: mealId, glucose_mgdl: glucoseMgdl }),
  });
}
