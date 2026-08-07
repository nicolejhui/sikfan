import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { VictoryChart, VictoryLine, VictoryArea, VictoryAxis, VictoryScatter } from 'victory-native';
import type { BottomSheetModal } from '@gorhom/bottom-sheet';
import { useNavigation, useRoute } from '@react-navigation/native';

import { defaultPalette, verdictColor, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { useMealStore, useGlucoseStore, useHistoryStore } from '../store';
import { formatPortion, verdictWord } from '../store/types';
import ConfirmDishSheet from '../components/ConfirmDishSheet';
import GlucosePad from '../components/GlucosePad';

function sumMacros(dishes: { carbs_g: number; protein_g: number; fat_g: number; calories: number }[]) {
  return dishes.reduce(
    (acc, d) => ({
      carbs_g: acc.carbs_g + d.carbs_g,
      protein_g: acc.protein_g + d.protein_g,
      fat_g: acc.fat_g + d.fat_g,
      calories: acc.calories + d.calories,
    }),
    { carbs_g: 0, protein_g: 0, fat_g: 0, calories: 0 }
  );
}

const CHART_HEIGHT = 186;

const VERDICT_ICON: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  spike: 'arrow-up',
  steady: 'pulse',
  drop: 'arrow-down',
};

function MacroBar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <View style={styles.macroRow}>
      <Text style={styles.macroLabel}>{label}</Text>
      <View style={styles.macroTrack}>
        <View style={[styles.macroFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
      <Text style={styles.macroValue}>{Math.round(value)}</Text>
    </View>
  );
}

