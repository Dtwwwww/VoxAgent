const TARGET_SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 320;

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
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private workletUrl: string | null = null;

  constructor(private readonly onFrame: (frame: ArrayBuffer) => void) {}

  async start(): Promise<void> {
    if (this.stream) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let worklet: AudioWorkletNode | null = null;
    let sink: GainNode | null = null;
    let url: string | null = null;
    try {
      context = new AudioContext();
      const packetizer = new PcmFramePacketizer(context.sampleRate, this.onFrame);
      url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
      await context.audioWorklet.addModule(url);
      source = context.createMediaStreamSource(stream);
      worklet = new AudioWorkletNode(context, "voxagent-capture", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      sink = context.createGain();
      sink.gain.value = 0;
      worklet.port.onmessage = (event: MessageEvent<Float32Array>) => packetizer.push(event.data);
      source.connect(worklet);
      worklet.connect(sink);
      sink.connect(context.destination);
      await context.resume();
      this.stream = stream;
      this.context = context;
      this.source = source;
      this.worklet = worklet;
      this.sink = sink;
      this.workletUrl = url;
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

  async stop(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    this.worklet?.disconnect();
    this.sink?.disconnect();
    if (this.context) await this.context.close();
    if (this.workletUrl) URL.revokeObjectURL(this.workletUrl);
    this.stream = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
    this.workletUrl = null;
  }
}
