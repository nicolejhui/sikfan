import React, { useCallback, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { confirmDish } from '../api/meals';
import { ApiError } from '../api/client';
import type { ConfirmDishRequest } from '../api/types';

interface ConfirmDishSheetProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  mealId: string;
  cropId: string;
  dishName: string | null;
  confidence: number | null;
  onToast: (message: string) => void;
  // Called once POST /confirm-dish succeeds for CORRECT/ADD_NEW, with the
  // user-typed name (not the server's normalized DB key — see submit()
  // below) and whether the correction changed the dish's macros server-side
  // (FOOD-016a), so the caller knows whether to re-fetch glucose too.
  onCorrected: (correctedName: string, macrosChanged: boolean) => void;
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
  const [submitting, setSubmitting] = useState(false);

  const handleSheetChange = useCallback(
    (index: number) => {
      // index >= 0 means presenting/presented; -1 means dismissed. Reset the
      // "correct it" sub-form on every open so a prior edit doesn't linger.
      setMode('initial');
      setSubmitting(false);
      if (index >= 0) setCorrectedText(dishName ?? '');
    },
    [dishName]
  );

  const submit = useCallback(
    async (payload: ConfirmDishRequest, successToast: string) => {
      setSubmitting(true);
      try {
        // response.updated_label is normalize_dish_name()'s DB key
        // ("kimchi_jjigae") — the ChromaDB/pipeline canonical form, not a
        // display string. Reflect back what the user actually typed instead.
        const response = await confirmDish(payload);
        if (payload.action !== 'CONFIRM') onCorrected(payload.corrected_label, response.macros_changed);
        onToast(successToast);
        sheetRef.current?.dismiss();
      } catch (err) {
        const message = err instanceof ApiError ? err.message : 'Could not save — try again';
        onToast(message);
      } finally {
        setSubmitting(false);
      }
    },
    [onToast, onCorrected, sheetRef]
  );

  const handleLooksRight = useCallback(() => {
    void submit({ meal_id: mealId, crop_id: cropId, action: 'CONFIRM' }, 'Confirmed');
  }, [mealId, cropId, submit]);

  const handleSaveCorrection = useCallback(() => {
    void submit(
      { meal_id: mealId, crop_id: cropId, action: 'CORRECT', corrected_label: correctedText },
      'Saved'
    );
  }, [mealId, cropId, correctedText, submit]);

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
            <TouchableOpacity
              style={styles.correctButton}
              onPress={() => setMode('correcting')}
              activeOpacity={0.85}
              disabled={submitting}
            >
              <Text style={styles.correctButtonText}>Correct it</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.confirmButton}
              onPress={handleLooksRight}
              activeOpacity={0.85}
              disabled={submitting}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.confirmButtonText}>Looks right ✓</Text>
              )}
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
              editable={!submitting}
            />
            <TouchableOpacity
              style={styles.confirmButton}
              onPress={handleSaveCorrection}
              activeOpacity={0.85}
              disabled={submitting || !correctedText.trim()}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.confirmButtonText}>Save correction</Text>
              )}
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
