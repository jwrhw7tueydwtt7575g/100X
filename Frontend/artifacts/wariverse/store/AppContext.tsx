import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Haptics from 'expo-haptics';
import * as Location from 'expo-location';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Platform } from 'react-native';
import { getCopy } from '@/constants/copy';
import { mockConversationApi } from '@/services/mockApi';
import { speechService, textToSpeechService } from '@/services/speechService';
import type { ConversationResponse, Language, LocationState, Message, User } from '@/types/domain';

type AppContextValue = {
  language: Language;
  setLanguage: (language: Language) => void;
  copy: ReturnType<typeof getCopy>;
  messages: Message[];
  isLoading: boolean;
  isReady: boolean;
  error: string | null;
  readAloud: boolean;
  voiceInput: boolean;
  setReadAloud: (value: boolean) => void;
  setVoiceInput: (value: boolean) => void;
  location: LocationState;
  sendMessage: (text: string, isVoice?: boolean) => Promise<void>;
  confirmSOS: () => Promise<ConversationResponse | null>;
  speak: (text: string, language?: Language) => Promise<void>;
  stopSpeaking: () => Promise<void>;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<void>;
  cancelRecording: () => Promise<void>;
  isRecording: boolean;
  recordingSeconds: number;
  requestLocation: () => Promise<void>;
  clearConversation: () => Promise<void>;
  isOnboarded: boolean;
  finishOnboarding: (language: Language) => Promise<void>;
  user: User | null;
  loginWithPhone: (phoneNumber: string) => Promise<{ success: boolean; otp: string }>;
  verifyOTP: (phoneNumber: string, otp: string) => Promise<boolean>;
  logout: () => Promise<void>;
};

