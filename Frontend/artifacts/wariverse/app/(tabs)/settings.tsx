import { Feather } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import React from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import colors from '@/constants/colors';
import { languages } from '@/constants/copy';
import { useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { useApp } from '@/store/AppContext';

function useSafeTabBarHeight() {
  try {
    return useBottomTabBarHeight();
  } catch {
    return 60;
  }
}

export default function SettingsScreen() {
  const router = useRouter();
  const { copy, language, setLanguage, readAloud, setReadAloud, voiceInput, setVoiceInput, location, requestLocation, clearConversation, user, logout } = useApp();
  const insets = useSafeAreaInsets();
  const tabBarHeight = useSafeTabBarHeight();
  const confirmClear = () => Alert.alert(copy.clearConversation, 'This removes the conversation saved on this device.', [{ text: copy.cancel, style: 'cancel' }, { text: 'Clear', style: 'destructive', onPress: () => void clearConversation() }]);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={[styles.content, { paddingTop: insets.top + 18, paddingBottom: tabBarHeight + 22 }]}>
      <Text style={styles.kicker}>WariVerse</Text>
      <Text style={styles.title}>{copy.settings}</Text>

      <SectionTitle label="Account" />
      {user ? (
        <View style={styles.locationRow}>
          <View style={styles.settingIcon}>
            <Feather name="user-check" size={17} color={colors.light.teal} />
          </View>
          <View style={styles.settingCopy}>
            <Text style={styles.settingTitle}>{user.name || 'Warkari'}</Text>
            <Text style={styles.settingDescription}>+91 {user.phoneNumber} · Verified</Text>
          </View>
          <Pressable onPress={() => void logout()} style={styles.logoutBtn}>
            <Text style={styles.logoutText}>Sign Out</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable accessibilityRole="button" onPress={() => router.push('/auth')} style={styles.locationRow}>
          <View style={styles.settingIcon}>
            <Feather name="user-plus" size={17} color={colors.light.teal} />
          </View>
          <View style={styles.settingCopy}>
            <Text style={styles.settingTitle}>Sign In / Register</Text>
            <Text style={styles.settingDescription}>Sign in with mobile number & OTP</Text>
          </View>
          <Feather name="chevron-right" size={17} color={colors.light.mutedForeground} />
        </Pressable>
      )}

      <SectionTitle label={copy.language} />
      <View style={styles.languageRow}>
        {languages.map((item) => (
          <Pressable
            key={item.id}
            accessibilityRole="radio"
            accessibilityState={{ selected: language === item.id }}
            onPress={() => setLanguage(item.id)}
            style={({ pressed }) => [styles.languageOption, language === item.id && styles.languageActive, pressed && { opacity: 0.7 }]}
          >
            <Text style={[styles.languageLabel, language === item.id && styles.languageLabelActive]}>{item.label}</Text>
            {language === item.id && <Feather name="check" size={15} color={colors.light.white} />}
          </Pressable>
        ))}
      </View>

      <SectionTitle label="Preferences" />
      <SettingRow icon="volume-2" title={copy.readAloud} description="Play assistant answers when available" value={readAloud} onChange={setReadAloud} />
      <SettingRow icon="mic" title={copy.voiceInput} description="Use the microphone in chat" value={voiceInput} onChange={setVoiceInput} />

      <SectionTitle label={copy.location} />
      <Pressable accessibilityRole="button" onPress={() => void requestLocation()} style={styles.locationRow}>
        <View style={styles.settingIcon}>
          <Feather name="map-pin" size={17} color={colors.light.teal} />
        </View>
        <View style={styles.settingCopy}>
          <Text style={styles.settingTitle}>{location.permission === 'granted' ? 'Location enabled' : 'Location permission'}</Text>
          <Text style={styles.settingDescription}>{location.permission === 'granted' ? 'Ready for routes, facilities, and SOS' : copy.allowLocation}</Text>
        </View>
        <Feather name="chevron-right" size={17} color={colors.light.mutedForeground} />
      </Pressable>
      {location.permission === 'denied' && <Text style={styles.denied}>{copy.locationDenied}</Text>}

      <SectionTitle label="Session" />
      <Pressable accessibilityRole="button" onPress={confirmClear} style={styles.locationRow}>
        <View style={[styles.settingIcon, styles.dangerIcon]}>
          <Feather name="trash-2" size={17} color={colors.light.destructive} />
        </View>
        <View style={styles.settingCopy}>
          <Text style={styles.settingTitle}>{copy.clearConversation}</Text>
          <Text style={styles.settingDescription}>Remove saved messages from this device</Text>
        </View>
        <Feather name="chevron-right" size={17} color={colors.light.mutedForeground} />
      </Pressable>

      <View style={styles.about}>
        <View style={styles.aboutMark}>
          <Feather name="navigation" size={17} color={colors.light.white} />
        </View>
        <Text style={styles.aboutName}>WariVerse</Text>
        <Text style={styles.aboutText}>A kinder way to find your way through the Wari.</Text>
        <Text style={styles.version}>Version 1.0 · Frontend demo</Text>
      </View>
    </ScrollView>
  );
}

