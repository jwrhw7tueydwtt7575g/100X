import { Feather } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import colors from '@/constants/colors';
import type { CrowdDensityWidget, EscalationWidget, FacilityWidget, ForecastWidget, Language, LostFoundWidget, RouteWidget, SOSWidget, TempleInfoWidget, ToolWidget } from '@/types/domain';

const iconFor: Record<string, keyof typeof Feather.glyphMap> = { medical: 'heart', water: 'droplet', toilet: 'grid', rest: 'coffee', food: 'shopping-bag', accommodation: 'home' };
const labelFor: Record<string, string> = { medical: 'Medical', water: 'Drinking water', toilet: 'Toilet', rest: 'Rest shelter', food: 'Food', accommodation: 'Accommodation' };

export function ToolWidgetRenderer({ widget, language, onViewMap, onViewRoute, onConfirmSOS, onTalk }: { widget: ToolWidget; language: Language; onViewMap?: () => void; onViewRoute?: () => void; onConfirmSOS?: () => void; onTalk?: () => void }) {
  switch (widget.type) {
    case 'crowd_density': return <CrowdCard data={widget.data} language={language} onViewMap={onViewMap} />;
    case 'congestion_forecast': return <ForecastCard data={widget.data} />;
    case 'route_guidance': return <RouteCard data={widget.data} language={language} onViewRoute={onViewRoute} />;
    case 'nearby_facility': return <FacilityCard data={widget.data} onViewMap={onViewMap} />;
    case 'temple_info': return <TempleCard data={widget.data} />;
    case 'lost_and_found': return <LostCard data={widget.data} />;
    case 'sos': return <SOSCard data={widget.data} language={language} onConfirm={onConfirmSOS} />;
    case 'human_escalation': return <EscalationCard data={widget.data} onTalk={onTalk} />;
    default: return null;
  }
}

function Shell({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'orange' | 'teal' | 'yellow' | 'red' }) {
  return <View style={[styles.shell, tone === 'orange' && styles.orange, tone === 'teal' && styles.teal, tone === 'yellow' && styles.yellow, tone === 'red' && styles.red]}>{children}</View>;
}

function CardHeader({ icon, title, eyebrow }: { icon: keyof typeof Feather.glyphMap; title: string; eyebrow: string }) {
  return <View style={styles.cardHeader}><View style={styles.iconBox}><Feather name={icon} size={17} color={colors.light.teal} /></View><View style={styles.headerText}><Text style={styles.eyebrow}>{eyebrow.toUpperCase()}</Text><Text style={styles.cardTitle}>{title}</Text></View></View>;
}

function SmallButton({ label, icon, onPress, danger = false }: { label: string; icon: keyof typeof Feather.glyphMap; onPress?: () => void; danger?: boolean }) {
  return <Pressable accessibilityRole="button" onPress={onPress} style={({ pressed }) => [styles.smallButton, danger && styles.dangerButton, pressed && styles.pressed]}><Feather name={icon} size={14} color={danger ? colors.light.destructive : colors.light.teal} /><Text style={[styles.smallButtonText, danger && styles.dangerText]}>{label}</Text></Pressable>;
}

function CrowdCard({ data, language, onViewMap }: { data: CrowdDensityWidget['data']; language: Language; onViewMap?: () => void }) {
  const status = language === 'mr' ? 'जास्त' : language === 'hi' ? 'ज़्यादा' : 'High';
  return <Shell tone="orange"><CardHeader icon="users" eyebrow="Live crowd" title={data.zoneName} /><View style={styles.crowdRow}><View><Text style={styles.metric}>{data.density}%</Text><Text style={styles.metricLabel}>{language === 'mr' ? 'गर्दीची पातळी' : language === 'hi' ? 'भीड़ का स्तर' : 'Crowd level'} · {status}</Text></View><View style={styles.meter}><View style={[styles.meterFill, { width: `${data.density}%` }]} /><View style={styles.meterDot} /></View></View><Text style={styles.updated}>Updated {data.updatedAt}</Text>{onViewMap && <SmallButton label={language === 'mr' ? 'नकाशावर पहा' : language === 'hi' ? 'नक्शे पर देखें' : 'View on map'} icon="map" onPress={onViewMap} />}</Shell>;
}

