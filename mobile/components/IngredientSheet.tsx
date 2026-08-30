import React, { useCallback, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetScrollView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { correctIngredients } from '../api/meals';
import { ApiError } from '../api/client';
import type { CorrectIngredientsResponse, IngredientCandidate } from '../api/types';
import { formatDishName } from '../store/types';
import type { DishComponent, DishResult } from '../store/types';

interface IngredientSheetProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  mealId: string;
  cropId: string;
  component: DishComponent | null;
  dish: DishResult | null;
  candidates: IngredientCandidate[];
  onToast: (message: string) => void;
  // summary is the confirmation-line text ("1 ingredient corrected" /
  // "1 ingredient removed"), matched to CarbCorrection's own copy. meta
  // identifies what happened so the breakdown list can show a "fixed" pill
  // on the resulting row — the response's folded component list alone
  // doesn't say which entry is new vs. carried over.
  onCorrected: (
    response: CorrectIngredientsResponse,
    summary: string,
    meta: { kind: 'swap'; name: string } | { kind: 'remove' }
  ) => void;
}

function componentGrams(component: DishComponent, dishPortionG: number | null): number | null {
  if (dishPortionG == null) return null;
  return component.proportion * dishPortionG;
}

export default function IngredientSheet({
  sheetRef,
  mealId,
  cropId,
  component,
  dish,
  candidates,
  onToast,
  onCorrected,
}: IngredientSheetProps) {
  const [submitting, setSubmitting] = useState(false);
  const [removed, setRemoved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSheetChange = useCallback((index: number) => {
    if (index < 0) {
      setSubmitting(false);
      setRemoved(false);
      setError(null);
    }
  }, []);

  const renderBackdrop = useCallback(
    (props: BottomSheetBackdropProps) => (
      <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} pressBehavior="close" />
    ),
    []
  );

  const grams = component && dish ? componentGrams(component, dish.portion_g) : null;
  const carbs = component && grams != null ? (component.per_100g.carbs_g * grams) / 100 : null;
  const onlyComponent = (dish?.components?.length ?? 0) <= 1;

  const submit = async (
    payload: Parameters<typeof correctIngredients>[0]['edits'],
    summary: string,
    meta: { kind: 'swap'; name: string } | { kind: 'remove' }
  ) => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await correctIngredients({ meal_id: mealId, crop_id: cropId, edits: payload });
      onCorrected(res, summary, meta);
      sheetRef.current?.dismiss();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't save — try again";
      setError(message);
      onToast(message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSwap = (candidate: IngredientCandidate) => {
    if (submitting || !component) return;
    void submit(
      [{ action: 'swap', component_name: component.name, replacement_name: candidate.name }],
      '1 ingredient corrected · projection updated',
      { kind: 'swap', name: candidate.name }
    );
  };

  const handleRemove = () => {
    if (submitting || onlyComponent || !component) return;
    setRemoved(true);
    void submit(
      [{ action: 'remove', component_name: component.name }],
      '1 ingredient removed · projection updated',
      { kind: 'remove' }
    );
  };

  return (
    <BottomSheetModal
      ref={sheetRef}
      snapPoints={['62%']}
      enablePanDownToClose
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBackground}
      handleIndicatorStyle={styles.handleIndicator}
    >
      <BottomSheetScrollView contentContainerStyle={styles.content}>
        <Text style={styles.header}>We read this as</Text>
        <View style={styles.currentRow}>
          <View style={styles.radioSelected} />
          <View style={styles.rowInfo}>
            <Text style={styles.rowName}>{formatDishName(component?.name)}</Text>
            <Text style={styles.rowMeta}>
              {grams != null ? `${Math.round(grams)}g` : '—'}
              {carbs != null ? ` · ${Math.round(carbs)}g carbs` : ''}
            </Text>
          </View>
        </View>

        {candidates.length > 0 && (
          <>
            <Text style={styles.sectionLabel}>Or maybe</Text>
            {candidates.map((candidate) => {
              const altGrams = grams;
              const altCarbs = altGrams != null ? (candidate.per_100g.carbs_g * altGrams) / 100 : null;
              return (
                <TouchableOpacity
                  key={candidate.name}
                  style={styles.altRow}
                  onPress={() => handleSwap(candidate)}
                  disabled={submitting}
                  activeOpacity={0.7}
                >
                  <View style={styles.radio} />
                  <View style={styles.rowInfo}>
                    <Text style={styles.rowName}>{formatDishName(candidate.name)}</Text>
                    <Text style={styles.rowMeta}>
                      {altGrams != null ? `${Math.round(altGrams)}g` : '—'}
                      {altCarbs != null ? ` · ${Math.round(altCarbs)}g carbs` : ''}
                    </Text>
                  </View>
                </TouchableOpacity>
              );
            })}
          </>
        )}

        {error && <Text style={styles.errorText}>{error}</Text>}

        <TouchableOpacity
          style={[styles.destructiveRow, (onlyComponent || submitting || removed) && styles.destructiveRowDisabled]}
          onPress={handleRemove}
          disabled={onlyComponent || submitting || removed}
          activeOpacity={0.7}
        >
          {submitting && removed ? (
            <ActivityIndicator size="small" color={defaultPalette.warn.fg} />
          ) : (
            <Ionicons name="close-circle-outline" size={16} color={defaultPalette.warn.fg} />
          )}
          <Text style={styles.destructiveText}>
            {removed ? 'Not in my dish · removed' : "It's not in my dish"}
          </Text>
        </TouchableOpacity>
        {onlyComponent && (
          <Text style={styles.destructiveHelper}>
            This is the only ingredient left — if none of this is your meal, correct the dish itself instead.
          </Text>
        )}
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
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginBottom: spacing.sm,
  },
  currentRow: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 46,
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.md,
    paddingHorizontal: spacing.sm,
    marginBottom: spacing.md,
  },
  sectionLabel: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginBottom: spacing.xs,
  },
  altRow: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 46,
    paddingHorizontal: spacing.sm,
  },
  radio: {
    width: 18,
    height: 18,
    borderRadius: radius.full,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    marginRight: spacing.sm,
  },
  radioSelected: {
    width: 18,
    height: 18,
    borderRadius: radius.full,
    borderWidth: 5,
    borderColor: defaultPalette.brand,
    marginRight: spacing.sm,
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
  errorText: {
    fontSize: fontSize.xs,
    color: defaultPalette.warn.fg,
    marginTop: spacing.sm,
  },
  destructiveRow: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 44,
    marginTop: spacing.md,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: defaultPalette.hair,
    gap: spacing.xs,
  },
  destructiveRowDisabled: {
    opacity: 0.5,
  },
  destructiveText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: defaultPalette.warn.fg,
  },
  destructiveHelper: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginTop: spacing.xs,
  },
});
