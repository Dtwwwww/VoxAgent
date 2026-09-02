import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import fixtureSource from "../../../contracts/protocol-fixtures.json?raw";
import { MicrophoneCapture, PcmFramePacketizer } from "../audio/capture";
import { AudioPlayback } from "../audio/playback";
import { parseClientEvent, parseClientEventJson, parseServerEvent, parseServerEventJson } from "../protocol";
import { useVoiceSession } from "../useVoiceSession";

const SESSION_ID = "00000000-0000-4000-8000-000000000001";
const fixtures = JSON.parse(fixtureSource.replace(
  /("(?:turn_id|preview_id|sequence|sample_rate|byte_length|frame_samples|frame_bytes)"\s*:\s*)(-?\d+)\.0(?=\s*[,}])/gu,
  "$1\"$2.0\"",
)) as {
  valid_client: unknown[];
  invalid_client: unknown[];
  valid_server: unknown[];
  invalid_server: unknown[];
};

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly sent: (string | ArrayBuffer)[] = [];
  readyState = MockWebSocket.CONNECTING;
  binaryType: BinaryType = "blob";
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  constructor(readonly url: string | URL) {
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  receive(data: string | ArrayBuffer) {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }

  jsonMessages() {
    return this.sent.filter((item): item is string => typeof item === "string").map((item) => JSON.parse(item));
  }
}

class MockBufferSource {
  buffer: AudioBuffer | null = null;
  onended: (() => void) | null = null;
  connect = vi.fn();
  disconnect = vi.fn();
  start = vi.fn();
  stop = vi.fn();
}

class MockAudioContext {
  static instances: MockAudioContext[] = [];
  static nextSampleRate = 48_000;
  static decoder: (() => Promise<AudioBuffer>) | null = null;
  static workletFailure: Error | null = null;
  static workletGates: Array<Promise<void>> = [];
  static resumeGates: Array<Promise<void>> = [];
  static closeGates: Array<Promise<void>> = [];
  readonly sampleRate = 48_000;
  readonly currentTime = 0;
  readonly destination = {} as AudioDestinationNode;
  readonly audioWorklet = { addModule: vi.fn(async () => {
    if (MockAudioContext.workletFailure) throw MockAudioContext.workletFailure;
    await (MockAudioContext.workletGates.shift() ?? Promise.resolve());
  }) };
  readonly sources: MockBufferSource[] = [];
  close = vi.fn(async () => { await (MockAudioContext.closeGates.shift() ?? Promise.resolve()); });
  resume = vi.fn(async () => { await (MockAudioContext.resumeGates.shift() ?? Promise.resolve()); });
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
  createGain = vi.fn(() => ({ gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() }));

  constructor() {
    MockAudioContext.instances.push(this);
  }

  decodeAudioData = vi.fn(() => MockAudioContext.decoder?.() ?? Promise.resolve({
      duration: 0.1,
      sampleRate: MockAudioContext.nextSampleRate,
    } as AudioBuffer));

  createBufferSource() {
    const source = new MockBufferSource();
    this.sources.push(source);
    return source as unknown as AudioBufferSourceNode;
  }
}

class MockAudioWorkletNode {
  readonly port = { onmessage: null as ((event: MessageEvent<Float32Array>) => void) | null };
  connect = vi.fn();
  disconnect = vi.fn();
}

function readyEvent() {
  return {
    type: "session.ready",
    session_id: SESSION_ID,
    model_id: "qwen2.5:7b",
    offline: true,
    input_audio: {
      encoding: "pcm_s16le",
      sample_rate: 16_000,
      channels: 1,
      frame_duration_ms: 20,
      frame_samples: 320,
      frame_bytes: 640,
    },
  };
}

function wavBytes(sampleRate = 24_000): ArrayBuffer {
  const bytes = new ArrayBuffer(46);
  const view = new DataView(bytes);
  const write = (offset: number, value: string) => [...value].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 38, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, 2, true);
  return bytes;
}

function emit(socket: MockWebSocket, event: object) {
  act(() => socket.receive(JSON.stringify(event)));
}

