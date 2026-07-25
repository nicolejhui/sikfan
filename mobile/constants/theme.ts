// Design tokens ported from the SikFan design project's theme.jsx —
// a soft, calm pastel system for a Type-1-diabetic, CGM-wearing audience.
// Keep field names in sync with theme.jsx so future syncs stay a diff, not a rewrite.

export type Verdict = 'spike' | 'steady' | 'drop';
export type PaletteName = 'sunrise' | 'matcha' | 'mist';

export interface VerdictColors {
  /** Primary accent — text on tint, icons, sparkline strokes */
  fg: string;
  /** Darker variant — text on white/surface, emphasis */
  deep: string;
  /** Pale wash — chip/card backgrounds */
  tint: string;
  /** Ring/border accent at low opacity contexts */
  ring: string;
}

export interface Palette {
  name: PaletteName;
  label: string;
  /** App background */
  canvas: string;
  /** Secondary background (alternating sections) */
  canvas2: string;
  /** Card / sheet backgrounds */
  surface: string;
  /** Slightly-tinted card background */
  surfaceSoft: string;
  /** Primary text */
  ink: string;
  /** Secondary / muted text */
  inkSoft: string;
  /** Faint text (timestamps, placeholders) */
  inkFaint: string;
  /** Hairline dividers, subtle borders */
  hair: string;
  /** Drop-shadow color */
  shadow: string;
  /** Brand accent (wordmark, links, primary CTA) */
  brand: string;
  /** Verdict: steady — in-range / good */
  good: VerdictColors;
  /** Verdict: spike — high blood sugar warning */
  warn: VerdictColors;
  /** Verdict: drop — low blood sugar */
  low: VerdictColors;
}

// ---------------------------------------------------------------------------
// Palettes
// ---------------------------------------------------------------------------

export const sunrise: Palette = {
  name: 'sunrise',
  label: 'Sunrise',
  canvas: '#F3EEE6', canvas2: '#ECE4D8',
  surface: '#FFFFFF', surfaceSoft: '#FBF7F1',
  ink: '#33303A', inkSoft: '#7C7682', inkFaint: '#A8A2AC',
  hair: 'rgba(51,48,58,0.09)', shadow: 'rgba(61,52,40,0.10)',
  brand: '#3D5A53',
  good: { fg: '#5C8A6E', deep: '#3E6B50', tint: '#E7F0E9', ring: '#9DC6A9' },
  warn: { fg: '#C57E5B', deep: '#A65F3C', tint: '#F6E8DF', ring: '#E2AD8F' },
  low:  { fg: '#7585C6', deep: '#5666AE', tint: '#E8EAF6', ring: '#AAB3E0' },
};

export const matcha: Palette = {
  name: 'matcha',
  label: 'Matcha',
  canvas: '#EBEFE6', canvas2: '#E0E7D7',
  surface: '#FFFFFF', surfaceSoft: '#F7FAF3',
  ink: '#2E332C', inkSoft: '#717A6C', inkFaint: '#A2A99B',
  hair: 'rgba(46,51,44,0.09)', shadow: 'rgba(48,56,40,0.10)',
  brand: '#4A6B4F',
  good: { fg: '#5A8A66', deep: '#3F6A4A', tint: '#E5F0E6', ring: '#9AC6A4' },
  warn: { fg: '#C68559', deep: '#A5663A', tint: '#F4EADD', ring: '#E1B188' },
  low:  { fg: '#6E84BC', deep: '#52679F', tint: '#E6EAF3', ring: '#A4B0DA' },
};

export const mist: Palette = {
  name: 'mist',
  label: 'Mist',
  canvas: '#EAEEF0', canvas2: '#DEE5E9',
  surface: '#FFFFFF', surfaceSoft: '#F5F8FA',
  ink: '#2B313A', inkSoft: '#6E7783', inkFaint: '#9CA5AF',
  hair: 'rgba(43,49,58,0.09)', shadow: 'rgba(40,50,62,0.11)',
  brand: '#3A5566',
  good: { fg: '#4F8A84', deep: '#356B66', tint: '#E2F0EE', ring: '#92C7C0' },
  warn: { fg: '#C17C66', deep: '#A15C45', tint: '#F4E8E2', ring: '#DEA992' },
  low:  { fg: '#6982C2', deep: '#4E66A4', tint: '#E6EAF5', ring: '#A3B0DD' },
};

export const palettes: Record<PaletteName, Palette> = { sunrise, matcha, mist };

/** Default palette for the app */
export const defaultPalette: Palette = sunrise;

// ---------------------------------------------------------------------------
// Verdict helper
// ---------------------------------------------------------------------------

/**
 * Returns the palette color group that represents the given glucose verdict.
 *
 * spike  → warn  (terracotta)
 * steady → good  (sage green)
 * drop   → low   (periwinkle)
 * null   → neutral ink-based group, for chips before a verdict is known
 */
export function verdictColor(verdict: Verdict | null | undefined, palette: Palette = defaultPalette): VerdictColors {
  switch (verdict) {
    case 'spike':  return palette.warn;
    case 'steady': return palette.good;
    case 'drop':   return palette.low;
    default:       return { fg: palette.inkFaint, deep: palette.inkSoft, tint: palette.hair, ring: palette.hair };
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
