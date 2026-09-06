import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import fixtureSource from "../../../contracts/protocol-fixtures.json?raw";
import { MicrophoneCapture, PcmFramePacketizer } from "../audio/capture";
import { AudioPlayback } from "../audio/playback";
import { BrowserSpeechProvider, type BrowserSpeechCallbacks } from "../audio/webSpeech";
import { parseClientEvent, parseClientEventJson, parseServerEvent, parseServerEventJson } from "../protocol";
import { RealtimeVoiceEngine } from "../realtime/RealtimeVoiceEngine";
import { useVoiceSession } from "../useVoiceSession";

const SESSION_ID = "00000000-0000-4000-8000-000000000001";
const fixtures = JSON.parse(fixtureSource.replace(
  /("(?:turn_id|request_id|start_offset|preview_id|sequence|sample_rate|byte_length|frame_samples|frame_bytes)"\s*:\s*)(-?\d+)\.0(?=\s*[,}])/gu,
  "$1\"$2.0\"",
)) as {
  valid_client: unknown[];
  invalid_client: unknown[];
  valid_server: unknown[];
  invalid_server: unknown[];
  astral_text_boundaries: Array<{
    event_type: "voice.transcript.submit";
    scalar: string;
    valid_count: number;
    invalid_count: number;
    request_id: number;
  }>;
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

function microphoneStream(stop = vi.fn()): MediaStream {
  const track = {
    readyState: "live",
    getSettings: () => ({}),
    stop,
  } as unknown as MediaStreamTrack;
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
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
  readonly state = "suspended";
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
    value: {
      getUserMedia: vi.fn(),
      enumerateDevices: vi.fn().mockResolvedValue([]),
      getSupportedConstraints: () => ({
        deviceId: true,
        channelCount: true,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      }),
    },
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
    expect(() => parseClientEventJson('{"type":"assistant.speak","turn_id":7,"request_id":1.0}')).toThrow();
    expect(() => parseServerEventJson(`{"type":"asr.partial","session_id":"${SESSION_ID}","turn_id":1.0,"text":"你好"}`)).toThrow();
    expect(() => parseServerEventJson(`{"type":"tts.done","session_id":"${SESSION_ID}","turn_id":1,"request_id":1e0}`)).toThrow();
    expect(() => parseServerEventJson(`{"type":"tts.chunk","session_id":"${SESSION_ID}","turn_id":1,"sequence":0.0e0,"sample_rate":24000,"mime_type":"audio/wav","byte_length":46}`)).toThrow();
  });

  it("strictly correlates browser transcript submissions with server turn events", () => {
    expect(parseClientEventJson('{"type":"voice.transcript.submit","text":"你好","request_id":9}')).toEqual({
      type: "voice.transcript.submit",
      text: "你好",
      request_id: 9,
    });
    expect(parseServerEventJson(`{"type":"asr.final","session_id":"${SESSION_ID}","turn_id":3,"text":"你好","request_id":9}`)).toEqual({
      type: "asr.final",
      session_id: SESSION_ID,
      turn_id: 3,
      text: "你好",
      request_id: 9,
    });
    expect(parseServerEventJson(`{"type":"turn.cancelled","session_id":"${SESSION_ID}","turn_id":3,"request_id":9}`)).toEqual({
      type: "turn.cancelled",
      session_id: SESSION_ID,
      turn_id: 3,
      request_id: 9,
    });
    expect(() => parseClientEventJson('{"type":"voice.transcript.submit","text":"你好"}')).toThrow();
    expect(() => parseClientEventJson('{"type":"voice.transcript.submit","text":"你好","request_id":1.0}')).toThrow();
  });

  it("honors the shared astral 4,000/4,001-code-point boundary fixture", () => {
    const boundary = fixtures.astral_text_boundaries[0];
    expect([...boundary.scalar]).toHaveLength(1);
    const valid = {
      type: boundary.event_type,
      text: boundary.scalar.repeat(boundary.valid_count),
      request_id: boundary.request_id,
    };

    const parsed = parseClientEvent(valid);
    if (parsed.type !== "voice.transcript.submit") throw new TypeError("unexpected event type");
    expect([...parsed.text]).toHaveLength(4_000);
    expect(() => parseClientEvent({
      ...valid,
      text: boundary.scalar.repeat(boundary.invalid_count),
    })).toThrow();
  });

  it("parses strict memory proposals with source turn attribution", () => {
    expect(parseServerEvent({
      type: "memory.proposed",
      session_id: SESSION_ID,
      turn_id: 4,
      proposal_index: 0,
      source_message_id: 19,
      kind: "preference",
      content: "用户喜欢无糖咖啡",
      importance: 0.8,
      requires_confirmation: false,
    })).toMatchObject({ type: "memory.proposed", turn_id: 4, content: "用户喜欢无糖咖啡" });
  });

  it("parses exact reviewable context sources", () => {
    const event = parseServerEvent({
      type: "context.sources",
      session_id: SESSION_ID,
      turn_id: 4,
      memories: [{ id: 2, content: "喜欢茶", source_message_id: 8, source_text: "我喜欢喝茶", source_turn_id: 3 }],
      knowledge: [{ chunk_id: 7, document_id: 4, display_name: "手册.pdf", content: "八十度水温", page_number: 5 }],
    });

    expect(event).toMatchObject({ type: "context.sources", turn_id: 4 });
    expect(() => parseServerEvent({ ...event, local_path: "D:\\secret.pdf" })).toThrow();
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

  it("uses one selected track for diagnostics, levels, and optional PCM forwarding", async () => {
    const stop = vi.fn();
    const settings = { deviceId: "realtek", channelCount: 1 } as MediaTrackSettings;
    const track = { getSettings: vi.fn(() => settings), stop } as unknown as MediaStreamTrack;
    const stream = {
      getAudioTracks: () => [track],
      getTracks: () => [track],
    } as unknown as MediaStream;
    const onFrame = vi.fn();
    const onLevel = vi.fn();
    const onSettings = vi.fn();
    Object.assign(navigator.mediaDevices, {
      getSupportedConstraints: () => ({
        deviceId: true,
        channelCount: true,
        echoCancellation: true,
        autoGainControl: true,
      }),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(stream);
    const capture = new MicrophoneCapture({
      deviceId: "realtek",
      forwardPcm: false,
      onFrame,
      onLevel,
      onSettings,
    });

    await expect(capture.start()).resolves.toBe(track);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        deviceId: { exact: "realtek" },
        channelCount: 1,
        echoCancellation: true,
        autoGainControl: true,
      },
    });
    expect(onSettings).toHaveBeenCalledWith(settings);

    const worklet = (capture as unknown as { worklet: MockAudioWorkletNode }).worklet;
    worklet.port.onmessage?.(new MessageEvent("message", { data: new Float32Array(960).fill(0.5) }));
    expect(onLevel).toHaveBeenLastCalledWith(0.5);
    expect(onFrame).not.toHaveBeenCalled();

    capture.setForwardPcm(true);
    worklet.port.onmessage?.(new MessageEvent("message", { data: new Float32Array(960).fill(0.5) }));
    expect(onFrame).toHaveBeenCalledOnce();
    expect(onFrame.mock.calls[0][0].byteLength).toBe(640);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce();

    capture.setForwardPcm(false);
    worklet.port.onmessage?.(new MessageEvent("message", { data: new Float32Array(960).fill(0.5) }));
    expect(onFrame).toHaveBeenCalledOnce();
    await capture.stop();
    expect(stop).toHaveBeenCalledOnce();
  });
});

