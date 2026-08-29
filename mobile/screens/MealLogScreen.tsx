import React, { useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Image, useWindowDimensions } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useNavigation } from '@react-navigation/native';
import type { StackNavigationProp } from '@react-navigation/stack';

import { defaultPalette, verdictColor, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import { useHistoryStore } from '../store';
import { formatDishName, verdictWord, type LoggedMeal, type Verdict } from '../store/types';
import type { LogStackParamList } from '../navigation';
import { useMealThumbnail } from '../hooks/useMealThumbnail';

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

const VERDICT_ICON: Record<Verdict, React.ComponentProps<typeof Ionicons>['name']> = {
  spike: 'arrow-up',
  steady: 'pulse',
  drop: 'arrow-down',
};

const LEGEND_ITEMS: { verdict: Verdict; label: string }[] = [
  { verdict: 'steady', label: 'Stabilizes' },
  { verdict: 'spike', label: 'Spikes' },
  { verdict: 'drop', label: 'Drops' },
];

function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function dayLabel(isoTimestamp: string): string {
  const d = new Date(isoTimestamp);
  const diffDays = Math.round((startOfDay(new Date()).getTime() - startOfDay(d).getTime()) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

function timeLabel(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function groupByDay(meals: LoggedMeal[]): { label: string; meals: LoggedMeal[] }[] {
  const groups: { label: string; meals: LoggedMeal[] }[] = [];
  for (const meal of meals) {
    const label = dayLabel(meal.meal_timestamp);
    let group = groups.find((g) => g.label === label);
    if (!group) {
      group = { label, meals: [] };
      groups.push(group);
    }
    group.meals.push(meal);
  }
  return groups;
}

const GRID_GAP = 11;

function MealCard({ meal, isNew, onPress }: { meal: LoggedMeal; isNew: boolean; onPress: () => void }) {
  const dishName = meal.dishes[0]?.name ?? 'Unknown dish';
  const colors = verdictColor(meal.verdict);
  const thumbnailUri = useMealThumbnail(meal);
  const [thumbnailBroken, setThumbnailBroken] = useState(false);

  // width: '47%' + aspectRatio: 1 doesn't resolve a height here since every
  // child of `card` is absolutely positioned (no in-flow content for Yoga to
  // size against) — compute an explicit square size instead.
  const { width: screenWidth } = useWindowDimensions();
  const cardSize = (screenWidth - spacing.md * 2 - GRID_GAP) / 2;

  return (
    <TouchableOpacity style={[styles.card, { width: cardSize, height: cardSize }]} onPress={onPress} activeOpacity={0.85}>
      {thumbnailUri && !thumbnailBroken ? (
        <Image
          source={{ uri: thumbnailUri }}
          style={styles.cardPhoto}
          onError={() => setThumbnailBroken(true)}
        />
      ) : (
        <View style={styles.cardPhoto} />
      )}

      <View style={[styles.verdictChip, { backgroundColor: 'rgba(255,255,255,0.85)' }]}>
        <Ionicons name={VERDICT_ICON[meal.verdict ?? 'steady']} size={11} color={colors.deep} />
        <Text style={[styles.verdictChipText, { color: colors.deep }]}>{verdictWord(meal.verdict)}</Text>
      </View>

      {isNew && (
        <View style={styles.newBadge}>
          <Text style={styles.newBadgeText}>NEW</Text>
        </View>
      )}

      <LinearGradient
        colors={['transparent', 'rgba(20,18,16,0.82)']}
        style={styles.cardScrim}
        pointerEvents="none"
      >
        <Text style={styles.cardDishName} numberOfLines={1}>{formatDishName(dishName)}</Text>
        <Text style={styles.cardTime}>{timeLabel(meal.meal_timestamp)}</Text>
      </LinearGradient>
    </TouchableOpacity>
  );
}

export default function MealLogScreen() {
  const navigation = useNavigation<StackNavigationProp<LogStackParamList, 'MealLog'>>();
  const meals = useHistoryStore((s) => s.meals);

  const newestMealId = meals[0]?.meal_id ?? null;
  const weekCount = meals.filter(
    (m) => Date.now() - new Date(m.meal_timestamp).getTime() < WEEK_MS
  ).length;
  const groups = groupByDay(meals);

  const openMeal = (mealId: string) => {
    navigation.navigate('Results', { mealId });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Meal log</Text>
        <Text style={styles.subtitle}>
          {meals.length} {meals.length === 1 ? 'meal' : 'meals'} logged · {weekCount} this week
        </Text>

        <View style={styles.legendRow}>
          {LEGEND_ITEMS.map((item) => (
            <View key={item.verdict} style={styles.legendItem}>
              <View style={[styles.legendDot, { backgroundColor: verdictColor(item.verdict).fg }]} />
              <Text style={styles.legendLabel}>{item.label}</Text>
            </View>
          ))}
        </View>

        {meals.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyStateText}>Scan your first meal to see it here.</Text>
          </View>
        ) : (
          groups.map((group) => (
            <View key={group.label} style={styles.group}>
              <Text style={styles.groupHeader}>{group.label}</Text>
              <View style={styles.grid}>
                {group.meals.map((meal) => (
                  <MealCard
                    key={meal.meal_id}
                    meal={meal}
                    isNew={meal.meal_id === newestMealId}
                    onPress={() => openMeal(meal.meal_id)}
                  />
                ))}
              </View>
            </View>
          ))
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
  title: {
    color: defaultPalette.ink,
    fontSize: fontSize.xxl,
    fontWeight: fontWeight.bold,
  },
  subtitle: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.sm,
    marginTop: spacing.xs / 2,
    marginBottom: spacing.md,
  },
  legendRow: {
    flexDirection: 'row',
    marginBottom: spacing.lg,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    marginRight: spacing.md,
  },
  legendDot: {
    width: 8,
    height: 8,
    borderRadius: radius.full,
    marginRight: spacing.xs,
  },
  legendLabel: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
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
  group: {
    marginBottom: spacing.lg,
  },
  groupHeader: {
    color: defaultPalette.ink,
    fontSize: fontSize.md,
    fontWeight: fontWeight.semibold,
    marginBottom: spacing.sm,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: GRID_GAP,
  },
  card: {
    borderRadius: radius.lg,
    overflow: 'hidden',
    backgroundColor: defaultPalette.surfaceSoft,
  },
  cardPhoto: {
    ...StyleSheet.absoluteFill,
    backgroundColor: defaultPalette.surfaceSoft,
  },
  verdictChip: {
    position: 'absolute',
    top: spacing.sm,
    left: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  verdictChipText: {
    fontSize: fontSize.xs,
    fontWeight: fontWeight.semibold,
    marginLeft: 3,
  },
  newBadge: {
    position: 'absolute',
    top: spacing.sm,
    right: spacing.sm,
    backgroundColor: defaultPalette.brand,
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  newBadgeText: {
    color: '#fff',
    fontSize: 9,
    fontWeight: fontWeight.bold,
  },
  cardScrim: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
  },
  cardDishName: {
    color: '#fff',
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
  },
  cardTime: {
    color: 'rgba(255,255,255,0.85)',
    fontSize: fontSize.xs,
    marginTop: 1,
  },
});
