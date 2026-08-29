import AsyncStorage from '@react-native-async-storage/async-storage';
import { Feather } from '@expo/vector-icons';
import React, { useEffect, useId, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { WebView } from 'react-native-webview';
import colors from '@/constants/colors';
import { communityApi, type CommunityServiceItem } from '@/services/api';
import type { LocationState } from '@/types/domain';

const PANDHARPUR_CENTER = { lat: 17.6778, lng: 75.326 };
const MY_SEVAS_KEY = 'wariverse-my-sevas';

const ZONES = [
  { name: 'Gate 2', density: 46, lat: 17.679, lng: 75.3245, color: '#6d9b78' },
  { name: 'Gate 3', density: 82, lat: 17.6812, lng: 75.327, color: '#e06435' },
  { name: 'Temple', density: 64, lat: 17.6775, lng: 75.3283, color: '#d59e2e' },
];

const ROUTE_COORDS = [
  [17.679, 75.3245],
  [17.6812, 75.327],
  [17.6775, 75.3283],
];

const MAPBOX_TOKEN = process.env.EXPO_PUBLIC_MAPBOX_TOKEN || '';

export function MapCanvas({
  mode = 'crowd',
  location,
  destLat,
  destLng,
  destName,
  destPhone,
  onRecenter,
}: {
  mode?: 'crowd' | 'route';
  location: LocationState;
  destLat?: number;
  destLng?: number;
  destName?: string;
  destPhone?: string;
  onRecenter?: () => void;
}) {
  const containerId = useId().replace(/:/g, '_');
  const iframeRef = useRef<any>(null);
  const webviewRef = useRef<WebView>(null);
  const [sevas, setSevas] = useState<CommunityServiceItem[]>([]);
  const [routeCoords, setRouteCoords] = useState<[number, number][] | null>(null);

  useEffect(() => {
    let isMounted = true;
    Promise.all([
      communityApi
        .list(location.latitude ?? undefined, location.longitude ?? undefined)
        .then((res) => res.services || [])
        .catch(() => []),
      AsyncStorage.getItem(MY_SEVAS_KEY)
        .then((res) => (res ? (JSON.parse(res) as CommunityServiceItem[]) : []))
        .catch(() => []),
    ]).then(([backendSevas, localSevas]) => {
      if (!isMounted) return;
      const map = new Map<string, CommunityServiceItem>();
      [...backendSevas, ...localSevas].forEach((item) => {
        if (item && item.id && item.isActive !== false) {
          map.set(item.id, item);
        }
      });
      setSevas(Array.from(map.values()));
    });

    return () => {
      isMounted = false;
    };
  }, [location.latitude, location.longitude]);

  useEffect(() => {
    if (!destLat || !destLng || !MAPBOX_TOKEN) {
      setRouteCoords(null);
      return;
    }
    const userLat = location.latitude || PANDHARPUR_CENTER.lat;
    const userLng = location.longitude || PANDHARPUR_CENTER.lng;

    const url = `https://api.mapbox.com/directions/v5/mapbox/walking/${userLng},${userLat};${destLng},${destLat}?geometries=geojson&access_token=${MAPBOX_TOKEN}`;
    fetch(url)
      .then((res) => res.json())
      .then((data) => {
        if (data && data.routes && data.routes[0]) {
          const coords = data.routes[0].geometry.coordinates.map((c: [number, number]) => [c[1], c[0]] as [number, number]);
          setRouteCoords(coords);
        } else {
          setRouteCoords([[userLat, userLng], [destLat, destLng]]);
        }
      })
      .catch(() => {
        setRouteCoords([[userLat, userLng], [destLat, destLng]]);
      });
  }, [location.latitude, location.longitude, destLat, destLng]);

  const sevaMarkersScript = sevas
    .map(
      (s) => `
        L.marker([${s.latitude}, ${s.longitude}], {
          icon: L.divIcon({
            className: 'custom-badge-wrap',
            html: '<div class="custom-badge seva-badge" style="border-color: #0d9488; background: #ccfbf1; color: #0f766e; font-weight: 700;">🚩 SEVA: ${s.title.replace(
              /'/g,
              "\\'"
            )}</div>',
            iconSize: [120, 24],
            iconAnchor: [60, 12]
          })
        }).addTo(map).bindPopup('<b>🚩 ${s.title.replace(/'/g, "\\'")}</b><br/><b>Provider:</b> ${s.providerName.replace(
        /'/g,
        "\\'"
      )}<br/>📍 ${s.address.replace(/'/g, "\\'")}<br/>📞 Tel: ${s.contactPhone}');
      `
    )
    .join('\n');

  const liveRouteScript = destLat && destLng
    ? `
      const destLat = ${destLat};
      const destLng = ${destLng};
      const destTitle = "${(destName || 'Destination').replace(/'/g, "\\'")}";
      const destPhone = "${(destPhone || '').replace(/'/g, "\\'")}";
      const coords = ${JSON.stringify(routeCoords || [[location.latitude || PANDHARPUR_CENTER.lat, location.longitude || PANDHARPUR_CENTER.lng], [destLat, destLng]])};
      const routeLine = L.polyline(coords, {
        color: '#0d9488',
        weight: 6,
        opacity: 0.9
      }).addTo(map);

      const destMarker = L.marker([destLat, destLng], {
        icon: L.divIcon({
          className: 'custom-badge-wrap',
          html: '<div class="custom-badge" style="border-color: #0d9488; background: #0d9488; color: white; font-weight: 700;">📍 ' + destTitle + '</div>',
          iconSize: [140, 26],
          iconAnchor: [70, 13]
        })
      }).addTo(map).bindPopup('<b>📍 ' + destTitle + '</b>' + (destPhone ? '<br/>📞 Phone: <a href="tel:' + destPhone + '">' + destPhone + '</a>' : ''));

      map.fitBounds(routeLine.getBounds(), { padding: [40, 40] });
    `
    : mode === 'route'
    ? `
      if (!locationHasGps) {
        const routeLine = L.polyline(${JSON.stringify(ROUTE_COORDS)}, {
          color: '#e06435',
          weight: 5,
          opacity: 0.85,
          dashArray: '8, 8'
        }).addTo(map);
        map.fitBounds(routeLine.getBounds(), { padding: [30, 30] });
      } else {
        map.setView([userLat, userLng], 16);
      }
    `
    : '';

  const htmlContent = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
      <style>
        html, body, #map { height: 100%; margin: 0; padding: 0; background: #eef3e9; font-family: sans-serif; }
        .custom-badge {
          background: white;
          border-radius: 12px;
          padding: 5px 9px;
          border: 2px solid #2d6a4f;
          font-weight: 700;
          font-size: 11px;
          box-shadow: 0 2px 6px rgba(0,0,0,0.15);
          white-space: nowrap;
        }
        .user-dot {
          width: 18px;
          height: 18px;
          background: #2d6a4f;
          border: 3px solid white;
          border-radius: 50%;
          box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        .leaflet-control-attribution { display: none !important; }
      </style>
    </head>
    <body>
      <div id="map"></div>
      <script>
        const userLat = ${location.latitude || PANDHARPUR_CENTER.lat};
        const userLng = ${location.longitude || PANDHARPUR_CENTER.lng};
        const locationHasGps = ${location.latitude !== null && location.longitude !== null};
        const map = L.map('map', { zoomControl: false }).setView([userLat, userLng], 15);
        L.tileLayer('https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/256/{z}/{x}/{y}@2x?access_token=${MAPBOX_TOKEN}', {
          maxZoom: 19
        }).addTo(map);

        ${
          mode === 'crowd'
            ? ZONES.map(
                (z) => `
          L.marker([${z.lat}, ${z.lng}], {
            icon: L.divIcon({
              className: 'custom-badge-wrap',
              html: '<div class="custom-badge" style="border-color: ${z.color}; color: ${z.color};">${z.name} • ${z.density}%</div>',
              iconSize: [80, 24],
              iconAnchor: [40, 12]
            })
          }).addTo(map).bindPopup('<b>${z.name}</b><br/>Crowd density: ${z.density}%');
        `
              ).join('\n')
            : ''
        }

        /* Render Live Walking Route if Destination active */
        ${liveRouteScript}

        /* Render Community Seva Offerings */
        ${sevaMarkersScript}

        // User location marker
        L.marker([userLat, userLng], {
          icon: L.divIcon({
            className: 'user-icon',
            html: '<div class="user-dot"></div>',
            iconSize: [18, 18],
            iconAnchor: [9, 9]
          })
        }).addTo(map).bindPopup('You are here');

        window.recenterMap = function() {
          map.flyTo([userLat, userLng], 16, { duration: 1 });
        };
      </script>
    </body>
    </html>
  `;

  const handleRecenter = () => {
    if (onRecenter) onRecenter();
    if (Platform.OS === 'web' && iframeRef.current?.contentWindow?.recenterMap) {
      iframeRef.current.contentWindow.recenterMap();
    } else if (webviewRef.current) {
      webviewRef.current.injectJavaScript('if (window.recenterMap) { window.recenterMap(); } true;');
    }
  };

  return (
    <View style={styles.mapWrap}>
      {Platform.OS === 'web' ? (
        <iframe
          ref={iframeRef}
          id={containerId}
          srcDoc={htmlContent}
          style={styles.iframe as any}
          title="Leaflet Wari Map"
        />
      ) : (
        <WebView
          ref={webviewRef}
          originWhitelist={['*']}
          source={{ html: htmlContent }}
          style={styles.iframe as any}
          javaScriptEnabled={true}
          domStorageEnabled={true}
        />
      )}

      {location.permission === 'granted' && (
        <View style={styles.locationLabel}>
          <View style={styles.liveDot} />
          <Text style={styles.locationText}>You are here</Text>
        </View>
      )}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Recenter map"
        onPress={handleRecenter}
        style={({ pressed }) => [styles.recenter, pressed && { opacity: 0.7 }]}
      >
        <Feather name="crosshair" size={18} color={colors.light.teal} />
        <Text style={styles.recenterText}>Recenter</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  mapWrap: {
    flex: 1,
    minHeight: 390,
    borderRadius: 24,
    overflow: 'hidden',
    position: 'relative',
    borderWidth: 1,
    borderColor: '#c6d7c9',
    backgroundColor: colors.light.map,
  },
  iframe: {
    width: '100%',
    height: '100%',
    borderWidth: 0,
    borderRadius: 24,
  },
  fallback: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  fallbackText: {
    color: colors.light.mutedForeground,
    fontFamily: 'Inter_500Medium',
    fontSize: 12,
  },
  locationLabel: {
    position: 'absolute',
    left: 16,
    bottom: 16,
    backgroundColor: colors.light.white,
    borderRadius: 18,
    paddingHorizontal: 10,
    paddingVertical: 8,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
    elevation: 3,
  },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.light.teal },
  locationText: { color: colors.light.foreground, fontFamily: 'Inter_500Medium', fontSize: 11 },
  recenter: {
    position: 'absolute',
    right: 16,
    bottom: 16,
    backgroundColor: colors.light.white,
    borderRadius: 16,
    paddingHorizontal: 11,
    paddingVertical: 9,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
    elevation: 3,
  },
  recenterText: { color: colors.light.teal, fontFamily: 'Inter_600SemiBold', fontSize: 11 },
});