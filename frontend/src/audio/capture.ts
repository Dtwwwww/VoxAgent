import { microphoneConstraints } from "./devices";

const TARGET_SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 320;

function abortError(): DOMException {
  return new DOMException("Microphone setup was cancelled", "AbortError");
}

function abortable<T>(operation: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return operation;
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      callback();
    };
    const onAbort = () => finish(() => reject(abortError()));
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => finish(() => resolve(value)),
      (error: unknown) => finish(() => reject(error)),
    );
  });
}

export class PcmFramePacketizer {
  private readonly ratio: number;
  private sourcePosition = 0;
  private bufferStart = 0;
  private sourceBuffer: number[] = [];
  private samples: number[] = [];

  constructor(sourceSampleRate: number, private readonly onFrame: (frame: ArrayBuffer) => void) {
    if (sourceSampleRate < TARGET_SAMPLE_RATE) throw new Error("microphone sample rate must be at least 16 kHz");
    this.ratio = sourceSampleRate / TARGET_SAMPLE_RATE;
  }

  push(input: Float32Array): void {
    this.sourceBuffer.push(...input);
    const bufferEnd = this.bufferStart + this.sourceBuffer.length;
    while (this.sourcePosition < bufferEnd) {
      const index = Math.floor(this.sourcePosition);
      const fraction = this.sourcePosition - index;
      if (fraction > 0 && index + 1 >= bufferEnd) break;
      const current = this.sourceBuffer[index - this.bufferStart];
      const next = this.sourceBuffer[index + 1 - this.bufferStart] ?? current;
      const sample = current * (1 - fraction) + next * fraction;
      this.samples.push(Math.max(-1, Math.min(1, sample)));
      this.sourcePosition += this.ratio;
    }
    const consumed = Math.min(Math.floor(this.sourcePosition) - this.bufferStart, this.sourceBuffer.length);
    if (consumed > 0) {
      this.sourceBuffer.splice(0, consumed);
      this.bufferStart += consumed;
    }
    while (this.samples.length >= FRAME_SAMPLES) this.emitFrame(this.samples.splice(0, FRAME_SAMPLES));
  }

  private emitFrame(samples: number[]): void {
    const frame = new ArrayBuffer(FRAME_SAMPLES * Int16Array.BYTES_PER_ELEMENT);
    const view = new DataView(frame);
    samples.forEach((sample, index) => {
      const pcm = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
      view.setInt16(index * 2, pcm, true);
    });
    this.onFrame(frame);
  }
}

const WORKLET_SOURCE = `
class VoxAgentCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor("voxagent-capture", VoxAgentCaptureProcessor);
`;

export class MicrophoneCapture {
  private forwardPcm: boolean;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private track: MediaStreamTrack | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private workletUrl: string | null = null;

  constructor(private readonly options: MicrophoneCaptureOptions) {
    this.forwardPcm = options.forwardPcm;
  }

  async start(signal?: AbortSignal): Promise<MediaStreamTrack> {
    if (this.track) return this.track;
    const supported = navigator.mediaDevices.getSupportedConstraints();
    const requested = microphoneConstraints(this.options.deviceId).audio as MediaTrackConstraints;
    const audio: MediaTrackConstraints = {};
    if (this.options.deviceId) audio.deviceId = requested.deviceId;
    if (supported.channelCount) audio.channelCount = 1;
    if (supported.echoCancellation) audio.echoCancellation = true;
    if (supported.noiseSuppression) audio.noiseSuppression = true;
    if (supported.autoGainControl) audio.autoGainControl = true;
    const media = navigator.mediaDevices.getUserMedia({ audio });
    if (signal) void media.then((lateStream) => {
      if (signal.aborted) lateStream.getTracks().forEach((track) => track.stop());
    }, () => undefined);
    const stream = await abortable(media, signal);
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let worklet: AudioWorkletNode | null = null;
    let sink: GainNode | null = null;
    let url: string | null = null;
    try {
      const track = stream.getAudioTracks()[0];
      if (!track || track.readyState === "ended") throw new Error("No live microphone audio track available");
      this.options.onSettings(track.getSettings());
      context = new AudioContext();
      const packetizer = new PcmFramePacketizer(context.sampleRate, this.options.onFrame);
      url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
      await abortable(context.audioWorklet.addModule(url), signal);
      source = context.createMediaStreamSource(stream);
      worklet = new AudioWorkletNode(context, "voxagent-capture", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      sink = context.createGain();
      sink.gain.value = 0;
      worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
        const samples = event.data;
        const rms = samples.length === 0
          ? 0
          : Math.sqrt(samples.reduce((total, sample) => total + sample * sample, 0) / samples.length);
        this.options.onLevel(Math.max(0, Math.min(1, rms)));
        if (this.forwardPcm) packetizer.push(samples);
      };
      source.connect(worklet);
      worklet.connect(sink);
      sink.connect(context.destination);
      await abortable(context.resume(), signal);
      this.stream = stream;
      this.track = track;
      this.context = context;
      this.source = source;
      this.worklet = worklet;
      this.sink = sink;
      this.workletUrl = url;
      return track;
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      source?.disconnect();
      worklet?.disconnect();
      sink?.disconnect();
      if (context) await context.close();
      if (url) URL.revokeObjectURL(url);
      throw error;
    }
  }

  setForwardPcm(enabled: boolean): void {
    this.forwardPcm = enabled;
  }

  async stop(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    this.worklet?.disconnect();
    this.sink?.disconnect();
    if (this.context) await this.context.close();
    if (this.workletUrl) URL.revokeObjectURL(this.workletUrl);
    this.stream = null;
    this.track = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
    this.workletUrl = null;
  }
}

export interface MicrophoneCaptureOptions {
  deviceId: string | null;
  forwardPcm: boolean;
  onFrame(frame: ArrayBuffer): void;
  onLevel(level: number): void;
  onSettings(settings: MediaTrackSettings): void;
}
