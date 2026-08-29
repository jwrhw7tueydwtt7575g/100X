import Constants from 'expo-constants';
import type { ConversationResponse, Language, ToolWidget } from '@/types/domain';

/**
 * Real backend client.
 *
 * The backend speaks snake_case; this app speaks camelCase. The conversion is
 * done here, once, with a recursive key transform rather than field-by-field.
 *
 * Why not camelCase aliases on the backend instead? Because widget `data` is an
 * untyped dict there — Pydantic aliases only rename declared model fields, so
 * `zone_id`, `updated_at`, `route_coordinates` and the rest would come through
 * unchanged no matter what aliases were configured. A transform on this side is
 * the only thing that covers them.
 */

export type MessageRequest = {
  sessionId: string;
  language: Language;
  message: string;
  isVoice?: boolean;
  latitude?: number | null;
  longitude?: number | null;
};

export const DEFAULT_SESSION_ID = 'wariverse-session';

/** Milliseconds before a request is abandoned. Pilgrims are often on 2G. */
const REQUEST_TIMEOUT_MS = 20_000;

export function getApiBaseUrl(): string {
  const configured = process.env.EXPO_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/+$/, '');

  // On a physical device `localhost` is the phone itself, so fall back to the
  // host running Metro.
  const hostUri =
    Constants.expoConfig?.hostUri ??
    (Constants as any).manifest2?.extra?.expoGo?.debuggerHost;
  if (hostUri) {
    const host = String(hostUri).split(':')[0];
    if (host && host !== 'localhost' && host !== '127.0.0.1') {
      return `http://${host}:8000`;
    }
  }
  return 'http://localhost:8000';
}

/* -------------------------------------------------------------------------- */
/* snake_case → camelCase                                                      */
/* -------------------------------------------------------------------------- */

function toCamel(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_, character: string) => character.toUpperCase());
}

/**
 * Deep-converts object keys. Arrays are walked, primitives pass through, and
 * `Date` is left alone — though the backend sends strings, so it should not
 * appear.
 */
export function camelizeKeys<T = unknown>(input: unknown): T {
  if (Array.isArray(input)) {
    return input.map((item) => camelizeKeys(item)) as unknown as T;
  }
  if (input !== null && typeof input === 'object') {
    const output: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input as Record<string, unknown>)) {
      output[toCamel(key)] = camelizeKeys(value);
    }
    return output as T;
  }
  return input as T;
}

/* -------------------------------------------------------------------------- */
/* transport                                                                   */
/* -------------------------------------------------------------------------- */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly requestId?: string
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(
  path: string,
  body: unknown,
  token?: string | null
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const payload = await response.json().catch(() => null);

    if (!response.ok) {
      // The backend wraps errors as { error: { code, message }, request_id }.
      const message =
        payload?.error?.message ?? `Request failed with status ${response.status}`;
      throw new ApiError(message, response.status, payload?.request_id);
    }
    return camelizeKeys<T>(payload);
  } finally {
    clearTimeout(timer);
  }
}

/* -------------------------------------------------------------------------- */
/* endpoints                                                                   */
/* -------------------------------------------------------------------------- */

export const conversationApi = {
  async sendMessage(
    input: MessageRequest,
    token?: string | null
  ): Promise<ConversationResponse> {
    const body: Record<string, unknown> = {
      session_id: input.sessionId,
      language: input.language,
      message: input.message,
      is_voice: input.isVoice ?? false,
    };
    // Only send coordinates we actually have — null would fail validation.
    if (typeof input.latitude === 'number' && typeof input.longitude === 'number') {
      body.latitude = input.latitude;
      body.longitude = input.longitude;
    }
    return request<ConversationResponse>('/api/conversation/message', body, token);
  },

  async confirmSOS(
    language: Language,
    sessionId: string = DEFAULT_SESSION_ID,
    token?: string | null
  ): Promise<ConversationResponse> {
    return request<ConversationResponse>(
      '/api/conversation/sos/confirm',
      { session_id: sessionId, language },
      token
    );
  },
};

export type { ToolWidget };
