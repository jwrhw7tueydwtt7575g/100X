export type Language = 'mr' | 'hi' | 'en';

export type User = {
  id: string;
  phoneNumber: string;
  name?: string;
  isAuthenticated: boolean;
  createdAt: string;
};

export type MessageRole = 'user' | 'assistant' | 'system';

export type CrowdStatus = 'LOW' | 'MODERATE' | 'HIGH' | 'VERY_HIGH';

export type ToolWidget =
  | CrowdDensityWidget
  | ForecastWidget
  | RouteWidget
  | FacilityWidget
  | TempleInfoWidget
  | LostFoundWidget
  | SOSWidget
  | EscalationWidget;

export type Message = {
  id: string;
  role: MessageRole;
  text?: string;
  timestamp: string;
  language?: Language;
  isVoice?: boolean;
  widgets?: ToolWidget[];
};

export type ConversationResponse = {
  sessionId: string;
  messageId: string;
  language: Language;
  responseText: string;
  widgets?: ToolWidget[];
};

export type CrowdDensityWidget = {
  type: 'crowd_density';
  data: {
    zoneId: string;
    zoneName: string;
    density: number;
    status: CrowdStatus;
    latitude?: number;
    longitude?: number;
    updatedAt: string;
  };
};

export type ForecastWidget = {
  type: 'congestion_forecast';
  data: {
    zoneId: string;
    zoneName: string;
    points: { time: string; value: number }[];
    recommendation?: string;
    updatedAt: string;
  };
};

export type RouteWidget = {
  type: 'route_guidance';
  data: {
    origin: { latitude: number; longitude: number; label?: string };
    destination: { latitude: number; longitude: number; label?: string };
    routeCoordinates: { latitude: number; longitude: number }[];
    estimatedTime?: string;
    distance?: string;
    avoidAreas?: string[];
  };
};

export type FacilityWidget = {
  type: 'nearby_facility';
  data: {
    category: 'medical' | 'water' | 'toilet' | 'rest' | 'food' | 'accommodation';
    name: string;
    distance?: string;
    latitude?: number;
    longitude?: number;
    availability?: string;
    contact?: string;
    phone?: string;
  };
};

export type TempleInfoWidget = {
  type: 'temple_info';
  data: {
    title: string;
    timings?: string;
    rituals?: string[];
    events?: string[];
    description?: string;
  };
};

export type LostFoundWidget = {
  type: 'lost_and_found';
  data: {
    incidentType: 'PERSON' | 'ITEM';
    status: string;
    referenceId?: string;
    nextAction?: string;
  };
};

export type SOSWidget = {
  type: 'sos';
  data: {
    status: 'CONFIRMATION_REQUIRED' | 'PROCESSING' | 'ACTIVATED' | 'FAILED';
    message: string;
    controlRoomStatus?: string;
    timestamp?: string;
  };
};

export type EscalationWidget = {
  type: 'human_escalation';
  data: {
    status: string;
    message: string;
    contactAvailable?: boolean;
  };
};

export type LocationState = {
  latitude: number | null;
  longitude: number | null;
  permission: 'unknown' | 'granted' | 'denied';
};

export type Copy = {
  greeting: string;
  greetingSub: string;
  placeholder: string;
  listening: string;
  understanding: string;
  checking: string;
  crowd: string;
  facility: string;
  route: string;
  temple: string;
  help: string;
  settings: string;
  map: string;
  send: string;
  speak: string;
  stop: string;
  viewMap: string;
  viewRoute: string;
  nearby: string;
  poorConnection: string;
  getStarted: string;
  chooseLanguage: string;
  continueLabel: string;
  welcomeDescription: string;
  noMessages: string;
  helpTitle: string;
  helpDescription: string;
  emergency: string;
  emergencyPrompt: string;
  confirmSOS: string;
  cancel: string;
  location: string;
  allowLocation: string;
  locationDenied: string;
  clearConversation: string;
  readAloud: string;
  voiceInput: string;
  language: string;
  about: string;
  liveMap: string;
  recenter: string;
  recent: string;
  updated: string;
};