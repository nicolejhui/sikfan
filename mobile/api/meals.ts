import { File, Paths } from 'expo-file-system';
import { apiFetchJson, ApiError, BASE_URL, API_KEY } from './client';
import type { ConfirmDishRequest, ConfirmDishResponse, JobStatusResponse, LogMealResponse } from './types';

export async function submitMeal(imageUri: string): Promise<{ meal_id: string; status: string }> {
  const formData = new FormData();
  formData.append('file', new File(imageUri) as unknown as Blob, 'meal.jpg');

  return apiFetchJson('/analyze-meal', { method: 'POST', body: formData });
}

export async function pollMealStatus(mealId: string): Promise<JobStatusResponse> {
  return apiFetchJson(`/meal-status/${mealId}`);
}

export async function logMeal(
  mealId: string,
  confirmedDishes: string[]
): Promise<LogMealResponse> {
  return apiFetchJson('/log-meal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meal_id: mealId, confirmed_dishes: confirmedDishes }),
  });
}

export async function confirmDish(body: ConfirmDishRequest): Promise<ConfirmDishResponse> {
  return apiFetchJson('/confirm-dish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function getMealImage(mealId: string): Promise<string> {
  const headers: Record<string, string> = {};
  if (API_KEY) headers['X-API-Key'] = API_KEY;

  const destination = new File(Paths.cache, `meal-${mealId}.jpg`);

  try {
    const file = await File.downloadFileAsync(`${BASE_URL}/meal-image/${mealId}`, destination, {
      headers,
      idempotent: true,
    });
    return file.uri;
  } catch (err) {
    const message = err instanceof Error ? err.message : `Failed to fetch meal image for ${mealId}`;
    throw new ApiError(0, message);
  }
}
