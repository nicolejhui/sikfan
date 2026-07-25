import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Animated, Easing, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import type { StackNavigationProp } from '@react-navigation/stack';
import Svg, { Defs, RadialGradient, LinearGradient, Stop, Rect } from 'react-native-svg';

import { useMealStore } from '../store/mealStore';
import { defaultPalette, spacing, radius, fontSize, fontWeight } from '../constants/theme';
import type { CameraStackParamList } from '../navigation';

type Nav = StackNavigationProp<CameraStackParamList, 'Analyzing'>;

const FRAME_SIZE = 248;
const BEAM_HEIGHT = 64;
const BEAM_TRAVEL = FRAME_SIZE - BEAM_HEIGHT;
const MIN_CHIP_DELAY_MS = 1150;

const STEP_LABELS = ['Scan', 'Identify', 'Portion', 'Model'];
const STEP_HEADLINES = [
  'Scanning meal',
  'Identifying dish',
  'Estimating portion',
  'Predicting carbs, macros & impact',
];

export default function AnalyzingScreen() {
  const navigation = useNavigation<Nav>();
  const status = useMealStore((s) => s.status);
  const dishName = useMealStore((s) => s.dishName);
  const confidence = useMealStore((s) => s.confidence);
  const error = useMealStore((s) => s.error);
  const reset = useMealStore((s) => s.reset);

  const [stepIndex, setStepIndex] = useState(0);
  const [chipVisible, setChipVisible] = useState(false);

  const mountedAt = useRef(Date.now());
  const beamAnim = useRef(new Animated.Value(0)).current;
  const chipAnim = useRef(new Animated.Value(0)).current;

  // Scan beam sweep — loops top to bottom for the duration of the analysis.
  useEffect(() => {
    const loop = Animated.loop(
      Animated.timing(beamAnim, {
        toValue: 1,
        duration: 1900,
        easing: Easing.linear,
        useNativeDriver: true,
      })
    );
    loop.start();
    return () => loop.stop();
  }, []);

  // Advance the 4 progress steps roughly every second.
  useEffect(() => {
    if (stepIndex >= STEP_LABELS.length - 1) return;
    const timer = setTimeout(() => setStepIndex((i) => i + 1), 1000);
    return () => clearTimeout(timer);
  }, [stepIndex]);

  // Reveal the detected-dish chip once data has arrived, honouring the min animation beat.
  useEffect(() => {
    if (!dishName) return;
    const elapsed = Date.now() - mountedAt.current;
    const delay = Math.max(0, MIN_CHIP_DELAY_MS - elapsed);
    const timer = setTimeout(() => setChipVisible(true), delay);
    return () => clearTimeout(timer);
  }, [dishName]);

  useEffect(() => {
    if (!chipVisible) return;
    Animated.timing(chipAnim, {
      toValue: 1,
      duration: 280,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [chipVisible]);

  useEffect(() => {
    if (status === 'done') {
      const parent = navigation.getParent() as { navigate: (name: string, params?: object) => void } | undefined;
      parent?.navigate('MainTabs', { screen: 'HomeTab', params: { screen: 'Results' } });
    } else if (status === 'error') {
      reset();
      navigation.replace('Camera', { error: error ?? 'Something went wrong. Please try again.' });
    }
  }, [status]);

  const beamTranslateY = beamAnim.interpolate({ inputRange: [0, 1], outputRange: [0, BEAM_TRAVEL] });

  return (
    <View style={styles.container}>
      <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
        <Defs>
          <RadialGradient id="ambient" cx="50%" cy="38%" r="65%">
            <Stop offset="0%" stopColor={defaultPalette.good.fg} stopOpacity={0.35} />
            <Stop offset="100%" stopColor={defaultPalette.good.fg} stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect width="100%" height="100%" fill="url(#ambient)" />
      </Svg>

      <View style={styles.pill}>
        <Ionicons name="sparkles" size={13} color="#fff" style={{ marginRight: 6 }} />
        <Text style={styles.pillLabel}>Analyzing meal</Text>
      </View>

      <View style={styles.frameWrap}>
        <View style={styles.frame}>
          <Animated.View
            style={[styles.beamWrap, { transform: [{ translateY: beamTranslateY }] }]}
          >
            <Svg width={FRAME_SIZE} height={BEAM_HEIGHT}>
              <Defs>
                <LinearGradient id="beam" x1="0" y1="0" x2="0" y2="1">
                  <Stop offset="0%" stopColor={defaultPalette.good.fg} stopOpacity={0} />
                  <Stop offset="50%" stopColor={defaultPalette.good.fg} stopOpacity={0.55} />
                  <Stop offset="100%" stopColor={defaultPalette.good.fg} stopOpacity={0} />
                </LinearGradient>
              </Defs>
              <Rect width={FRAME_SIZE} height={BEAM_HEIGHT} fill="url(#beam)" />
            </Svg>
          </Animated.View>

          <View style={styles.reticle} pointerEvents="none">
            <View style={[styles.corner, styles.cornerTL]} />
            <View style={[styles.corner, styles.cornerTR]} />
            <View style={[styles.corner, styles.cornerBL]} />
            <View style={[styles.corner, styles.cornerBR]} />
          </View>
        </View>

        {chipVisible && (
          <Animated.View
            style={[
              styles.chip,
              {
                opacity: chipAnim,
                transform: [
                  { translateY: chipAnim.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) },
                ],
              },
            ]}
          >
            <View style={styles.chipIcon}>
              <Ionicons name="checkmark" size={13} color="#fff" />
            </View>
            <Text style={styles.chipLabel} numberOfLines={1}>
              {dishName}
            </Text>
            {confidence != null && (
              <Text style={styles.chipConfidence}>{Math.round(confidence * 100)}%</Text>
            )}
          </Animated.View>
        )}
      </View>

      <Text style={styles.headline}>{STEP_HEADLINES[stepIndex]}</Text>

      <View style={styles.progressTrack}>
        <View
          style={[styles.progressFill, { width: `${((stepIndex + 1) / STEP_LABELS.length) * 100}%` }]}
        />
      </View>

      <View style={styles.stepLabels}>
        {STEP_LABELS.map((label, i) => (
          <Text
            key={label}
            style={[styles.stepLabel, i <= stepIndex && styles.stepLabelActive]}
          >
            {label}
          </Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#12181A',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
  },
  pill: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 64 : 32,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radius.full,
    backgroundColor: 'rgba(255,255,255,0.12)',
  },
  pillLabel: {
    color: '#fff',
    fontSize: fontSize.sm,
    fontWeight: fontWeight.medium,
  },
  frameWrap: {
    width: FRAME_SIZE,
    marginBottom: spacing.xl,
  },
  frame: {
    width: FRAME_SIZE,
    height: FRAME_SIZE,
    borderRadius: radius.xl,
    backgroundColor: 'rgba(255,255,255,0.06)',
    overflow: 'hidden',
  },
  beamWrap: {
    position: 'absolute',
    top: 0,
    left: 0,
  },
  reticle: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  corner: {
    position: 'absolute',
    width: 30,
    height: 30,
    borderColor: defaultPalette.good.fg,
    borderRadius: 14,
  },
  cornerTL: { top: 10, left: 10, borderTopWidth: 3, borderLeftWidth: 3 },
  cornerTR: { top: 10, right: 10, borderTopWidth: 3, borderRightWidth: 3 },
  cornerBL: { bottom: 10, left: 10, borderBottomWidth: 3, borderLeftWidth: 3 },
  cornerBR: { bottom: 10, right: 10, borderBottomWidth: 3, borderRightWidth: 3 },
  chip: {
    position: 'absolute',
    bottom: -18,
    left: 12,
    right: 12,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: radius.lg,
    backgroundColor: defaultPalette.surface,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 10,
    elevation: 6,
  },
  chipIcon: {
    width: 22,
    height: 22,
    borderRadius: 11,
    backgroundColor: defaultPalette.good.fg,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 8,
  },
  chipLabel: {
    flex: 1,
    color: defaultPalette.ink,
    fontSize: fontSize.sm,
    fontWeight: fontWeight.semibold,
  },
  chipConfidence: {
    color: defaultPalette.inkSoft,
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
    marginLeft: 8,
  },
  headline: {
    color: 'rgba(255,255,255,0.9)',
    fontSize: fontSize.md,
    fontWeight: fontWeight.medium,
    textAlign: 'center',
    marginBottom: spacing.md,
  },
  progressTrack: {
    width: '100%',
    maxWidth: 280,
    height: 4,
    borderRadius: radius.full,
    backgroundColor: 'rgba(255,255,255,0.12)',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: radius.full,
    backgroundColor: defaultPalette.good.fg,
  },
  stepLabels: {
    width: '100%',
    maxWidth: 280,
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  stepLabel: {
    color: 'rgba(255,255,255,0.35)',
    fontSize: fontSize.xs,
    fontWeight: fontWeight.medium,
  },
  stepLabelActive: {
    color: 'rgba(255,255,255,0.92)',
  },
});
