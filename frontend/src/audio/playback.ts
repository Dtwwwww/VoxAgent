export type PlaybackMetadata =
  | { kind: "turn"; turnId: number; sequence: number; sampleRate: number }
  | { kind: "preview"; previewId: number; sampleRate: number };

type TaggedSource = AudioBufferSourceNode & { turnId?: number; previewId?: number };

interface TurnQueue {
  nextSequence: number;
  tailTime: number;
  pending: Map<number, AudioBuffer>;
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

  async enqueue(metadata: PlaybackMetadata, bytes: ArrayBuffer): Promise<void> {
    if (nativeWavSampleRate(bytes) !== metadata.sampleRate) throw new Error("WAV sample rate does not match metadata");
    if (metadata.kind === "preview") {
      this.stopPreview();
      const generation = this.previewGeneration;
      const context = this.getContext();
      const buffer = await context.decodeAudioData(bytes.slice(0));
      if (generation !== this.previewGeneration) return;
      const source = this.makeSource(buffer);
      source.previewId = metadata.previewId;
      source.start();
      return;
    }

    if (this.cancelledTurns.has(metadata.turnId)) return;
    const generation = this.conversationGeneration;
    const context = this.getContext();
    const buffer = await context.decodeAudioData(bytes.slice(0));
    if (generation !== this.conversationGeneration || this.cancelledTurns.has(metadata.turnId)) return;
    const queue = this.turns.get(metadata.turnId) ?? { nextSequence: 0, tailTime: context.currentTime, pending: new Map() };
    queue.pending.set(metadata.sequence, buffer);
    this.turns.set(metadata.turnId, queue);
    while (queue.pending.has(queue.nextSequence)) {
      const next = queue.pending.get(queue.nextSequence)!;
      queue.pending.delete(queue.nextSequence);
      const source = this.makeSource(next);
      source.turnId = metadata.turnId;
      const startAt = Math.max(context.currentTime, queue.tailTime);
      source.start(startAt);
      queue.tailTime = startAt + next.duration;
      queue.nextSequence += 1;
    }
  }

  stopTurn(turnId: number): void {
    this.cancelledTurns.add(turnId);
    this.turns.delete(turnId);
    for (const source of this.sources) if (source.turnId === turnId) this.stopSource(source);
  }

  stopConversation(): void {
    this.conversationGeneration += 1;
    for (const source of this.sources) if (source.turnId !== undefined) this.stopSource(source);
    this.turns.clear();
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
  }

  async close(): Promise<void> {
    this.stopAll();
    if (this.context) await this.context.close();
    this.context = null;
    this.cancelledTurns.clear();
  }

  private getContext(): AudioContext {
    this.context ??= new AudioContext();
    return this.context;
  }

  private makeSource(buffer: AudioBuffer): TaggedSource {
    const source = this.getContext().createBufferSource() as TaggedSource;
    source.buffer = buffer;
    source.connect(this.getContext().destination);
    source.onended = () => this.sources.delete(source);
    this.sources.add(source);
    return source;
  }

  private stopSource(source: TaggedSource): void {
    try { source.stop(); } catch { /* A source may already have ended. */ }
    source.disconnect();
    this.sources.delete(source);
  }
}
