import { Feather } from '@expo/vector-icons';
import React, { useEffect, useId, useRef } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import colors from '@/constants/colors';
import type { LocationState } from '@/types/domain';

const PANDHARPUR_CENTER = { lat: 17.6778, lng: 75.326 };

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

export function MapCanvas({
  mode = 'crowd',
  location,
  onRecenter,
}: {
  mode?: 'crowd' | 'route';
  location: LocationState;
  onRecenter?: () => void;
}) {
  const containerId = useId().replace(/:/g, '_');
  const iframeRef = useRef<any>(null);

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
        const map = L.map('map', { zoomControl: false }).setView([${PANDHARPUR_CENTER.lat}, ${PANDHARPUR_CENTER.lng}], 15);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
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
        `,
              ).join('\n')
            : ''
        }

        ${
          mode === 'route'
            ? `
          const routeLine = L.polyline(${JSON.stringify(ROUTE_COORDS)}, {
            color: '#e06435',
            weight: 5,
            opacity: 0.85,
            dashArray: '8, 8'
          }).addTo(map);
          map.fitBounds(routeLine.getBounds(), { padding: [30, 30] });
        `
            : ''
        }

        // User location marker
        const userLat = ${location.latitude || PANDHARPUR_CENTER.lat};
        const userLng = ${location.longitude || PANDHARPUR_CENTER.lng};
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
        <View style={styles.fallback}>
          <Text style={styles.fallbackText}>Leaflet map loading...</Text>
        </View>
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