function openSession() {
  const hook = renderHook(() => useVoiceSession({ url: "ws://127.0.0.1:8765/v1/voice?token=test" }));
  act(() => hook.result.current.connect());
  const socket = MockWebSocket.instances.at(-1)!;
  act(() => socket.open());
  emit(socket, readyEvent());
  return { hook, socket };
}

beforeEach(() => {
  MockWebSocket.instances = [];
  MockAudioContext.instances = [];
  MockAudioContext.decoder = null;
  MockAudioContext.workletFailure = null;
  MockAudioContext.nextSampleRate = 48_000;
  MockAudioContext.workletGates = [];
  MockAudioContext.resumeGates = [];
  MockAudioContext.closeGates = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  vi.stubGlobal("AudioContext", MockAudioContext);
  vi.stubGlobal("AudioWorkletNode", MockAudioWorkletNode);
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:worklet"), revokeObjectURL: vi.fn() });
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn() },
  });
  localStorage.clear();
});

describe("the frozen WebSocket protocol", () => {
  it("accepts every shared valid fixture and rejects every invalid fixture", () => {
    for (const event of fixtures.valid_client) expect(() => parseClientEvent(event)).not.toThrow();
    for (const event of fixtures.invalid_client) expect(() => parseClientEvent(event)).toThrow();
    for (const event of fixtures.valid_server) expect(() => parseServerEvent(event)).not.toThrow();
    for (const event of fixtures.invalid_server) expect(() => parseServerEvent(event)).toThrow();
  });

  it("rejects decimal JSON tokens for protocol fields frozen as strict integers", () => {
    expect(parseClientEventJson('{"type":"assistant.speak","turn_id":7}')).toEqual({ type: "assistant.speak", turn_id: 7 });
    expect(parseServerEventJson(`{"type":"vad.started","session_id":"${SESSION_ID}","turn_id":1}`)).toEqual({ type: "vad.started", session_id: SESSION_ID, turn_id: 1 });
    expect(() => parseClientEventJson('{"type":"assistant.speak","turn_id":7.0}')).toThrow();
    expect(() => parseServerEventJson(`{"type":"vad.started","session_id":"${SESSION_ID}","turn_id":1e0}`)).toThrow();
    expect(() => parseClientEventJson('{"type":"assistant.speak","turn_id":7.0e0}')).toThrow();
    expect(() => parseServerEventJson(`{"type":"tts.chunk","session_id":"${SESSION_ID}","turn_id":1,"sequence":0.0e0,"sample_rate":24000,"mime_type":"audio/wav","byte_length":46}`)).toThrow();
  });
});

describe("PCM microphone framing", () => {
  it("downsamples to exact 320-sample little-endian PCM16 frames", () => {
    const frames: ArrayBuffer[] = [];
    const packetizer = new PcmFramePacketizer(48_000, (frame) => frames.push(frame));
    packetizer.push(Float32Array.from({ length: 960 }, (_, index) => index % 2 === 0 ? 1 : -1));

    expect(frames).toHaveLength(1);
    expect(frames[0].byteLength).toBe(640);
    const view = new DataView(frames[0]);
    expect(view.getInt16(0, true)).toBeGreaterThan(10_000);
    expect(view.getInt16(2, true)).toBeLessThan(-10_000);
  });

  it("keeps downsampling phase stable across AudioWorklet block boundaries", () => {
    const input = Float32Array.from({ length: 960 }, (_, index) => ((index % 97) - 48) / 48);
    const contiguous: ArrayBuffer[] = [];
    const chunked: ArrayBuffer[] = [];
    new PcmFramePacketizer(48_000, (frame) => contiguous.push(frame)).push(input);
    const packetizer = new PcmFramePacketizer(48_000, (frame) => chunked.push(frame));
    for (let offset = 0; offset < input.length; offset += 128) packetizer.push(input.slice(offset, offset + 128));

    expect(chunked).toHaveLength(1);
    expect(new Int16Array(chunked[0])).toEqual(new Int16Array(contiguous[0]));
  });
});

