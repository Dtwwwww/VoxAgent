import type { VoiceInfo, VoiceSpeed } from "./protocol";

export interface VoiceSettings {
  voiceKey: string | null;
  speed: VoiceSpeed;
  speechMode: "online-preferred" | "local-only";
  microphoneDeviceId: string | null;
  microphoneLabel: string | null;
  browserVoiceKey: string | null;
  onlineSpeechNoticeAccepted: boolean;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const VOICE_SETTINGS_KEY = "voxagent.voice-settings.v2";
const V1_VOICE_SETTINGS_KEY = "voxagent.voice-settings.v1";
const LEGACY_VOICE_KEY = "voxagent.voice";
const defaults: VoiceSettings = {
  voiceKey: null,
  speed: 1.0,
  speechMode: "online-preferred",
  microphoneDeviceId: null,
  microphoneLabel: null,
  browserVoiceKey: null,
  onlineSpeechNoticeAccepted: false,
};

function speed(value: unknown): VoiceSpeed {
  return value === 0.8 || value === 1.0 || value === 1.2 ? value : 1.0;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function parseSettings(raw: string | null): VoiceSettings {
  const parsed: unknown = JSON.parse(raw ?? "null");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { ...defaults };
  const value = parsed as Record<string, unknown>;
  return {
    voiceKey: optionalString(value.voiceKey),
    speed: speed(value.speed),
    speechMode: value.speechMode === "local-only" ? "local-only" : "online-preferred",
    microphoneDeviceId: optionalString(value.microphoneDeviceId),
    microphoneLabel: optionalString(value.microphoneLabel),
    browserVoiceKey: optionalString(value.browserVoiceKey),
    onlineSpeechNoticeAccepted: value.onlineSpeechNoticeAccepted === true,
  };
}

function migrateV1Settings(raw: string): VoiceSettings {
  const parsed: unknown = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { ...defaults };
  const value = parsed as Record<string, unknown>;
  return {
    ...defaults,
    voiceKey: optionalString(value.voiceKey),
    speed: speed(value.speed),
  };
}

export function loadVoiceSettings(storage: StorageLike): VoiceSettings {
  const current = storage.getItem(VOICE_SETTINGS_KEY);
  try {
    if (current !== null) return parseSettings(current);
  } catch {
    return { ...defaults };
  }

  const legacy = storage.getItem(V1_VOICE_SETTINGS_KEY);
  if (legacy === null) return { ...defaults };
  let migrated: VoiceSettings;
  try {
    migrated = migrateV1Settings(legacy);
  } catch {
    migrated = { ...defaults };
  }
  saveVoiceSettings(storage, migrated);
  storage.removeItem(V1_VOICE_SETTINGS_KEY);
  return migrated;
}

export function saveVoiceSettings(storage: StorageLike, settings: VoiceSettings): void {
  storage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({
    voiceKey: settings.voiceKey,
    speed: speed(settings.speed),
    speechMode: settings.speechMode,
    microphoneDeviceId: settings.microphoneDeviceId,
    microphoneLabel: settings.microphoneLabel,
    browserVoiceKey: settings.browserVoiceKey,
    onlineSpeechNoticeAccepted: settings.onlineSpeechNoticeAccepted,
  }));
  storage.removeItem(V1_VOICE_SETTINGS_KEY);
  storage.removeItem(LEGACY_VOICE_KEY);
}

export function reconcileVoiceSettings(settings: VoiceSettings, voices: readonly VoiceInfo[]): VoiceSettings {
  if (settings.voiceKey && voices.some((voice) => voice.voice_key === settings.voiceKey)) return settings;
  const fallback = voices.find((voice) => voice.is_default) ?? voices[0];
  return { ...settings, voiceKey: fallback?.voice_key ?? null };
}