function ForecastCard({ data }: { data: ForecastWidget['data'] }) {
  const max = Math.max(...data.points.map((p) => p.value));
  return <Shell tone="yellow"><CardHeader icon="trending-up" eyebrow="Congestion forecast" title={data.zoneName} /><Text style={styles.forecastCaption}>Next few hours</Text><View style={styles.chart}>{data.points.map((point) => <View style={styles.chartItem} key={point.time}><Text style={styles.chartValue}>{point.value}%</Text><View style={styles.barTrack}><View style={[styles.bar, { height: `${(point.value / max) * 100}%` }]} /></View><Text style={styles.chartTime}>{point.time}</Text></View>)}</View>{data.recommendation && <View style={styles.recommendation}><Feather name="sunrise" size={15} color={colors.light.accentForeground} /><Text style={styles.recommendationText}>{data.recommendation}</Text></View>}<Text style={styles.updated}>{data.updatedAt}</Text></Shell>;
}

function RouteCard({ data, language, onViewRoute }: { data: RouteWidget['data']; language: Language; onViewRoute?: () => void }) {
  return <Shell tone="teal"><CardHeader icon="navigation" eyebrow="Recommended route" title={data.destination.label ?? 'Temple entrance'} /><View style={styles.routeLine}><View style={styles.routeDot} /><Text style={styles.routeLabel}>{data.origin.label ?? 'Current location'}</Text><View style={styles.routePath} /><View style={[styles.routeDot, styles.routeDotEnd]} /><Text style={styles.routeLabel}>{data.destination.label ?? 'Destination'}</Text></View><View style={styles.routeMeta}><View><Text style={styles.metaLabel}>DISTANCE</Text><Text style={styles.metaValue}>{data.distance ?? '—'}</Text></View><View><Text style={styles.metaLabel}>WALK</Text><Text style={styles.metaValue}>{data.estimatedTime ?? '—'}</Text></View></View>{data.avoidAreas?.map((area) => <Text key={area} style={styles.avoid}><Feather name="alert-circle" size={13} color={colors.light.destructive} /> Avoid {area}</Text>)}{onViewRoute && <SmallButton label={language === 'mr' ? 'रस्ता पहा' : language === 'hi' ? 'रास्ता देखें' : 'View route'} icon="map" onPress={onViewRoute} />}</Shell>;
}

function FacilityCard({ data, onViewMap }: { data: FacilityWidget['data']; onViewMap?: () => void }) {
  return <Shell tone="teal"><CardHeader icon={iconFor[data.category] ?? 'map-pin'} eyebrow={labelFor[data.category] ?? 'Nearby facility'} title={data.name} /><Text style={styles.facilityDistance}>{data.distance ?? 'Distance unavailable'} <Text style={styles.muted}>away</Text></Text>{data.availability && <View style={styles.available}><View style={styles.liveDot} /><Text style={styles.availableText}>{data.availability}</Text></View>}{onViewMap && <SmallButton label="View on map" icon="map" onPress={onViewMap} />}</Shell>;
}

function TempleCard({ data }: { data: TempleInfoWidget['data'] }) {
  return <Shell tone="yellow"><CardHeader icon="home" eyebrow="Temple information" title={data.title} />{data.timings && <InfoRow icon="clock" label="Darshan" value={data.timings} />}{data.rituals?.map((ritual) => <InfoRow key={ritual} icon="sun" label="Ritual" value={ritual} />)}{data.description && <Text style={styles.description}>{data.description}</Text>}</Shell>;
}

