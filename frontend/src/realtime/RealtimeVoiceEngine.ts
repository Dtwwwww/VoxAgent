import type { MicrophoneCapture } from "../audio/capture";
import type {
  BrowserSpeechCallbacks,
  BrowserSpeechFailure,
  BrowserSpeechProvider,
} from "../audio/webSpeech";
import type { ClientEvent, ServerEvent } from "../protocol";
import type { StreamingSentenceQueue } from "./sentenceQueue";

export type SpeechMode = "online-preferred" | "local-only";
export type ActiveSpeechProvider = "browser" | "local";
export type RealtimeVoiceState =
  | "off"
  | "connecting"
  | "listening"
  | "user_speaking"
  | "transcribing"
  | "thinking"
  | "responding"
  | "speaking"
  | "fallback";
export type RealtimeNotice = "interrupted" | null;

export interface RealtimeSnapshot {
  active: boolean;
  state: RealtimeVoiceState;
  provider: ActiveSpeechProvider | null;
  interimText: string;
  inputLevel: number;
  fallbackReason: string | null;
  notice: RealtimeNotice;
}

export interface RealtimeVoiceDependencies {
  capture: MicrophoneCapture;
  browserSpeech: BrowserSpeechProvider;
  sentenceQueue: StreamingSentenceQueue;
  nextSpeechRequestId(): number;
  sendJson(event: ClientEvent): void;
  onSnapshot(snapshot: RealtimeSnapshot): void;
  onTerminalError(error: BrowserSpeechFailure): void;
  cancelLocalPlayback(): void;
}

export interface StartRealtimeOptions {
  mode: SpeechMode;
  deviceId: string | null;
  browserVoiceKey: string | null;
  speechRate: number;
  allowDefaultInputFallback: boolean;
}

interface TurnIdentity {
  sessionId: string;
  turnId: number;
}

interface QueuedSentence {
  generation: number;
  speechEpoch: number;
  turn: TurnIdentity;
  text: string;
}

interface LocalSpeechRequest {
  turn: TurnIdentity;
  requestId: number;
}

const BROWSER_FALLBACK_CODES = new Set<BrowserSpeechFailure["code"]>([
  "unsupported",
  "track_not_supported",
  "network",
  "language-not-supported",
  "service-not-allowed",
]);

const INITIAL_SNAPSHOT: RealtimeSnapshot = {
  active: false,
  state: "off",
  provider: null,
  interimText: "",
  inputLevel: 0,
  fallbackReason: null,
  notice: null,
};

function sameTurn(left: TurnIdentity | null, right: TurnIdentity): boolean {
  return left !== null && left.sessionId === right.sessionId && left.turnId === right.turnId;
}

