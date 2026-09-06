import React, { useCallback, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { correctMacros, resetCorrections } from '../api/meals';
import { ApiError } from '../api/client';
import type { CorrectionDirection, CorrectionMagnitude, CorrectMacrosResponse } from '../api/types';

// FOOD-023: direction + magnitude only — the reason chips are gone. A claim
// about one ingredient's grams (the old "hidden"/"broth" chips) belongs in
// the ingredient-edit flow (IngredientSheet's "How much was actually
// there?"), which now also teaches the portion prior (FOOD-022). The one
// exception is "leftover", which isn't a reason at all but a SCOPE — "just
// this meal", not "this dish is systematically mis-estimated" — so it's a
// checkbox on too_high only, not a chip row.

const MAGNITUDES: { key: CorrectionMagnitude; label: string }[] = [
  { key: 'little', label: 'A little' },
  { key: 'lot', label: 'A lot' },
];

// The percentage shown must come from what the server actually applied, not
// from the magnitude chip — FOOD-020 D6 made too_low the reciprocal of
// too_high (0.85/0.60 vs 1.176/1.667), so "raised a lot" reads 67%, not 40%.
function buildConfirmationText(res: CorrectMacrosResponse): string {
  if (res.old_portion_g == null || res.new_portion_g == null || res.old_portion_g === 0) {
    return 'Carbs updated · projection updated';
  }
  const ratio = res.new_portion_g / res.old_portion_g;
  const pct = Math.round(Math.abs(1 - ratio) * 100);
  const verb = res.direction === 'too_high' ? 'lowered' : 'raised';
  return `Carbs ${verb} ${pct}% · projection updated`;
}

interface CarbCorrectionProps {
  mealId: string;
  cropId: string;
  corrected: boolean;
  // MOB-016 rev-2: results.jsx's `unsure` state — confidence below LOW_CONF.
  // Styles the entry row warn-colored with "Fix the estimate" copy instead
  // of the neutral default, and only while the dish hasn't been corrected
  // yet (a correction already answers "does this look right?").
  unsure: boolean;
  confirmationText: string | null;
  // MOB-016 rev-2: results.jsx renders these as independent lines driven
  // straight off `excluded.length`/`Object.keys(swaps).length` — not folded
  // into one combined sentence, and never shown for an "add" (the design has
  // no confirmation line for that case, only the row's own "added" pill).
  removedCount: number;
  correctedCount: number;
  onToast: (message: string) => void;
  // null means "something changed but there's no new headline to show" (a
  // "looks right" confirmation) — the caller still refetches but leaves the
  // existing corrected/confirmationText state alone.
  onCorrected: (confirmationText: string | null) => void;
  onReset: () => void;
}

export default function CarbCorrection({
  mealId,
  cropId,
  corrected,
  unsure,
  confirmationText,
  removedCount,
  correctedCount,
  onToast,
  onCorrected,
  onReset,
}: CarbCorrectionProps) {
  const [correcting, setCorrecting] = useState(false);
  const [direction, setDirection] = useState<CorrectionDirection | null>(null);
  const [leftoverOnly, setLeftoverOnly] = useState(false);
  const [magnitude, setMagnitude] = useState<CorrectionMagnitude | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const closePanel = useCallback(() => {
    setCorrecting(false);
    setDirection(null);
    setLeftoverOnly(false);
    setMagnitude(null);
    setError(null);
  }, []);

  const fireDirectionCorrection = useCallback(
    async (dir: 'too_high' | 'too_low', mag: CorrectionMagnitude, isLeftover: boolean) => {
      setSubmitting(true);
      setError(null);
      try {
        const res = await correctMacros({
          meal_id: mealId,
          crop_id: cropId,
          direction: dir,
          reason: isLeftover ? 'leftover' : 'portion',
          magnitude: mag,
        });
        onCorrected(buildConfirmationText(res));
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Couldn't save — try again";
        setError(message);
        // Revert the selection rather than leaving a chip highlighted for a
        // correction that never actually applied (GlucosePad's pattern).
        setMagnitude(null);
      } finally {
        setSubmitting(false);
      }
    },
    [mealId, cropId, onCorrected]
  );

  const handleDirectionPress = useCallback(
    (dir: CorrectionDirection) => {
      if (submitting) return;
      setError(null);
      if (dir === 'looks_right') {
        setDirection('looks_right');
        setLeftoverOnly(false);
        setMagnitude(null);
        void (async () => {
          setSubmitting(true);
          try {
            await correctMacros({ meal_id: mealId, crop_id: cropId, direction: 'looks_right' });
            onCorrected(null);
            closePanel();
          } catch (err) {
            const message = err instanceof ApiError ? err.message : "Couldn't save — try again";
            setError(message);
            setDirection(null);
          } finally {
            setSubmitting(false);
          }
        })();
        return;
      }
      setDirection(dir);
      setLeftoverOnly(false);
      setMagnitude(null);
    },
    [submitting, mealId, cropId, onCorrected, closePanel]
  );

  const handleLeftoverToggle = useCallback(() => {
    // Toggling the checkbox only updates local state — the correction isn't
    // submitted until a magnitude is also chosen (handleMagnitudePress).
    // Firing here too would send two separate persisted corrections for one
    // user gesture, which double- (or with any back-and-forth, multi-)
    // counts as evidence toward FOOD-020's learned portion prior.
    if (submitting || direction !== 'too_high') return;
    setLeftoverOnly((v) => !v);
  }, [submitting, direction]);

  const handleMagnitudePress = useCallback(
    (mag: CorrectionMagnitude) => {
      if (submitting || direction === null || direction === 'looks_right') return;
      setMagnitude(mag);
      void fireDirectionCorrection(direction, mag, leftoverOnly);
    },
    [submitting, direction, leftoverOnly, fireDirectionCorrection]
  );

  const handleUndoAll = useCallback(async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await resetCorrections(mealId, cropId);
      onReset();
      closePanel();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Couldn't undo — try again";
      onToast(message);
    } finally {
      setSubmitting(false);
    }
  }, [submitting, mealId, cropId, onReset, onToast, closePanel]);

  return (
    <View>
      <TouchableOpacity
        style={[
          styles.entryRow,
          corrected && styles.entryRowCorrected,
          !corrected && unsure && styles.entryRowUnsure,
        ]}
        onPress={() => setCorrecting(true)}
        activeOpacity={0.8}
      >
        <View
          style={[
            styles.entryIcon,
            corrected && styles.entryIconCorrected,
            !corrected && unsure && styles.entryIconUnsure,
          ]}
        >
          <Ionicons
            name="pencil"
            size={14}
            color={corrected || unsure ? '#fff' : defaultPalette.inkSoft}
          />
        </View>
        <Text
          style={[
            styles.entryText,
            corrected && styles.entryTextCorrected,
            !corrected && unsure && styles.entryTextUnsure,
          ]}
        >
          {corrected ? 'Edit your correction' : unsure ? 'Fix the estimate' : 'Estimate look off?'}
        </Text>
        <Ionicons
          name="arrow-forward"
          size={14}
          color={corrected ? defaultPalette.brand : unsure ? defaultPalette.warn.deep : defaultPalette.inkFaint}
        />
      </TouchableOpacity>

      {correcting && (
        <View style={styles.panel}>
          <View style={styles.panelHeader}>
            <Text style={styles.panelTitle}>Do the carbs look right?</Text>
            <TouchableOpacity onPress={closePanel} disabled={submitting} hitSlop={8}>
              <Ionicons name="close" size={18} color={defaultPalette.inkSoft} />
            </TouchableOpacity>
          </View>

          <View style={styles.segmentedRow}>
            {(['too_high', 'looks_right', 'too_low'] as CorrectionDirection[]).map((dir) => {
              const selected = direction === dir;
              const label = dir === 'too_high' ? 'Too high' : dir === 'too_low' ? 'Too low' : 'Looks right';
              return (
                <TouchableOpacity
                  key={dir}
                  style={[styles.segmentButton, selected && styles.segmentButtonSelected]}
                  onPress={() => handleDirectionPress(dir)}
                  disabled={submitting}
                  activeOpacity={0.8}
                >
                  <Text style={[styles.segmentButtonText, selected && styles.segmentButtonTextSelected]}>
                    {label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {(direction === 'too_high' || direction === 'too_low') && (
            <>
              {direction === 'too_high' && (
                <TouchableOpacity
                  style={styles.checkboxRow}
                  onPress={handleLeftoverToggle}
                  disabled={submitting}
                  activeOpacity={0.8}
                >
                  <View style={[styles.checkboxBox, leftoverOnly && styles.checkboxBoxChecked]}>
                    {leftoverOnly && <Ionicons name="checkmark" size={12} color="#fff" />}
                  </View>
                  <Text style={styles.checkboxLabel}>Just this meal — I'm not finishing it</Text>
                </TouchableOpacity>
              )}

              <Text style={styles.sectionLabel}>By how much?</Text>
              <View style={styles.chipRow}>
                {MAGNITUDES.map((opt) => {
                  const selected = magnitude === opt.key;
                  return (
                    <TouchableOpacity
                      key={opt.key}
                      style={[styles.chip, selected && styles.chipSelected]}
                      onPress={() => handleMagnitudePress(opt.key)}
                      disabled={submitting}
                      activeOpacity={0.8}
                    >
                      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt.label}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            </>
          )}

          {submitting && (
            <View style={styles.inlineLoading}>
              <ActivityIndicator size="small" color={defaultPalette.brand} />
            </View>
          )}

          {error && <Text style={styles.errorText}>{error}</Text>}

          {!submitting && confirmationText && (
            <View style={styles.confirmationRow}>
              <Ionicons name="sparkles" size={13} color={defaultPalette.brand} />
              <Text style={styles.confirmationText}>{confirmationText}</Text>
            </View>
          )}
          {!submitting && removedCount > 0 && (
            <View style={styles.confirmationRow}>
              <Ionicons name="sparkles" size={13} color={defaultPalette.brand} />
              <Text style={styles.confirmationText}>
                {removedCount} ingredient{removedCount > 1 ? 's' : ''} removed · projection updated
              </Text>
            </View>
          )}
          {!submitting && correctedCount > 0 && (
            <View style={styles.confirmationRow}>
              <Ionicons name="sparkles" size={13} color={defaultPalette.brand} />
              <Text style={styles.confirmationText}>
                {correctedCount} ingredient{correctedCount > 1 ? 's' : ''} corrected · projection updated
              </Text>
            </View>
          )}

          {corrected && (
            <View style={styles.footerRow}>
              <TouchableOpacity onPress={handleUndoAll} disabled={submitting}>
                <Text style={styles.undoText}>Undo all corrections</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  entryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: 44,
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: defaultPalette.hair,
  },
  entryRowCorrected: {},
  entryRowUnsure: {},
  entryIcon: {
    width: 30,
    height: 30,
    borderRadius: radius.sm,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  entryIconCorrected: {
    backgroundColor: defaultPalette.brand,
  },
  entryIconUnsure: {
    backgroundColor: defaultPalette.warn.deep,
  },
  entryText: {
    flex: 1,
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkSoft,
  },
  entryTextCorrected: {
    color: defaultPalette.brand,
    fontWeight: fontWeight.semibold,
  },
  entryTextUnsure: {
    color: defaultPalette.warn.deep,
    fontWeight: fontWeight.semibold,
  },
  panel: {
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
  },
  panelHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  panelTitle: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
    color: defaultPalette.ink,
  },
  segmentedRow: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  segmentButton: {
    flex: 1,
    borderRadius: radius.md,
    paddingVertical: 9,
    paddingHorizontal: 4,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    backgroundColor: defaultPalette.surface,
    alignItems: 'center',
  },
  segmentButtonSelected: {
    backgroundColor: defaultPalette.brand,
    borderColor: defaultPalette.brand,
  },
  segmentButtonText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkSoft,
  },
  segmentButtonTextSelected: {
    color: '#fff',
  },
  sectionLabel: {
    fontSize: fontSize.xs,
    color: defaultPalette.inkFaint,
    marginTop: spacing.sm,
    marginBottom: spacing.xs,
  },
  checkboxRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.sm,
    gap: spacing.xs,
  },
  checkboxBox: {
    width: 18,
    height: 18,
    borderRadius: 4,
    borderWidth: 1.5,
    borderColor: defaultPalette.hair,
    backgroundColor: defaultPalette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkboxBoxChecked: {
    backgroundColor: defaultPalette.brand,
    borderColor: defaultPalette.brand,
  },
  checkboxLabel: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkSoft,
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  chip: {
    borderRadius: radius.full,
    paddingVertical: 7,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    backgroundColor: defaultPalette.surface,
  },
  chipSelected: {
    backgroundColor: defaultPalette.brand,
    borderColor: defaultPalette.brand,
  },
  chipText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkSoft,
  },
  chipTextSelected: {
    color: '#fff',
  },
  inlineLoading: {
    marginTop: spacing.sm,
    alignItems: 'center',
  },
  errorText: {
    marginTop: spacing.sm,
    fontSize: fontSize.xs,
    color: defaultPalette.warn.fg,
  },
  confirmationRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.sm,
    gap: spacing.xs / 2,
  },
  confirmationText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.brand,
  },
  footerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'flex-end',
    marginTop: spacing.md,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: defaultPalette.hair,
  },
  undoText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkFaint,
  },
});
