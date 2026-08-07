jest.mock('expo-file-system', () => ({
  cacheDirectory: '/tmp/',
  downloadAsync: jest.fn(),
}));

const downloadAsync = require('expo-file-system').downloadAsync as jest.Mock;

let ApiError: typeof import('../api/client').ApiError;
let submitMeal: typeof import('../api/meals').submitMeal;
let pollMealStatus: typeof import('../api/meals').pollMealStatus;
let logMeal: typeof import('../api/meals').logMeal;
let getMealImage: typeof import('../api/meals').getMealImage;
let analyzeGlucose: typeof import('../api/glucose').analyzeGlucose;

beforeAll(() => {
  process.env.EXPO_PUBLIC_API_URL = 'https://api.example.com';
  process.env.EXPO_PUBLIC_API_KEY = 'test-key';

  // eslint-disable-next-line @typescript-eslint/no-var-requires
  ApiError = require('../api/client').ApiError;
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  ({ submitMeal, pollMealStatus, logMeal, getMealImage } = require('../api/meals'));
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  ({ analyzeGlucose } = require('../api/glucose'));
});

beforeEach(() => {
  global.fetch = jest.fn();
  downloadAsync.mockReset();
});

function mockJsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'error',
    json: async () => body,
  } as Response;
}

test('submitMeal POSTs multipart form data to /analyze-meal with the API key header', async () => {
  (global.fetch as jest.Mock).mockResolvedValue(
    mockJsonResponse(200, { meal_id: 'meal_1', status: 'pending' })
  );

  const result = await submitMeal('file://photo.jpg');

  expect(result).toEqual({ meal_id: 'meal_1', status: 'pending' });
  const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('https://api.example.com/analyze-meal');
  expect(options.method).toBe('POST');
  expect(options.headers['X-API-Key']).toBe('test-key');
  expect(options.body).toBeInstanceOf(FormData);
});

test('pollMealStatus GETs /meal-status/{meal_id}', async () => {
  (global.fetch as jest.Mock).mockResolvedValue(
    mockJsonResponse(200, { meal_id: 'meal_1', status: 'complete', result: null, error: null })
  );

  await pollMealStatus('meal_1');

  const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('https://api.example.com/meal-status/meal_1');
  expect(options.method).toBe('GET');
});

test('logMeal POSTs meal_id and confirmed_dishes as JSON to /log-meal', async () => {
  (global.fetch as jest.Mock).mockResolvedValue(
    mockJsonResponse(200, { meal_id: 'meal_1', logged: true, meal_timestamp: '2026-07-25T12:00:00Z' })
  );

  await logMeal('meal_1', ['braised_beef_noodle']);

  const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('https://api.example.com/log-meal');
  expect(options.method).toBe('POST');
  expect(JSON.parse(options.body)).toEqual({
    meal_id: 'meal_1',
    confirmed_dishes: ['braised_beef_noodle'],
  });
});

test('analyzeGlucose GETs /glucose/{meal_id}', async () => {
  (global.fetch as jest.Mock).mockResolvedValue(mockJsonResponse(200, { meal_id: 'meal_1' }));

  await analyzeGlucose('meal_1');

  const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('https://api.example.com/glucose/meal_1');
  expect(options.method).toBe('GET');
});

test('getMealImage downloads with the API key header and returns the local file URI', async () => {
  downloadAsync.mockResolvedValue({ uri: 'file:///tmp/meal-meal_1.jpg', status: 200 });

  const uri = await getMealImage('meal_1');

  expect(uri).toBe('file:///tmp/meal-meal_1.jpg');
  const [url, targetPath, options] = downloadAsync.mock.calls[0];
  expect(url).toBe('https://api.example.com/meal-image/meal_1');
  expect(targetPath).toBe('/tmp/meal-meal_1.jpg');
  expect(options.headers['X-API-Key']).toBe('test-key');
});

test.each([401, 404, 422])('throws ApiError on a %i response', async (status) => {
  (global.fetch as jest.Mock).mockResolvedValue(mockJsonResponse(status, { detail: 'nope' }));

  await expect(pollMealStatus('meal_1')).rejects.toBeInstanceOf(ApiError);
  await expect(pollMealStatus('meal_1')).rejects.toMatchObject({ status, message: 'nope' });
});

test('ApiError unwraps this backend\'s {detail: {code, message}} error shape', async () => {
  (global.fetch as jest.Mock).mockResolvedValue(
    mockJsonResponse(422, {
      detail: { code: 'no_pre_meal_glucose', message: 'No CGM reading found near this meal time.' },
    })
  );

  await expect(analyzeGlucose('meal_1')).rejects.toMatchObject({
    status: 422,
    code: 'no_pre_meal_glucose',
    message: 'No CGM reading found near this meal time.',
  });
});
