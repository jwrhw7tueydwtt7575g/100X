import { getApiBaseUrl } from '@/services/api';
import type { Language } from '@/types/domain';

export interface SpeechService {
  startRecording(language?: Language): Promise<void>;
  stopRecording(language: Language): Promise<string>;
  cancelRecording(): Promise<void>;
}

export interface TextToSpeechService {
  speak(text: string, language: Language): Promise<void>;
  stop(): Promise<void>;
}

const languageTag: Record<Language, string> = { mr: 'mr-IN', hi: 'hi-IN', en: 'en-IN' };

let activeRecognition: any = null;
let activeMediaRecorder: any = null;
let activeAudioChunks: Blob[] = [];
let liveTranscript = '';

export const speechService: SpeechService = {
  async startRecording(language: Language = 'en') {
    liveTranscript = '';
    activeAudioChunks = [];

    if (typeof window !== 'undefined') {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SpeechRecognition) {
        try {
          const recognition = new SpeechRecognition();
          recognition.continuous = true;
          recognition.interimResults = true;
          recognition.lang = languageTag[language] || 'en-IN';
          recognition.onresult = (event: any) => {
            let current = '';
            for (let i = 0; i < event.results.length; i++) {
              current += event.results[i][0].transcript;
            }
            if (current.trim()) {
              liveTranscript = current.trim();
            }
          };
          recognition.onerror = (err: any) => {
            console.warn('SpeechRecognition error:', err);
          };
          recognition.start();
          activeRecognition = recognition;
        } catch (e) {
          console.warn('Failed to start Web SpeechRecognition:', e);
        }
      }

      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const recorder = new (window as any).MediaRecorder(stream);
          activeAudioChunks = [];
          recorder.ondataavailable = (event: any) => {
            if (event.data && event.data.size > 0) {
              activeAudioChunks.push(event.data);
            }
          };
          recorder.start(100);
          activeMediaRecorder = recorder;
        } catch (e) {
          console.warn('Failed to start MediaRecorder:', e);
        }
      }
    }
  },

  async stopRecording(language: Language): Promise<string> {
    if (activeRecognition) {
      try {
        activeRecognition.stop();
      } catch {}
      activeRecognition = null;
    }

    let recordedBlob: Blob | null = null;
    if (activeMediaRecorder) {
      await new Promise<void>((resolve) => {
        try {
          activeMediaRecorder.onstop = () => {
            if (activeAudioChunks.length > 0) {
              recordedBlob = new Blob(activeAudioChunks, { type: 'audio/webm' });
            }
            resolve();
          };
          activeMediaRecorder.stop();
          if (activeMediaRecorder.stream) {
            activeMediaRecorder.stream.getTracks().forEach((track: any) => track.stop());
          }
        } catch {
          resolve();
        }
      });
      activeMediaRecorder = null;
    }

    const finalBlob: any = recordedBlob;
    if (finalBlob && finalBlob.size > 100) {
      try {
        const formData = new FormData();
        formData.append('file', finalBlob, 'speech.webm');
        formData.append('language', language);
        const resp = await fetch(`${getApiBaseUrl()}/api/voice/transcribe`, {
          method: 'POST',
          body: formData,
        });
        if (resp.ok) {
          const data = await resp.json();
          if (data.transcript && data.transcript.trim()) {
            return data.transcript.trim();
          }
        }
      } catch (err) {
        console.warn('Backend OpenAI Whisper STT failed, falling back to live transcript:', err);
      }
    }

    if (liveTranscript.trim()) {
      return liveTranscript.trim();
    }

    return '';
  },

  async cancelRecording() {
    if (activeRecognition) {
      try {
        activeRecognition.abort();
      } catch {}
      activeRecognition = null;
    }
    if (activeMediaRecorder) {
      try {
        activeMediaRecorder.stop();
        if (activeMediaRecorder.stream) {
          activeMediaRecorder.stream.getTracks().forEach((track: any) => track.stop());
        }
      } catch {}
      activeMediaRecorder = null;
    }
    activeAudioChunks = [];
    liveTranscript = '';
  },
};

let activeAudioElement: any = null;

function getExpoSpeech(): any {
  try {
    const name = 'expo-speech';
    return require(name);
  } catch {
    return null;
  }
}

export const textToSpeechService: TextToSpeechService = {
  async speak(text: string, language: Language) {
    if (!text || !text.trim()) return;
    const cleanText = text.trim();
    const langTag = languageTag[language] || 'en-IN';

    // 1. Try native expo-speech on iOS/Android physical mobile devices
    const Speech = getExpoSpeech();
    if (Speech && typeof Speech.speak === 'function') {
      try {
        if (typeof Speech.stop === 'function') {
          Speech.stop();
        }
        Speech.speak(cleanText, {
          language: langTag,
          pitch: 1.0,
          rate: 0.95,
        });
        return;
      } catch (err) {
        console.warn('expo-speech native playback failed, trying HTML5/Web Audio:', err);
      }
    }

    // 2. Try HTML5 Audio (Web or Web-view)
    if (typeof window !== 'undefined' && typeof (window as any).Audio !== 'undefined') {
      if (activeAudioElement) {
        try { activeAudioElement.pause(); } catch {}
        activeAudioElement = null;
      }
      if ('speechSynthesis' in window) {
        try { window.speechSynthesis.cancel(); } catch {}
      }

      try {
        const audioUrl = `${getApiBaseUrl()}/api/voice/speak?text=${encodeURIComponent(cleanText)}&language=${encodeURIComponent(language)}`;
        const audio = new (window as any).Audio(audioUrl);
        activeAudioElement = audio;
        await audio.play();
        return;
      } catch (err) {
        console.warn('Backend TTS audio playback failed, falling back to Web SpeechSynthesis:', err);
      }

      // 3. Try Web SpeechSynthesis
      if ('speechSynthesis' in window) {
        try {
          const utterance = new SpeechSynthesisUtterance(cleanText);
          utterance.lang = langTag;
          window.speechSynthesis.speak(utterance);
        } catch (e) {
          console.warn('Web SpeechSynthesis failed:', e);
        }
      }
    }
  },

  async stop() {
    const Speech = getExpoSpeech();
    if (Speech && typeof Speech.stop === 'function') {
      try { Speech.stop(); } catch {}
    }
    if (typeof window !== 'undefined') {
      if (activeAudioElement) {
        try { activeAudioElement.pause(); } catch {}
        activeAudioElement = null;
      }
      if ('speechSynthesis' in window) {
        try { window.speechSynthesis.cancel(); } catch {}
      }
    }
  },
};