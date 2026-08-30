import React, { useCallback, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { correctMacros, resetCorrections } from '../api/meals';
import { ApiError } from '../api/client';
import type { CorrectionDirection, CorrectionMagnitude, CorrectionReason, CorrectMacrosResponse } from '../api/types';

// API-013's reason enum is shared across directions; only the label (and
// which reasons are offered) changes with direction — portion/broth/leftover
// for "too high", portion/hidden for "too low" (FOOD-020 PERSISTED_REASONS /
// COUNTED_REASONS).
const REASONS_BY_DIRECTION: Record<'too_high' | 'too_low', { key: CorrectionReason; label: string }[]> = {
  too_high: [
    { key: 'portion', label: 'Portion was smaller' },
    { key: 'broth', label: "It's mostly broth" },
    { key: 'leftover', label: 'Not eating the full portion' },
  ],
  too_low: [
    { key: 'portion', label: 'Portion was bigger' },
    { key: 'hidden', label: 'More food than it looks' },
  ],
};

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
  confirmationText: string | null;
  onToast: (message: string) => void;
  // null means "something changed but there's no new headline to show" (a
  // "looks right" confirmation) — the caller still refetches but leaves the
  // existing corrected/confirmationText state alone.
  onCorrected: (confirmationText: string | null) => void;
  onReset: () => void;
  onOpenFixMode: () => void;
}

export default function CarbCorrection({
  mealId,
  cropId,
  corrected,
  confirmationText,
  onToast,
  onCorrected,
  onReset,
  onOpenFixMode,
}: CarbCorrectionProps) {
  const [correcting, setCorrecting] = useState(false);
  const [direction, setDirection] = useState<CorrectionDirection | null>(null);
  const [reason, setReason] = useState<CorrectionReason | null>(null);
  const [magnitude, setMagnitude] = useState<CorrectionMagnitude | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const closePanel = useCallback(() => {
    setCorrecting(false);
    setDirection(null);
    setReason(null);
    setMagnitude(null);
    setError(null);
  }, []);

  const fireDirectionCorrection = useCallback(
    async (dir: 'too_high' | 'too_low', r: CorrectionReason, mag: CorrectionMagnitude) => {
      setSubmitting(true);
      setError(null);
      try {
        const res = await correctMacros({
          meal_id: mealId,
          crop_id: cropId,
          direction: dir,
          reason: r,
          magnitude: mag,
        });
        onCorrected(buildConfirmationText(res));
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Couldn't save — try again";
        setError(message);
        // Revert the selection rather than leaving a chip highlighted for a
        // correction that never actually applied (GlucosePad's pattern).
        setReason(null);
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
        setReason(null);
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
      setReason(null);
      setMagnitude(null);
    },
    [submitting, mealId, cropId, onCorrected, closePanel]
  );

  const handleReasonPress = useCallback(
    (r: CorrectionReason) => {
      if (submitting || direction === null || direction === 'looks_right') return;
      setReason(r);
      const mag = magnitude ?? 'little';
      setMagnitude(mag);
      void fireDirectionCorrection(direction, r, mag);
    },
    [submitting, direction, magnitude, fireDirectionCorrection]
  );

  const handleMagnitudePress = useCallback(
    (mag: CorrectionMagnitude) => {
      if (submitting || direction === null || direction === 'looks_right' || reason === null) return;
      setMagnitude(mag);
      void fireDirectionCorrection(direction, reason, mag);
    },
    [submitting, direction, reason, fireDirectionCorrection]
  );

  const handleFixIngredient = useCallback(() => {
    closePanel();
    onOpenFixMode();
  }, [closePanel, onOpenFixMode]);

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

  const reasonOptions =
    direction === 'too_high' || direction === 'too_low' ? REASONS_BY_DIRECTION[direction] : [];

  return (
    <View>
      <TouchableOpacity
        style={[styles.entryRow, corrected && styles.entryRowCorrected]}
        onPress={() => setCorrecting(true)}
        activeOpacity={0.8}
      >
        <View style={[styles.entryIcon, corrected && styles.entryIconCorrected]}>
          <Ionicons name="pencil" size={14} color={corrected ? '#fff' : defaultPalette.inkSoft} />
        </View>
        <Text style={[styles.entryText, corrected && styles.entryTextCorrected]}>
          {corrected ? 'Edit your correction' : 'Estimate look off?'}
        </Text>
        <Ionicons
          name="arrow-forward"
          size={14}
          color={corrected ? defaultPalette.brand : defaultPalette.inkFaint}
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

          {reasonOptions.length > 0 && (
            <>
              <Text style={styles.sectionLabel}>What's off?</Text>
              <View style={styles.chipRow}>
                {reasonOptions.map((opt) => {
                  const selected = reason === opt.key;
                  return (
                    <TouchableOpacity
                      key={opt.key}
                      style={[styles.chip, selected && styles.chipSelected]}
                      onPress={() => handleReasonPress(opt.key)}
                      disabled={submitting}
                      activeOpacity={0.8}
                    >
                      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{opt.label}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

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

          <View style={styles.footerRow}>
            <TouchableOpacity onPress={handleFixIngredient} disabled={submitting} style={styles.footerLink}>
              <Text style={styles.footerLinkText}>An ingredient is wrong or missing</Text>
              <Ionicons name="arrow-forward" size={13} color={defaultPalette.brand} />
            </TouchableOpacity>
            {corrected && (
              <TouchableOpacity onPress={handleUndoAll} disabled={submitting}>
                <Text style={styles.undoText}>Undo all</Text>
              </TouchableOpacity>
            )}
          </View>
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
    justifyContent: 'space-between',
    marginTop: spacing.md,
    paddingTop: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: defaultPalette.hair,
  },
  footerLink: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  footerLinkText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.brand,
  },
  undoText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    color: defaultPalette.inkFaint,
  },
});
