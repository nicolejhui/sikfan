import React, { useCallback, useState } from 'react';
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';

interface ConfirmDishSheetProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  mealId: string;
  cropId: string;
  dishName: string | null;
  confidence: number | null;
  onToast: (message: string) => void;
  // Client-side-only reflection of a correction — no API call (MOB-010 is a
  // stub; Epic 10's real POST /confirm-dish + ChromaDB write replaces this).
  onCorrected: (correctedName: string) => void;
}

// Matches ConfirmDishRequest in api.py: corrected_label is `str | None = None`
// and every tested CONFIRM curl example (docs/API-LAYER-TICKETS.md) omits the
// key entirely rather than sending `corrected_label: null` — key omission is
// the frozen convention for this field, not explicit null. See
// data/MOBILE_DECISIONS.md Decision 4.
type ConfirmCorrectPayload =
  | { meal_id: string; crop_id: string; action: 'CONFIRM' }
  | { meal_id: string; crop_id: string; action: 'CORRECT' | 'ADD_NEW'; corrected_label: string };

function logStub(payload: ConfirmCorrectPayload) {
  // Epic 10 greps this prefix to find the POST /confirm-dish integration point.
  console.log('[MOB-010 stub] confirm/correct:', payload);
}

export default function ConfirmDishSheet({
  sheetRef,
  mealId,
  cropId,
  dishName,
  confidence,
  onToast,
  onCorrected,
}: ConfirmDishSheetProps) {
  const [mode, setMode] = useState<'initial' | 'correcting'>('initial');
  const [correctedText, setCorrectedText] = useState(dishName ?? '');

  const handleSheetChange = useCallback(
    (index: number) => {
      // index >= 0 means presenting/presented; -1 means dismissed. Reset the
      // "correct it" sub-form on every open so a prior edit doesn't linger.
      setMode('initial');
      if (index >= 0) setCorrectedText(dishName ?? '');
    },
    [dishName]
  );

  const handleLooksRight = useCallback(() => {
    logStub({ meal_id: mealId, crop_id: cropId, action: 'CONFIRM' });
    onToast('Confirmed');
    sheetRef.current?.dismiss();
  }, [mealId, cropId, onToast, sheetRef]);

  const handleSaveCorrection = useCallback(() => {
    logStub({ meal_id: mealId, crop_id: cropId, action: 'CORRECT', corrected_label: correctedText });
    onCorrected(correctedText);
    onToast('Saved');
    sheetRef.current?.dismiss();
  }, [mealId, cropId, correctedText, onToast, onCorrected, sheetRef]);

  const renderBackdrop = useCallback(
    (props: BottomSheetBackdropProps) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} pressBehavior="close" />
    ),
    []
  );

  return (
    <BottomSheetModal
      ref={sheetRef}
      snapPoints={['38%']}
      enablePanDownToClose
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBackground}
      handleIndicatorStyle={styles.handleIndicator}
    >
      <BottomSheetView style={styles.content}>
        <Text style={styles.dishName} numberOfLines={1}>{dishName ?? 'Unknown dish'}</Text>
        {confidence != null && (
          <View style={styles.confidenceBadge}>
            <Ionicons name="sparkles" size={11} color={defaultPalette.brand} />
            <Text style={styles.confidenceText}>{Math.round(confidence * 100)}%</Text>
          </View>
        )}

        {mode === 'initial' ? (
          <View style={styles.actionsRow}>
            <TouchableOpacity style={styles.correctButton} onPress={() => setMode('correcting')} activeOpacity={0.85}>
              <Text style={styles.correctButtonText}>Correct it</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.confirmButton} onPress={handleLooksRight} activeOpacity={0.85}>
              <Text style={styles.confirmButtonText}>Looks right ✓</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <View style={styles.correctingGroup}>
            <TextInput
              style={styles.input}
              value={correctedText}
              onChangeText={setCorrectedText}
              autoFocus
              placeholder="Dish name"
              placeholderTextColor={defaultPalette.inkFaint}
            />
            <TouchableOpacity style={styles.confirmButton} onPress={handleSaveCorrection} activeOpacity={0.85}>
              <Text style={styles.confirmButtonText}>Save correction</Text>
            </TouchableOpacity>
          </View>
        )}
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
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.lg,
  },
  dishName: {
    color: defaultPalette.ink,
    fontSize: fontSize.xl,
    fontWeight: fontWeight.semibold,
  },
  confidenceBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    marginTop: spacing.xs,
  },
  confidenceText: {
    color: defaultPalette.ink,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    marginLeft: 4,
  },
  actionsRow: {
    flexDirection: 'row',
    marginTop: spacing.lg,
  },
  correctingGroup: {
    marginTop: spacing.lg,
  },
  input: {
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.md,
    color: defaultPalette.ink,
    marginBottom: spacing.md,
  },
  confirmButton: {
    flex: 1,
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
  confirmButtonText: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
  correctButton: {
    flex: 1,
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  correctButtonText: {
    color: defaultPalette.ink,
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
});
