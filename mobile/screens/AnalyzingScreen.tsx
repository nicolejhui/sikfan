import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { defaultPalette } from '../constants/theme';

export default function AnalyzingScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>AnalyzingScreen</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: defaultPalette.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  label: {
    color: defaultPalette.textMuted,
    fontSize: 16,
  },
});