function LostCard({ data }: { data: LostFoundWidget['data'] }) {
  return <Shell tone="orange"><CardHeader icon="search" eyebrow="Lost & found request" title={data.incidentType === 'PERSON' ? 'Person report' : 'Item report'} /><View style={styles.statusPill}><View style={styles.liveDot} /><Text style={styles.statusText}>{data.status}</Text></View>{data.referenceId && <InfoRow icon="hash" label="Reference" value={data.referenceId} />}{data.nextAction && <Text style={styles.description}>{data.nextAction}</Text>}</Shell>;
}

function SOSCard({ data, language, onConfirm }: { data: SOSWidget['data']; language: Language; onConfirm?: () => void }) {
  const waiting = data.status === 'CONFIRMATION_REQUIRED';
  return <Shell tone="red"><CardHeader icon="shield" eyebrow="Emergency assistance" title={data.status === 'ACTIVATED' ? 'SOS activated' : 'Assistance request'} /><Text style={styles.description}>{data.message}</Text>{data.controlRoomStatus && <InfoRow icon="radio" label="Control room" value={data.controlRoomStatus} />}{data.timestamp && <InfoRow icon="clock" label="Requested" value={data.timestamp} />}{waiting && onConfirm && <SmallButton label={language === 'mr' ? 'SOS निश्चित करा' : language === 'hi' ? 'SOS की पुष्टि करें' : 'Confirm SOS'} icon="phone-call" onPress={onConfirm} danger />}</Shell>;
}

function EscalationCard({ data, onTalk }: { data: EscalationWidget['data']; onTalk?: () => void }) {
  return <Shell tone="teal"><CardHeader icon="user-check" eyebrow="Volunteer assistance" title={data.status} /><Text style={styles.description}>{data.message}</Text>{data.contactAvailable && onTalk && <SmallButton label="Talk to a volunteer" icon="message-circle" onPress={onTalk} />}</Shell>;
}

function InfoRow({ icon, label, value }: { icon: keyof typeof Feather.glyphMap; label: string; value: string }) {
  return <View style={styles.infoRow}><Feather name={icon} size={14} color={colors.light.inkSoft} /><Text style={styles.infoLabel}>{label}</Text><Text style={styles.infoValue}>{value}</Text></View>;
}

