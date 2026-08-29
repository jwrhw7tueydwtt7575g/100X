import AsyncStorage from '@react-native-async-storage/async-storage';
import type { ConversationResponse, Language, ToolWidget } from '@/types/domain';

type MessageRequest = { sessionId: string; language: Language; message: string; latitude?: number | null; longitude?: number | null };

const now = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

function calcDistanceStr(lat1: number, lon1: number, lat2: number, lon2: number): string {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  const d = R * c;
  return d < 1 ? `${Math.round(d * 1000)} m` : `${d.toFixed(1)} km`;
}

const routeWidget: ToolWidget = {
  type: 'route_guidance',
  data: {
    origin: { latitude: 18.517, longitude: 73.856, label: 'Current location' },
    destination: { latitude: 18.519, longitude: 73.851, label: 'Temple entrance' },
    routeCoordinates: [
      { latitude: 18.517, longitude: 73.856 },
      { latitude: 18.516, longitude: 73.854 },
      { latitude: 18.518, longitude: 73.853 },
      { latitude: 18.519, longitude: 73.851 },
    ],
    estimatedTime: '18 min walk',
    distance: '1.2 km',
    avoidAreas: ['Gate 3 — high congestion'],
  },
};

function crowdWidget(): ToolWidget {
  return {
    type: 'crowd_density',
    data: {
      zoneId: 'gate-3',
      zoneName: 'Gate 3',
      density: 82,
      status: 'HIGH',
      latitude: 18.518,
      longitude: 73.853,
      updatedAt: '2 min ago',
    },
  };
}

function facilityWidget(): ToolWidget {
  return {
    type: 'nearby_facility',
    data: {
      category: 'medical',
      name: 'Wari Medical Center',
      distance: '0.8 km',
      latitude: 18.516,
      longitude: 73.855,
      availability: 'Open · Volunteer staffed',
    },
  };
}

function responseText(language: Language, kind: string, extra?: { category?: string; name?: string; distance?: string }): string {
  if (kind === 'crowd') {
    return language === 'mr' ? 'गेट ३ वर सध्या जास्त गर्दी आहे. शक्य असल्यास थोडा वेळ थांबा.' : language === 'hi' ? 'गेट 3 पर अभी भीड़ ज़्यादा है। संभव हो तो थोड़ी देर रुकें।' : 'Gate 3 is currently busy. If you can, consider waiting a little while.';
  }
  if (kind === 'facility') {
    const cat = extra?.category || 'medical';
    const dist = extra?.distance || '0.3 km';
    const name = extra?.name || 'facility';
    if (cat === 'food') {
      return language === 'mr' ? `तुमच्या जवळ ${name} (${dist} अंतरावर) उपलब्ध आहे.` : language === 'hi' ? `आपके पास ${name} (${dist} दूर) उपलब्ध है।` : `The nearest food & dining option is ${name} (${dist} away).`;
    }
    if (cat === 'accommodation') {
      return language === 'mr' ? `तुमच्या जवळ ${name} (${dist} अंतरावर) राहण्याची सोय आहे.` : language === 'hi' ? `आपके पास ${name} (${dist} दूर) ठहरने की सुविधा है।` : `The nearest stay & accommodation option is ${name} (${dist} away).`;
    }
    if (cat === 'water') {
      return language === 'mr' ? `तुमच्या जवळ ${name} (${dist} अंतरावर) पिण्याचे पाणी उपलब्ध आहे.` : language === 'hi' ? `आपके पास ${name} (${dist} दूर) पीने का पानी उपलब्ध है।` : `The nearest drinking water post is ${name} (${dist} away).`;
    }
    if (cat === 'toilet') {
      return language === 'mr' ? `तुमच्या जवळ ${name} (${dist} अंतरावर) स्वच्छतागृह आहे.` : language === 'hi' ? `आपके पास ${name} (${dist} दूर) शौचालय है।` : `The nearest restroom block is ${name} (${dist} away).`;
    }
    return language === 'mr' ? `तुमच्या जवळ ${name} (${dist} अंतरावर) आहे.` : language === 'hi' ? `आपके पास ${name} (${dist} दूर) है।` : `The nearest medical post is ${name} (${dist} away).`;
  }
  if (kind === 'route') {
    return language === 'mr' ? 'मी तुमच्यासाठी कमी गर्दीचा मार्ग दाखवत आहे.' : language === 'hi' ? 'मैं आपके लिए कम भीड़ वाला रास्ता दिखा रहा हूँ।' : 'I found a quieter route to the temple for you.';
  }
  if (kind === 'forecast') {
    return language === 'mr' ? 'गेट ३ ला भेट देण्यासाठी सकाळी १० पूर्वीचा वेळ चांगला आहे.' : language === 'hi' ? 'गेट 3 जाने के लिए सुबह 10 बजे से पहले का समय बेहतर रहेगा।' : 'Before 10 AM looks like the best time to visit Gate 3.';
  }
  if (kind === 'temple') {
    return language === 'mr' ? 'मंदिराची आजची माहिती येथे आहे.' : language === 'hi' ? 'मंदिर की आज की जानकारी यहाँ है।' : 'Here is the latest temple information.';
  }
  if (kind === 'lost') {
    return language === 'mr' ? 'तुमची हरवलेली व्यक्तीची विनंती स्वयंसेवक टीमकडे पाठवली आहे.' : language === 'hi' ? 'आपके खोए हुए व्यक्ति की सूचना स्वयंसेवक टीम को भेज दी गई है।' : 'Your lost person report has been shared with the volunteer team.';
  }
  if (kind === 'escalation') {
    return language === 'mr' ? 'या प्रश्नासाठी स्वयंसेवक तुमची अधिक चांगली मदत करू शकतात.' : language === 'hi' ? 'इस सवाल में स्वयंसेवक आपकी बेहतर मदद कर सकते हैं।' : 'A volunteer can help you more directly with this request.';
  }
  return language === 'mr' ? 'मी तुमच्या वारीच्या प्रवासात मदत करण्यासाठी येथे आहे.' : language === 'hi' ? 'मैं आपकी वारी यात्रा में मदद करने के लिए यहाँ हूँ।' : 'I’m here to help with your Wari journey.';
}