describe("queued native-rate playback", () => {
  it("preserves completion when tts done arrives before final audio decode", async () => {
    const completed = vi.fn();
    const playback = new AudioPlayback(completed);

    playback.finishTurn(2);
    expect(completed).not.toHaveBeenCalled();
    await playback.enqueue({ kind: "turn", turnId: 2, sequence: 0, sampleRate: 24_000 }, wavBytes());
    MockAudioContext.instances[0].sources[0].onended?.();

    expect(completed).toHaveBeenCalledWith({ kind: "turn", turnId: 2 });
  });

  it("reports natural completion only after the final scheduled source ends", async () => {
    const completed = vi.fn();
    const playback = new AudioPlayback(completed);
    await playback.enqueue({ kind: "turn", turnId: 3, sequence: 0, sampleRate: 24_000 }, wavBytes());
    await playback.enqueue({ kind: "turn", turnId: 3, sequence: 1, sampleRate: 24_000 }, wavBytes());
    const [first, second] = MockAudioContext.instances[0].sources;

    first.onended?.();
    expect(completed).not.toHaveBeenCalled();
    playback.finishTurn(3);
    second.onended?.();

    expect(completed).toHaveBeenCalledOnce();
    expect(completed).toHaveBeenCalledWith({ kind: "turn", turnId: 3 });
  });

  it("keeps the turn queue until a later synthesized chunk and assistant completion arrive", async () => {
    const completed = vi.fn();
    const playback = new AudioPlayback(completed);
    await playback.enqueue({ kind: "turn", turnId: 4, sequence: 0, sampleRate: 24_000 }, wavBytes());
    const first = MockAudioContext.instances[0].sources[0];
    first.onended?.();
    expect(completed).not.toHaveBeenCalled();

    await playback.enqueue({ kind: "turn", turnId: 4, sequence: 1, sampleRate: 24_000 }, wavBytes());
    const second = MockAudioContext.instances[0].sources[1];
    expect(second.start).toHaveBeenCalled();
    playback.finishTurn(4);
    second.onended?.();

    expect(completed).toHaveBeenCalledWith({ kind: "turn", turnId: 4 });
  });

  it("waits for an in-flight final chunk decode before completing the turn", async () => {
    const completed = vi.fn();
    const playback = new AudioPlayback(completed);
    await playback.enqueue({ kind: "turn", turnId: 5, sequence: 0, sampleRate: 24_000 }, wavBytes());
    MockAudioContext.instances[0].sources[0].onended?.();
    const decoded = deferred<AudioBuffer>();
    MockAudioContext.decoder = () => decoded.promise;

    const finalChunk = playback.enqueue({ kind: "turn", turnId: 5, sequence: 1, sampleRate: 24_000 }, wavBytes());
    playback.finishTurn(5);
    expect(completed).not.toHaveBeenCalled();
    decoded.resolve({ duration: 0.1, sampleRate: 48_000 } as AudioBuffer);
    await finalChunk;
    const finalSource = MockAudioContext.instances[0].sources[1];
    expect(finalSource.start).toHaveBeenCalled();
    finalSource.onended?.();

    expect(completed).toHaveBeenCalledWith({ kind: "turn", turnId: 5 });
  });

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
  it("updates delayed browser voices without starting a call and cleans up its listener", () => {
    const originalSpeechSynthesis = Object.getOwnPropertyDescriptor(window, "speechSynthesis");
    let voices: SpeechSynthesisVoice[] = [];
    let voiceChangeListener: EventListener | null = null;
    const speechSynthesis = {
      getVoices: vi.fn(() => voices),
      speak: vi.fn(),
      cancel: vi.fn(),
      addEventListener: vi.fn((type: string, listener: EventListener) => {
        if (type === "voiceschanged") voiceChangeListener = listener;
      }),
      removeEventListener: vi.fn(),
    };
    Object.defineProperty(window, "speechSynthesis", { configurable: true, value: speechSynthesis });

    const hook = renderHook(() => useVoiceSession({ url: "ws://127.0.0.1:8765/v1/voice?token=test" }));
    expect(hook.result.current.browserVoices).toEqual([]);
    expect(voiceChangeListener).not.toBeNull();

    voices = [{
      voiceURI: "zh-xiaoxiao",
      name: "Microsoft Xiaoxiao",
      lang: "zh-CN",
      localService: false,
    } as SpeechSynthesisVoice];
    act(() => voiceChangeListener?.(new Event("voiceschanged")));

    expect(hook.result.current.browserVoices).toEqual([
      { key: "zh-xiaoxiao", name: "Microsoft Xiaoxiao", lang: "zh-CN", localService: false },
    ]);
    expect(navigator.mediaDevices.enumerateDevices).not.toHaveBeenCalled();
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    hook.unmount();
    expect(speechSynthesis.removeEventListener).toHaveBeenCalledWith("voiceschanged", voiceChangeListener);
    if (originalSpeechSynthesis) Object.defineProperty(window, "speechSynthesis", originalSpeechSynthesis);
    else Reflect.deleteProperty(window, "speechSynthesis");
  });

  it("starts a browser realtime call and submits correlated final transcripts", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockResolvedValue([
        { deviceId: "default", groupId: "realtek-group", label: "Default", kind: "audioinput", toJSON: () => ({}) },
        { deviceId: "realtek", groupId: "realtek-group", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) },
      ]),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();

    await act(async () => hook.result.current.startRealtimeCall());

    expect(hook.result.current.realtime).toMatchObject({ active: true, provider: "browser", state: "listening" });
    expect(hook.result.current.selectedMicrophoneId).toBe("realtek");
    expect(browserStart).toHaveBeenCalledWith(expect.anything(), expect.anything(), true);
    act(() => callbacks.onFinal("帮我总结文档"));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "voice.transcript.submit", text: "帮我总结文档", request_id: 1 });

    act(() => hook.result.current.submitText("文字问题"));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "文字问题", speak_response: false });
  });

  it("authorizes redacted microphones, re-enumerates, and reacquires the preferred Realtek track exactly", async () => {
    const authorizationStop = vi.fn();
    const actualStop = vi.fn();
    const actualSettings = { deviceId: "realtek", groupId: "physical", channelCount: 1 } as MediaTrackSettings;
    const actualTrack = {
      readyState: "live",
      getSettings: () => actualSettings,
      stop: actualStop,
    } as unknown as MediaStreamTrack;
    const actualStream = {
      getAudioTracks: () => [actualTrack],
      getTracks: () => [actualTrack],
    } as unknown as MediaStream;
    vi.mocked(navigator.mediaDevices.enumerateDevices)
      .mockResolvedValueOnce([
        { deviceId: "default", groupId: "", label: "", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
        { deviceId: "virtual", groupId: "", label: "", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
      ])
      .mockResolvedValueOnce([
        { deviceId: "virtual", groupId: "virtual", label: "ToDesk Virtual Audio", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
        { deviceId: "realtek", groupId: "physical", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
      ]);
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce(microphoneStream(authorizationStop))
      .mockResolvedValueOnce(actualStream);
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const { hook } = openSession();

    await act(async () => hook.result.current.startRealtimeCall());

    expect(navigator.mediaDevices.enumerateDevices).toHaveBeenCalledTimes(2);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenNthCalledWith(1, { audio: true });
    expect(authorizationStop).toHaveBeenCalledOnce();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenNthCalledWith(2, {
      audio: expect.objectContaining({ deviceId: { exact: "realtek" } }),
    });
    expect(hook.result.current.selectedMicrophoneId).toBe("realtek");
    expect(hook.result.current.microphoneSettings).toEqual(actualSettings);
  });

  it("stops a late permission stream without letting an obsolete call start win", async () => {
    const permission = deferred<MediaStream>();
    const lateStop = vi.fn();
    const currentStream = microphoneStream();
    vi.mocked(navigator.mediaDevices.enumerateDevices)
      .mockResolvedValueOnce([
        { deviceId: "default", groupId: "", label: "", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
      ])
      .mockResolvedValue([
        { deviceId: "realtek", groupId: "physical", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
      ]);
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockReturnValueOnce(permission.promise)
      .mockResolvedValueOnce(currentStream);
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const { hook } = openSession();

    let obsoleteStart!: Promise<void>;
    act(() => { obsoleteStart = hook.result.current.startRealtimeCall(); });
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({ audio: true }));
    await act(async () => hook.result.current.stopRealtimeCall());
    await act(async () => hook.result.current.startRealtimeCall());
    permission.resolve(microphoneStream(lateStop));
    await act(async () => obsoleteStart);

    expect(lateStop).toHaveBeenCalledOnce();
    expect(browserStart).toHaveBeenCalledOnce();
    expect(hook.result.current.selectedMicrophoneId).toBe("realtek");
    expect(hook.result.current.realtime).toMatchObject({ active: true, provider: "browser", state: "listening" });
  });

  it("restarts an active realtime call on a newly selected physical microphone", async () => {
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const firstTrackStop = vi.fn();
    const secondTrackStop = vi.fn();
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockResolvedValue([
        { deviceId: "realtek", groupId: "realtek-group", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) },
        { deviceId: "usb", groupId: "usb-group", label: "USB Microphone", kind: "audioinput", toJSON: () => ({}) },
      ]),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce(microphoneStream(firstTrackStop))
      .mockResolvedValueOnce(microphoneStream(secondTrackStop));
    const { hook } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    await act(async () => hook.result.current.selectMicrophone("usb"));

    expect(firstTrackStop).toHaveBeenCalledOnce();
    expect(browserStart).toHaveBeenCalledTimes(2);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({ deviceId: { exact: "usb" } }),
    });
    expect(hook.result.current.selectedMicrophoneId).toBe("usb");
    expect(hook.result.current.realtime).toMatchObject({ active: true, state: "listening" });
    expect(secondTrackStop).not.toHaveBeenCalled();
  });

  it("lets the latest rapid microphone selection win while the prior capture is closing", async () => {
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const closeGate = deferred();
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockResolvedValue([
        { deviceId: "realtek", groupId: "realtek-group", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) },
        { deviceId: "usb", groupId: "usb-group", label: "USB Microphone", kind: "audioinput", toJSON: () => ({}) },
      ]),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce(microphoneStream())
      .mockResolvedValue(microphoneStream());
    const { hook } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    MockAudioContext.closeGates = [closeGate.promise];

    let selectUsb!: Promise<void>;
    act(() => { selectUsb = hook.result.current.selectMicrophone("usb"); });
    await waitFor(() => expect(hook.result.current.realtime.active).toBe(false));
    let selectRealtek!: Promise<void>;
    act(() => { selectRealtek = hook.result.current.selectMicrophone("realtek"); });
    await act(async () => {
      closeGate.resolve();
      await Promise.all([selectUsb, selectRealtek]);
    });

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({ deviceId: { exact: "realtek" } }),
    });
    expect(browserStart).toHaveBeenCalledTimes(2);
    expect(hook.result.current.selectedMicrophoneId).toBe("realtek");
    expect(hook.result.current.realtime).toMatchObject({ active: true, state: "listening" });
  });

  it("does not reopen a microphone when the call is stopped during device restart", async () => {
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const closeGate = deferred();
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockResolvedValue([
        { deviceId: "realtek", groupId: "realtek-group", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) },
        { deviceId: "usb", groupId: "usb-group", label: "USB Microphone", kind: "audioinput", toJSON: () => ({}) },
      ]),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    MockAudioContext.closeGates = [closeGate.promise];

    let selecting!: Promise<void>;
    act(() => { selecting = hook.result.current.selectMicrophone("usb"); });
    await waitFor(() => expect(hook.result.current.realtime.active).toBe(false));
    let stopping!: Promise<void>;
    act(() => { stopping = hook.result.current.stopRealtimeCall(); });
    await act(async () => {
      closeGate.resolve();
      await Promise.all([selecting, stopping]);
    });

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
    expect(browserStart).toHaveBeenCalledOnce();
    expect(hook.result.current.realtime).toMatchObject({ active: false, state: "off" });
  });

  it("routes each server event through the engine once and keeps partial ASR transient", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const engineEvents = vi.spyOn(RealtimeVoiceEngine.prototype, "handleServerEvent");
    const { hook, socket } = openSession();
    act(() => hook.result.current.setSpeechMode("local-only"));
    await act(async () => hook.result.current.startRealtimeCall());
    engineEvents.mockClear();

    emit(socket, { type: "vad.started", session_id: SESSION_ID, turn_id: 3 });
    emit(socket, { type: "asr.partial", session_id: SESSION_ID, turn_id: 3, text: "临时字幕" });

    expect(engineEvents).toHaveBeenCalledTimes(2);
    expect(hook.result.current.realtime.interimText).toBe("临时字幕");
    expect(hook.result.current.messages).toEqual([]);
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 3, text: "最终问题" });
    expect(hook.result.current.messages).toEqual([
      expect.objectContaining({ role: "user", origin: "voice", text: "最终问题", status: "complete" }),
    ]);
  });

  it("turns local partial then final ASR into exactly one permanent user message", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    act(() => hook.result.current.setSpeechMode("local-only"));
    await act(async () => hook.result.current.startRealtimeCall());

    emit(socket, { type: "vad.started", session_id: SESSION_ID, turn_id: 4 });
    emit(socket, { type: "asr.partial", session_id: SESSION_ID, turn_id: 4, text: "本地" });
    emit(socket, { type: "asr.partial", session_id: SESSION_ID, turn_id: 4, text: "本地最终" });
    expect(hook.result.current.messages).toEqual([]);
    expect(hook.result.current.realtime.interimText).toBe("本地最终");

    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 4, text: "本地最终转写" });

    expect(hook.result.current.messages).toEqual([
      expect.objectContaining({
        turnId: 4,
        role: "user",
        origin: "voice",
        text: "本地最终转写",
        status: "complete",
      }),
    ]);
    expect(hook.result.current.realtime.interimText).toBe("");
  });

  it("persists notice acceptance without changing it when local-only starts", async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook } = openSession();

    act(() => hook.result.current.acceptOnlineSpeechNotice());
    act(() => hook.result.current.setSpeechMode("local-only"));
    await act(async () => hook.result.current.startRealtimeCall());

    expect(hook.result.current.onlineSpeechNoticeAccepted).toBe(true);
    expect(JSON.parse(localStorage.getItem("voxagent.voice-settings.v2")!)).toMatchObject({
      speechMode: "local-only",
      onlineSpeechNoticeAccepted: true,
    });
  });

  it("switches providers in an active call without reconnecting the socket or reacquiring the microphone", async () => {
    const browserStart = vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValue([
      { deviceId: "realtek", groupId: "physical", label: "Microphone Array (Realtek)", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
    ]);
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    act(() => hook.result.current.acceptOnlineSpeechNotice());
    act(() => hook.result.current.setSpeechMode("local-only"));
    await act(async () => hook.result.current.startRealtimeCall());
    expect(hook.result.current.realtime.provider).toBe("local");

    act(() => hook.result.current.setSpeechMode("online-preferred"));
    await waitFor(() => expect(hook.result.current.realtime.provider).toBe("browser"));

    expect(browserStart).toHaveBeenCalledOnce();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce();
    expect(MockWebSocket.instances).toEqual([socket]);
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
  });

  it("uses positive monotonic request IDs for browser transcript submissions", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    act(() => callbacks.onFinal("第一问"));
    const first = socket.jsonMessages().at(-1)!;
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 1, request_id: first.request_id, text: "第一问" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 1 });
    act(() => callbacks.onFinal("第二问"));
    const second = socket.jsonMessages().at(-1)!;

    expect(first).toEqual({ type: "voice.transcript.submit", text: "第一问", request_id: 1 });
    expect(second).toEqual({ type: "voice.transcript.submit", text: "第二问", request_id: 2 });
  });

  it("does not persist a stale browser ASR echo after a realtime restart", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("旧问题"));
    const oldRequestId = socket.jsonMessages().at(-1)!.request_id as number;
    await act(async () => hook.result.current.stopRealtimeCall());
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("新问题"));
    const newRequestId = socket.jsonMessages().at(-1)!.request_id as number;

    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 1, request_id: oldRequestId, text: "旧问题" });
    expect(hook.result.current.messages).toEqual([]);
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 2, request_id: newRequestId, text: "新问题" });

    expect(hook.result.current.messages).toEqual([
      expect.objectContaining({ turnId: 2, role: "user", origin: "voice", text: "新问题" }),
    ]);
  });

  it("speaks a completed assistant message with the selected browser voice", async () => {
    const browserVoice = { key: "zh-xiaoxiao", name: "Xiaoxiao", lang: "zh-CN", localService: false };
    vi.spyOn(BrowserSpeechProvider.prototype, "voices").mockReturnValue([browserVoice]);
    const speak = vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockResolvedValue();
    const { hook, socket } = openSession();
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "这是完整回答。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    act(() => hook.result.current.selectBrowserVoice(browserVoice.key));

    await act(async () => {
      hook.result.current.speakMessage(7);
      await Promise.resolve();
    });

    expect(speak).toHaveBeenCalledOnce();
    expect(speak).toHaveBeenCalledWith("这是完整回答。", browserVoice.key, 1);
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("falls back to one local replay when browser speech fails", async () => {
    const browserVoice = { key: "zh-xiaoxiao", name: "Xiaoxiao", lang: "zh-CN", localService: false };
    vi.spyOn(BrowserSpeechProvider.prototype, "voices").mockReturnValue([browserVoice]);
    const speak = vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockRejectedValue(new Error("browser speech failed"));
    const { hook, socket } = openSession();
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "回退回答。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    act(() => hook.result.current.selectBrowserVoice(browserVoice.key));

    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toHaveLength(1));
    emit(socket, { type: "tts.error", session_id: SESSION_ID, turn_id: 7, request_id: 1, code: "tts_failed", message: "failed", recoverable: true });

    expect(speak).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toHaveLength(1);
  });

  it("plays the engine's correlated local fallback audio", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockRejectedValue(new Error("browser speech failed"));
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("请回答"));
    const transcriptRequestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 7, request_id: transcriptRequestId, text: "请回答" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "浏览器失败后回退。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const speechRequestId = socket.jsonMessages().at(-1)!.request_id as number;
    const wav = wavBytes();

    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: speechRequestId });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: speechRequestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));

    expect(MockAudioContext.instances.flatMap((context) => context.sources).filter((source) => source.start.mock.calls.length > 0)).toHaveLength(1);
  });

  it("keeps completed text visible and returns to listening when Kokoro fallback fails", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockRejectedValue(
      new Error("No Chinese browser voice available"),
    );
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("请回答"));
    const transcriptRequestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, {
      type: "asr.final",
      session_id: SESSION_ID,
      turn_id: 8,
      request_id: transcriptRequestId,
      text: "请回答",
    });
    emit(socket, {
      type: "assistant.delta",
      session_id: SESSION_ID,
      turn_id: 8,
      delta: "即使朗读失败，这段文字也必须保留。",
    });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 8 });
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({
      type: "assistant.speak",
      turn_id: 8,
    }));
    const speechRequestId = socket.jsonMessages().at(-1)!.request_id as number;

    emit(socket, {
      type: "tts.error",
      session_id: SESSION_ID,
      turn_id: 8,
      request_id: speechRequestId,
      code: "tts_failed",
      message: "朗读失败，文字回答仍然可用",
      recoverable: true,
    });

    expect(hook.result.current.messages).toContainEqual(expect.objectContaining({
      turnId: 8,
      role: "assistant",
      text: "即使朗读失败，这段文字也必须保留。",
      status: "complete",
    }));
    expect(hook.result.current.realtime).toMatchObject({
      active: true,
      provider: "local",
      state: "listening",
    });
  });

  it("uses one request sequence for engine fallback and manual replay", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockRejectedValue(new Error("browser speech failed"));
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("请回答"));
    const transcriptRequestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 7, request_id: transcriptRequestId, text: "请回答" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "回退后重播。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    await waitFor(() => expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toHaveLength(1));
    const engineRequestId = socket.jsonMessages().filter((event) => event.type === "assistant.speak")[0].request_id as number;

    act(() => hook.result.current.setSpeechMode("local-only"));
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toHaveLength(2));
    const manualRequestId = socket.jsonMessages().filter((event) => event.type === "assistant.speak")[1].request_id as number;

    expect(manualRequestId).toBeGreaterThan(engineRequestId);
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: engineRequestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources)).toHaveLength(0);
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: manualRequestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources).filter((source) => source.start.mock.calls.length > 0)).toHaveLength(1);
  });

  it("uses local replay immediately in local-only mode", async () => {
    const speak = vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockResolvedValue();
    const { hook, socket } = openSession();
    act(() => hook.result.current.setSpeechMode("local-only"));
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "本地回答。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });

    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 7, request_id: 1 }));

    expect(speak).not.toHaveBeenCalled();
  });

  it("ignores stale browser completion after replay is stopped", async () => {
    const speech = deferred();
    const browserVoice = { key: "zh-xiaoxiao", name: "Xiaoxiao", lang: "zh-CN", localService: false };
    vi.spyOn(BrowserSpeechProvider.prototype, "voices").mockReturnValue([browserVoice]);
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockReturnValue(speech.promise);
    const cancelSpeech = vi.spyOn(BrowserSpeechProvider.prototype, "cancelSpeech");
    const { hook, socket } = openSession();
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "不要恢复。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    act(() => hook.result.current.selectBrowserVoice(browserVoice.key));
    act(() => hook.result.current.speakMessage(7));

    act(() => hook.result.current.stopSpeaking(7));
    speech.resolve();
    await act(async () => speech.promise);

    expect(cancelSpeech).toHaveBeenCalled();
    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("keeps typed input silent while cancelling an active browser replay", async () => {
    const speech = deferred();
    const browserVoice = { key: "zh-xiaoxiao", name: "Xiaoxiao", lang: "zh-CN", localService: false };
    vi.spyOn(BrowserSpeechProvider.prototype, "voices").mockReturnValue([browserVoice]);
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockReturnValue(speech.promise);
    const cancelSpeech = vi.spyOn(BrowserSpeechProvider.prototype, "cancelSpeech");
    const { hook, socket } = openSession();
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "正在朗读。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });
    act(() => hook.result.current.selectBrowserVoice(browserVoice.key));
    act(() => hook.result.current.speakMessage(7));
    cancelSpeech.mockClear();

    act(() => hook.result.current.submitText("只要文字"));
    speech.resolve();
    await act(async () => speech.promise);

    expect(cancelSpeech).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "只要文字", speak_response: false });
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("does not turn intentional realtime speech cancellation into typed-input fallback", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    const engineSpeech = deferred();
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockReturnValue(engineSpeech.promise);
    vi.spyOn(BrowserSpeechProvider.prototype, "cancelSpeech").mockImplementation(() => {
      engineSpeech.reject(new DOMException("cancelled", "AbortError"));
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("语音问题"));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 7, request_id: requestId, text: "语音问题" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "正在通过浏览器朗读。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });

    act(() => hook.result.current.submitText("只返回文字"));
    await act(async () => Promise.resolve());

    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "只返回文字", speak_response: false });
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("keeps a typed reply silent while an online realtime call remains active", async () => {
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    const browserSpeak = vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockResolvedValue();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    act(() => hook.result.current.submitText("只回答文字"));
    emit(socket, {
      type: "assistant.delta",
      session_id: SESSION_ID,
      turn_id: 9,
      delta: "这是文字输入的回答。",
    });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 9 });

    expect(hook.result.current.realtime).toMatchObject({ active: true, provider: "browser" });
    expect(socket.jsonMessages()).toContainEqual({
      type: "text.submit",
      text: "只回答文字",
      speak_response: false,
    });
    expect(hook.result.current.messages).toContainEqual(expect.objectContaining({
      turnId: 9,
      role: "assistant",
      text: "这是文字输入的回答。",
      status: "complete",
    }));
    expect(browserSpeak).not.toHaveBeenCalled();
    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("does not turn intentional realtime speech cancellation into manual-replay fallback", async () => {
    let callbacks!: BrowserSpeechCallbacks;
    const engineSpeech = deferred();
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, nextCallbacks) => {
      callbacks = nextCallbacks;
    });
    const speak = vi.spyOn(BrowserSpeechProvider.prototype, "speak")
      .mockReturnValueOnce(engineSpeech.promise)
      .mockResolvedValueOnce();
    vi.spyOn(BrowserSpeechProvider.prototype, "cancelSpeech").mockImplementation(() => {
      engineSpeech.reject(new DOMException("cancelled", "AbortError"));
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    act(() => callbacks.onFinal("语音问题"));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, { type: "asr.final", session_id: SESSION_ID, turn_id: 7, request_id: requestId, text: "语音问题" });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 7, delta: "可手动重播。" });
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 7 });

    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(speak).toHaveBeenCalledTimes(2));
    await act(async () => Promise.resolve());

    expect(socket.jsonMessages().filter((event) => event.type === "assistant.speak")).toEqual([]);
  });

  it("stops realtime voice on socket close and session reset", async () => {
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const stop = vi.spyOn(RealtimeVoiceEngine.prototype, "stop");
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    emit(socket, readyEvent());
    expect(stop).toHaveBeenCalledOnce();
    expect(hook.result.current.realtime.state).toBe("off");

    await act(async () => hook.result.current.startRealtimeCall());
    act(() => socket.close());
    await waitFor(() => expect(stop).toHaveBeenCalledTimes(2));
    expect(hook.result.current.realtime.state).toBe("off");
  });

  it("stops realtime voice when local data is cleared", async () => {
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const stop = vi.spyOn(RealtimeVoiceEngine.prototype, "stop");
    const { hook } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    act(() => hook.result.current.clearLocalData());

    expect(stop).toHaveBeenCalledOnce();
    expect(hook.result.current.realtime.state).toBe("off");
  });

  it("stops realtime voice when the hook unmounts", async () => {
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockResolvedValue();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const stop = vi.spyOn(RealtimeVoiceEngine.prototype, "stop");
    const { hook } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());

    hook.unmount();

    expect(stop).toHaveBeenCalledOnce();
  });

  it.each(["stop", "socket-close", "session-reset", "clear", "unmount"] as const)(
    "does not start realtime capture after %s wins during device enumeration",
    async (cleanupKind) => {
      const enumeration = deferred<MediaDeviceInfo[]>();
      Object.assign(navigator.mediaDevices, { enumerateDevices: vi.fn(() => enumeration.promise) });
      const { hook, socket } = openSession();
      let starting!: Promise<void>;
      act(() => { starting = hook.result.current.startRealtimeCall(); });
      await waitFor(() => expect(navigator.mediaDevices.enumerateDevices).toHaveBeenCalledOnce());

      if (cleanupKind === "stop") await act(async () => hook.result.current.stopRealtimeCall());
      if (cleanupKind === "socket-close") act(() => socket.close());
      if (cleanupKind === "session-reset") emit(socket, readyEvent());
      if (cleanupKind === "clear") act(() => hook.result.current.clearLocalData());
      if (cleanupKind === "unmount") hook.unmount();
      enumeration.resolve([{
        deviceId: "late-realtek",
        groupId: "late-group",
        label: "Microphone Array (Realtek)",
        kind: "audioinput",
        toJSON: () => ({}),
      }]);
      await act(async () => starting);

      expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
      expect(localStorage.getItem("voxagent.voice-settings.v2")).toBeNull();
    },
  );

  it("binds retrieved sources to the matching assistant answer", () => {
    const { hook, socket } = openSession();
    emit(socket, {
      type: "context.sources",
      session_id: SESSION_ID,
      turn_id: 2,
      memories: [{ id: 2, content: "喜欢茶", source_message_id: 8, source_text: "我喜欢喝茶", source_turn_id: 3 }],
      knowledge: [{ chunk_id: 7, document_id: 4, display_name: "手册.pdf", content: "八十度水温", page_number: 5 }],
    });
    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 2, delta: "回答" });

    expect(hook.result.current.messages.at(-1)?.sources).toHaveLength(2);
    expect(hook.result.current.messages.at(-1)?.sources?.[1]).toMatchObject({ kind: "knowledge", displayName: "手册.pdf" });
  });
  it("surfaces and dismisses reviewable memory proposals", () => {
    const { hook, socket } = openSession();

    emit(socket, {
      type: "memory.proposed",
      session_id: SESSION_ID,
      turn_id: 4,
      proposal_index: 0,
      source_message_id: 19,
      kind: "preference",
      content: "用户喜欢无糖咖啡",
      importance: 0.8,
      requires_confirmation: false,
    });

    expect(hook.result.current.memoryProposals).toHaveLength(1);
    expect(hook.result.current.memoryProposals[0]).toMatchObject({
      id: "4:0",
      sourceTurnId: 4,
      sourceMessageId: 19,
      content: "用户喜欢无糖咖啡",
    });
    act(() => hook.result.current.dismissMemoryProposal("4:0"));
    expect(hook.result.current.memoryProposals).toEqual([]);
  });

  it("waits for session readiness after the WebSocket opens", () => {
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    act(() => hook.result.current.connect());
    const socket = MockWebSocket.instances[0];
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    act(() => socket.open());
    expect(socket.jsonMessages()).toEqual([{ type: "session.start" }]);
    expect(hook.result.current.connectionStatus).toBe("initializing");
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();

    emit(socket, readyEvent());
    expect(hook.result.current.connectionStatus).toBe("connected");
  });

  it("distinguishes backend close codes for quick reconnection guidance", () => {
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    act(() => hook.result.current.connect());
    const socket = MockWebSocket.instances[0];
    act(() => socket.onclose?.(new CloseEvent("close", { code: 4401 })));
    expect(hook.result.current.connectionStatus).toBe("disconnected");
    expect(hook.result.current.error?.message).toContain("token");

    act(() => hook.result.current.connect());
    const busySocket = MockWebSocket.instances.at(-1)!;
    act(() => busySocket.onclose?.(new CloseEvent("close", { code: 4409 })));
    expect(hook.result.current.error?.message).toContain("会话占用");
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

  it("selects the preferred physical microphone before requesting media", async () => {
    const stop = vi.fn();
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockResolvedValue([
        { deviceId: "virtual", groupId: "virtual-group", label: "ToDesk Virtual Audio", kind: "audioinput", toJSON: () => ({}) },
        { deviceId: "realtek", groupId: "realtek-group", label: "麦克风阵列 (Realtek(R) Audio", kind: "audioinput", toJSON: () => ({}) },
      ]),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream(stop));
    const { hook } = openSession();

    await act(async () => hook.result.current.startMicrophone());

    expect(navigator.mediaDevices.enumerateDevices).toHaveBeenCalledOnce();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: expect.objectContaining({ deviceId: { exact: "realtek" } }),
    }));
  });

  it("falls back to browser default constraints when microphone enumeration fails", async () => {
    const stop = vi.fn();
    Object.assign(navigator.mediaDevices, {
      enumerateDevices: vi.fn().mockRejectedValue(new Error("enumeration unavailable")),
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream(stop));
    const { hook } = openSession();

    await act(async () => hook.result.current.startMicrophone());

    expect(navigator.mediaDevices.enumerateDevices).toHaveBeenCalledOnce();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith(expect.objectContaining({
      audio: expect.not.objectContaining({ deviceId: expect.anything() }),
    }));
  });

  it("commits audio and closes microphone tracks without closing the socket", async () => {
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce(microphoneStream(stop));
    const { hook, socket } = openSession();

    await act(async () => hook.result.current.startMicrophone());
    await act(async () => hook.result.current.stopMicrophone());

    expect(stop).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "audio.commit" });
    expect(socket.readyState).toBe(MockWebSocket.OPEN);
    expect(hook.result.current.isMicrophoneActive).toBe(false);
  });

  it("stops current playback immediately when microphone capture starts", () => {
    const media = deferred<MediaStream>();
    vi.mocked(navigator.mediaDevices.getUserMedia).mockReturnValueOnce(media.promise);
    const stopAll = vi.spyOn(AudioPlayback.prototype, "stopAll");
    const { hook } = openSession();

    act(() => { void hook.result.current.startMicrophone(); });

    expect(stopAll).toHaveBeenCalledOnce();
  });

  it("does not request media when microphone stop wins while enumeration is pending", async () => {
    const enumeration = deferred<MediaDeviceInfo[]>();
    Object.assign(navigator.mediaDevices, { enumerateDevices: vi.fn(() => enumeration.promise) });
    const { hook } = openSession();
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.startMicrophone(); });

    await act(async () => hook.result.current.stopMicrophone());
    enumeration.resolve([]);
    await act(async () => pending);

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
    expect(hook.result.current.isMicrophoneActive).toBe(false);
  });

  it("disconnects before pending microphone enumeration resolves without requesting media", async () => {
    const enumeration = deferred<MediaDeviceInfo[]>();
    Object.assign(navigator.mediaDevices, { enumerateDevices: vi.fn(() => enumeration.promise) });
    const { hook } = openSession();
    act(() => { void hook.result.current.startMicrophone(); });
    let disconnected = false;
    const disconnecting = hook.result.current.disconnect().then(() => { disconnected = true; });

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    try {
      expect(disconnected).toBe(true);
    } finally {
      enumeration.resolve([]);
      await act(async () => disconnecting);
    }

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
  });

  it("does not revive microphone capture after unmount while enumeration is pending", async () => {
    const enumeration = deferred<MediaDeviceInfo[]>();
    Object.assign(navigator.mediaDevices, { enumerateDevices: vi.fn(() => enumeration.promise) });
    const hook = renderHook(() => useVoiceSession({ url: "ws://localhost/v1/voice" }));
    let pending!: Promise<void>;
    act(() => { pending = hook.result.current.startMicrophone(); });

    hook.unmount();
    enumeration.resolve([]);
    await pending;

    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled();
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
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce());
    await act(async () => hook.result.current.disconnect());
    resolveMedia(microphoneStream(stop));
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
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledOnce());
    hook.unmount();
    resolveMedia(microphoneStream(stop));
    await pending;
    expect(stop).toHaveBeenCalledOnce();
  });

  it("cleans a local audio context and worklet URL when microphone setup fails", async () => {
    const stop = vi.fn();
    MockAudioContext.workletFailure = new Error("worklet failed");
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream(stop));
    const capture = new MicrophoneCapture({
      deviceId: null,
      forwardPcm: true,
      onFrame: vi.fn(),
      onLevel: vi.fn(),
      onSettings: vi.fn(),
    });

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
    vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValue([
      { deviceId: "default", groupId: "physical", label: "Default microphone", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
    ]);
    vi.mocked(navigator.mediaDevices.getUserMedia)
      .mockResolvedValueOnce(microphoneStream(oldStop))
      .mockResolvedValueOnce(microphoneStream(newStop));
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
    await waitFor(() => expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2));
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
    vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValue([
      { deviceId: "default", groupId: "physical", label: "Default microphone", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
    ]);
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream(stop));
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

  it("keeps typed replies silent even when legacy settings enabled auto-speak", async () => {
    localStorage.setItem("voxagent.voice-settings.v1", JSON.stringify({
      voiceKey: null,
      speed: 1,
      speakTextReplies: true,
    }));
    const { hook, socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 8, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    const source = MockAudioContext.instances[0].sources[0];

    act(() => hook.result.current.submitText(" hello "));

    expect(source.stop).toHaveBeenCalledOnce();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "hello", speak_response: false });
    expect(hook.result.current.messages.at(-1)).toEqual(expect.objectContaining({ role: "user", origin: "text", text: "hello" }));
    act(() => hook.result.current.submitText("again"));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "text.submit", text: "again", speak_response: false });
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
    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("preparing");
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(hook.result.current.voiceStatus).toBe("speaking");
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("speaking");
    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 1 });
    act(() => MockAudioContext.instances[0].sources[0].onended?.());
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("requests speech for an existing assistant message", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 7, request_id: 1 }));
  });

  it("prepares a turn for replay before requesting its speech", async () => {
    const prepareTurnReplay = vi.spyOn(AudioPlayback.prototype, "prepareTurnReplay");
    const { hook, socket } = openSession();

    act(() => hook.result.current.speakMessage(7));

    expect(prepareTurnReplay).toHaveBeenCalledWith(7);
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 7, request_id: 1 }));
  });

  it("unlocks audio and shows preparing before replay audio arrives", () => {
    const { hook, socket } = openSession();

    act(() => hook.result.current.speakMessage(7));

    expect(MockAudioContext.instances).toHaveLength(1);
    expect(MockAudioContext.instances[0].resume).toHaveBeenCalledOnce();
    expect(hook.result.current.speakingTurnId).toBe(7);
    expect(hook.result.current.voiceStatus).toBe("preparing");
    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7 });
    expect(hook.result.current.voiceStatus).toBe("preparing");
  });

  it("waits for audio unlock before sending replay and does not send after stop", async () => {
    const resumeGate = deferred();
    MockAudioContext.resumeGates = [resumeGate.promise];
    const { hook, socket } = openSession();

    act(() => hook.result.current.speakMessage(7));
    expect(socket.jsonMessages()).not.toContainEqual({ type: "assistant.speak", turn_id: 7 });
    act(() => hook.result.current.stopSpeaking(7));
    resumeGate.resolve();
    await act(async () => resumeGate.promise);

    expect(socket.jsonMessages()).not.toContainEqual({ type: "assistant.speak", turn_id: 7 });
  });

  it("does not reactivate a replay stopped before late tts events arrive", () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    act(() => hook.result.current.stopSpeaking(7));

    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: 1 });
    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 7, request_id: 1 });

    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("rejects late audio from an older replay request of the same turn", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const firstRequest = socket.jsonMessages().at(-1)!.request_id as number;
    act(() => hook.result.current.stopSpeaking(7));
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)?.request_id).not.toBe(firstRequest));
    const secondRequest = socket.jsonMessages().at(-1)!.request_id as number;
    const wav = wavBytes();

    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: firstRequest, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances[0].sources).toHaveLength(0);

    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: secondRequest });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: secondRequest, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances[0].sources).toHaveLength(1);
  });

  it("does not accept automatic request zero audio during a manual replay", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;
    const wav = wavBytes();

    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: 0, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances[0].sources).toHaveLength(0);

    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: requestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances[0].sources).toHaveLength(1);
  });

  it("starts decoded replay audio when matching tts done arrives during decoding", async () => {
    const decoded = deferred<AudioBuffer>();
    MockAudioContext.decoder = () => decoded.promise;
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;
    const wav = wavBytes();

    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: requestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    const receiving = act(async () => socket.receive(wav));
    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 7, request_id: requestId });
    decoded.resolve({ duration: 0.1, sampleRate: 48_000 } as AudioBuffer);
    await receiving;

    expect(MockAudioContext.instances[0].sources[0].start).toHaveBeenCalledOnce();
    expect(MockAudioContext.instances[0].sources[0].stop).not.toHaveBeenCalled();
    expect(hook.result.current.voiceStatus).toBe("speaking");
  });

  it("keeps speaking until playback ends after tts done", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    act(() => hook.result.current.speakMessage(7));
    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: 1 });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(hook.result.current.voiceStatus).toBe("speaking");

    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 7, request_id: 1 });
    expect(hook.result.current.voiceStatus).toBe("speaking");
    act(() => MockAudioContext.instances[0].sources[0].onended?.());

    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("ignores a stale tts done event while a newer replay is preparing", () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    emit(socket, { type: "turn.cancelled", session_id: SESSION_ID, turn_id: 7 });
    act(() => hook.result.current.speakMessage(8));

    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 7 });

    expect(hook.result.current.speakingTurnId).toBe(8);
    expect(hook.result.current.voiceStatus).toBe("preparing");
  });

  it("preserves a newer replay when an old turn is cancelled", () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    act(() => hook.result.current.speakMessage(8));

    emit(socket, { type: "turn.cancelled", session_id: SESSION_ID, turn_id: 7 });

    expect(hook.result.current.speakingTurnId).toBe(8);
    expect(hook.result.current.voiceStatus).toBe("preparing");
  });

  it("does not replace playback status with thinking for a later text delta", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    act(() => hook.result.current.speakMessage(7));
    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: 1 });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: 1, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));

    emit(socket, { type: "assistant.delta", session_id: SESSION_ID, turn_id: 9, delta: "新回答" });

    expect(hook.result.current.voiceStatus).toBe("speaking");
  });

  it("stops queued playback when tts synthesis fails", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;
    emit(socket, { type: "tts.started", session_id: SESSION_ID, turn_id: 7, request_id: requestId });
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 7, request_id: requestId, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    const source = MockAudioContext.instances[0].sources[0];

    emit(socket, { type: "tts.error", session_id: SESSION_ID, turn_id: 7, request_id: requestId, code: "tts_failed", message: "朗读失败", recoverable: true });

    expect(source.stop).toHaveBeenCalledOnce();
    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("leaves preparing state when cleanup finds no speakable text", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.speakMessage(7));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toMatchObject({ type: "assistant.speak", turn_id: 7 }));
    const requestId = socket.jsonMessages().at(-1)!.request_id as number;

    emit(socket, { type: "tts.error", session_id: SESSION_ID, turn_id: 7, request_id: requestId, code: "tts_empty", message: "没有可朗读文字", recoverable: true });

    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("exposes the active spoken turn and stops it locally", async () => {
    const stopTurn = vi.spyOn(AudioPlayback.prototype, "stopTurn");
    const { hook, socket } = openSession();
    const wav = wavBytes();

    act(() => hook.result.current.speakMessage(3));
    await waitFor(() => expect(socket.jsonMessages().at(-1)).toEqual({ type: "assistant.speak", turn_id: 3, request_id: 1 }));
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 3, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(hook.result.current.speakingTurnId).toBe(3);

    act(() => hook.result.current.stopSpeaking(3));
    expect(stopTurn).toHaveBeenCalledWith(3);
    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("exposes and stops the active voice preview without playing a late result", async () => {
    const stopPreview = vi.spyOn(AudioPlayback.prototype, "stopPreview");
    const { hook, socket } = openSession();

    act(() => hook.result.current.previewVoice("default_voice", 1));
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "voice.preview", voice_key: "default_voice", speed: 1 });
    expect(hook.result.current.previewingVoiceKey).toBe("default_voice");

    act(() => hook.result.current.stopVoicePreview());
    expect(stopPreview).toHaveBeenCalled();
    expect(socket.jsonMessages().at(-1)).toEqual({ type: "voice.preview.cancel" });
    expect(hook.result.current.previewingVoiceKey).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");

    const wav = wavBytes();
    emit(socket, { type: "voice.preview.chunk", preview_id: 1, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(MockAudioContext.instances.flatMap((context) => context.sources)).toHaveLength(0);
  });

  it("owns browser previews and ignores them completely in local-only mode", async () => {
    const browserVoice = { key: "zh-preview", name: "Chinese", lang: "zh-CN", localService: true };
    vi.spyOn(BrowserSpeechProvider.prototype, "voices").mockReturnValue([browserVoice]);
    const preview = deferred();
    const speak = vi.spyOn(BrowserSpeechProvider.prototype, "speak").mockReturnValue(preview.promise);
    const cancelSpeech = vi.spyOn(BrowserSpeechProvider.prototype, "cancelSpeech");
    const { hook, socket } = openSession();
    act(() => hook.result.current.selectBrowserVoice(browserVoice.key));

    act(() => hook.result.current.previewVoice(browserVoice.key, 1, "browser"));
    expect(speak).toHaveBeenCalledWith("你好，我是声灵，很高兴认识你。", browserVoice.key, 1, expect.anything());
    expect(socket.jsonMessages().filter((event) => event.type === "voice.preview")).toEqual([]);
    act(() => hook.result.current.stopVoicePreview());
    expect(cancelSpeech).toHaveBeenCalledWith(expect.anything());
    expect(socket.jsonMessages().filter((event) => event.type === "turn.cancel")).toEqual([]);

    act(() => hook.result.current.setSpeechMode("local-only"));
    speak.mockClear();
    act(() => hook.result.current.previewVoice(browserVoice.key, 1, "browser"));
    expect(speak).not.toHaveBeenCalled();
  });

  it("clears spoken state when browser playback ends naturally", async () => {
    const { hook, socket } = openSession();
    const wav = wavBytes();
    emit(socket, { type: "tts.chunk", session_id: SESSION_ID, turn_id: 3, sequence: 0, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));
    expect(hook.result.current.speakingTurnId).toBe(3);
    emit(socket, { type: "assistant.done", session_id: SESSION_ID, turn_id: 3 });
    emit(socket, { type: "tts.done", session_id: SESSION_ID, turn_id: 3 });

    act(() => MockAudioContext.instances[0].sources[0].onended?.());

    expect(hook.result.current.speakingTurnId).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
  });

  it("does not restore speaking state when cancelled decoding finishes late", async () => {
    const decoded = deferred<AudioBuffer>();
    MockAudioContext.decoder = () => decoded.promise;
    const { hook, socket } = openSession();
    act(() => hook.result.current.previewVoice("default_voice", 1));
    const wav = wavBytes();
    emit(socket, { type: "voice.preview.chunk", preview_id: 1, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    const receiving = act(async () => socket.receive(wav));

    act(() => hook.result.current.stopVoicePreview());
    decoded.resolve({ duration: 0.1, sampleRate: 48_000 } as AudioBuffer);
    await receiving;

    expect(hook.result.current.previewingVoiceKey).toBeNull();
    expect(hook.result.current.voiceStatus).toBe("idle");
    expect(MockAudioContext.instances[0].sources).toHaveLength(0);
  });

  it("can preview again after a stopped preview without predicting server ids", async () => {
    const { hook, socket } = openSession();
    act(() => hook.result.current.previewVoice("default_voice", 1));
    act(() => hook.result.current.stopVoicePreview());
    act(() => hook.result.current.previewVoice("default_voice", 1));
    const wav = wavBytes();
    emit(socket, { type: "voice.preview.chunk", preview_id: 2, sample_rate: 24_000, mime_type: "audio/wav", byte_length: wav.byteLength });
    await act(async () => socket.receive(wav));

    expect(MockAudioContext.instances[0].sources[0].start).toHaveBeenCalledOnce();
    expect(hook.result.current.previewingVoiceKey).toBe("default_voice");
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

  it("ignores old engine callbacks after a WebSocket reconnect", async () => {
    const recognitionRuns: BrowserSpeechCallbacks[] = [];
    vi.spyOn(BrowserSpeechProvider.prototype, "start").mockImplementation(async (_track, callbacks) => {
      recognitionRuns.push(callbacks);
    });
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
    const { hook, socket: oldSocket } = openSession();
    await act(async () => hook.result.current.startRealtimeCall());
    const oldCallbacks = recognitionRuns[0];

    await act(async () => hook.result.current.disconnect());
    act(() => hook.result.current.connect());
    const newSocket = MockWebSocket.instances.at(-1)!;
    act(() => newSocket.open());
    emit(newSocket, readyEvent());
    await act(async () => hook.result.current.startRealtimeCall());
    const currentMessages = newSocket.jsonMessages().length;

    act(() => {
      oldCallbacks.onInterim("旧会话临时字幕");
      oldCallbacks.onFinal("旧会话最终转写");
      oldCallbacks.onError({ code: "network", recoverable: true });
      oldCallbacks.onRecognitionEnd();
      oldSocket.receive(JSON.stringify({
        type: "asr.final",
        session_id: SESSION_ID,
        turn_id: 1,
        request_id: 1,
        text: "旧会话最终转写",
      }));
    });

    expect(newSocket.jsonMessages()).toHaveLength(currentMessages);
    expect(hook.result.current.messages).toEqual([]);
    expect(hook.result.current.realtime).toMatchObject({
      active: true,
      provider: "browser",
      state: "listening",
      interimText: "",
      fallbackReason: null,
    });
  });

  it("tears down microphone and playback on an unexpected socket close", async () => {
    const stop = vi.fn();
    vi.mocked(navigator.mediaDevices.enumerateDevices).mockResolvedValue([
      { deviceId: "default", groupId: "physical", label: "Default microphone", kind: "audioinput", toJSON: () => ({}) } as MediaDeviceInfo,
    ]);
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream(stop));
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
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValue(microphoneStream());
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
    vi.mocked(navigator.mediaDevices.getUserMedia).mockResolvedValueOnce(microphoneStream(stop));
    const { hook, socket } = openSession();
    await act(async () => hook.result.current.startMicrophone());
    act(() => hook.result.current.selectVoice("clear_female", 0.8));

    await act(async () => hook.result.current.disconnect());

    expect(JSON.parse(localStorage.getItem("voxagent.voice-settings.v2")!)).toEqual({
      voiceKey: "clear_female",
      speed: 0.8,
      speechMode: "online-preferred",
      microphoneDeviceId: null,
      microphoneLabel: null,
      browserVoiceKey: null,
      onlineSpeechNoticeAccepted: false,
    });
    expect(stop).toHaveBeenCalledOnce();
    expect(socket.readyState).toBe(MockWebSocket.CLOSED);
    await waitFor(() => expect(hook.result.current.connectionStatus).toBe("disconnected"));
  });
});