function SectionTitle({ label }: { label: string }) { return <Text style={styles.sectionTitle}>{label}</Text>; }
function SettingRow({ icon, title, description, value, onChange }: { icon: keyof typeof Feather.glyphMap; title: string; description: string; value: boolean; onChange: (value: boolean) => void }) {
  return (
    <View style={styles.locationRow}>
      <View style={styles.settingIcon}><Feather name={icon} size={17} color={colors.light.teal} /></View>
      <View style={styles.settingCopy}><Text style={styles.settingTitle}>{title}</Text><Text style={styles.settingDescription}>{description}</Text></View>
      <Switch accessibilityLabel={title} value={value} onValueChange={onChange} trackColor={{ false: colors.light.input, true: '#9dc7b4' }} thumbColor={value ? colors.light.teal : colors.light.white} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.light.background },
  content: { paddingHorizontal: 18 },
  kicker: { color: colors.light.mutedForeground, fontFamily: 'Inter_600SemiBold', fontSize: 10, letterSpacing: 1 },
  title: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 29, letterSpacing: -0.8, marginTop: 4 },
  sectionTitle: { color: colors.light.mutedForeground, fontFamily: 'Inter_700Bold', fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', marginTop: 27, marginBottom: 10 },
  languageRow: { flexDirection: 'row', gap: 8 },
  languageOption: { minHeight: 42, backgroundColor: colors.light.card, borderWidth: 1, borderColor: colors.light.border, borderRadius: 13, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 6 },
  languageActive: { backgroundColor: colors.light.teal, borderColor: colors.light.teal },
  languageLabel: { color: colors.light.teal, fontFamily: 'Inter_600SemiBold', fontSize: 12 },
  languageLabelActive: { color: colors.light.white },
  locationRow: { backgroundColor: colors.light.card, borderWidth: 1, borderColor: colors.light.border, borderRadius: 17, minHeight: 67, padding: 11, flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 9 },
  settingIcon: { width: 35, height: 35, borderRadius: 12, backgroundColor: colors.light.tealSoft, alignItems: 'center', justifyContent: 'center' },
  dangerIcon: { backgroundColor: '#fae4df' },
  settingCopy: { flex: 1 },
  settingTitle: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 12 },
  settingDescription: { color: colors.light.mutedForeground, fontFamily: 'Inter_400Regular', fontSize: 10, marginTop: 3 },
  logoutBtn: { backgroundColor: '#fae4df', borderRadius: 10, paddingHorizontal: 10, paddingVertical: 6 },
  logoutText: { color: colors.light.destructive, fontFamily: 'Inter_600SemiBold', fontSize: 11 },
  denied: { color: colors.light.destructive, fontFamily: 'Inter_400Regular', fontSize: 11, marginTop: 0 },
  about: { alignItems: 'center', paddingVertical: 27 },
  aboutMark: { width: 35, height: 35, borderRadius: 12, backgroundColor: colors.light.primary, alignItems: 'center', justifyContent: 'center', transform: [{ rotate: '-10deg' }] },
  aboutName: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 15, marginTop: 9 },
  aboutText: { color: colors.light.mutedForeground, fontFamily: 'Inter_400Regular', fontSize: 11, marginTop: 5 },
  version: { color: colors.light.mutedForeground, fontFamily: 'Inter_400Regular', fontSize: 9, marginTop: 10 },
});