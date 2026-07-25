import React from 'react';
import { ActivityIndicator, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StatusBar } from 'expo-status-bar';
import { useSikFanFonts } from './constants/fonts';
import { defaultPalette } from './constants/theme';
import RootNavigator from './navigation';

export default function App() {
  const [fontsLoaded, fontError] = useSikFanFonts();

  // Hold the splash / show a minimal loader while fonts load.
  // FontError is a non-critical failure — the OS fallback font renders instead.
  if (!fontsLoaded && !fontError) {
    return (
      <View style={{ flex: 1, backgroundColor: defaultPalette.canvas, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator color={defaultPalette.brand} />
      </View>
    );
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <StatusBar style="dark" />
      <RootNavigator />
    </GestureHandlerRootView>
  );
}
