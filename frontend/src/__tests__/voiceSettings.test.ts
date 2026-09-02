import { describe, expect, it } from "vitest";

import { loadVoiceSettings, reconcileVoiceSettings, saveVoiceSettings } from "../voiceSettings";

const defaults = { voiceKey: null, speed: 1.0 as const, speakTextReplies: false };
const voices = [{ voice_key: "default_voice", display_name: "声灵默认音色", description: "自然清晰，适合日常对话", gender: "neutral", is_default: true, previewable: true }];

describe("voice settings", () => {
  it("recovers safely from malformed, private, or invalid saved data", () => {
    const storage = new Map<string, string>();
    const api = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) };
    storage.set("voxagent.voice-settings.v1", "{");
    expect(loadVoiceSettings(api)).toEqual(defaults);
    storage.set("voxagent.voice-settings.v1", JSON.stringify({ voiceKey: "default_voice", speed: 9, speakTextReplies: true, token: "secret" }));
    expect(loadVoiceSettings(api)).toEqual({ voiceKey: "default_voice", speed: 1.0, speakTextReplies: true });
  });

  it("stores only retained public settings and reconciles unknown voices to server default", () => {
    const storage = new Map<string, string>([["voxagent.voice", "legacy"]]);
    const api = { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) };
    saveVoiceSettings(api, { voiceKey: "default_voice", speed: 0.8, speakTextReplies: true });
    const stored = storage.get("voxagent.voice-settings.v1")!;
    expect(JSON.parse(stored)).toEqual({ voiceKey: "default_voice", speed: 0.8, speakTextReplies: true });
    expect(stored).not.toMatch(/token|transcript|path|audio|engine|native|anonymous/i);
    expect(storage.has("voxagent.voice")).toBe(false);
    expect(reconcileVoiceSettings({ voiceKey: "removed", speed: 1.2, speakTextReplies: true }, voices)).toEqual({ voiceKey: "default_voice", speed: 1.2, speakTextReplies: true });
  });
});
