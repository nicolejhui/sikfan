/**
 * Font family names used throughout the app.
 *
 * Variant selection:
 *   - Modern (default): Plus Jakarta Sans
 *   - Editorial:        Fraunces
 */
export const FontFamily = {
  // Plus Jakarta Sans weights
  jakartaRegular: 'PlusJakartaSans-Regular',
  jakartaMedium: 'PlusJakartaSans-Medium',
  jakartaSemiBold: 'PlusJakartaSans-SemiBold',
  jakartaBold: 'PlusJakartaSans-Bold',

  // Fraunces weights
  frauncesRegular: 'Fraunces-Regular',
  frauncesMedium: 'Fraunces-Medium',
  frauncesSemiBold: 'Fraunces-SemiBold',
  frauncesBold: 'Fraunces-Bold',
} as const;

/** Default font family (Modern variant = Plus Jakarta Sans) */
export const defaultFontFamily = FontFamily.jakartaRegular;

/**
 * Hook that signals fonts are loaded. Returns [true, null] immediately until
 * the actual .ttf files are placed in assets/fonts/ and uncommented below.
 *
 * To enable real font loading:
 *   1. Download font files into assets/fonts/ (see assets/fonts/.gitkeep for URLs)
 *   2. Uncomment the useFonts block below and remove the stub return
 */
export function useSikFanFonts(): [boolean, Error | null] {
  // Stub: skip font loading until .ttf files are present.
  // Replace with the block below once fonts are downloaded.
  return [true, null];

  // -- Uncomment once fonts are in assets/fonts/ --
  // import { useFonts } from 'expo-font';
  // return useFonts({
  //   [FontFamily.jakartaRegular]:  require('../assets/fonts/PlusJakartaSans-Regular.ttf'),
  //   [FontFamily.jakartaMedium]:   require('../assets/fonts/PlusJakartaSans-Medium.ttf'),
  //   [FontFamily.jakartaSemiBold]: require('../assets/fonts/PlusJakartaSans-SemiBold.ttf'),
  //   [FontFamily.jakartaBold]:     require('../assets/fonts/PlusJakartaSans-Bold.ttf'),
  //   [FontFamily.frauncesRegular]:  require('../assets/fonts/Fraunces-Regular.ttf'),
  //   [FontFamily.frauncesMedium]:   require('../assets/fonts/Fraunces-Medium.ttf'),
  //   [FontFamily.frauncesSemiBold]: require('../assets/fonts/Fraunces-SemiBold.ttf'),
  //   [FontFamily.frauncesBold]:     require('../assets/fonts/Fraunces-Bold.ttf'),
  // });
}
