import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import Svg, { Polyline } from 'react-native-svg';
import { useNavigation } from '@react-navigation/native';

import { defaultPalette, verdictColor, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { useHistoryStore } from '../store';
import { verdictWord, type LoggedMeal } from '../store/types';

const USER_NAME = 'Mina';

function greetingForNow(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function relativeTime(isoTimestamp: string): string {
  const diffMs = Date.now() - new Date(isoTimestamp).getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

// Flat placeholder line — LoggedMeal does not carry a glucose curve (that
// lives in glucoseStore, keyed by the currently-tracked meal only).
const FLAT_SPARKLINE_POINTS = Array.from({ length: 8 }, (_, i) => `${i * 8},14`).join(' ');

function Sparkline({ color }: { color: string }) {
  return (
    <Svg width={56} height={28}>
      <Polyline
        points={FLAT_SPARKLINE_POINTS}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}

function MealRow({ meal }: { meal: LoggedMeal }) {
  const dishName = meal.dishes[0]?.name ?? 'Unknown dish';
  const color = verdictColor(meal.verdict);

  return (
    <View style={styles.mealRow}>
      <View style={styles.thumbnail} />
      <View style={styles.mealInfo}>
        <Text style={styles.mealName} numberOfLines={1}>{dishName}</Text>
        <Text style={styles.mealTime}>{relativeTime(meal.meal_timestamp)}</Text>
      </View>
      <View style={[styles.verdictChip, { backgroundColor: color.tint }]}>
        <Text style={[styles.verdictChipText, { color: color.deep }]}>{verdictWord(meal.verdict)}</Text>
      </View>
      <Sparkline color={color.fg} />
    </View>
  );
}

export default function HomeScreen() {
  const navigation = useNavigation();
  const meals = useHistoryStore((s) => s.meals);

  const goToCamera = () => navigation.getParent()?.navigate('CameraModal' as never);
  const goToLogTab = () => navigation.getParent()?.navigate('LogTab' as never);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
      <View style={styles.topBar}>
        <View>
          <Text style={styles.greeting}>{greetingForNow()}, {USER_NAME}</Text>
          <Text style={styles.wordmark}>
            Sik<Text style={{ color: defaultPalette.brand }}>Fan</Text>
          </Text>
        </View>
        <View style={styles.avatar}>
          <Text style={styles.avatarInitial}>{USER_NAME[0]}</Text>
        </View>
      </View>

      <TouchableOpacity style={styles.scanCta} onPress={goToCamera} activeOpacity={0.85}>
        <View style={styles.scanIconCircle}>
          <Ionicons name="camera" size={22} color={defaultPalette.brand} />
        </View>
        <View style={styles.scanTextGroup}>
          <Text style={styles.scanTitle}>Scan a meal</Text>
          <Text style={styles.scanSubtitle}>See its blood-sugar impact before you eat</Text>
        </View>
      </TouchableOpacity>

      <View style={styles.sectionHeaderRow}>
        <TouchableOpacity onPress={goToLogTab}>
          <Text style={styles.sectionHeader}>Meal log</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={goToLogTab}>
          <Text style={styles.seeAll}>See all</Text>
        </TouchableOpacity>
      </View>

      {meals.length === 0 ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyStateText}>Scan your first meal to see it here.</Text>
        </View>
      ) : (
        meals.map((meal) => <MealRow key={meal.meal_id} meal={meal} />)
      )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: defaultPalette.canvas,
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  topBar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.lg,
  },
  greeting: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    marginBottom: spacing.xs,
  },
  wordmark: {
    color: defaultPalette.ink,
    fontSize: fontSize.xxl,
    fontWeight: fontWeight.bold,
  },
  avatar: {
    width: 40,
    height: 40,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarInitial: {
    color: '#fff',
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
  },
  scanCta: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: defaultPalette.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
    padding: spacing.md,
    marginBottom: spacing.lg,
    shadowColor: defaultPalette.shadow,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 1,
    shadowRadius: 8,
    elevation: 2,
  },
  scanIconCircle: {
    width: 44,
    height: 44,
    borderRadius: radius.full,
    backgroundColor: defaultPalette.surfaceSoft,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.md,
  },
  scanTextGroup: {
    flex: 1,
  },
  scanTitle: {
    color: defaultPalette.ink,
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
  },
  scanSubtitle: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    marginTop: spacing.xs / 2,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  sectionHeader: {
    color: defaultPalette.ink,
    fontSize: fontSize.lg,
    fontWeight: fontWeight.semibold,
  },
  seeAll: {
    color: defaultPalette.brand,
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
  },
  emptyState: {
    padding: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: defaultPalette.surfaceSoft,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
  },
  emptyStateText: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    textAlign: 'center',
  },
  mealRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: defaultPalette.hair,
  },
  thumbnail: {
    width: 48,
    height: 48,
    borderRadius: radius.md,
    backgroundColor: defaultPalette.surfaceSoft,
    borderWidth: 1,
    borderColor: defaultPalette.hair,
  },
  mealInfo: {
    flex: 1,
    marginLeft: spacing.sm,
    marginRight: spacing.sm,
  },
  mealName: {
    color: defaultPalette.ink,
    fontSize: fontSize.md,
    fontWeight: fontWeight.medium,
  },
  mealTime: {
    color: defaultPalette.inkFaint,
    fontSize: fontSize.xs,
    marginTop: 2,
  },
  verdictChip: {
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs / 2,
    marginRight: spacing.sm,
  },
  verdictChipText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.semibold,
  },
});
