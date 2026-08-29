import type { Language } from '@/types/domain';

export interface SpeechService {
  startRecording(): Promise<void>;
  stopRecording(language: Language): Promise<string>;
  cancelRecording(): Promise<void>;
}

export interface TextToSpeechService {
  speak(text: string, language: Language): Promise<void>;
  stop(): Promise<void>;
}

const languageTag: Record<Language, string> = { mr: 'mr-IN', hi: 'hi-IN', en: 'en-IN' };

export const speechService: SpeechService = {
  async startRecording() {},
  async stopRecording(language) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    return language === 'mr' ? 'गेट नंबर ३ वर गर्दी किती आहे?' : language === 'hi' ? 'मेरे पास मेडिकल सेंटर कहाँ है?' : 'How crowded is Gate 3?';
  },
  async cancelRecording() {},
};

export const textToSpeechService: TextToSpeechService = {
  async speak(text, language) {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = languageTag[language];
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    }
  },
  async stop() {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
  },
};