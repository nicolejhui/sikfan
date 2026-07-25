import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { defaultPalette } from '../constants/theme';

export default function MealLogScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>MealLogScreen</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: defaultPalette.canvas,
    alignItems: 'center',
    justifyContent: 'center',
  },
  label: {
    color: defaultPalette.inkFaint,
    fontSize: 16,
  },
});