export const mockConversationApi = {
  async sendMessage(request: MessageRequest): Promise<ConversationResponse> {
    await new Promise((resolve) => setTimeout(resolve, 650));
    const query = request.message.toLowerCase();
    let kind = 'normal';
    let widgets: ToolWidget[] = [];
    let facilityExtra: { category?: string; name?: string; distance?: string } | undefined = undefined;

    if (query.includes('crowd') || query.includes('गर्दी') || query.includes('भीड़') || query.includes('gate') || query.includes('gate-')) {
      kind = 'crowd';
      widgets = [crowdWidget()];
    } else if (
      query.includes('hospital') || query.includes('doctor') || query.includes('medical') || query.includes('facility') ||
      query.includes('toilet') || query.includes('water') || query.includes('food') || query.includes('restaurant') ||
      query.includes('police') || query.includes('station') || query.includes('rest') || query.includes('hotel') ||
      query.includes('stay') || query.includes('lodging') || query.includes('मेडिकल') || query.includes('अन्नछत्र') ||
      query.includes('जेवण') || query.includes('पाणी') || query.includes('शौचालय') || query.includes('सुविधा') ||
      query.includes('हॉस्पिटल') || query.includes('पोलीस')
    ) {
      kind = 'facility';
      let category: 'food' | 'accommodation' | 'water' | 'medical' | 'toilet' | 'rest' = 'medical';
      let name = 'Wari Medical Post & First Aid';
      let distStr = '0.8 km';
      let lat = request.latitude ?? 17.6778;
      let lng = request.longitude ?? 75.3283;
      let phone: string | undefined = undefined;

      if (query.includes('food') || query.includes('restaurant') || query.includes('hotel') || query.includes('dhaba') || query.includes('जेवण') || query.includes('भोजन') || query.includes('अन्नछत्र') || query.includes('खाना') || query.includes('breakfast')) {
        category = 'food';
        name = 'Shree Vitthal Free Food Annachatra';
        distStr = '0.3 km';
      } else if (query.includes('stay') || query.includes('accommodation') || query.includes('lodging') || query.includes('room') || query.includes('मुक्काम') || query.includes('निवास')) {
        category = 'accommodation';
        name = 'Wari Bhakta Niwas & Free Guest Stay';
        distStr = '0.5 km';
      } else if (query.includes('water') || query.includes('paani') || query.includes('पाणी') || query.includes('पानी')) {
        category = 'water';
        name = 'Pure Drinking Water Seva Post';
        distStr = '0.1 km';
      } else if (query.includes('toilet') || query.includes('washroom') || query.includes('restroom') || query.includes('शौचालय') || query.includes('स्वच्छतागृह')) {
        category = 'toilet';
        name = 'Public Sanitation & Washroom Block';
        distStr = '0.4 km';
      } else if (query.includes('police') || query.includes('पोलीस') || query.includes('पुलिस')) {
        name = 'Wari Police Assistance Desk';
        distStr = '0.6 km';
      }

      const token = process.env.EXPO_PUBLIC_MAPBOX_TOKEN || '';
      let mapboxFound = false;
      const userLat = request.latitude ?? 17.6778;
      const userLng = request.longitude ?? 75.3283;

      if (token) {
        try {
          const mapboxCategoryMap: Record<string, string> = {
            medical: 'hospital',
            food: 'restaurant',
            accommodation: 'hotel',
            water: 'drinking_water',
            toilet: 'restroom',
            rest: 'park',
          };
          const mbCat = mapboxCategoryMap[category] || 'restaurant';
          const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(mbCat)}.json?proximity=${userLng},${userLat}&access_token=${token}&country=IN&limit=5`;
          const res = await fetch(url);
          const data = await res.json();
          if (data && data.features && data.features.length > 0) {
            const first = data.features[0];
            name = first.text || first.place_name?.split(',')[0] || name;
            lng = first.center[0];
            lat = first.center[1];
            mapboxFound = true;
            if (first.properties && (first.properties.tel || first.properties.phone)) {
              phone = first.properties.tel || first.properties.phone;
            }
            distStr = calcDistanceStr(userLat, userLng, lat, lng);
          }
        } catch {
          // Fallback gracefully
        }
      }

      // Check if user has published custom Sevas in AsyncStorage if mapbox search didn't run or query asks for free/seva
      if (!mapboxFound || query.includes('free') || query.includes('seva') || query.includes('मोफत') || query.includes('मुफ्त') || query.includes('लंगर')) {
        try {
          const storedSevas = await AsyncStorage.getItem('wariverse-my-sevas');
          if (storedSevas) {
            const parsedSevas = JSON.parse(storedSevas) as any[];
            const matching = parsedSevas.find((s) => s.category === category && s.isActive !== false);
            if (matching) {
              name = `${matching.title} (${matching.providerName})`;
              lat = matching.latitude;
              lng = matching.longitude;
              phone = matching.contactPhone;
              distStr = calcDistanceStr(userLat, userLng, lat, lng);
            }
          }
        } catch {
          // Fallback to default name
        }
      }

      facilityExtra = { category, name, distance: distStr };

      widgets = [{
        type: 'nearby_facility',
        data: {
          category,
          name,
          distance: distStr,
          latitude: lat,
          longitude: lng,
          phone,
          availability: 'Open · 24x7 Staffed',
        },
      }];
    } else if (
      query.includes('route') || query.includes('way') || query.includes('path') || query.includes('direction') ||
      query.includes('reach') || query.includes('show route') || query.includes('temple') || query.includes('रस्ता') ||
      query.includes('रास्ता') || query.includes('मंदिर') || query.includes('मार्ग')
    ) {
      kind = 'route';
      widgets = [routeWidget];
    } else if (query.includes('when') || query.includes('forecast') || query.includes('avoid') || query.includes('कब') || query.includes('वेळ')) {
      kind = 'forecast';
      widgets = [{
        type: 'congestion_forecast',
        data: {
          zoneId: 'gate-3',
          zoneName: 'Gate 3',
          points: [{ time: '8 AM', value: 38 }, { time: '10 AM', value: 62 }, { time: '12 PM', value: 89 }, { time: '2 PM', value: 72 }, { time: '4 PM', value: 54 }],
          recommendation: responseText(request.language, 'forecast'),
          updatedAt: 'Updated just now',
        },
      }];
    } else if (query.includes('temple') || query.includes('दर्शन') || query.includes('मंदिर')) {
      kind = 'temple';
      widgets = [{
        type: 'temple_info',
        data: {
          title: 'Temple information',
          timings: '6:00 AM – 11:00 PM',
          rituals: ['Morning aarti · 6:30 AM', 'Evening aarti · 7:00 PM'],
          description: 'Please follow volunteer guidance and keep walkways clear.',
        },
      }];
    } else if (query.includes('lost') || query.includes('हरव') || query.includes('खो') || query.includes('missing')) {
      kind = 'lost';
      widgets = [{
        type: 'lost_and_found',
        data: { incidentType: 'PERSON', status: 'Searching', referenceId: 'WF-2026-00124', nextAction: 'Stay near the last known location and keep your phone reachable.' },
      }];
    } else if (query.includes('volunteer') || query.includes('human') || query.includes('मदत') || query.includes('help')) {
      kind = 'escalation';
      widgets = [{
        type: 'human_escalation',
        data: { status: 'Volunteer available', message: 'I can connect you with a Wari volunteer for personal assistance.', contactAvailable: true },
      }];
    } else if (query.includes('emergency') || query.includes('sos') || query.includes('आपत्कालीन') || query.includes('आपात')) {
      widgets = [{ type: 'sos', data: { status: 'CONFIRMATION_REQUIRED', message: 'Emergency assistance will be requested and your current location may be shared with the control room.' } }];
    }

    return {
      sessionId: request.sessionId,
      messageId: `assistant-${Date.now()}`,
      language: request.language,
      responseText: responseText(request.language, kind, facilityExtra),
      widgets,
    };
  },
  async confirmSOS(language: Language): Promise<ConversationResponse> {
    await new Promise((resolve) => setTimeout(resolve, 900));
    return {
      sessionId: 'wariverse-session',
      messageId: `sos-${Date.now()}`,
      language,
      responseText: language === 'mr' ? 'मदतीची विनंती पाठवली आहे.' : language === 'hi' ? 'मदद की request भेज दी गई है।' : 'Help has been requested.',
      widgets: [{ type: 'sos', data: { status: 'ACTIVATED', message: language === 'mr' ? 'मदतीची विनंती पाठवली आहे.' : language === 'hi' ? 'मदद की request भेज दी गई है।' : 'Help has been requested.', controlRoomStatus: 'Connected', timestamp: now() } }],
    };
  },
};