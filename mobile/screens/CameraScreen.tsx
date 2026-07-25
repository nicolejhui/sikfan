import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Platform, Animated } from 'react-native';
import { CameraView, useCameraPermissions, type CameraCapturedPicture } from 'expo-camera';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation, useRoute } from '@react-navigation/native';
import type { StackNavigationProp } from '@react-navigation/stack';
import type { RouteProp } from '@react-navigation/native';

import { useMealStore } from '../store/mealStore';
import type { CameraStackParamList } from '../navigation';

type Nav = StackNavigationProp<CameraStackParamList, 'Camera'>;
type Route = RouteProp<CameraStackParamList, 'Camera'>;

export default function CameraScreen() {
  const navigation = useNavigation<Nav>();
  const route = useRoute<Route>();
  const startScan = useMealStore((s) => s.startScan);
  const reset = useMealStore((s) => s.reset);
  const [permission, requestPermission] = useCameraPermissions();
  const [flashOn, setFlashOn] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const cameraRef = useRef<CameraView>(null);
  const toastAnim = useRef(new Animated.Value(0)).current;

  React.useEffect(() => {
    if (!permission) return;
    if (!permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [permission]);

  useEffect(() => {
    const error = route.params?.error;
    if (!error) return;
    reset();
    setToastMessage(error);
    navigation.setParams({ error: undefined });

    Animated.sequence([
      Animated.timing(toastAnim, { toValue: 1, duration: 200, useNativeDriver: true }),
      Animated.delay(2200),
      Animated.timing(toastAnim, { toValue: 0, duration: 200, useNativeDriver: true }),
    ]).start(() => setToastMessage(null));
  }, [route.params?.error]);

  function goToAnalyzing(uri: string) {
    startScan(uri);
    navigation.replace('Analyzing');
  }

  async function handleShutter() {
    if (capturing || !cameraRef.current) return;
    setCapturing(true);
    try {
      const photo: CameraCapturedPicture | undefined = await cameraRef.current.takePictureAsync();
      if (photo?.uri) goToAnalyzing(photo.uri);
    } finally {
      setCapturing(false);
    }
  }

  async function handleGalleryPick() {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 1,
    });
    if (!result.canceled && result.assets?.[0]?.uri) {
      goToAnalyzing(result.assets[0].uri);
    }
  }

  return (
    <View style={styles.container}>
      {permission?.granted && (
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing="back"
          enableTorch={flashOn}
        />
      )}

      <View style={styles.topBar}>
        <TouchableOpacity
          style={styles.iconButton}
          onPress={() => navigation.getParent()?.goBack()}
        >
          <Ionicons name="close" size={22} color="#fff" />
        </TouchableOpacity>

        <View style={styles.autoDetectPill}>
          <Ionicons name="sparkles" size={13} color="#fff" style={{ marginRight: 6 }} />
          <Text style={styles.autoDetectLabel}>Auto-detect on</Text>
        </View>

        <TouchableOpacity style={styles.iconButton} onPress={() => setFlashOn((v) => !v)}>
          <Ionicons name={flashOn ? 'flash' : 'flash-off'} size={20} color="#fff" />
        </TouchableOpacity>
      </View>

      <View style={styles.reticleWrap} pointerEvents="none">
        <View style={styles.reticle}>
          <View style={[styles.corner, styles.cornerTL]} />
          <View style={[styles.corner, styles.cornerTR]} />
          <View style={[styles.corner, styles.cornerBL]} />
          <View style={[styles.corner, styles.cornerBR]} />
        </View>
        <Text style={styles.reticleLabel}>Center your plate</Text>
      </View>

      <View style={styles.bottomBar}>
        <TouchableOpacity style={styles.sideButton} onPress={handleGalleryPick}>
          <Ionicons name="images-outline" size={26} color="#fff" />
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.shutterOuter}
          onPress={handleShutter}
          disabled={capturing}
          activeOpacity={0.8}
        >
          <View style={styles.shutterInner} />
        </TouchableOpacity>

        <View style={styles.sideButton}>
          <Ionicons name="camera-reverse-outline" size={26} color="rgba(255,255,255,0.35)" />
        </View>
      </View>

      {toastMessage && (
        <Animated.View
          style={[
            styles.toast,
            {
              opacity: toastAnim,
              transform: [
                { translateY: toastAnim.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }) },
              ],
            },
          ]}
        >
          <Ionicons name="alert-circle" size={16} color="#fff" style={{ marginRight: 8 }} />
          <Text style={styles.toastLabel}>{toastMessage}</Text>
        </Animated.View>
      )}
    </View>
  );
}

const RETICLE_SIZE = 34;

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  topBar: {
    position: 'absolute',
    top: Platform.OS === 'ios' ? 56 : 24,
    left: 20,
    right: 20,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.15)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  autoDetectPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: 'rgba(255,255,255,0.15)',
  },
  autoDetectLabel: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '500',
  },
  reticleWrap: {
    position: 'absolute',
    top: '38%',
    left: 0,
    right: 0,
    alignItems: 'center',
  },
  reticle: {
    width: 220,
    height: 220,
  },
  corner: {
    position: 'absolute',
    width: RETICLE_SIZE,
    height: RETICLE_SIZE,
    borderColor: '#fff',
    borderRadius: 10,
  },
  cornerTL: { top: 0, left: 0, borderTopWidth: 3, borderLeftWidth: 3 },
  cornerTR: { top: 0, right: 0, borderTopWidth: 3, borderRightWidth: 3 },
  cornerBL: { bottom: 0, left: 0, borderBottomWidth: 3, borderLeftWidth: 3 },
  cornerBR: { bottom: 0, right: 0, borderBottomWidth: 3, borderRightWidth: 3 },
  reticleLabel: {
    marginTop: 16,
    color: 'rgba(255,255,255,0.85)',
    fontSize: 14,
  },
  bottomBar: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 48 : 28,
    left: 0,
    right: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 40,
  },
  sideButton: {
    width: 48,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterOuter: {
    width: 76,
    height: 76,
    borderRadius: 999,
    borderWidth: 5,
    borderColor: 'rgba(255,255,255,0.9)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  shutterInner: {
    width: 60,
    height: 60,
    borderRadius: 999,
    backgroundColor: '#fff',
  },
  toast: {
    position: 'absolute',
    bottom: Platform.OS === 'ios' ? 140 : 116,
    left: 24,
    right: 24,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 14,
    backgroundColor: 'rgba(30,20,20,0.92)',
  },
  toastLabel: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '500',
    flexShrink: 1,
  },
});