export default function ResultsScreen() {
  const navigation = useNavigation();
  const route = useRoute();
  const routeMealId = (route.params as { mealId?: string } | undefined)?.mealId;

  const scanMealId = useMealStore((s) => s.mealId);
  const scanDishName = useMealStore((s) => s.dishName);
  const scanConfidence = useMealStore((s) => s.confidence);
  const scanPortion = useMealStore((s) => s.portion);
  const scanPortionBucket = useMealStore((s) => s.portionBucket);
  const scanMacros = useMealStore((s) => s.macros);
  const scanDishes = useMealStore((s) => s.dishes);
  const scanMealTimestamp = useMealStore((s) => s.mealTimestamp);

  // A meal opened from the Meal Log grid carries its own mealId in route
  // params; pull its data from historyStore instead of the in-progress scan.
  const loggedMeal = useHistoryStore((s) =>
    routeMealId ? s.meals.find((m) => m.meal_id === routeMealId) : undefined
  );

  const mealId = loggedMeal ? loggedMeal.meal_id : scanMealId;
  const primaryDish = loggedMeal ? loggedMeal.dishes[0] : scanDishes[0];
  const dishName = loggedMeal ? primaryDish?.name ?? null : scanDishName;
  const confidence = loggedMeal ? primaryDish?.confidence ?? null : scanConfidence;
  const portion = loggedMeal ? primaryDish?.portion_g ?? null : scanPortion;
  const portionBucket = loggedMeal ? primaryDish?.portion_bucket ?? null : scanPortionBucket;
  const macros = loggedMeal ? sumMacros(loggedMeal.dishes) : scanMacros;
  const dishes = loggedMeal ? loggedMeal.dishes : scanDishes;
  const mealTimestamp = loggedMeal ? loggedMeal.meal_timestamp : scanMealTimestamp;

  const prediction = useGlucoseStore((s) => s.prediction);
  const storeVerdict = useGlucoseStore((s) => s.verdict);
  const predictionStatus = useGlucoseStore((s) => s.predictionStatus);
  const fetchPrediction = useGlucoseStore((s) => s.fetchPrediction);
  const preMealGlucose = useGlucoseStore((s) => s.preMealGlucose);
  const submitManualGlucose = useGlucoseStore((s) => s.submitManualGlucose);
  const readings = useGlucoseStore((s) => s.readings);
  const verdict = loggedMeal ? loggedMeal.verdict : storeVerdict;

  const addMeal = useHistoryStore((s) => s.addMeal);
  const updateHistoryDishName = useHistoryStore((s) => s.updateDishName);
  const updateScanDishName = useMealStore((s) => s.updateDishName);

  const [toast, setToast] = useState<string | null>(null);
  const showToast = (message: string, duration = 1300) => {
    setToast(message);
    setTimeout(() => setToast(null), duration);
  };

  const cropId = primaryDish?.crop_id ?? null;
  const canEdit = !!mealId && !!cropId;
  const sheetRef = useRef<BottomSheetModal>(null);

  // Guards against double-tapping "Log this meal": the ref catches a second
  // tap landing before the first commit re-renders (synchronous, unlike
  // state); the selector catches re-visiting an already-logged meal's screen.
  const hasLoggedRef = useRef(false);
  const alreadyLogged = useHistoryStore((s) => !!mealId && s.meals.some((m) => m.meal_id === mealId));

  const handleCorrected = (correctedName: string) => {
    if (!cropId) return;
    if (loggedMeal) {
      updateHistoryDishName(loggedMeal.meal_id, cropId, correctedName);
    } else {
      updateScanDishName(cropId, correctedName);
    }
  };

  useEffect(() => {
    if (!mealId) return;
    void fetchPrediction(mealId);
  }, [mealId]);

  const glucosePadRef = useRef<BottomSheetModal>(null);
  const handleManualGlucoseSet = async (value: number) => {
    if (!mealId) return;
    await submitManualGlucose(mealId, value);
  };

  const colors = verdictColor(verdict);

  const curveData = prediction?.curve.map((p) => ({ x: p.minutes, y: p.predicted_bg })) ?? [];
  const bandData = prediction?.curve.map((p) => ({
    x: p.minutes,
    y: p.confidence_upper,
    y0: p.confidence_lower,
  })) ?? [];

  const settles = prediction?.curve.length ? prediction.curve[prediction.curve.length - 1].predicted_bg : null;

  const actualData = readings.map((r) => ({ x: r.minutes, y: r.glucose_mgdl }));

  const macroMax = macros ? Math.max(macros.carbs_g, macros.protein_g, macros.fat_g, macros.calories / 4) : 0;

  const handleLog = () => {
    if (!mealId || hasLoggedRef.current || alreadyLogged) return;
    hasLoggedRef.current = true;
    addMeal({
      meal_id: mealId,
      dishes,
      total_carbs_g: macros?.carbs_g ?? 0,
      image_url: null,
      meal_timestamp: mealTimestamp ?? new Date().toISOString(),
      verdict,
    });
    showToast('Logged to your day');
    setTimeout(() => {
      (navigation.getParent() as { navigate: (name: string, params?: object) => void } | undefined)
        ?.navigate('MainTabs', { screen: 'LogTab', params: { screen: 'MealLog' } });
    }, 1300);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <LinearGradient
        colors={[colors.tint, 'transparent']}
        style={styles.topGradient}
        pointerEvents="none"
      />

      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => navigation.goBack()} style={styles.backButton}>
          <Ionicons name="chevron-back" size={22} color={defaultPalette.ink} />
        </TouchableOpacity>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.dishHeader}>
          <View style={styles.thumbnail} />
          <View style={styles.dishInfo}>
            <Text style={styles.dishName} numberOfLines={1}>{dishName ?? 'Unknown dish'}</Text>
            <View style={styles.confidenceRow}>
              {confidence != null && (
                <View style={styles.confidenceBadge}>
                  <Ionicons name="sparkles" size={11} color={defaultPalette.brand} />
                  <Text style={styles.confidenceText}>{Math.round(confidence * 100)}%</Text>
                </View>
              )}
              {!!formatPortion(portionBucket, portion) && (
                <Text style={styles.portionLabel}>{formatPortion(portionBucket, portion)}</Text>
              )}
            </View>
          </View>
          <TouchableOpacity
            style={[styles.editButton, !canEdit && styles.editButtonDisabled]}
            onPress={() => sheetRef.current?.present()}
            disabled={!canEdit}
          >
            <Ionicons name="pencil" size={16} color={defaultPalette.inkSoft} />
          </TouchableOpacity>
        </View>

        {/* MOB-012: no glucose UI at all until an anchor exists (real CGM,
            found automatically, or manual) — nothing dimmed/placeholder'd,
            genuinely absent. Macro breakdown and dish header above are never
            gated by this. */}
        {predictionStatus === 'needs_manual_entry' && (
          <TouchableOpacity style={styles.bloodSugarCta} onPress={() => glucosePadRef.current?.present()} activeOpacity={0.85}>
            <View style={styles.bloodSugarCtaIcon}>
              <Ionicons name="water" size={18} color="#fff" />
            </View>
            <View style={styles.bloodSugarCtaText}>
              <Text style={styles.bloodSugarCtaTitle}>Add your blood sugar</Text>
              <Text style={styles.bloodSugarCtaSubtitle}>Anchor this projection to your current reading</Text>
            </View>
            <Text style={styles.bloodSugarPlaceholder}>– – –</Text>
          </TouchableOpacity>
        )}

        {predictionStatus === 'error' && (
          <View style={styles.predictionErrorCard}>
            <Text style={styles.predictionErrorText}>Couldn't load glucose prediction. Try again shortly.</Text>
          </View>
        )}

        {predictionStatus === 'ready' && prediction && (
          <>
            {preMealGlucose != null && (
              <TouchableOpacity style={styles.bloodSugarSet} onPress={() => glucosePadRef.current?.present()} activeOpacity={0.85}>
                <View>
                  <Text style={styles.bloodSugarValue}>{preMealGlucose} mg/dL</Text>
                  <Text style={styles.bloodSugarSetLabel}>Anchored to your reading</Text>
                </View>
                <Ionicons name="pencil" size={16} color={defaultPalette.inkSoft} />
              </TouchableOpacity>
            )}

            <View style={[styles.verdictBanner, { backgroundColor: colors.tint }]}>
              <Ionicons name={VERDICT_ICON[verdict ?? 'steady']} size={20} color={colors.deep} />
              <Text style={[styles.verdictWord, { color: colors.deep }]}>{verdictWord(verdict)}</Text>
              <Text style={[styles.verdictDelta, { color: colors.fg }]}>
                {prediction.outcome.delta_from_baseline >= 0 ? '+' : ''}
                {Math.round(prediction.outcome.delta_from_baseline)}
              </Text>
            </View>

            <View style={styles.chartCard}>
              <VictoryChart height={CHART_HEIGHT} padding={{ top: 12, bottom: 28, left: 40, right: 12 }}>
                <VictoryAxis
                  dependentAxis
                  style={{ axis: { stroke: 'transparent' }, tickLabels: { fontSize: 10, fill: defaultPalette.inkFaint } }}
                />
                <VictoryAxis
                  tickValues={[0, 60, 120, 180]}
                  style={{ axis: { stroke: defaultPalette.hair }, tickLabels: { fontSize: 10, fill: defaultPalette.inkFaint } }}
                />
                <VictoryArea
                  data={bandData}
                  style={{ data: { fill: colors.fg, fillOpacity: 0.2, stroke: 'transparent' } }}
                />
                <VictoryLine
                  data={curveData}
                  style={{ data: { stroke: colors.fg, strokeWidth: 2 } }}
                />
                {readings.length > 0 && (
                  <VictoryLine
                    data={actualData}
                    style={{ data: { stroke: colors.deep, strokeWidth: 3 } }}
                  />
                )}
                {readings.length > 0 && (
                  <VictoryScatter
                    data={actualData}
                    size={3}
                    style={{ data: { fill: colors.deep } }}
                  />
                )}
              </VictoryChart>
            </View>

            <View style={styles.statStrip}>
              <View style={styles.statItem}>
                <Text style={styles.statValue}>{Math.round(prediction.predicted_peak_bg)}</Text>
                <Text style={styles.statLabel}>Peak (mg/dL)</Text>
              </View>
              <View style={styles.statItem}>
                <Text style={styles.statValue}>{prediction.predicted_time_to_peak_minutes}</Text>
                <Text style={styles.statLabel}>Peak at (min)</Text>
              </View>
              <View style={styles.statItem}>
                <Text style={styles.statValue}>{settles != null ? Math.round(settles) : '—'}</Text>
                <Text style={styles.statLabel}>Settles (mg/dL)</Text>
              </View>
            </View>
          </>
        )}

        {macros && (
          <View style={styles.macrosCard}>
            <Text style={styles.macrosTitle}>Macros</Text>
            <MacroBar label="Carbs" value={macros.carbs_g} max={macroMax} color={defaultPalette.brand} />
            <MacroBar label="Protein" value={macros.protein_g} max={macroMax} color={defaultPalette.good.fg} />
            <MacroBar label="Fat" value={macros.fat_g} max={macroMax} color={defaultPalette.warn.fg} />
            <MacroBar label="Calories" value={macros.calories} max={macroMax * 4} color={defaultPalette.low.fg} />
          </View>
        )}

        <View style={styles.actionsRow}>
          <TouchableOpacity style={styles.boltButton}>
            <Ionicons name="flash-outline" size={20} color={defaultPalette.inkSoft} />
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.logButton, alreadyLogged && styles.logButtonDisabled]}
            onPress={handleLog}
            activeOpacity={0.85}
            disabled={alreadyLogged}
          >
            <Text style={styles.logButtonText}>{alreadyLogged ? 'Logged' : 'Log this meal'}</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      {toast && (
        <View style={styles.toast}>
          <Text style={styles.toastText}>{toast}</Text>
        </View>
      )}

      {mealId && cropId && (
        <ConfirmDishSheet
          sheetRef={sheetRef}
          mealId={mealId}
          cropId={cropId}
          dishName={dishName}
          confidence={confidence}
          onToast={showToast}
          onCorrected={handleCorrected}
        />
      )}

      {mealId && (
        <GlucosePad
          sheetRef={glucosePadRef}
          initial={preMealGlucose}
          last={preMealGlucose ?? 0}
          onSet={handleManualGlucoseSet}
          onClose={() => {}}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: defaultPalette.canvas,
  },
  topGradient: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: 160,
  },
  topBar: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
  },
  backButton: {
    width: 36,
    height: 36,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  dishHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.sm,
    marginBottom: spacing.md,
  },
  thumbnail: {
    width: 56,
    height: 56,
    borderRadius: radius.md,
    backgroundColor: defaultPalette.surfaceSoft,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
  },
  dishInfo: {
    flex: 1,
    marginLeft: spacing.sm,
  },
  dishName: {
    color: defaultPalette.ink,
    fontSize: fontSize.xl,
    fontWeight: fontWeight.semibold,
  },
  confidenceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: spacing.xs / 2,
  },
  confidenceBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    marginRight: spacing.sm,
  },
  confidenceText: {
    color: defaultPalette.ink,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    marginLeft: 4,
  },
  portionLabel: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
  },
  editButton: {
    width: 32,
    height: 32,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
  },
  editButtonDisabled: {
    opacity: 0.4,
  },
  bloodSugarCta: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: defaultPalette.brand,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  bloodSugarCtaIcon: {
    width: 36,
    height: 36,
    borderRadius: radius.full,
    backgroundColor: 'rgba(255,255,255,0.2)',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  bloodSugarCtaText: {
    flex: 1,
  },
  bloodSugarCtaTitle: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
  bloodSugarCtaSubtitle: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: fontSize.xs,
    marginTop: 2,
  },
  bloodSugarPlaceholder: {
    color: 'rgba(255,255,255,0.6)',
    fontSize: fontSize.md,
    fontWeight: fontWeight.medium,
    letterSpacing: 2,
  },
  bloodSugarSet: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: defaultPalette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  bloodSugarValue: {
    color: defaultPalette.ink,
    fontSize: fontSize.xxl,
    fontWeight: fontWeight.bold,
  },
  bloodSugarSetLabel: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
    marginTop: 2,
  },
  predictionErrorCard: {
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  predictionErrorText: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    textAlign: 'center',
  },
  verdictBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  verdictWord: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
    marginLeft: spacing.sm,
    flex: 1,
  },
  verdictDelta: {
    fontSize: fontSize.lg,
    fontWeight: fontWeight.bold,
  },
  chartCard: {
    backgroundColor: defaultPalette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    marginBottom: spacing.md,
    paddingVertical: spacing.xs,
  },
  statStrip: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  statItem: {
    alignItems: 'center',
    flex: 1,
  },
  statValue: {
    color: defaultPalette.ink,
    fontSize: fontSize.lg,
    fontWeight: fontWeight.bold,
  },
  statLabel: {
    color: defaultPalette.inkFaint,
    fontSize: fontSize.xs,
    marginTop: 2,
  },
  macrosCard: {
    backgroundColor: defaultPalette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  macrosTitle: {
    color: defaultPalette.ink,
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    marginBottom: spacing.sm,
  },
  macroRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  macroLabel: {
    width: 60,
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
  },
  macroTrack: {
    flex: 1,
    height: 8,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surfaceSoft,
    overflow: 'hidden',
    marginHorizontal: spacing.sm,
  },
  macroFill: {
    height: '100%',
    borderRadius: radius.full,
  },
  macroValue: {
    width: 40,
    textAlign: 'right',
    color: defaultPalette.ink,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
  },
  actionsRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  boltButton: {
    width: 48,
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.sm,
  },
  logButton: {
    flex: 1,
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
  logButtonDisabled: {
    opacity: 0.5,
  },
  logButtonText: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
  toast: {
    position: 'absolute',
    bottom: 40,
    alignSelf: 'center',
    backgroundColor: defaultPalette.ink,
    borderRadius: radius.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  toastText: {
    color: '#fff',
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
  },
});
