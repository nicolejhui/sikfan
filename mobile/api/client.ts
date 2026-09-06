export const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
export const API_KEY = process.env.EXPO_PUBLIC_API_KEY;

export class ApiError extends Error {
  status: number;
  /** Machine-readable error code, e.g. "no_pre_meal_glucose" — undefined if
   * the response didn't use this backend's {detail: {code, message}} shape. */
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions {
  method?: string;
  body?: BodyInit;
  headers?: Record<string, string>;
}

const REQUEST_TIMEOUT_MS = 15000;

interface ExtractedError {
  message: string;
  code?: string;
}

async function extractError(response: Response): Promise<ExtractedError> {
  try {
    const data = await response.json();
    // This backend's error bodies are {"detail": {"code": "...", "message": "..."}}
    // — detail is an object, not a flat string. Unwrap it; fall back to a
    // flat-string detail (or data.message) for resilience against other shapes.
    if (data?.detail && typeof data.detail === 'object') {
      return { message: data.detail.message ?? response.statusText, code: data.detail.code };
    }
    return { message: data?.detail ?? data?.message ?? response.statusText };
  } catch {
    return { message: response.statusText };
  }
}

export async function apiFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { ...options.headers };
  if (API_KEY) headers['X-API-Key'] = API_KEY;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      body: options.body,
      headers,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new ApiError(0, 'Request timed out — try again.');
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    const { message, code } = await extractError(response);
    throw new ApiError(response.status, message, code);
  }

  return response;
}

export async function apiFetchJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const response = await apiFetch(path, options);
  return (await response.json()) as T;
}