function eventTurn(event: { session_id: string; turn_id: number }): TurnIdentity {
  return { sessionId: event.session_id, turnId: event.turn_id };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class RealtimeVoiceEngine {
  private generation = 0;
  private recognitionRun = 0;
  private speechEpoch = 0;
  private snapshot: RealtimeSnapshot = { ...INITIAL_SNAPSHOT };
  private options: StartRealtimeOptions | null = null;
  private track: MediaStreamTrack | null = null;
  private captureAbort: AbortController | null = null;
  private stopPromise: Promise<void> | null = null;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;
  private noticeTimer: ReturnType<typeof setTimeout> | null = null;
  private emptyRecognitionRuns = 0;
  private fallbackUsed = false;
  private browserFinalOpen = true;
  private awaitingBrowserRequestId: number | null = null;
  private activeTurn: TurnIdentity | null = null;
  private activeTurnHasFinal = false;
  private responseDone = false;
  private pendingSentences: QueuedSentence[] = [];
  private activeSpeechLoopEpoch: number | null = null;
  private fallbackSpeechTurn: TurnIdentity | null = null;
  private fallbackSpeechDone = false;
  private fallbackSpeechRequested = false;
  private localSpeechRequest: LocalSpeechRequest | null = null;
  private transcriptRequestCounter = 0;
  private readonly cancelledTurnKeys = new Set<string>();
  private readonly highestTurnBySession = new Map<string, number>();

  constructor(private readonly dependencies: RealtimeVoiceDependencies) {}

  async start(options: StartRealtimeOptions): Promise<void> {
    if (this.snapshot.active) await this.stop();
    else if (this.stopPromise) await this.stopPromise;

    const generation = ++this.generation;
    this.options = { ...options };
    this.track = null;
    this.captureAbort = new AbortController();
    this.emptyRecognitionRuns = 0;
    this.fallbackUsed = false;
    this.browserFinalOpen = true;
    this.clearTurnOwnership();
    this.dependencies.sentenceQueue.reset();
    this.dependencies.capture.setForwardPcm(options.mode === "local-only");
    this.publish({
      ...INITIAL_SNAPSHOT,
      active: true,
      state: "connecting",
    });

    let track: MediaStreamTrack;
    try {
      track = await this.dependencies.capture.start(this.captureAbort.signal);
    } catch (error) {
      if (this.isCurrent(generation)) this.failTerminal(this.captureFailure(error), generation);
      return;
    }
    if (!this.isCurrent(generation)) return;
    if (!track || track.readyState === "ended") {
      this.failTerminal({
        code: "recognition_error",
        recoverable: false,
        message: "No live microphone audio track available",
      }, generation);
      return;
    }
    this.track = track;

    if (options.mode === "local-only") {
      this.publish({ provider: "local", state: "listening" });
      return;
    }

    this.publish({ provider: "browser" });
    await this.startBrowserRecognition(generation);
  }

  async stop(): Promise<void> {
    if (!this.snapshot.active) return this.stopPromise ?? Promise.resolve();

    ++this.generation;
    this.captureAbort?.abort();
    this.captureAbort = null;
    this.clearTimers();
    ++this.recognitionRun;
    ++this.speechEpoch;
    const cancelPendingTurn = this.preparePendingCancellation();

    this.dependencies.browserSpeech.stopRecognition();
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    this.pendingSentences = [];
    if (cancelPendingTurn) this.dependencies.sendJson({ type: "turn.cancel" });

    this.track = null;
    this.options = null;
    this.clearTurnOwnership();
    this.publish({ ...INITIAL_SNAPSHOT });

    return this.beginCaptureStop();
  }

  cancelCurrentOutput(): void {
    if (!this.snapshot.active) return;
    ++this.speechEpoch;
    const cancelPendingTurn = this.preparePendingCancellation();
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    this.pendingSentences = [];
    if (cancelPendingTurn) this.dependencies.sendJson({ type: "turn.cancel" });
    this.clearTurnOwnership();
    this.browserFinalOpen = true;
    this.publish({ state: "listening", interimText: "" });
  }

  handleServerEvent(event: ServerEvent): void {
    if (!this.snapshot.active) return;

    switch (event.type) {
      case "vad.started":
        this.handleVadStarted(event);
        return;
      case "vad.stopped":
        if (this.snapshot.provider === "local" && sameTurn(this.activeTurn, eventTurn(event))) {
          this.publish({ state: "transcribing" });
        }
        return;
      case "asr.partial":
        if (this.snapshot.provider === "local" && sameTurn(this.activeTurn, eventTurn(event))) {
          this.publish({ interimText: event.text, state: "transcribing" });
        }
        return;
      case "asr.final":
        this.handleAsrFinal(event);
        return;
      case "assistant.delta":
        this.handleAssistantDelta(event);
        return;
      case "assistant.done":
        this.handleAssistantDone(event);
        return;
      case "tts.started":
        if (this.acceptsTtsEvent(event)) this.publish({ state: "speaking" });
        return;
      case "tts.done":
      case "tts.error":
        if (this.acceptsTtsEvent(event)) {
          this.localSpeechRequest = null;
          this.fallbackSpeechTurn = null;
          this.clearActiveTurn();
          this.publish({ state: "listening", interimText: "" });
        }
        return;
      case "turn.cancelled":
        this.handleTurnCancelled(event);
        return;
      default:
        return;
    }
  }

  private async startBrowserRecognition(generation: number): Promise<void> {
    if (!this.isCurrentBrowser(generation) || !this.track || !this.options) return;
    const run = ++this.recognitionRun;
    let runEnded = false;
    let runHadFinal = false;
    const callbacks: BrowserSpeechCallbacks = {
      onInterim: (rawText) => {
        if (!this.isCurrentBrowserRun(generation, run) || !this.browserFinalOpen) return;
        const text = rawText.trim();
        if (text) this.publish({ interimText: text });
      },
      onFinal: (rawText) => {
        if (!this.isCurrentBrowserRun(generation, run) || runEnded) return;
        const text = rawText.trim();
        if (!text) return;
        runHadFinal = true;
        this.emptyRecognitionRuns = 0;
        if (!this.browserFinalOpen) return;
        this.browserFinalOpen = false;
        const requestId = ++this.transcriptRequestCounter;
        this.awaitingBrowserRequestId = requestId;
        this.dependencies.sendJson({ type: "voice.transcript.submit", text, request_id: requestId });
        this.publish({ interimText: "", state: "thinking" });
      },
      onSpeechStart: () => {
        if (!this.isCurrentBrowserRun(generation, run)) return;
        this.handleUserSpeechStart();
      },
      onSpeechEnd: () => {
        if (!this.isCurrentBrowserRun(generation, run)) return;
        if (this.snapshot.state === "user_speaking") this.publish({ state: "transcribing" });
      },
      onRecognitionEnd: () => {
        if (!this.isCurrentBrowserRun(generation, run) || runEnded) return;
        runEnded = true;
        this.emptyRecognitionRuns = runHadFinal ? 0 : this.emptyRecognitionRuns + 1;
        if (this.emptyRecognitionRuns >= 2) {
          this.switchToLocal("empty-recognition");
          return;
        }
        this.clearRestartTimer();
        this.restartTimer = setTimeout(() => {
          this.restartTimer = null;
          if (!this.isCurrentBrowserRun(generation, run)) return;
          void this.startBrowserRecognition(generation);
        }, 150);
      },
      onError: (error) => {
        if (!this.isCurrentBrowserRun(generation, run)) return;
        this.handleBrowserFailure(error, generation);
      },
    };

    try {
      await this.dependencies.browserSpeech.start(
        this.track,
        callbacks,
        this.options.allowDefaultInputFallback,
      );
    } catch (error) {
      if (this.isCurrentBrowserRun(generation, run)) {
        this.handleBrowserFailure({
          code: "track_not_supported",
          recoverable: true,
          message: errorMessage(error),
        }, generation);
      }
      return;
    }

    if (this.isCurrentBrowserRun(generation, run) && this.snapshot.state === "connecting") {
      this.publish({ state: "listening" });
    }
  }

  private handleBrowserFailure(error: BrowserSpeechFailure, generation: number): void {
    if (!this.isCurrentBrowser(generation)) return;
    if (BROWSER_FALLBACK_CODES.has(error.code)) {
      this.switchToLocal(error.code);
      return;
    }
    if (error.code === "recognition_error" && error.recoverable) return;
    this.failTerminal(error, generation);
  }

  private handleUserSpeechStart(): void {
    const interrupted = this.snapshot.state === "thinking"
      || this.snapshot.state === "responding"
      || this.snapshot.state === "speaking";
    if (!interrupted) {
      this.browserFinalOpen = true;
      this.publish({ state: "user_speaking" });
      return;
    }

    ++this.speechEpoch;
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    this.pendingSentences = [];
    if (this.preparePendingCancellation()) this.dependencies.sendJson({ type: "turn.cancel" });
    this.clearTurnOwnership();
    this.browserFinalOpen = true;
    this.clearNoticeTimer();
    const generation = this.generation;
    this.publish({ state: "user_speaking", interimText: "", notice: "interrupted" });
    this.noticeTimer = setTimeout(() => {
      this.noticeTimer = null;
      if (this.isCurrent(generation) && this.snapshot.notice === "interrupted") {
        this.publish({ notice: null });
      }
    }, 1_200);
  }

  private handleVadStarted(event: Extract<ServerEvent, { type: "vad.started" }>): void {
    if (this.snapshot.provider !== "local") return;
    const turn = eventTurn(event);
    if (!this.isFreshTurn(turn)) return;
    this.handleUserSpeechStart();
    this.claimTurn(turn);
    this.activeTurn = turn;
    this.activeTurnHasFinal = false;
    this.responseDone = false;
  }

  private handleAsrFinal(event: Extract<ServerEvent, { type: "asr.final" }>): void {
    const turn = eventTurn(event);
    if (this.cancelledTurnKeys.has(this.turnKey(turn))) return;
    if (this.snapshot.provider === "browser") {
      if (this.awaitingBrowserRequestId === null || event.request_id !== this.awaitingBrowserRequestId) return;
      if (!this.isFreshTurn(turn)) return;
      this.awaitingBrowserRequestId = null;
      this.claimTurn(turn);
      this.activeTurn = turn;
    } else if (!sameTurn(this.activeTurn, turn)) {
      return;
    }

    this.activeTurnHasFinal = true;
    this.responseDone = false;
    this.dependencies.sentenceQueue.reset();
    this.publish({ state: "thinking", interimText: "" });
  }

  private handleAssistantDelta(event: Extract<ServerEvent, { type: "assistant.delta" }>): void {
    const turn = eventTurn(event);
    if (!this.activeTurnHasFinal || !sameTurn(this.activeTurn, turn)) return;
    if (this.snapshot.state !== "speaking") this.publish({ state: "responding" });
    if (this.snapshot.provider !== "browser") return;

    const generation = this.generation;
    const speechEpoch = this.speechEpoch;
    const sentences = this.dependencies.sentenceQueue.push(event.delta);
    for (const text of sentences) {
      this.pendingSentences.push({ generation, speechEpoch, turn, text });
    }
    this.pumpBrowserSpeech();
  }

  private handleAssistantDone(event: Extract<ServerEvent, { type: "assistant.done" }>): void {
    const turn = eventTurn(event);
    if (sameTurn(this.fallbackSpeechTurn, turn)) {
      this.fallbackSpeechDone = true;
      this.requestFallbackSpeech();
      return;
    }
    if (!this.activeTurnHasFinal || !sameTurn(this.activeTurn, turn)) return;
    this.responseDone = true;
    if (this.snapshot.provider === "browser") this.finishBrowserResponseIfReady();
  }

  private pumpBrowserSpeech(): void {
    const loopEpoch = this.speechEpoch;
    if (this.activeSpeechLoopEpoch === loopEpoch) return;
    this.activeSpeechLoopEpoch = loopEpoch;
    void this.runBrowserSpeechLoop().finally(() => {
      if (this.activeSpeechLoopEpoch !== loopEpoch) return;
      this.activeSpeechLoopEpoch = null;
      if (this.pendingSentences.length > 0) this.pumpBrowserSpeech();
      else this.finishBrowserResponseIfReady();
    });
  }

  private async runBrowserSpeechLoop(): Promise<void> {
    while (this.pendingSentences.length > 0) {
      const sentence = this.pendingSentences.shift();
      if (!sentence || !this.acceptsSentence(sentence)) return;
      this.publish({ state: "speaking" });
      try {
        await this.dependencies.browserSpeech.speak(
          sentence.text,
          this.options?.browserVoiceKey ?? null,
          this.options?.speechRate ?? 1,
        );
      } catch {
        if (!this.acceptsSentence(sentence)) return;
        const done = this.responseDone;
        this.pendingSentences = [];
        this.switchToLocal("speech-synthesis", sentence.turn, done);
        return;
      }
      if (!this.acceptsSentence(sentence)) return;
    }
  }

  private acceptsSentence(sentence: QueuedSentence): boolean {
    return this.isCurrentBrowser(sentence.generation)
      && sentence.speechEpoch === this.speechEpoch
      && sameTurn(this.activeTurn, sentence.turn);
  }

  private finishBrowserResponseIfReady(): void {
    if (
      !this.snapshot.active
      || this.snapshot.provider !== "browser"
      || !this.responseDone
      || this.activeSpeechLoopEpoch === this.speechEpoch
      || this.pendingSentences.length > 0
    ) return;
    this.clearActiveTurn();
    this.browserFinalOpen = true;
    this.dependencies.sentenceQueue.reset();
    this.publish({ state: "listening", interimText: "" });
  }

  private switchToLocal(
    reason: string,
    preservedSpeechTurn: TurnIdentity | null = null,
    responseAlreadyDone = false,
  ): void {
    if (!this.snapshot.active || this.snapshot.provider !== "browser" || this.fallbackUsed) return;
    this.fallbackUsed = true;
    ++this.generation;
    ++this.recognitionRun;
    ++this.speechEpoch;
    this.clearTimers();
    const cancelPendingTurn = preservedSpeechTurn === null && this.preparePendingCancellation();

    this.dependencies.browserSpeech.stopRecognition();
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    this.pendingSentences = [];
    if (cancelPendingTurn) this.dependencies.sendJson({ type: "turn.cancel" });
    this.clearTurnOwnership();

    this.fallbackSpeechTurn = preservedSpeechTurn;
    this.fallbackSpeechDone = responseAlreadyDone;
    this.dependencies.capture.setForwardPcm(true);
    this.publish({
      provider: "local",
      state: "fallback",
      fallbackReason: reason,
      interimText: "",
      notice: null,
    });
    this.publish({ state: preservedSpeechTurn ? "responding" : "listening" });
    this.requestFallbackSpeech();
  }

  private requestFallbackSpeech(): void {
    if (!this.fallbackSpeechTurn || !this.fallbackSpeechDone || this.fallbackSpeechRequested) return;
    this.fallbackSpeechRequested = true;
    const requestId = this.dependencies.nextSpeechRequestId();
    this.localSpeechRequest = { turn: this.fallbackSpeechTurn, requestId };
    this.dependencies.sendJson({
      type: "assistant.speak",
      turn_id: this.fallbackSpeechTurn.turnId,
      request_id: requestId,
    });
    this.publish({ state: "speaking" });
  }

  private acceptsTtsEvent(event: Extract<ServerEvent, { type: "tts.started" | "tts.done" | "tts.error" }>): boolean {
    const turn = eventTurn(event);
    if (this.localSpeechRequest) {
      return sameTurn(this.localSpeechRequest.turn, turn)
        && this.localSpeechRequest.requestId === event.request_id;
    }
    return this.snapshot.provider === "local"
      && this.activeTurnHasFinal
      && sameTurn(this.activeTurn, turn)
      && event.request_id === 0;
  }

  private handleTurnCancelled(event: Extract<ServerEvent, { type: "turn.cancelled" }>): void {
    const turn = eventTurn(event);
    const key = this.turnKey(turn);
    if (this.cancelledTurnKeys.has(key)) return;
    const matchesOwnedTurn = sameTurn(this.activeTurn, turn)
      || sameTurn(this.fallbackSpeechTurn, turn)
      || sameTurn(this.localSpeechRequest?.turn ?? null, turn);
    const matchesAwaitingBrowser = this.awaitingBrowserRequestId !== null
      && event.request_id === this.awaitingBrowserRequestId;
    if (!matchesAwaitingBrowser && !matchesOwnedTurn) return;
    ++this.speechEpoch;
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    this.pendingSentences = [];
    this.clearTurnOwnership();
    this.browserFinalOpen = true;
    this.publish({ state: "listening", interimText: "" });
  }

  private failTerminal(error: BrowserSpeechFailure, generation: number): void {
    if (!this.isCurrent(generation)) return;
    ++this.generation;
    this.captureAbort?.abort();
    this.captureAbort = null;
    this.clearTimers();
    ++this.recognitionRun;
    ++this.speechEpoch;
    const cancelPendingTurn = this.preparePendingCancellation();
    this.dependencies.browserSpeech.stopRecognition();
    this.dependencies.browserSpeech.cancelSpeech();
    this.dependencies.cancelLocalPlayback();
    this.dependencies.sentenceQueue.cancel();
    if (cancelPendingTurn) this.dependencies.sendJson({ type: "turn.cancel" });
    this.pendingSentences = [];
    this.track = null;
    this.options = null;
    this.clearTurnOwnership();
    this.publish({ ...INITIAL_SNAPSHOT });
    this.dependencies.onTerminalError(error);
    void this.beginCaptureStop().catch(() => undefined);
  }

  private captureFailure(error: unknown): BrowserSpeechFailure {
    const name = error instanceof DOMException ? error.name : "";
    const message = errorMessage(error);
    if (name === "NotAllowedError" || name === "SecurityError") {
      return { code: "recognition_error", recoverable: false, message: message || "Microphone permission denied" };
    }
    if (name === "NotFoundError") {
      return { code: "recognition_error", recoverable: false, message: message || "Microphone device not found" };
    }
    return { code: "recognition_error", recoverable: false, message };
  }

  private hasPendingTurn(): boolean {
    return this.awaitingBrowserRequestId !== null
      || this.activeTurn !== null
      || this.fallbackSpeechTurn !== null
      || this.localSpeechRequest !== null;
  }

  private preparePendingCancellation(): boolean {
    if (!this.hasPendingTurn()) return false;
    const knownTurn = this.activeTurn ?? this.fallbackSpeechTurn ?? this.localSpeechRequest?.turn ?? null;
    if (knownTurn) this.rememberCancelledTurn(knownTurn);
    return true;
  }

  private isFreshTurn(turn: TurnIdentity): boolean {
    return turn.turnId > (this.highestTurnBySession.get(turn.sessionId) ?? 0);
  }

  private claimTurn(turn: TurnIdentity): void {
    this.highestTurnBySession.set(turn.sessionId, turn.turnId);
  }

  private rememberCancelledTurn(turn: TurnIdentity): void {
    if (this.cancelledTurnKeys.size >= 128) {
      const oldest = this.cancelledTurnKeys.values().next().value as string | undefined;
      if (oldest !== undefined) this.cancelledTurnKeys.delete(oldest);
    }
    this.cancelledTurnKeys.add(this.turnKey(turn));
  }

  private turnKey(turn: TurnIdentity): string {
    return `${turn.sessionId}:${turn.turnId}`;
  }

  private clearActiveTurn(): void {
    this.activeTurn = null;
    this.activeTurnHasFinal = false;
    this.responseDone = false;
  }

  private clearTurnOwnership(): void {
    this.awaitingBrowserRequestId = null;
    this.clearActiveTurn();
    this.fallbackSpeechTurn = null;
    this.fallbackSpeechDone = false;
    this.fallbackSpeechRequested = false;
    this.localSpeechRequest = null;
  }

  private isCurrent(generation: number): boolean {
    return this.snapshot.active && generation === this.generation;
  }

  private isCurrentBrowser(generation: number): boolean {
    return this.isCurrent(generation) && this.snapshot.provider === "browser";
  }

  private isCurrentBrowserRun(generation: number, run: number): boolean {
    return this.isCurrentBrowser(generation) && run === this.recognitionRun;
  }

  private clearRestartTimer(): void {
    if (this.restartTimer !== null) clearTimeout(this.restartTimer);
    this.restartTimer = null;
  }

  private clearNoticeTimer(): void {
    if (this.noticeTimer !== null) clearTimeout(this.noticeTimer);
    this.noticeTimer = null;
  }

  private clearTimers(): void {
    this.clearRestartTimer();
    this.clearNoticeTimer();
  }

  private beginCaptureStop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    let stopping: Promise<void>;
    try {
      stopping = this.dependencies.capture.stop();
    } catch (error) {
      stopping = Promise.reject(error);
    }
    let settled!: Promise<void>;
    settled = stopping.finally(() => {
      if (this.stopPromise === settled) this.stopPromise = null;
    });
    this.stopPromise = settled;
    return settled;
  }

  private publish(patch: Partial<RealtimeSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.dependencies.onSnapshot({ ...this.snapshot });
  }
}
