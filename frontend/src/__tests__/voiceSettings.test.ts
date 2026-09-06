import { describe, expect, it } from "vitest";

import { VOICE_SETTINGS_KEY, loadVoiceSettings, reconcileVoiceSettings, saveVoiceSettings } from "../voiceSettings";

const defaults = {
  voiceKey: null,
  speed: 1.0 as const,
  speechMode: "online-preferred" as const,
  microphoneDeviceId: null,
  microphoneLabel: null,
  browserVoiceKey: null,
  onlineSpeechNoticeAccepted: false,
};
const voices = [{ voice_key: "default_voice", display_name: "声灵默认音色", description: "自然清晰，适合日常对话", gender: "neutral", is_default: true, previewable: true }];

describe("voice settings", () => {
  it("recovers safely from malformed, private, or invalid saved data", () => {
    const storage = new Map<string, string>();
    const api = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) };
    storage.set(VOICE_SETTINGS_KEY, "{");
    expect(loadVoiceSettings(api)).toEqual(defaults);
    storage.set(VOICE_SETTINGS_KEY, JSON.stringify({
      voiceKey: "default_voice",
      speed: 9,
      speechMode: "remote-only",
      microphoneDeviceId: 42,
      microphoneLabel: [],
      browserVoiceKey: false,
      onlineSpeechNoticeAccepted: "yes",
      token: "secret",
    }));
    expect(loadVoiceSettings(api)).toEqual({ ...defaults, voiceKey: "default_voice" });
  });

  it("stores only retained public settings and reconciles unknown voices to server default", () => {
    const storage = new Map<string, string>([["voxagent.voice", "legacy"]]);
    const api = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) };
    const settings = {
      ...defaults,
      voiceKey: "default_voice",
      speed: 0.8 as const,
      microphoneDeviceId: "realtek-device",
      microphoneLabel: "Microphone Array (Realtek)",
      browserVoiceKey: "Microsoft Xiaoxiao Online (Natural) - Chinese (Mainland)",
      onlineSpeechNoticeAccepted: true,
    };
    saveVoiceSettings(api, settings);
    const stored = storage.get(VOICE_SETTINGS_KEY)!;
    expect(JSON.parse(stored)).toEqual(settings);
    expect(stored).not.toMatch(/token|transcript|path|audio|engine|native|anonymous/i);
    expect(storage.has("voxagent.voice")).toBe(false);
    expect(reconcileVoiceSettings({ ...settings, voiceKey: "removed", speed: 1.2 }, voices)).toEqual({ ...settings, voiceKey: "default_voice", speed: 1.2 });
  });

  it("migrates v1 settings once with safe v2 defaults", () => {
    const storage = new Map<string, string>([["voxagent.voice-settings.v1", JSON.stringify({
      voiceKey: "default_voice",
      speed: 1.2,
      speechMode: "local-only",
      microphoneDeviceId: "private-device",
      microphoneLabel: "Injected microphone",
      browserVoiceKey: "injected-browser-voice",
      onlineSpeechNoticeAccepted: true,
    })]]);
    const api = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) };

    expect(loadVoiceSettings(api)).toEqual({ ...defaults, voiceKey: "default_voice", speed: 1.2 });
    expect(JSON.parse(storage.get(VOICE_SETTINGS_KEY)!)).toEqual({ ...defaults, voiceKey: "default_voice", speed: 1.2 });
    expect(storage.has("voxagent.voice-settings.v1")).toBe(false);
  });
});