describe("queued native-rate playback", () => {
  it("validates the WAV native rate while allowing browser decode resampling", async () => {
    const playback = new AudioPlayback();
    await playback.enqueue({ kind: "turn", turnId: 3, sequence: 1, sampleRate: 24_000 }, wavBytes());
    expect(MockAudioContext.instances[0].sources).toHaveLength(0);

    await playback.enqueue({ kind: "turn", turnId: 3, sequence: 0, sampleRate: 24_000 }, wavBytes());
    const sources = MockAudioContext.instances[0].sources;
    expect(sources).toHaveLength(2);
    expect(sources[0].buffer?.sampleRate).toBe(48_000);
    expect(sources[0].start).toHaveBeenCalledWith(0);
    expect(sources[1].start).toHaveBeenCalledWith(0.1);
  });

  it("replaces an older preview immediately", async () => {
    const playback = new AudioPlayback();
    await playback.enqueue({ kind: "preview", previewId: 1, sampleRate: 24_000 }, wavBytes());
    const first = MockAudioContext.instances[0].sources[0];
    await playback.enqueue({ kind: "preview", previewId: 2, sampleRate: 24_000 }, wavBytes());
    expect(first.stop).toHaveBeenCalledOnce();
  });

  it("allows reused turn identifiers after a session has closed", async () => {
    const playback = new AudioPlayback();
    playback.stopTurn(1);
    await playback.close();
    await playback.enqueue({ kind: "turn", turnId: 1, sequence: 0, sampleRate: 24_000 }, wavBytes());
    expect(MockAudioContext.instances[0].sources).toHaveLength(1);
  });

  it("restarts sequence zero when a completed turn is prepared for replay", async () => {
    const playback = new AudioPlayback();
    await playback.enqueue({ kind: "turn", turnId: 5, sequence: 0, sampleRate: 24_000 }, wavBytes());
    const first = MockAudioContext.instances[0].sources[0];

    playback.prepareTurnReplay(5);
    await playback.enqueue({ kind: "turn", turnId: 5, sequence: 0, sampleRate: 24_000 }, wavBytes());

    const sources = MockAudioContext.instances[0].sources;
    expect(first.stop).toHaveBeenCalledOnce();
    expect(sources).toHaveLength(2);
    expect(sources[1].start).toHaveBeenCalledWith(0);
  });

  it("allows a stopped turn to be prepared and replayed", async () => {
    const playback = new AudioPlayback();
    playback.stopTurn(5);

    playback.prepareTurnReplay(5);
    await playback.enqueue({ kind: "turn", turnId: 5, sequence: 0, sampleRate: 24_000 }, wavBytes());

    expect(MockAudioContext.instances[0].sources[0].start).toHaveBeenCalledWith(0);
  });

  it("does not start conversation audio whose decode finishes after interruption", async () => {
    let finishDecode!: (buffer: AudioBuffer) => void;
    MockAudioContext.decoder = () => new Promise((resolve) => { finishDecode = resolve; });
    const playback = new AudioPlayback();
    const pending = playback.enqueue({ kind: "turn", turnId: 2, sequence: 0, sampleRate: 24_000 }, wavBytes());
    playback.stopConversation();
    finishDecode({ duration: 0.1, sampleRate: 24_000 } as AudioBuffer);
    await pending;
    expect(MockAudioContext.instances[0].sources).toHaveLength(0);
  });

  it("rejects WAV bytes whose header disagrees with metadata", async () => {
    const playback = new AudioPlayback();
    await expect(playback.enqueue({ kind: "turn", turnId: 1, sequence: 0, sampleRate: 24_000 }, wavBytes(44_100))).rejects.toThrow();
    expect(MockAudioContext.instances).toHaveLength(0);
  });
});

