export type PlaybackMetadata =
  | { kind: "turn"; turnId: number; sequence: number; sampleRate: number }
  | { kind: "preview"; previewId: number; sampleRate: number };

type TaggedSource = AudioBufferSourceNode & {
  turnId?: number;
  previewId?: number;
  outputGain?: GainNode;
};

export type PlaybackCompletion =
  | { kind: "turn"; turnId: number }
  | { kind: "preview"; previewId: number };

interface TurnQueue {
  nextSequence: number;
  tailTime: number;
  pending: Map<number, AudioBuffer>;
  complete: boolean;
  inFlight: number;
}

function ascii(view: DataView, offset: number, length: number): string {
  return String.fromCharCode(...Array.from({ length }, (_, index) => view.getUint8(offset + index)));
}

function nativeWavSampleRate(bytes: ArrayBuffer): number {
  const view = new DataView(bytes);
  if (view.byteLength < 12 || ascii(view, 0, 4) !== "RIFF" || ascii(view, 8, 4) !== "WAVE") {
    throw new Error("audio payload is not a RIFF/WAVE file");
  }
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const chunkType = ascii(view, offset, 4);
    const chunkLength = view.getUint32(offset + 4, true);
    const dataOffset = offset + 8;
    if (chunkType === "fmt ") {
      if (chunkLength < 16 || dataOffset + 16 > view.byteLength) throw new Error("WAV fmt chunk is incomplete");
      const sampleRate = view.getUint32(dataOffset + 4, true);
      if (sampleRate < 1) throw new Error("WAV sample rate must be positive");
      return sampleRate;
    }
    offset = dataOffset + chunkLength + (chunkLength % 2);
  }
  throw new Error("WAV fmt chunk is missing");
}

export class AudioPlayback {
  private context: AudioContext | null = null;
  private readonly sources = new Set<TaggedSource>();
  private readonly turns = new Map<number, TurnQueue>();
  private previewGeneration = 0;
  private conversationGeneration = 0;
  private cancelledTurns = new Set<number>();
  private completedTurns = new Set<number>();

  constructor(private readonly onCompletion: (completion: PlaybackCompletion) => void = () => undefined) {}

  async enqueue(metadata: PlaybackMetadata, bytes: ArrayBuffer): Promise<boolean> {
    if (nativeWavSampleRate(bytes) !== metadata.sampleRate) throw new Error("WAV sample rate does not match metadata");
    if (metadata.kind === "preview") {
      this.stopPreview();
      const generation = this.previewGeneration;
      const context = this.getContext();
      const buffer = await context.decodeAudioData(bytes.slice(0));
      if (generation !== this.previewGeneration) return false;
      const source = this.makeSource(buffer);
      source.previewId = metadata.previewId;
      source.start();
      return true;
    }

    if (this.cancelledTurns.has(metadata.turnId)) return false;
    const generation = this.conversationGeneration;
    const context = this.getContext();
    const queue = this.turns.get(metadata.turnId) ?? {
      nextSequence: 0,
      tailTime: context.currentTime,
      pending: new Map(),
      complete: this.completedTurns.has(metadata.turnId),
      inFlight: 0,
    };
    this.turns.set(metadata.turnId, queue);
    queue.inFlight += 1;
    try {
      const buffer = await context.decodeAudioData(bytes.slice(0));
      if (
        generation !== this.conversationGeneration
        || this.cancelledTurns.has(metadata.turnId)
        || this.turns.get(metadata.turnId) !== queue
      ) return false;
      queue.pending.set(metadata.sequence, buffer);
      let started = false;
      while (queue.pending.has(queue.nextSequence)) {
        const next = queue.pending.get(queue.nextSequence)!;
        queue.pending.delete(queue.nextSequence);
        const source = this.makeSource(next);
        source.turnId = metadata.turnId;
        const startAt = Math.max(context.currentTime, queue.tailTime);
        source.start(startAt);
        started = true;
        queue.tailTime = startAt + next.duration;
        queue.nextSequence += 1;
      }
      return started;
    } finally {
      queue.inFlight -= 1;
      if (this.turns.get(metadata.turnId) === queue) this.finishTurnIfIdle(metadata.turnId, queue);
    }
  }

  finishTurn(turnId: number): void {
    this.completedTurns.add(turnId);
    const queue = this.turns.get(turnId);
    if (!queue) return;
    queue.complete = true;
    this.finishTurnIfIdle(turnId, queue);
  }

  stopTurn(turnId: number): void {
    this.cancelledTurns.add(turnId);
    this.completedTurns.delete(turnId);
    this.turns.delete(turnId);
    for (const source of this.sources) if (source.turnId === turnId) this.stopSource(source);
  }

  prepareTurnReplay(turnId: number): void {
    this.cancelledTurns.delete(turnId);
    this.completedTurns.delete(turnId);
    this.turns.delete(turnId);
    for (const source of this.sources) if (source.turnId === turnId) this.stopSource(source);
  }

  stopConversation(): void {
    this.conversationGeneration += 1;
    for (const source of this.sources) if (source.turnId !== undefined) this.stopSource(source);
    this.turns.clear();
    this.completedTurns.clear();
  }

  stopPreview(): void {
    this.previewGeneration += 1;
    for (const source of this.sources) if (source.previewId !== undefined) this.stopSource(source);
  }

  stopAll(): void {
    this.previewGeneration += 1;
    this.conversationGeneration += 1;
    for (const source of [...this.sources]) this.stopSource(source);
    this.turns.clear();
    this.completedTurns.clear();
  }

  async close(): Promise<void> {
    this.stopAll();
    if (this.context) await this.context.close();
    this.context = null;
    this.cancelledTurns.clear();
    this.completedTurns.clear();
  }

  private getContext(): AudioContext {
    this.context ??= new AudioContext();
    return this.context;
  }

  private makeSource(buffer: AudioBuffer): TaggedSource {
    const source = this.getContext().createBufferSource() as TaggedSource;
    source.buffer = buffer;
    const gain = this.getContext().createGain();
    gain.gain.value = 1.15;
    source.connect(gain);
    gain.connect(this.getContext().destination);
    source.outputGain = gain;
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      source.outputGain?.disconnect();
      if (source.turnId !== undefined) {
        const queue = this.turns.get(source.turnId);
        if (queue) this.finishTurnIfIdle(source.turnId, queue);
      } else if (source.previewId !== undefined) {
        this.onCompletion({ kind: "preview", previewId: source.previewId });
      }
    };
    this.sources.add(source);
    return source;
  }

  private finishTurnIfIdle(turnId: number, queue: TurnQueue): void {
    const hasMoreSources = [...this.sources].some((item) => item.turnId === turnId);
    if (!queue.complete || queue.inFlight > 0 || hasMoreSources || queue.pending.size > 0) return;
    this.turns.delete(turnId);
    this.completedTurns.delete(turnId);
    this.onCompletion({ kind: "turn", turnId });
  }

  private stopSource(source: TaggedSource): void {
    source.onended = null;
    try { source.stop(); } catch { /* A source may already have ended. */ }
    source.disconnect();
    source.outputGain?.disconnect();
    this.sources.delete(source);
  }
}
