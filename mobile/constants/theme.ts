export type Verdict = 'spike' | 'steady' | 'drop';
export type PaletteName = 'sunrise' | 'matcha' | 'mist';

export interface Palette {
  name: PaletteName;
  /** Brand primary (buttons, active tabs, accents) */
  primary: string;
  /** Card / sheet backgrounds */
  surface: string;
  /** App background */
  background: string;
  /** Primary text */
  text: string;
  /** Secondary / muted text */
  textMuted: string;
  /** Verdict: spike — high blood sugar warning */
  warn: string;
  /** Verdict: steady — in-range / good */
  good: string;
  /** Verdict: drop — low blood sugar */
  low: string;
  /** Dividers, input borders */
  border: string;
}

// ---------------------------------------------------------------------------
// Palettes
// ---------------------------------------------------------------------------

export const sunrise: Palette = {
  name: 'sunrise',
  primary: '#F97316',     // orange-500
  surface: '#1C1511',
  background: '#0F0A06',
  text: '#FAF7F4',
  textMuted: '#8A7B6E',
  warn: '#EF4444',        // red-500
  good: '#22C55E',        // green-500
  low: '#3B82F6',         // blue-500
  border: '#2C231A',
};

export const matcha: Palette = {
  name: 'matcha',
  primary: '#4ADE80',     // green-400
  surface: '#0D1F10',
  background: '#050F07',
  text: '#F0FAF2',
  textMuted: '#6B8F71',
  warn: '#F97316',        // orange-500
  good: '#4ADE80',        // green-400
  low: '#60A5FA',         // blue-400
  border: '#1A3020',
};

export const mist: Palette = {
  name: 'mist',
  primary: '#60A5FA',     // blue-400
  surface: '#111827',
  background: '#07090F',
  text: '#F8FAFC',
  textMuted: '#6B7280',
  warn: '#F43F5E',        // rose-500
  good: '#34D399',        // emerald-400
  low: '#818CF8',         // indigo-400
  border: '#1F2937',
};

export const palettes: Record<PaletteName, Palette> = { sunrise, matcha, mist };

/** Default palette for the app */
export const defaultPalette: Palette = sunrise;

// ---------------------------------------------------------------------------
// Verdict helper
// ---------------------------------------------------------------------------

/**
 * Returns the palette colour that represents the given glucose verdict.
 *
 * spike  → warn  (red/orange)
 * steady → good  (green)
 * drop   → low   (blue)
 */
export function verdictColor(verdict: Verdict | null | undefined, palette: Palette = defaultPalette): string {
  switch (verdict) {
    case 'spike':  return palette.warn;
    case 'steady': return palette.good;
    case 'drop':   return palette.low;
    default:       return palette.textMuted;
  }
}

// ---------------------------------------------------------------------------
// Spacing & Typography scale
// ---------------------------------------------------------------------------

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
} as const;

export const radius = {
  sm: 8,
  md: 14,
  lg: 18,
  xl: 26,
  full: 999,
} as const;

export const fontSize = {
  xs: 11,
  sm: 13,
  md: 15,
  lg: 17,
  xl: 20,
  xxl: 24,
  display: 32,
  hero: 50,
} as const;

export const fontWeight = {
  regular: '400' as const,
  medium: '500' as const,
  semibold: '600' as const,
  bold: '700' as const,
};
