import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, useWindowDimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useNavigation, useRoute } from '@react-navigation/native';
import { VictoryChart, VictoryLine, VictoryArea, VictoryAxis, VictoryScatter } from 'victory-native';
import { Ionicons } from '@expo/vector-icons';

import { defaultPalette, verdictColor, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { useGlucoseStore, useHistoryStore } from '../store';
import { formatDishName } from '../store/types';

const CHART_HEIGHT = 186;
const MIN_READINGS_FOR_SUMMARY = 3;

function formatElapsed(mealTimestamp: string): string {
  const elapsedMs = Date.now() - new Date(mealTimestamp).getTime();
  const totalMin = Math.max(0, Math.round(elapsedMs / 60_000));
  if (totalMin < 1) return 'just now';
  if (totalMin < 60) return `${totalMin} min ago`;
  const hrs = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  return mins > 0 ? `${hrs} hr ${mins} min ago` : `${hrs} hr ago`;
}

function formatLogTime(mealTimestamp: string): string {
  return new Date(mealTimestamp).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export default function PostMealTrackingScreen() {
  const navigation = useNavigation();
  const route = useRoute();
  const { mealId } = route.params as { mealId: string };

  // VictoryChart defaults to width 450 when unset, which overflows the
  // chartCard on any phone narrower than ~482px — give it the card's actual
  // content width instead.
  const { width: screenWidth } = useWindowDimensions();
  const chartWidth = screenWidth - spacing.md * 2;

  const loggedMeal = useHistoryStore((s) => s.meals.find((m) => m.meal_id === mealId));
  const dishName = loggedMeal?.dishes[0]?.name ?? 'Unknown dish';
  const mealTimestamp = loggedMeal?.meal_timestamp ?? null;

  const prediction = useGlucoseStore((s) => s.prediction);
  const verdict = useGlucoseStore((s) => s.verdict);
  const readings = useGlucoseStore((s) => s.readings);
  const actuals = useGlucoseStore((s) => s.actuals);
  const pollingActive = useGlucoseStore((s) => s.pollingActive);
  const stopPolling = useGlucoseStore((s) => s.stopPolling);

  const [, forceTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => forceTick((n) => n + 1), 60_000);
    return () => clearInterval(timer);
  }, []);

  const colors = verdictColor(verdict);

  const curveData = prediction?.curve.map((p) => ({ x: p.minutes, y: p.predicted_bg })) ?? [];
  const bandData = prediction?.curve.map((p) => ({
    x: p.minutes,
    y: p.confidence_upper,
    y0: p.confidence_lower,
  })) ?? [];
  const actualData = readings.map((r) => ({ x: r.minutes, y: r.glucose_mgdl }));

  const showSummary = readings.length >= MIN_READINGS_FOR_SUMMARY && !!actuals;

  const handleDone = () => {
    if (pollingActive) stopPolling();
    (navigation.getParent() as { navigate: (name: string, params?: object) => void } | undefined)
      ?.navigate('MainTabs', { screen: 'HomeTab', params: { screen: 'Home' } });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.dishName} numberOfLines={1}>{formatDishName(dishName)}</Text>
        {mealTimestamp && (
          <Text style={styles.subhead}>
            Logged {formatLogTime(mealTimestamp)} · {formatElapsed(mealTimestamp)}
          </Text>
        )}
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.chartCard}>
          <VictoryChart width={chartWidth} height={CHART_HEIGHT} padding={{ top: 12, bottom: 28, left: 40, right: 12 }}>
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

        <View style={[styles.statusChip, { backgroundColor: pollingActive ? colors.tint : defaultPalette.surfaceSoft }]}>
          <View style={[styles.statusDot, { backgroundColor: pollingActive ? colors.deep : defaultPalette.inkFaint }]} />
          <Text style={[styles.statusText, { color: pollingActive ? colors.deep : defaultPalette.inkSoft }]}>
            {pollingActive ? 'Tracking · updates every 5 min' : 'Tracking complete'}
          </Text>
        </View>

        {prediction?.model_confidence === 'low' && (
          <View style={styles.glucoseLowConfidenceNotice}>
            <Ionicons name="alert-circle-outline" size={14} color={defaultPalette.warn.fg} />
            <Text style={styles.glucoseLowConfidenceText}>
              This projection is a rough estimate — these macros are outside what the model has learned from so far.
            </Text>
          </View>
        )}

        {showSummary ? (
          <View style={styles.statStrip}>
            <View style={styles.statItem}>
              <Text style={styles.statValue}>{Math.round(actuals!.actual_peak_bg)}</Text>
              <Text style={styles.statLabel}>Actual peak (mg/dL)</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statValue}>{actuals!.time_to_peak_minutes}</Text>
              <Text style={styles.statLabel}>Time to peak (min)</Text>
            </View>
            <View style={styles.statItem}>
              <Text style={styles.statValue}>{Math.round(actuals!.tir_ratio * 100)}%</Text>
              <Text style={styles.statLabel}>Time in range</Text>
            </View>
          </View>
        ) : (
          <View style={styles.waitingCard}>
            <Text style={styles.waitingText}>Waiting for enough readings to summarize…</Text>
          </View>
        )}
      </ScrollView>

      <View style={styles.footer}>
        <TouchableOpacity style={styles.doneButton} onPress={handleDone} activeOpacity={0.85}>
          <Text style={styles.doneButtonText}>Done</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: defaultPalette.canvas,
  },
  header: {
    paddingHorizontal: spacing.md,
    paddingTop: spacing.sm,
    paddingBottom: spacing.md,
  },
  dishName: {
    color: defaultPalette.ink,
    fontSize: fontSize.xl,
    fontWeight: fontWeight.semibold,
  },
  subhead: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    marginTop: 2,
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  chartCard: {
    backgroundColor: defaultPalette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    overflow: 'hidden',
    marginBottom: spacing.md,
    paddingVertical: spacing.xs,
  },
  statusChip: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    borderRadius: radius.full,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    marginBottom: spacing.md,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: radius.full,
    marginRight: spacing.sm,
  },
  glucoseLowConfidenceNotice: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: spacing.xs,
    backgroundColor: defaultPalette.warn.tint,
    borderWidth: 1,
    borderColor: defaultPalette.warn.ring,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  glucoseLowConfidenceText: {
    flex: 1,
    fontSize: fontSize.xs,
    color: defaultPalette.warn.deep,
  },
  statusText: {
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
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
    textAlign: 'center',
  },
  waitingCard: {
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  waitingText: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    textAlign: 'center',
  },
  footer: {
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.lg,
  },
  doneButton: {
    height: 48,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
  doneButtonText: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
});