const AppContext = createContext<AppContextValue | null>(null);
const SETTINGS_KEY = 'wariverse-settings';
const MESSAGES_KEY = 'wariverse-messages';
const USER_KEY = 'wariverse-user';
const sessionId = 'wariverse-session';

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>('en');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readAloud, setReadAloudState] = useState(true);
  const [voiceInput, setVoiceInputState] = useState(true);
  const [isOnboarded, setIsOnboarded] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [location, setLocation] = useState<LocationState>({ latitude: 18.517, longitude: 73.856, permission: 'unknown' });
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    Promise.all([
      AsyncStorage.getItem(SETTINGS_KEY),
      AsyncStorage.getItem(MESSAGES_KEY),
      AsyncStorage.getItem(USER_KEY),
    ])
      .then(([settings, savedMessages, savedUser]) => {
        if (settings) {
          const parsed = JSON.parse(settings) as { language?: Language; readAloud?: boolean; voiceInput?: boolean; isOnboarded?: boolean };
          if (parsed.language) setLanguageState(parsed.language);
          if (typeof parsed.readAloud === 'boolean') setReadAloudState(parsed.readAloud);
          if (typeof parsed.voiceInput === 'boolean') setVoiceInputState(parsed.voiceInput);
          if (parsed.isOnboarded) setIsOnboarded(true);
        }
        if (savedMessages) setMessages(JSON.parse(savedMessages) as Message[]);
        if (savedUser) setUser(JSON.parse(savedUser) as User);
        setIsReady(true);
      })
      .catch(() => setIsReady(true));
  }, []);

  useEffect(() => {
    if (!isReady) return;
    AsyncStorage.setItem(MESSAGES_KEY, JSON.stringify(messages)).catch(() => undefined);
  }, [messages, isReady]);

  useEffect(() => {
    if (!isReady) return;
    AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify({ language, readAloud, voiceInput, isOnboarded })).catch(() => undefined);
  }, [language, readAloud, voiceInput, isOnboarded, isReady]);

  useEffect(() => {
    if (!isReady) return;
    if (user) {
      AsyncStorage.setItem(USER_KEY, JSON.stringify(user)).catch(() => undefined);
    } else {
      AsyncStorage.removeItem(USER_KEY).catch(() => undefined);
    }
  }, [user, isReady]);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    Haptics.selectionAsync().catch(() => undefined);
  }, []);
  const setReadAloud = useCallback((value: boolean) => setReadAloudState(value), []);
  const setVoiceInput = useCallback((value: boolean) => setVoiceInputState(value), []);
  const appCopy = getCopy(language);

  const sendMessage = useCallback(
    async (text: string, isVoice = false) => {
      if (isLoading || !text.trim()) return;
      setError(null);
      const userMessage: Message = { id: createId('user'), role: 'user', text: text.trim(), timestamp: new Date().toISOString(), language, isVoice };
      setMessages((current) => [...current, userMessage]);
      setIsLoading(true);
      try {
        const response = await mockConversationApi.sendMessage({ sessionId, language, message: text.trim() });
        const assistantMessage: Message = { id: response.messageId, role: 'assistant', text: response.responseText, timestamp: new Date().toISOString(), language: response.language, widgets: response.widgets };
        setMessages((current) => [...current, assistantMessage]);
        if (readAloud) await textToSpeechService.speak(response.responseText, language);
      } catch {
        setError('Something went wrong. Please try again.');
      } finally {
        setIsLoading(false);
      }
    },
    [isLoading, language, readAloud]
  );

  const confirmSOS = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await mockConversationApi.confirmSOS(language);
      setMessages((current) => [...current, { id: response.messageId, role: 'assistant', text: response.responseText, timestamp: new Date().toISOString(), language, widgets: response.widgets }]);
      return response;
    } catch {
      setError('Emergency request could not be completed. Please try again.');
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [language]);

  const speak = useCallback(
    async (text: string, targetLanguage = language) => {
      await textToSpeechService.speak(text, targetLanguage);
    },
    [language]
  );
  const stopSpeaking = useCallback(async () => textToSpeechService.stop(), []);

  const startRecording = useCallback(async () => {
    if (isRecording || !voiceInput) return;
    setError(null);
    await speechService.startRecording();
    setIsRecording(true);
    setRecordingSeconds(0);
    timerRef.current = setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => undefined);
  }, [isRecording, voiceInput]);

  const stopRecording = useCallback(async () => {
    if (!isRecording) return;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    setIsRecording(false);
    const transcript = await speechService.stopRecording(language);
    await sendMessage(transcript, true);
  }, [isRecording, language, sendMessage]);

  const cancelRecording = useCallback(async () => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    setIsRecording(false);
    setRecordingSeconds(0);
    await speechService.cancelRecording();
  }, []);

  const requestLocation = useCallback(async () => {
    if (Platform.OS === 'web') {
      setLocation((current) => ({ ...current, permission: 'granted' }));
      return;
    }
    const permission = await Location.requestForegroundPermissionsAsync();
    if (permission.status !== 'granted') {
      setLocation((current) => ({ ...current, permission: 'denied' }));
      Alert.alert(appCopy.location, appCopy.locationDenied);
      return;
    }
    const current = await Location.getCurrentPositionAsync({});
    setLocation({ latitude: current.coords.latitude, longitude: current.coords.longitude, permission: 'granted' });
  }, [appCopy.location, appCopy.locationDenied]);

  const clearConversation = useCallback(async () => {
    setMessages([]);
    await AsyncStorage.removeItem(MESSAGES_KEY);
  }, []);

  const finishOnboarding = useCallback(
    async (selectedLanguage: Language) => {
      setLanguageState(selectedLanguage);
      setIsOnboarded(true);
      await AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify({ language: selectedLanguage, readAloud, voiceInput, isOnboarded: true }));
    },
    [readAloud, voiceInput]
  );

  const loginWithPhone = useCallback(async (phoneNumber: string) => {
    const demoOTP = '123456';
    return { success: true, otp: demoOTP };
  }, []);

  const verifyOTP = useCallback(async (phoneNumber: string, otp: string) => {
    if (otp === '123456' || otp.length === 6) {
      const newUser: User = {
        id: createId('usr'),
        phoneNumber,
        name: `Warkari (${phoneNumber.slice(-4)})`,
        isAuthenticated: true,
        createdAt: new Date().toISOString(),
      };
      setUser(newUser);
      return true;
    }
    return false;
  }, []);

  const logout = useCallback(async () => {
    setUser(null);
    await AsyncStorage.removeItem(USER_KEY);
  }, []);

  const value = useMemo<AppContextValue>(
    () => ({
      language, setLanguage, copy: appCopy, messages, isLoading, isReady, error, readAloud, voiceInput, setReadAloud, setVoiceInput,
      location, sendMessage, confirmSOS, speak, stopSpeaking, startRecording, stopRecording, cancelRecording, isRecording, recordingSeconds,
      requestLocation, clearConversation, isOnboarded, finishOnboarding, user, loginWithPhone, verifyOTP, logout,
    }),
    [
      language, setLanguage, appCopy, messages, isLoading, isReady, error, readAloud, voiceInput, setReadAloud, setVoiceInput, location,
      sendMessage, confirmSOS, speak, stopSpeaking, startRecording, stopRecording, cancelRecording, isRecording, recordingSeconds,
      requestLocation, clearConversation, isOnboarded, finishOnboarding, user, loginWithPhone, verifyOTP, logout,
    ]
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used within AppProvider');
  return value;
}