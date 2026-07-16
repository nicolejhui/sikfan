import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { defaultPalette } from '../constants/theme';

export default function CameraScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.label}>CameraScreen</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
    alignItems: 'center',
    justifyContent: 'center',
  },
  label: {
    color: defaultPalette.textMuted,
    fontSize: 16,
  },
});