describe("useVoiceSession", () => {
  it("connects and starts a session without requesting microphone permission", () => {
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    act(() => hook.result.current.connect());
    const socket = MockWebSocket.instances[0];
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    act(() => socket.open());
    expect(socket.jsonMessages()).toEqual([{ type: "session.start" }]);
    expect(hook.result.current.connectionStatus).toBe("connected");
  });

  it("keeps text available and reports a recoverable Chinese error when microphone permission is denied", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    const { hook, socket } = openSession();

    await act(async () => hook.result.current.startMicrophone());
    expect(hook.result.current.error).toEqual(expect.objectContaining({ recoverable: true }));
    expect(hook.result.current.error?.message).toMatch(/[\u3400-\u9fff]/u);

    act(() => hook.result.current.submitText("  仍可打字  "));
    expect(socket.jsonMessages()).toContainEqual({ type: "text.submit", text: "仍可打字", speak_response: false });
  });

  it("commits audio and closes microphone tracks without closing the socket", async () => {
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce({
      getTracks: () => [{ stop }],
    } as unknown as MediaStream);
    const { hook, socket } = openSession();

    await act(async () => hook.result.current.startMicrophone());
    await act(async () => hook.result.current.stopMicrophone());

    expect(stop).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "audio.commit" });
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
    expect(hook.result.current.isMicrophoneActive).toBe(false);
  });

  it("makes microphone start single-flight and prevents a capture appearing after disconnect", async () => {
    let resolveMedia!: (stream: MediaStream) => void;
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => new Promise((resolve) => { resolveMedia = resolve; }));
    const { hook } = openSession();

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = hook.result.current.startMicrophone();
      second = hook.result.current.startMicrophone();
    });
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce();
    await act(async () => hook.result.current.disconnect());
    resolveMedia({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    await act(async () => Promise.all([first, second]));

    expect(stop).toHaveBeenCalledOnce();
    expect(hook.result.current.isMicrophoneActive).toBe(false);
  });

  it("disposes a microphone whose permission resolves after unmount", async () => {
    let resolveMedia!: (stream: MediaStream) => void;
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => new Promise((resolve) => { resolveMedia = resolve; }));
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.startMicrophone(); });
    hook.unmount();
    resolveMedia({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    await pending;
    expect(stop).toHaveBeenCalledOnce();
  });

  it("cleans a local audio context and worklet URL when microphone setup fails", async () => {
    const stop = vi.fn();
    MockAudioContext.workletFailure = new Error("worklet failed");
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    const capture = new MicrophoneCapture(vi.fn());

    await expect(capture.start()).rejects.toThrow("worklet failed");

    expect(stop).toHaveBeenCalledOnce();
    expect(MockAudioContext.instances[0].close).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:worklet");
  });

  it("immediately aborts microphone setup while addModule is pending and permits a fresh reconnect start", async () => {
    const oldWorklet = deferred();
    MockAudioContext.workletGates = [oldWorklet.promise, Promise.resolve()];
    const oldStop = vi.fn();
    const newStop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce({ getTracks: () => [{ stop: oldStop }] } as unknown as MediaStream)
      .mockResolvedValueOnce({ getTracks: () => [{ stop: newStop }] } as unknown as MediaStream);
    const { hook } = openSession();
    let oldStart!: Promise<void>;
    act(() => { oldStart = hook.result.current.startMicrophone(); });
    await waitFor(() => expect(MockAudioContext.instances[0].audioWorklet.addModule).toHaveBeenCalledOnce());

    await act(async () => hook.result.current.disconnect());
    const cleanedBeforeWorkletSettled = oldStop.mock.calls.length === 1
      && MockAudioContext.instances[0].close.mock.calls.length === 1
      && vi.mocked(URL.revokeObjectURL).mock.calls.length === 1;
    act(() => hook.result.current.connect());
    const nextSocket = MockWebSocket.instances.at(-1)!;
    act(() => nextSocket.open());
    emit(nextSocket, readyEvent());
    let newStart!: Promise<void>;
    act(() => { newStart = hook.result.current.startMicrophone(); });
    const requestedFreshMedia = vi.mocked(navigator.mediaDevices.getUserMedia).mock.calls.length === 2;
    oldWorklet.resolve();
    await act(async () => Promise.all([oldStart, newStart]));

    expect(cleanedBeforeWorkletSettled).toBe(true);
    expect(requestedFreshMedia).toBe(true);
    expect(hook.result.current.isMicrophoneActive).toBe(true);
  });

  it("immediately aborts microphone setup while resume is pending after unmount", async () => {
    const resumeGate = deferred();
    MockAudioContext.resumeGates = [resumeGate.promise];
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.startMicrophone(); });
    await waitFor(() => expect(MockAudioContext.instances[0].resume).toHaveBeenCalledOnce());

    hook.unmount();
    await waitFor(() => {
      expect(stop).toHaveBeenCalledOnce();
      expect(MockAudioContext.instances[0].close).toHaveBeenCalledOnce();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:worklet");
    });
    const cleanedBeforeResumeSettled = stop.mock.calls.length === 1
      && MockAudioContext.instances[0].close.mock.calls.length === 1
      && vi.mocked(URL.revokeObjectURL).mock.calls.length === 1;
    resumeGate.resolve();
    await pending;

    expect(cleanedBeforeResumeSettled).toBe(true);
  });

  it("submits typed text using the current auto-speak setting and stops active playback", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 8, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    const source = MockAudioContext.instances[0].sources[0];

    act(() => hook.result.current.submitText(" hello "));

    expect(source.stop).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "hello", speak_response: false });
    expect(hook.result.current.messages.at(-1)).toEqual(expect.objectContaining({ role: "user", origin: "text", text: "hello" }));
    act(() => hook.result.current.setSpeakTextReplies(true));
    act(() => hook.result.current.submitText("again"));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "again", speak_response: true });
  });

  it("exposes the precise voice lifecycle status transitions", async () => {
    const { hook, socket } = openSession();
    expect(hook.result.current.voiceStatus).toBe("idle");
    emit(socket, { type: "vad.started", session_id: SESSION_ID, turn_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("listening");
    emit(socket, { type: "vad.stopped", session_id: SESSION_ID, turn_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("transcribing");
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 1, text: "你好" });
    expect(hook.result.current.voiceStatus).toBe("thinking");
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(hook.result.current.voiceStatus).toBe("speaking");
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("requests speech for an existing assistant message", () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 7 });
  });

  it("prepares a turn for replay before requesting its speech", () => {
    const prepareTurnReplay = vi.spyOn(AudioPlayback.prototype, "prepareTurnReplay");
    const { hook, socket } = openSession();

    act(() => hook.result.current.speakMessage(7));

    expect(prepareTurnReplay).toHaveBeenCalledWith(7);
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 7 });
  });

  it("stops active conversation playback as soon as speech starts", async () => {
    const { socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 8, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    const source = MockAudioContext.instances[0].sources[0];

    emit(socket, { type: "vad.started", session_id: SESSION_ID, turn_id: 9 });
    expect(source.stop).toHaveBeenCalledOnce();
  });

  it("ignores stale binary payloads after turn cancellation and preview replacement", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 8, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    emit(socket, { type: "turn.cancelled", session_id: SESSION_ID, turn_id: 8 });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances).toHaveLength(0);
    expect(hook.result.current.error).toBeNull();

    emit(socket, { type: "voice.preview.chunk", preview_id: 1, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    act(() => hook.result.current.previewVoice("clear_female", 1));
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances).toHaveLength(0);
  });

  it.each(["vad", "text", "cancel"] as const)("durably rejects late old-turn audio after %s interruption", async (kind) => {
    const { hook, socket } = openSession();
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 8, delta: "old" });
    act(() => hook.result.current.speakMessage(8));
    if (kind === "vad") emit(socket, { type: "vad.started", session_id: SESSION_ID, turn_id: 9 });
    if (kind === "text") act(() => hook.result.current.submitText("new"));
    if (kind === "cancel") emit(socket, { type: "turn.cancelled", session_id: SESSION_ID, turn_id: 8 });
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 8, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources).every((source) => source.start.mock.calls.length === 0)).toBe(true);
  });

  it("durably rejects a late old preview after preview replacement", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.previewVoice("clear_female", 1));
    act(() => hook.result.current.previewVoice("clear_female", 1.2));
    const wav = wavBytes();
    emit(socket, { type: "voice.preview.chunk", preview_id: 1, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources)).toHaveLength(0);
    emit(socket, { type: "voice.preview.chunk", preview_id: 2, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources).filter((source) => source.start.mock.calls.length > 0)).toHaveLength(1);
  });

  it("plays the latest preview when the purged prior preview produces no response", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.previewVoice("clear_female", 1));
    act(() => hook.result.current.previewVoice("clear_female", 1.2));
    const wav = wavBytes();
    emit(socket, { type: "voice.preview.chunk", preview_id: 2, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources).filter((source) => source.start.mock.calls.length > 0)).toHaveLength(1);
  });

  it("fails closed without misassociating binary payloads after back-to-back metadata", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 2, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources)).toHaveLength(0);
    expect(hook.result.current.error).toEqual(expect.objectContaining({ code: "invalid_server_event", recoverable: true }));

    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 3, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources).filter((source) => source.start.mock.calls.length > 0)).toHaveLength(1);
  });

  it("ignores old socket callbacks and keeps reused turn IDs in separate drafts", async () => {
    const { hook, socket: oldSocket } = openSession();
    emit(oldSocket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: "old" });
    await act(async () => hook.result.current.disconnect());
    act(() => hook.result.current.connect());
    const currentSocket = MockWebSocket.instances.at(-1)!;
    act(() => currentSocket.open());
    emit(currentSocket, readyEvent());
    emit(currentSocket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: "new" });
    emit(oldSocket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: "stale" });

    expect(hook.result.current.messages.filter((message) => message.role === "assistant").map((message) => message.text)).toEqual(["old", "new"]);
  });

  it("tears down microphone and playback on an unexpected socket close", async () => {
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startMicrophone());
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    const source = MockAudioContext.instances.flatMap((context) => context.sources)[0];

    act(() => socket.close());
    await waitFor(() => expect(stop).toHaveBeenCalledOnce());
    expect(source.stop).toHaveBeenCalledOnce();
    expect(hook.result.current.connectionStatus).toBe("disconnected");
    expect(hook.result.current.isMicrophoneActive).toBe(false);
  });

  it("does not let delayed old-socket cleanup clear an immediately reconnected draft", async () => {
    const oldClose = deferred();
    MockAudioContext.closeGates = [oldClose.promise];
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream);
    const { hook, socket: oldSocket } = openSession();
    await act(async () => hook.result.current.startMicrophone());

    act(() => oldSocket.close());
    act(() => hook.result.current.connect());
    const newSocket = MockWebSocket.instances.at(-1)!;
    act(() => newSocket.open());
    emit(newSocket, readyEvent());
    emit(newSocket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: "new" });
    oldClose.resolve();
    await act(async () => Promise.resolve());
    emit(newSocket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: " draft" });

    expect(hook.result.current.connectionStatus).toBe("connected");
    expect(hook.result.current.messages.filter((message) => message.role === "assistant").map((message) => message.text)).toEqual(["new draft"]);
  });

  it("keeps voice and typed messages in one ordered stream and preserves cancelled text", () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.submitText("typed"));
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 2, text: "spoken" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 2, delta: "答" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 2, delta: "案" });
    emit(socket, { type: "turn.cancelled", session_id: SESSION_ID, turn_id: 2 });

    expect(hook.result.current.messages.map(({ role, text, status }) => ({ role, text, status }))).toEqual([
      { role: "user", text: "typed", status: "complete" },
      { role: "user", text: "spoken", status: "complete" },
      { role: "assistant", text: "答案", status: "cancelled" },
    ]);
  });

  it("persists only public voice settings and disconnects every local resource", async () => {
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce({ getTracks: () => [{ stop }] } as unknown as MediaStream);
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startMicrophone());
    act(() => hook.result.current.selectVoice("clear_female", 0.8));

    await act(async () => hook.result.current.disconnect());

    expect(JSON.parse(localStorage.getItem("voxagent.voice-settings.v1")!)).toEqual({ voiceKey: "clear_female", speed: 0.8, speakTextReplies: false });
    expect(stop).toHaveBeenCalledOnce();
    expect(socket.readyState).toBe(MockWebSocket.CLOSED);
    await waitFor(() => expect(hook.result.current.connectionStatus).toBe("disconnected"));
  });
});
