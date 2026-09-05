import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  browserDefaultMatchesSelection,
  choosePreferredMicrophone,
  classifyMicrophone,
  listMicrophones,
  microphoneConstraints,
} from "./devices";

function device(deviceId: string, label: string, groupId = "g"): MediaDeviceInfo {
  return { deviceId, groupId, label, kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo;
}

describe("microphone device selection", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: { enumerateDevices: vi.fn() },
    });
  });

  it("marks physical and virtual microphone names deterministically", () => {
    expect(classifyMicrophone({ deviceId: "r", kind: "audioinput", label: "麦克风阵列 (Realtek(R) Audio", groupId: "g" } as MediaDeviceInfo).virtual).toBe(false);
    expect(classifyMicrophone({ deviceId: "v", kind: "audioinput", label: "ToDesk Virtual Audio", groupId: "g" } as MediaDeviceInfo).virtual).toBe(true);
    expect(classifyMicrophone(device("x", "XReal microphone")).virtual).toBe(true);
    expect(classifyMicrophone(device("vb", "VB-Audio Cable")).virtual).toBe(true);
    expect(classifyMicrophone(device("mix", "Stereo Mix")).virtual).toBe(true);
  });

  it("lists only audio inputs with virtual diagnostics", async () => {
    vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValue([
      device("r", "麦克风阵列 (Realtek(R) Audio"),
      device("v", "ToDesk Virtual Audio"),
      { deviceId: "speaker", groupId: "g", label: "Speakers", kind: "audiooutput", toJSON: () => ({}) } as MediaDeviceInfo,
    ]);

    await expect(listMicrophones()).resolves.toEqual([
      expect.objectContaining({ deviceId: "r", virtual: false }),
      expect.objectContaining({ deviceId: "v", virtual: true }),
    ]);
  });

  it("prefers saved devices before physical Realtek microphones and browser default", () => {
    const devices = [
      classifyMicrophone(device("default", "Default", "default-group")),
      classifyMicrophone(device("v", "ToDesk Virtual Audio", "virtual-group")),
      classifyMicrophone(device("r", "麦克风阵列 (Realtek(R) Audio", "realtek-group")),
    ];

    expect(choosePreferredMicrophone(devices, null)?.deviceId).toBe("r");
    expect(choosePreferredMicrophone(devices, "v")?.deviceId).toBe("v");
    expect(choosePreferredMicrophone(devices, "missing")?.deviceId).toBe("r");
    expect(choosePreferredMicrophone([devices[0], devices[1]], null)?.deviceId).toBe("default");
  });

  it("recognizes browser default only for the selected default entry or matching non-empty group", () => {
    const devices = [
      classifyMicrophone(device("default", "Default", "physical-group")),
      classifyMicrophone(device("r", "麦克风阵列 (Realtek(R) Audio", "physical-group")),
      classifyMicrophone(device("empty", "Other", "")),
    ];

    expect(browserDefaultMatchesSelection(devices, "r")).toBe(true);
    expect(browserDefaultMatchesSelection(devices, "default")).toBe(true);
    expect(browserDefaultMatchesSelection(devices, "empty")).toBe(false);
    expect(browserDefaultMatchesSelection(devices, null)).toBe(false);
    expect(browserDefaultMatchesSelection(devices, "missing")).toBe(false);
  });

  it("builds exact mono microphone constraints with enhancements", () => {
    expect(microphoneConstraints("r")).toEqual({
      audio: {
        deviceId: { exact: "r" },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  });
});
