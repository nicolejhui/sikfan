import React, { useCallback, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { BottomSheetBackdrop, BottomSheetModal, BottomSheetScrollView } from '@gorhom/bottom-sheet';
import type { BottomSheetBackdropProps } from '@gorhom/bottom-sheet';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { correctIngredients } from '../api/meals';
import { ApiError } from '../api/client';
import type { CorrectIngredientsResponse, IngredientCandidate } from '../api/types';
import { formatDishName, componentGrams, componentCarbs } from '../store/types';
import type { DishComponent, DishResult } from '../store/types';
import AmountStepper, { FRACTIONS, DEFAULT_FRACTION_INDEX } from './AmountStepper';

interface IngredientSheetProps {
  sheetRef: React.RefObject<BottomSheetModal | null>;
  mealId: string;
  cropId: string;
  component: DishComponent | null;
  dish: DishResult | null;
  candidates: IngredientCandidate[];
  // MOB-016 rev-2: when this component is itself the result of an earlier
  // swap, its true original name — lets the current-reading row act as a
  // revert (results.jsx's "the current reading is itself a row").
  originalName: string | null;
  onToast: (message: string) => void;
  // summary is unused by the caller now (ResultsScreen computes its own
  // aggregate confirmation copy) but kept so a swap's resulting name and a
  // remove's captured figures reach the caller — the response's folded
  // component list alone doesn't say which entry is new vs. carried over,
  // and a removed component vanishes from it entirely.
  onCorrected: (
    response: CorrectIngredientsResponse,
    summary: string,
    meta:
      | { kind: 'swap'; name: string; fromName: string }
      | { kind: 'remove'; name: string; grams: number | null; carbs: number | null }
      | { kind: 'resize'; name: string; fromGrams: number | null; toGrams: number | null }
  ) => void;
}

// grams rounded to the nearest 5 — the underlying number comes from a
// pixel-area estimate times a category density, so displaying e.g. `262.5 g`
// would be false precision (plans/MOB-018-plan.md Ticket 2).
function roundedStagedGrams(scannedGrams: number, fraction: number): number {
  return Math.round((scannedGrams * fraction) / 5) * 5;
}

export default function IngredientSheet({
  sheetRef,
  mealId,
  cropId,
  component,
  dish,
  candidates,
  originalName,
  onToast,
  onCorrected,
}: IngredientSheetProps) {
  const [submitting, setSubmitting] = useState(false);
  const [removed, setRemoved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fractionIndex, setFractionIndex] = useState(DEFAULT_FRACTION_INDEX);

  const handleSheetChange = useCallback((index: number) => {
    if (index < 0) {
      setSubmitting(false);
      setRemoved(false);
      setError(null);
      setFractionIndex(DEFAULT_FRACTION_INDEX);
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

  const stagedFraction = FRACTIONS[fractionIndex];
  const stagedGrams = grams != null ? roundedStagedGrams(grams, stagedFraction) : null;
  const stagedCarbs =
    component && stagedGrams != null ? (component.per_100g.carbs_g * stagedGrams) / 100 : null;
  const amountChanged = fractionIndex !== DEFAULT_FRACTION_INDEX;

  const submit = async (
    payload: Parameters<typeof correctIngredients>[0]['edits'],
    summary: string,
    meta:
      | { kind: 'swap'; name: string; fromName: string }
      | { kind: 'remove'; name: string; grams: number | null; carbs: number | null }
      | { kind: 'resize'; name: string; fromGrams: number | null; toGrams: number | null }
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

  const handleSwap = (replacementName: string) => {
    if (submitting || !component) return;
    void submit(
      [{ action: 'swap', component_name: component.name, replacement_name: replacementName }],
      '1 ingredient corrected · projection updated',
      { kind: 'swap', name: replacementName, fromName: component.name }
    );
  };

  const handleSaveAmount = () => {
    if (submitting || !component || stagedGrams == null) return;
    void submit(
      [{ action: 'set_amount', component_name: component.name, grams: stagedGrams }],
      '1 ingredient corrected · projection updated',
      { kind: 'resize', name: component.name, fromGrams: grams, toGrams: stagedGrams }
    ).then(() => setFractionIndex(DEFAULT_FRACTION_INDEX));
  };

  const handleRemove = () => {
    if (submitting || onlyComponent || !component) return;
    setRemoved(true);
    void submit(
      [{ action: 'remove', component_name: component.name }],
      '1 ingredient removed · projection updated',
      { kind: 'remove', name: component.name, grams, carbs }
    );
  };

  return (
    <BottomSheetModal
      ref={sheetRef}
      snapPoints={['78%']}
      enablePanDownToClose
      onChange={handleSheetChange}
      backdropComponent={renderBackdrop}
      backgroundStyle={styles.sheetBackground}
      handleIndicatorStyle={styles.handleIndicator}
    >
      <BottomSheetScrollView contentContainerStyle={styles.content}>
        <Text style={styles.header}>We read this as</Text>
        <Text style={styles.titleName}>{formatDishName(component?.name)}</Text>
        <Text style={styles.titleMeta}>
          {grams != null ? `${Math.round(grams)}g` : '—'}
          {carbs != null ? ` · ${Math.round(carbs)}g carbs as scanned` : ''}
        </Text>

        {grams != null && (
          <>
            <Text style={styles.sectionLabel}>How much was actually there?</Text>
            <AmountStepper
              index={fractionIndex}
              onChange={setFractionIndex}
              grams={stagedGrams}
              disabled={submitting}
            />
            {amountChanged && stagedCarbs != null && carbs != null && (
              <Text style={styles.amountPreview}>
                {stagedCarbs >= carbs ? '+' : ''}
                {Math.round(stagedCarbs - carbs)}g carbs
              </Text>
            )}
            <TouchableOpacity
              style={[styles.saveAmountButton, (!amountChanged || submitting) && styles.saveAmountButtonDisabled]}
              onPress={handleSaveAmount}
              disabled={!amountChanged || submitting}
              activeOpacity={0.85}
            >
              {submitting ? (
                <ActivityIndicator size="small" color={defaultPalette.surface} />
              ) : (
                <Text style={styles.saveAmountText}>Save amount</Text>
              )}
            </TouchableOpacity>
          </>
        )}

        <Text style={styles.sectionLabel}>What is it actually?</Text>
        <View style={styles.radioList}>
          {originalName && (
            <TouchableOpacity
              style={styles.altRow}
              onPress={() => handleSwap(originalName)}
              disabled={submitting}
              activeOpacity={0.7}
            >
              <View style={styles.radio} />
              <Text style={styles.rowName}>{formatDishName(originalName)}</Text>
            </TouchableOpacity>
          )}
          {candidates.map((candidate) => {
            const altGrams = grams;
            const altCarbs = altGrams != null ? (candidate.per_100g.carbs_g * altGrams) / 100 : null;
            return (
              <TouchableOpacity
                key={candidate.name}
                style={styles.altRow}
                onPress={() => handleSwap(candidate.name)}
                disabled={submitting}
                activeOpacity={0.7}
              >
                <View style={styles.radio} />
                <Text style={styles.rowName}>{formatDishName(candidate.name)}</Text>
                <Text style={styles.rowCarbs}>
                  {altCarbs != null ? `${Math.round(altCarbs)}g` : altGrams != null ? `${Math.round(altGrams)}g` : ''}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {error && <Text style={styles.errorText}>{error}</Text>}

        <TouchableOpacity
          style={[
            styles.destructiveRow,
            removed && styles.destructiveRowActive,
            (onlyComponent || submitting || removed) && styles.destructiveRowDisabled,
          ]}
          onPress={handleRemove}
          disabled={onlyComponent || submitting || removed}
          activeOpacity={0.7}
        >
          {submitting && removed ? (
            <ActivityIndicator size="small" color={defaultPalette.warn.fg} />
          ) : (
            <Ionicons name="close" size={14} color={removed ? defaultPalette.warn.deep : defaultPalette.inkSoft} />
          )}
          <Text style={[styles.destructiveText, removed && styles.destructiveTextActive]}>
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
    fontWeight: fontWeight.semibold,
    color: defaultPalette.inkFaint,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  titleName: {
    fontSize: fontSize.xl,
    fontWeight: fontWeight.bold,
    color: defaultPalette.ink,
    marginTop: 2,
  },
  titleMeta: {
    fontSize: fontSize.sm,
    color: defaultPalette.inkSoft,
    marginTop: 2,
  },
  sectionLabel: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: defaultPalette.inkSoft,
    marginTop: spacing.md,
    marginBottom: spacing.xs,
  },
  amountPreview: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkSoft,
    marginTop: spacing.xs,
  },
  saveAmountButton: {
    marginTop: spacing.sm,
    minHeight: 44,
    borderRadius: radius.md,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveAmountButtonDisabled: {
    opacity: 0.4,
  },
  saveAmountText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: defaultPalette.surface,
  },
  radioList: {
    gap: spacing.xs,
  },
  altRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    minHeight: 46,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    borderRadius: radius.md,
    paddingHorizontal: spacing.sm,
    backgroundColor: defaultPalette.surface,
  },
  radio: {
    width: 18,
    height: 18,
    borderRadius: radius.full,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
  },
  rowName: {
    flex: 1,
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: defaultPalette.ink,
  },
  rowCarbs: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkFaint,
  },
  errorText: {
    fontSize: fontSize.xs,
    color: defaultPalette.warn.fg,
    marginTop: spacing.sm,
  },
  destructiveRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 46,
    marginTop: spacing.md,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    borderRadius: radius.md,
    paddingHorizontal: spacing.sm,
    gap: spacing.xs,
  },
  destructiveRowActive: {
    borderColor: defaultPalette.warn.ring,
    backgroundColor: defaultPalette.warn.tint,
  },
  destructiveRowDisabled: {
    opacity: 0.5,
  },
  destructiveText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: defaultPalette.inkSoft,
  },
  destructiveTextActive: {
    color: defaultPalette.warn.deep,
  },
  destructiveHelper: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginTop: spacing.xs,
  },
});
