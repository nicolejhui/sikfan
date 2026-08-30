import React, { useCallback, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetScrollView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { correctIngredients } from '../api/meals';
import { ApiError } from '../api/client';
import type { CorrectIngredientsResponse, IngredientCandidate } from '../api/types';
import { formatDishName } from '../store/types';

interface AddIngredientSheetProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  mealId: string;
  cropId: string;
  candidates: IngredientCandidate[];
  presentComponentNames: string[];
  onToast: (message: string) => void;
  onCorrected: (response: CorrectIngredientsResponse, summary: string, meta: { kind: 'add'; name: string }) => void;
}

function normalize(name: string): string {
  return name.trim().toLowerCase();
}

export default function AddIngredientSheet({
  sheetRef,
  mealId,
  cropId,
  candidates,
  presentComponentNames,
  onToast,
  onCorrected,
}: AddIngredientSheetProps) {
  const [submittingName, setSubmittingName] = useState<string | null>(null);

  const handleSheetChange = useCallback((index: number) => {
    if (index < 0) setSubmittingName(null);
  }, []);

  const renderBackdrop = useCallback(
    (props: BottomSheetBackdropProps) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} pressBehavior="close" />
    ),
    []
  );

  const presentSlugs = new Set(presentComponentNames.map(normalize));

  // Only candidates with a grams_hint are actionable — this UI has no
  // numeric grams entry, and API-013's IngredientEdit.grams must be > 0.
  const actionable = candidates.filter((c) => c.grams_hint != null);

  const handleAdd = async (candidate: IngredientCandidate) => {
    if (submittingName || candidate.grams_hint == null) return;
    setSubmittingName(candidate.name);
    try {
      const res = await correctIngredients({
        meal_id: mealId,
        crop_id: cropId,
        edits: [{ action: 'add', component_name: candidate.name, grams: candidate.grams_hint }],
      });
      onCorrected(res, '1 ingredient added · projection updated', { kind: 'add', name: candidate.name });
      sheetRef.current?.dismiss();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't save — try again";
      onToast(message);
    } finally {
      setSubmittingName(null);
    }
  };

  return (
    <BottomSheetModal
      ref={sheetRef}
      snapPoints={['55%']}
      enablePanDownToClose
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBackground}
      handleIndicatorStyle={styles.handleIndicator}
    >
      <BottomSheetScrollView contentContainerStyle={styles.content}>
        <Text style={styles.header}>What did we miss?</Text>
        <Text style={styles.subtitle}>Foods that are commonly part of this dish but easy to miss in a photo.</Text>

        {actionable.length === 0 && <Text style={styles.emptyText}>No suggestions right now.</Text>}

        {actionable.map((candidate) => {
          const alreadyCounted = presentSlugs.has(normalize(candidate.name));
          const grams = candidate.grams_hint ?? 0;
          const carbs = (candidate.per_100g.carbs_g * grams) / 100;
          return (
            <TouchableOpacity
              key={candidate.name}
              style={[styles.row, alreadyCounted && styles.rowDisabled]}
              onPress={() => handleAdd(candidate)}
              disabled={alreadyCounted || submittingName !== null}
              activeOpacity={0.7}
            >
              <View style={styles.rowInfo}>
                <Text style={styles.rowName}>{formatDishName(candidate.name)}</Text>
                <Text style={styles.rowMeta}>
                  {Math.round(grams)}g · {Math.round(carbs)}g carbs
                </Text>
              </View>
              <Text style={styles.rowState}>
                {alreadyCounted ? 'already counted' : submittingName === candidate.name ? 'adding…' : 'add'}
              </Text>
            </TouchableOpacity>
          );
        })}
      </BottomSheetScrollView>
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
    paddingBottom: spacing.xl,
  },
  header: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
    color: defaultPalette.ink,
  },
  subtitle: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkSoft,
    marginTop: spacing.xs / 2,
    marginBottom: spacing.md,
  },
  emptyText: {
    fontSize: fontSize.sm,
    color: defaultPalette.inkFaint,
    textAlign: 'center',
    marginTop: spacing.lg,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    minHeight: 46,
    borderTopWidth: 1,
    borderTopColor: defaultPalette.hair,
    paddingVertical: spacing.xs,
  },
  rowDisabled: {
    opacity: 0.5,
  },
  rowInfo: {
    flex: 1,
  },
  rowName: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: defaultPalette.ink,
  },
  rowMeta: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkSoft,
    marginTop: 1,
  },
  rowState: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.brand,
  },
});
