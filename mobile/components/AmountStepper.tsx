import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';

// MOB-018: fraction-of-scanned stepper (plans/MOB-018-plan.md D1/D2) — a
// discrete index into this table rather than a continuous slider or a
// gram-increment stepper. `1` sits at index 3, the "as scanned" resting spot.
export const FRACTIONS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5];
export const FRACTION_LABELS = ['¼', '½', '¾', '1', '1¼', '1½', '1¾', '2', '2½'];
export const DEFAULT_FRACTION_INDEX = 3;

interface AmountStepperProps {
  index: number;
  onChange: (index: number) => void;
  grams: number | null;
  disabled?: boolean;
}

export default function AmountStepper({ index, onChange, grams, disabled }: AmountStepperProps) {
  const atMin = index <= 0;
  const atMax = index >= FRACTIONS.length - 1;

  return (
    <View style={styles.well}>
      <TouchableOpacity
        style={[styles.button, (atMin || disabled) && styles.buttonDisabled]}
        onPress={() => onChange(Math.max(0, index - 1))}
        disabled={atMin || disabled}
        hitSlop={12}
        accessibilityRole="button"
        accessibilityLabel="Less"
      >
        <Ionicons name="remove" size={18} color={atMin || disabled ? defaultPalette.inkFaint : defaultPalette.ink} />
      </TouchableOpacity>

      <View style={styles.readout}>
        <Text style={styles.fractionLabel}>{FRACTION_LABELS[index]}×</Text>
        <Text style={styles.gramsLabel}>{grams != null ? `${Math.round(grams)} g` : '—'}</Text>
      </View>

      <TouchableOpacity
        style={[styles.button, (atMax || disabled) && styles.buttonDisabled]}
        onPress={() => onChange(Math.min(FRACTIONS.length - 1, index + 1))}
        disabled={atMax || disabled}
        hitSlop={12}
        accessibilityRole="button"
        accessibilityLabel="More"
      >
        <Ionicons name="add" size={18} color={atMax || disabled ? defaultPalette.inkFaint : defaultPalette.ink} />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  well: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.lg,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  button: {
    width: 44,
    height: 44,
    borderRadius: radius.full,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    backgroundColor: defaultPalette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonDisabled: {
    opacity: 0.4,
  },
  readout: {
    alignItems: 'center',
  },
  fractionLabel: {
    fontSize: fontSize.xl,
    fontWeight: fontWeight.bold,
    color: defaultPalette.brand,
  },
  gramsLabel: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginTop: 2,
  },
});
