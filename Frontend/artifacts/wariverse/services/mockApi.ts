import type { ConversationResponse, Language, ToolWidget } from '@/types/domain';

type MessageRequest = { sessionId: string; language: Language; message: string };

const now = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

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

function responseText(language: Language, kind: string): string {
  if (kind === 'crowd') {
    return language === 'mr' ? 'गेट ३ वर सध्या जास्त गर्दी आहे. शक्य असल्यास थोडा वेळ थांबा.' : language === 'hi' ? 'गेट 3 पर अभी भीड़ ज़्यादा है। संभव हो तो थोड़ी देर रुकें।' : 'Gate 3 is currently busy. If you can, consider waiting a little while.';
  }
  if (kind === 'facility') {
    return language === 'mr' ? 'तुमच्या जवळचे मेडिकल सेंटर 800 मीटर अंतरावर आहे.' : language === 'hi' ? 'आपके पास मेडिकल सेंटर 800 मीटर की दूरी पर है।' : 'The nearest medical center is 0.8 km away.';
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

    if (query.includes('crowd') || query.includes('गर्दी') || query.includes('भीड़') || query.includes('gate')) {
      kind = 'crowd';
      widgets = [crowdWidget()];
    } else if (query.includes('medical') || query.includes('facility') || query.includes('मेडिकल') || query.includes('सुविधा')) {
      kind = 'facility';
      widgets = [facilityWidget()];
    } else if (query.includes('route') || query.includes('temple') || query.includes('रस्ता') || query.includes('रास्ता') || query.includes('मंदिर')) {
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
      responseText: responseText(request.language, kind),
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