// analyze_meal response (FOOD-015 schema)
export interface DishResult {
  crop_id: string;
  name: string;
  confidence: number;
  status: string;   // "CONFIDENT" | "UNCERTAIN" | "UNKNOWN"
  carbs_g: number;
  protein_g: number;
  fat_g: number;
  calories: number;
  portion_g: number | null;
  portion_bucket: string | null;  // "small" | "medium" | "large"; null if not estimated
  needs_macro_entry: boolean;  // true if USDA had no match — carbs/macros above are 0, not verified-zero
}

export interface MealResult {
  meal_id: string;
  dishes: DishResult[];
  total_carbs_g: number;
  image_url: string | null;
}

// analyze_glucose response (GLUC-009 schema)
export interface CurvePoint {
  minutes: number;
  predicted_bg: number;
  confidence_lower: number;
  confidence_upper: number;
}

export interface GlucosePrediction {
  curve: CurvePoint[];
  predicted_peak_bg: number;
  predicted_time_to_peak_minutes: number;
  model_confidence: 'high' | 'medium' | 'low';
  outcome: {
    label: 'spike' | 'steady' | 'drop';
    confidence: 'high' | 'medium' | 'low';
    predicted_peak_bg: number;
    delta_from_baseline: number;
  };
}

export interface ActualReading {
  minutes: number;
  glucose_mgdl: number;
  timestamp: string;
}

export interface GlucoseActuals {
  curve: ActualReading[];
  actual_peak_bg: number;
  time_to_peak_minutes: number;
  tir_ratio: number;
  mard: number;
  chart_path: string;
}

export interface GlucoseDishEntry {
  name: string;
  carbs_g: number;
}

export interface GlucoseResponse {
  meal_id: string;
  meal_timestamp: string;
  dishes: GlucoseDishEntry[];
  total_carbs_g: number;
  pre_meal_glucose: number;
  pre_meal_trend: string;
  prediction: GlucosePrediction;
  actuals: GlucoseActuals | null;
  retrain_triggered: boolean;
}

// historyStore shape
export interface LoggedMeal {
  meal_id: string;
  dishes: DishResult[];
  total_carbs_g: number;
  image_url: string | null;
  meal_timestamp: string;
  verdict: 'spike' | 'steady' | 'drop' | null;
}

// Display helpers
export type Verdict = 'spike' | 'steady' | 'drop';

export function verdictWord(verdict: Verdict | null | undefined): string {
  switch (verdict) {
    case 'spike':  return 'Spikes';
    case 'drop':   return 'Drops';
    case 'steady': return 'Stabilizes';
    default:       return '—';
  }
}

export function formatPortion(bucket: string | null, grams: number | null): string {
  if (!grams) return '';
  const approx = `~${Math.round(grams)} g`;
  return bucket ? `${approx} · ${bucket}` : approx;
}
