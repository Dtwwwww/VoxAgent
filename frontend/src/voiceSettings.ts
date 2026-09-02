import type { VoiceInfo, VoiceSpeed } from "./protocol";

export interface VoiceSettings {
  voiceKey: string | null;
  speed: VoiceSpeed;
  speakTextReplies: boolean;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const VOICE_SETTINGS_KEY = "voxagent.voice-settings.v1";
const LEGACY_VOICE_KEY = "voxagent.voice";
const defaults: VoiceSettings = { voiceKey: null, speed: 1.0, speakTextReplies: false };

function speed(value: unknown): VoiceSpeed {
  return value === 0.8 || value === 1.0 || value === 1.2 ? value : 1.0;
}

export function loadVoiceSettings(storage: StorageLike): VoiceSettings {
  try {
    const parsed: unknown = JSON.parse(storage.getItem(VOICE_SETTINGS_KEY) ?? "null");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return defaults;
    const value = parsed as Record<string, unknown>;
    return {
      voiceKey: typeof value.voiceKey === "string" ? value.voiceKey : null,
      speed: speed(value.speed),
      speakTextReplies: value.speakTextReplies === true,
    };
  } catch {
    return defaults;
  }
}

export function saveVoiceSettings(storage: StorageLike, settings: VoiceSettings): void {
  storage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({
    voiceKey: settings.voiceKey,
    speed: speed(settings.speed),
    speakTextReplies: settings.speakTextReplies === true,
  }));
  storage.removeItem(LEGACY_VOICE_KEY);
}

export function reconcileVoiceSettings(settings: VoiceSettings, voices: readonly VoiceInfo[]): VoiceSettings {
  if (settings.voiceKey && voices.some((voice) => voice.voice_key === settings.voiceKey)) return settings;
  const fallback = voices.find((voice) => voice.is_default) ?? voices[0];
  return { ...settings, voiceKey: fallback?.voice_key ?? null };
}
