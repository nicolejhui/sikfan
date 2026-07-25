export const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
export const API_KEY = process.env.EXPO_PUBLIC_API_KEY;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

interface RequestOptions {
  method?: string;
  body?: BodyInit;
  headers?: Record<string, string>;
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    return data?.detail ?? data?.message ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { ...options.headers };
  if (API_KEY) headers['X-API-Key'] = API_KEY;

  const response = await fetch(`${BASE_URL}${path}`, {
    method: options.method ?? 'GET',
    body: options.body,
    headers,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }

  return response;
}

export async function apiFetchJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const response = await apiFetch(path, options);
  return (await response.json()) as T;
}
