import React from 'react';
import { TouchableOpacity, View, StyleSheet, Platform } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createStackNavigator } from '@react-navigation/stack';
import { Ionicons } from '@expo/vector-icons';

import { defaultPalette } from '../constants/theme';
import HomeScreen from '../screens/HomeScreen';
import ResultsScreen from '../screens/ResultsScreen';
import MealLogScreen from '../screens/MealLogScreen';
import CameraScreen from '../screens/CameraScreen';
import AnalyzingScreen from '../screens/AnalyzingScreen';
import TrendsScreen from '../screens/TrendsScreen';
import AboutScreen from '../screens/AboutScreen';

// ---------------------------------------------------------------------------
// Param lists
// ---------------------------------------------------------------------------

export type RootStackParamList = {
  MainTabs: undefined;
  CameraModal: undefined;
};

export type HomeStackParamList = {
  Home: undefined;
  Results: undefined;
};

export type LogStackParamList = {
  MealLog: undefined;
  Results: undefined;
};

export type CameraStackParamList = {
  Camera: { error?: string } | undefined;
  Analyzing: undefined;
};

export type TabParamList = {
  HomeTab: undefined;
  LogTab: undefined;
  CameraTab: undefined;   // custom FAB — no real screen
  TrendsTab: undefined;
  AboutTab: undefined;
};

// ---------------------------------------------------------------------------
// Navigators
// ---------------------------------------------------------------------------

const RootStack = createStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();
const HomeStack = createStackNavigator<HomeStackParamList>();
const LogStack = createStackNavigator<LogStackParamList>();
const CameraStack = createStackNavigator<CameraStackParamList>();

// ---------------------------------------------------------------------------
// Stack navigators for each tab
// ---------------------------------------------------------------------------

function HomeStackNavigator() {
  return (
    <HomeStack.Navigator screenOptions={{ headerShown: false }}>
      <HomeStack.Screen name="Home" component={HomeScreen} />
      <HomeStack.Screen name="Results" component={ResultsScreen} />
    </HomeStack.Navigator>
  );
}

function LogStackNavigator() {
  return (
    <LogStack.Navigator screenOptions={{ headerShown: false }}>
      <LogStack.Screen name="MealLog" component={MealLogScreen} />
      <LogStack.Screen name="Results" component={ResultsScreen} />
    </LogStack.Navigator>
  );
}

// ---------------------------------------------------------------------------
// Camera FAB component (custom tabBarButton for the center slot)
// ---------------------------------------------------------------------------

interface CameraFABProps {
  onPress?: () => void;
}

function CameraFAB({ onPress }: CameraFABProps) {
  return (
    <TouchableOpacity onPress={onPress} style={styles.fabContainer} activeOpacity={0.85}>
      <View style={styles.fabOuter}>
        <View style={styles.fabInner}>
          <Ionicons name="camera" size={26} color="#fff" />
        </View>
      </View>
    </TouchableOpacity>
  );
}

// ---------------------------------------------------------------------------
// Main tab navigator
// ---------------------------------------------------------------------------

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: styles.tabBar,
        tabBarActiveTintColor: defaultPalette.brand,
        tabBarInactiveTintColor: defaultPalette.inkFaint,
        tabBarShowLabel: true,
        tabBarLabelStyle: styles.tabBarLabel,
      }}
    >
      <Tab.Screen
        name="HomeTab"
        component={HomeStackNavigator}
        options={{
          tabBarLabel: 'Home',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="home-outline" size={size} color={color} />
          ),
        }}
      />
      <Tab.Screen
        name="LogTab"
        component={LogStackNavigator}
        options={{
          tabBarLabel: 'Log',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="receipt-outline" size={size} color={color} />
          ),
        }}
      />
      {/* Center camera FAB — custom button, no real screen */}
      <Tab.Screen
        name="CameraTab"
        component={HomeScreen}   /* placeholder, never shown */
        options={({ navigation }) => ({
          tabBarLabel: '',
          tabBarIcon: () => null,
          tabBarButton: () => (
            <CameraFAB onPress={() => navigation.navigate('CameraModal' as never)} />
          ),
        })}
      />
      <Tab.Screen
        name="TrendsTab"
        component={TrendsScreen}
        options={{
          tabBarLabel: 'Trends',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="trending-up-outline" size={size} color={color} />
          ),
        }}
      />
      <Tab.Screen
        name="AboutTab"
        component={AboutScreen}
        options={{
          tabBarLabel: 'About',
          tabBarIcon: ({ color, size }) => (
            <Ionicons name="information-circle-outline" size={size} color={color} />
          ),
        }}
      />
    </Tab.Navigator>
  );
}

// ---------------------------------------------------------------------------
// Camera modal stack (full-screen)
// ---------------------------------------------------------------------------

function CameraModalStack() {
  return (
    <CameraStack.Navigator
      screenOptions={{
        headerShown: false,
        presentation: 'fullScreenModal',
      }}
    >
      <CameraStack.Screen name="Camera" component={CameraScreen} />
      <CameraStack.Screen name="Analyzing" component={AnalyzingScreen} />
    </CameraStack.Navigator>
  );
}

// ---------------------------------------------------------------------------
// Root navigator
// ---------------------------------------------------------------------------

export default function RootNavigator() {
  return (
    <NavigationContainer>
      <RootStack.Navigator
        screenOptions={{
          headerShown: false,
          presentation: 'modal',
        }}
      >
        <RootStack.Screen name="MainTabs" component={MainTabs} />
        <RootStack.Screen name="CameraModal" component={CameraModalStack} />
      </RootStack.Navigator>
    </NavigationContainer>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  tabBar: {
    backgroundColor: defaultPalette.surface,
    borderTopColor: defaultPalette.hair,
    borderTopWidth: 1,
    height: Platform.OS === 'ios' ? 88 : 64,
    paddingBottom: Platform.OS === 'ios' ? 28 : 8,
    paddingTop: 8,
  },
  tabBarLabel: {
    fontSize: 10,
    fontWeight: '500',
  },
  // Camera FAB — floats above the tab bar via negative marginTop
  fabContainer: {
    alignItems: 'center',
    justifyContent: 'flex-end',
    flex: 1,
    marginTop: -20,
    paddingBottom: Platform.OS === 'ios' ? 20 : 4,
  },
  fabOuter: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: defaultPalette.surface,
    borderWidth: 4,
    borderColor: defaultPalette.canvas,
    alignItems: 'center',
    justifyContent: 'center',
    // Subtle shadow so FAB appears lifted
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
    elevation: 8,
  },
  fabInner: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: defaultPalette.brand,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
