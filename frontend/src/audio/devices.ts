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

function permissionAbortError(): DOMException {
  return new DOMException("Microphone permission request was cancelled", "AbortError");
}

async function requestMicrophonePermission(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw permissionAbortError();
  const request = navigator.mediaDevices.getUserMedia({ audio: true });
  if (signal) {
    void request.then((lateStream) => {
      if (signal.aborted) lateStream.getTracks().forEach((track) => track.stop());
    }, () => undefined);
  }
  const stream = signal
    ? await new Promise<MediaStream>((resolve, reject) => {
        let settled = false;
        const finish = (callback: () => void) => {
          if (settled) return;
          settled = true;
          signal.removeEventListener("abort", onAbort);
          callback();
        };
        const onAbort = () => finish(() => reject(permissionAbortError()));
        signal.addEventListener("abort", onAbort, { once: true });
        request.then(
          (value) => finish(() => resolve(value)),
          (error: unknown) => finish(() => reject(error)),
        );
      })
    : await request;
  stream.getTracks().forEach((track) => track.stop());
}

export async function listMicrophonesAfterPermission(signal?: AbortSignal): Promise<MicrophoneDevice[]> {
  const initial = await listMicrophones();
  if (signal?.aborted) throw permissionAbortError();
  const labelsAreUsable = initial.length > 0 && initial.some((device) => device.label.trim().length > 0);
  if (labelsAreUsable) return initial;
  await requestMicrophonePermission(signal);
  if (signal?.aborted) throw permissionAbortError();
  return listMicrophones();
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