const styles = StyleSheet.create({
  shell: { backgroundColor: colors.light.card, borderRadius: colors.radius, borderWidth: 1, borderColor: colors.light.border, padding: 15, marginBottom: 12, shadowColor: '#3a2d22', shadowOpacity: 0.04, shadowRadius: 12, shadowOffset: { width: 0, height: 5 }, elevation: 2 },
  orange: { backgroundColor: colors.light.orangeSoft, borderColor: '#f0c9b7' },
  teal: { backgroundColor: colors.light.tealSoft, borderColor: '#c5dcd0' },
  yellow: { backgroundColor: colors.light.yellowSoft, borderColor: '#ebd898' },
  red: { backgroundColor: '#fae4df', borderColor: '#efc2b8' },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 15 },
  iconBox: { width: 34, height: 34, borderRadius: 11, backgroundColor: colors.light.white, alignItems: 'center', justifyContent: 'center' },
  headerText: { flex: 1 },
  eyebrow: { color: colors.light.mutedForeground, fontFamily: 'Inter_600SemiBold', fontSize: 10, letterSpacing: 1 },
  cardTitle: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 16, marginTop: 2 },
  crowdRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end' },
  metric: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 34, letterSpacing: -1 },
  metricLabel: { color: colors.light.inkSoft, fontFamily: 'Inter_500Medium', fontSize: 12 },
  meter: { flex: 1, height: 10, borderRadius: 5, backgroundColor: '#efc7b8', marginLeft: 18, marginBottom: 6, overflow: 'hidden', position: 'relative' },
  meterFill: { height: '100%', backgroundColor: colors.light.primary, borderRadius: 5 },
  meterDot: { position: 'absolute', right: 5, top: 3, width: 4, height: 4, borderRadius: 2, backgroundColor: colors.light.white },
  updated: { color: colors.light.mutedForeground, fontFamily: 'Inter_400Regular', fontSize: 11, marginTop: 10 },
  smallButton: { minHeight: 38, borderRadius: 12, backgroundColor: colors.light.white, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 7, paddingHorizontal: 13, marginTop: 13, alignSelf: 'flex-start' },
  dangerButton: { backgroundColor: colors.light.destructive },
  smallButtonText: { color: colors.light.teal, fontFamily: 'Inter_600SemiBold', fontSize: 12 },
  dangerText: { color: colors.light.white },
  pressed: { opacity: 0.7, transform: [{ scale: 0.98 }] },
  forecastCaption: { color: colors.light.inkSoft, fontFamily: 'Inter_500Medium', fontSize: 12, marginBottom: 8 },
  chart: { height: 115, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
  chartItem: { alignItems: 'center', flex: 1, height: '100%', justifyContent: 'flex-end' },
  chartValue: { color: colors.light.accentForeground, fontSize: 10, fontFamily: 'Inter_600SemiBold', marginBottom: 4 },
  barTrack: { height: 72, width: 17, backgroundColor: '#f2df9f', borderRadius: 9, justifyContent: 'flex-end', overflow: 'hidden' },
  bar: { width: '100%', backgroundColor: colors.light.primary, borderRadius: 9 },
  chartTime: { color: colors.light.mutedForeground, fontSize: 9, fontFamily: 'Inter_500Medium', marginTop: 5 },
  recommendation: { flexDirection: 'row', gap: 7, backgroundColor: '#fff8e3', borderRadius: 10, padding: 9, marginTop: 12 },
  recommendationText: { flex: 1, color: colors.light.accentForeground, fontFamily: 'Inter_500Medium', fontSize: 11, lineHeight: 16 },
  routeLine: { minHeight: 81, marginLeft: 4, paddingLeft: 14, borderLeftWidth: 1, borderLeftColor: colors.light.mapLine, justifyContent: 'space-between', position: 'relative' },
  routeDot: { position: 'absolute', left: -5, top: 2, width: 9, height: 9, borderRadius: 5, backgroundColor: colors.light.teal, borderWidth: 2, borderColor: colors.light.tealSoft },
  routeDotEnd: { top: undefined, bottom: 2, backgroundColor: colors.light.primary },
  routePath: { flex: 1 },
  routeLabel: { color: colors.light.foreground, fontFamily: 'Inter_600SemiBold', fontSize: 12 },
  routeMeta: { flexDirection: 'row', gap: 30, marginTop: 15 },
  metaLabel: { color: colors.light.mutedForeground, fontFamily: 'Inter_600SemiBold', fontSize: 9, letterSpacing: 1 },
  metaValue: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 13, marginTop: 2 },
  avoid: { color: colors.light.destructive, fontFamily: 'Inter_500Medium', fontSize: 11, marginTop: 12 },
  facilityDistance: { color: colors.light.foreground, fontFamily: 'Inter_700Bold', fontSize: 21 },
  muted: { color: colors.light.inkSoft, fontFamily: 'Inter_400Regular', fontSize: 13 },
  available: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 8 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.light.teal },
  availableText: { color: colors.light.teal, fontFamily: 'Inter_500Medium', fontSize: 11 },
  description: { color: colors.light.inkSoft, fontFamily: 'Inter_400Regular', lineHeight: 18, fontSize: 12 },
  infoRow: { flexDirection: 'row', alignItems: 'center', gap: 7, minHeight: 26 },
  infoLabel: { color: colors.light.mutedForeground, fontFamily: 'Inter_500Medium', fontSize: 11, minWidth: 70 },
  infoValue: { flex: 1, color: colors.light.foreground, fontFamily: 'Inter_600SemiBold', fontSize: 12 },
  statusPill: { flexDirection: 'row', alignItems: 'center', gap: 7, alignSelf: 'flex-start', backgroundColor: colors.light.white, borderRadius: 20, paddingHorizontal: 10, paddingVertical: 7, marginBottom: 10 },
  statusText: { color: colors.light.foreground, fontFamily: 'Inter_600SemiBold', fontSize: 11 },
});