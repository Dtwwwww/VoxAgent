export interface MicrophoneDevice {
  deviceId: string;
  groupId: string;
  label: string;
  virtual: boolean;
  preferred: boolean;
  browserDefault: boolean;
}

const VIRTUAL_NAME = /virtual|todesk|xreal|vb-audio|stereo mix/iu;
const PREFERRED_NAME = /realtek|microphone array|麦克风阵列/iu;

export function classifyMicrophone(device: MediaDeviceInfo): MicrophoneDevice {
  const virtual = VIRTUAL_NAME.test(device.label);
  return {
    deviceId: device.deviceId,
    groupId: device.groupId,
    label: device.label,
    virtual,
    preferred: !virtual && PREFERRED_NAME.test(device.label),
    browserDefault: device.deviceId === "default",
  };
}

export async function listMicrophones(): Promise<MicrophoneDevice[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === "audioinput")
    .map(classifyMicrophone);
}

export function choosePreferredMicrophone(
  devices: readonly MicrophoneDevice[],
  savedDeviceId: string | null,
): MicrophoneDevice | null {
  const saved = savedDeviceId ? devices.find((device) => device.deviceId === savedDeviceId) : undefined;
  if (saved) return saved;

  const physical = devices.filter((device) => !device.virtual && !device.browserDefault);
  return physical.find((device) => device.preferred)
    ?? physical[0]
    ?? devices.find((device) => device.browserDefault)
    ?? null;
}

export function browserDefaultMatchesSelection(
  devices: readonly MicrophoneDevice[],
  selectedDeviceId: string | null,
): boolean {
  if (!selectedDeviceId) return false;
  const selected = devices.find((device) => device.deviceId === selectedDeviceId);
  const browserDefault = devices.find((device) => device.browserDefault);
  if (!selected || !browserDefault) return false;
  return selected.browserDefault
    || (selected.groupId.length > 0 && selected.groupId === browserDefault.groupId);
}

export function microphoneConstraints(deviceId: string | null): MediaStreamConstraints {
  return {
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };
}
