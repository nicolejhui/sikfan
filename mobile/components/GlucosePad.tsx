import React, { useCallback, useMemo, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';

interface GlucosePadProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  initial: number | null;
  last: number;
  onSet: (value: number) => Promise<void>;
  onClose: () => void;
}

const MIN_LOGGABLE = 40;
const MAX_LOGGABLE = 400;
const MAX_DIGITS = 3;

function rangeLabel(value: number): { label: string; color: string } {
  if (value < MIN_LOGGABLE) return { label: 'Too low to log', color: defaultPalette.low.fg };
  if (value < 70) return { label: 'Low', color: defaultPalette.low.fg };
  if (value <= 180) return { label: 'In range', color: defaultPalette.good.fg };
  if (value <= MAX_LOGGABLE) return { label: 'High', color: defaultPalette.warn.fg };
  return { label: 'Out of range', color: defaultPalette.warn.fg };
}

const KEYPAD_ROWS = [
  ['1', '2', '3'],
  ['4', '5', '6'],
  ['7', '8', '9'],
  ['', '0', 'backspace'],
];

export default function GlucosePad({ sheetRef, initial, last, onSet, onClose }: GlucosePadProps) {
  const [digits, setDigits] = useState(initial != null ? String(initial) : '');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const value = digits ? parseInt(digits, 10) : null;
  const inRangeToSubmit = value != null && value >= MIN_LOGGABLE && value <= MAX_LOGGABLE;
  const range = value != null ? rangeLabel(value) : null;

  const handlePress = useCallback((key: string) => {
    setSubmitError(null);
    if (key === 'backspace') {
      setDigits((d) => d.slice(0, -1));
    } else if (key !== '') {
      setDigits((d) => (d.length >= MAX_DIGITS ? d : d + key));
    }
  }, []);

  const handleQuickFill = useCallback(() => {
    setSubmitError(null);
    setDigits(String(last));
  }, [last]);

  const handleSubmit = useCallback(async () => {
    if (!inRangeToSubmit || value == null) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSet(value);
      sheetRef.current?.dismiss();
    } catch {
      // Keep the sheet open with the value intact so the user can retry
      // instead of silently losing what they typed.
      setSubmitError("Couldn't save — check your connection and try again.");
    } finally {
      setSubmitting(false);
    }
  }, [inRangeToSubmit, value, onSet, sheetRef]);

  const renderBackdrop = useCallback(
    (props: BottomSheetBackdropProps) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} pressBehavior="close" onPress={onClose} />
    ),
    [onClose]
  );

  const handleSheetChange = useCallback(
    (index: number) => {
      if (index >= 0) {
        setDigits(initial != null ? String(initial) : '');
        setSubmitError(null);
      }
    },
    [initial]
  );

  const submitLabel = initial != null ? 'Update reading' : 'Anchor projection';

  const rows = useMemo(() => KEYPAD_ROWS, []);

  return (
    <BottomSheetModal
      ref={sheetRef}
      snapPoints={['62%']}
      enablePanDownToClose
      onDismiss={onClose}
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBackground}
      handleIndicatorStyle={styles.handleIndicator}
    >
      <BottomSheetView style={styles.content}>
        <View style={styles.displayRow}>
          <Text style={styles.displayValue}>{digits || '– – –'}</Text>
          <Text style={styles.displayUnit}>mg/dL</Text>
        </View>
        <Text style={[styles.rangeLabel, { color: range?.color ?? defaultPalette.inkFaint }]}>
          {range?.label ?? ' '}
        </Text>

        {last > 0 && (
          <TouchableOpacity style={styles.quickFill} onPress={handleQuickFill} activeOpacity={0.8}>
            <Ionicons name="time-outline" size={14} color={defaultPalette.inkSoft} />
            <Text style={styles.quickFillText}>Last reading {last}</Text>
          </TouchableOpacity>
        )}

        <View style={styles.keypad}>
          {rows.map((row, i) => (
            <View key={i} style={styles.keypadRow}>
              {row.map((key, j) => (
                <TouchableOpacity
                  key={j}
                  style={[styles.key, key === '' && styles.keyEmpty]}
                  onPress={() => handlePress(key)}
                  disabled={key === ''}
                  activeOpacity={0.6}
                >
                  {key === 'backspace' ? (
                    <Ionicons name="backspace-outline" size={20} color={defaultPalette.ink} />
                  ) : (
                    <Text style={styles.keyText}>{key}</Text>
                  )}
                </TouchableOpacity>
              ))}
            </View>
          ))}
        </View>

        {submitError && <Text style={styles.submitError}>{submitError}</Text>}

        <TouchableOpacity
          style={[styles.submitButton, (!inRangeToSubmit || submitting) && styles.submitButtonDisabled]}
          onPress={handleSubmit}
          disabled={!inRangeToSubmit || submitting}
          activeOpacity={0.85}
        >
          <Text style={styles.submitButtonText}>{submitting ? 'Anchoring…' : submitLabel}</Text>
        </TouchableOpacity>
      </BottomSheetView>
    </BottomSheetModal>
  );
}

const styles = StyleSheet.create({
  sheetBackground: {
    backgroundColor: defaultPalette.surface,
    borderTopLeftRadius: radius.xl,
    borderTopRightRadius: radius.xl,
  },
  handleIndicator: {
    backgroundColor: defaultPalette.hair,
    width: 40,
  },
  content: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.lg,
    alignItems: 'center',
  },
  displayRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginTop: spacing.md,
  },
  displayValue: {
    fontSize: 50,
    fontWeight: fontWeight.bold,
    color: defaultPalette.ink,
  },
  displayUnit: {
    fontSize: fontSize.md,
    color: defaultPalette.inkSoft,
    marginLeft: spacing.xs,
    marginBottom: spacing.sm,
  },
  rangeLabel: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    marginTop: spacing.xs,
    minHeight: 18,
  },
  quickFill: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    marginTop: spacing.md,
  },
  quickFillText: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    marginLeft: 4,
  },
  keypad: {
    width: '100%',
    marginTop: spacing.lg,
  },
  keypadRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  key: {
    flex: 1,
    marginHorizontal: spacing.xs,
    height: 48,
    borderRadius: radius.md,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  keyEmpty: {
    backgroundColor: 'transparent',
  },
  keyText: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.medium,
    color: defaultPalette.ink,
  },
  submitError: {
    color: defaultPalette.warn.fg,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    marginTop: spacing.sm,
    textAlign: 'center',
  },
  submitButton: {
    width: '100%',
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.md,
  },
  submitButtonDisabled: {
    opacity: 0.4,
  },
  submitButtonText: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
});
