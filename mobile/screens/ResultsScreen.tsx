import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { VictoryChart, VictoryLine, VictoryArea, VictoryAxis } from 'victory-native';
import { useNavigation } from '@react-navigation/native';

import { defaultPalette, verdictColor, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { useMealStore, useGlucoseStore, useHistoryStore } from '../store';
import { formatPortion, verdictWord } from '../store/types';
import { analyzeGlucose } from '../api/glucose';

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

  const mealId = useMealStore((s) => s.mealId);
  const dishName = useMealStore((s) => s.dishName);
  const confidence = useMealStore((s) => s.confidence);
  const portion = useMealStore((s) => s.portion);
  const portionBucket = useMealStore((s) => s.portionBucket);
  const macros = useMealStore((s) => s.macros);
  const dishes = useMealStore((s) => s.dishes);
  const mealTimestamp = useMealStore((s) => s.mealTimestamp);

  const prediction = useGlucoseStore((s) => s.prediction);
  const verdict = useGlucoseStore((s) => s.verdict);
  const setPrediction = useGlucoseStore((s) => s.setPrediction);

  const addMeal = useHistoryStore((s) => s.addMeal);

  const [toastVisible, setToastVisible] = useState(false);

  useEffect(() => {
    if (!mealId || prediction) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await analyzeGlucose(mealId);
        if (!cancelled) {
          setPrediction(res.prediction, res.pre_meal_glucose, res.pre_meal_trend);
        }
      } catch {
        // Chart stays empty; user can still see dish/macro info.
      }
    })();
    return () => { cancelled = true; };
  }, [mealId, prediction]);

  const colors = verdictColor(verdict);

  const curveData = prediction?.curve.map((p) => ({ x: p.minutes, y: p.predicted_bg })) ?? [];
  const bandData = prediction?.curve.map((p) => ({
    x: p.minutes,
    y: p.confidence_upper,
    y0: p.confidence_lower,
  })) ?? [];

  const settles = prediction?.curve.length ? prediction.curve[prediction.curve.length - 1].predicted_bg : null;

  const macroMax = macros ? Math.max(macros.carbs_g, macros.protein_g, macros.fat_g, macros.calories / 4) : 0;

  const handleLog = () => {
    if (!mealId) return;
    addMeal({
      meal_id: mealId,
      dishes,
      total_carbs_g: macros?.carbs_g ?? 0,
      image_url: null,
      meal_timestamp: mealTimestamp ?? new Date().toISOString(),
      verdict,
    });
    setToastVisible(true);
    setTimeout(() => {
      setToastVisible(false);
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
          <TouchableOpacity style={styles.editButton}>
            <Ionicons name="pencil" size={16} color={defaultPalette.inkSoft} />
          </TouchableOpacity>
        </View>

        <View style={[styles.verdictBanner, { backgroundColor: colors.tint }]}>
          <Ionicons name={VERDICT_ICON[verdict ?? 'steady']} size={20} color={colors.deep} />
          <Text style={[styles.verdictWord, { color: colors.deep }]}>{verdictWord(verdict)}</Text>
          {prediction && (
            <Text style={[styles.verdictDelta, { color: colors.fg }]}>
              {prediction.outcome.delta_from_baseline >= 0 ? '+' : ''}
              {Math.round(prediction.outcome.delta_from_baseline)}
            </Text>
          )}
        </View>

        {prediction && (
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
            </VictoryChart>
          </View>
        )}

        {prediction && (
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
          <TouchableOpacity style={styles.logButton} onPress={handleLog} activeOpacity={0.85}>
            <Text style={styles.logButtonText}>Log this meal</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      {toastVisible && (
        <View style={styles.toast}>
          <Text style={styles.toastText}>Logged to your day</Text>
        </View>
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
