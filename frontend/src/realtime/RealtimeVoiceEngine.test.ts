import { afterEach, describe, expect, it, vi } from "vitest";

import type { MicrophoneCapture } from "../audio/capture";
import type {
  BrowserSpeechCallbacks,
  BrowserSpeechFailure,
  BrowserSpeechProvider,
} from "../audio/webSpeech";
import { StreamingSentenceQueue } from "./sentenceQueue";
import {
  RealtimeVoiceEngine,
  type RealtimeSnapshot,
  type StartRealtimeOptions,
} from "./RealtimeVoiceEngine";

const SESSION_ID = "123e4567-e89b-12d3-a456-426614174000";
const OTHER_SESSION_ID = "123e4567-e89b-12d3-a456-426614174001";

const ONLINE_OPTIONS: StartRealtimeOptions = {
  mode: "online-preferred",
  deviceId: "realtek",
  browserVoiceKey: "zh-voice",
  speechRate: 1,
  allowDefaultInputFallback: false,
};

class FakeCapture {
  readonly track = { readyState: "live" } as MediaStreamTrack;
  readonly start = vi.fn(async (_signal?: AbortSignal) => this.track);
  readonly setForwardPcm = vi.fn();
  readonly stop = vi.fn(async (): Promise<void> => undefined);
}

class FakeBrowserSpeech {
  readonly runs: BrowserSpeechCallbacks[] = [];
  readonly spoken: string[] = [];
  readonly start = vi.fn(async (
    _track: MediaStreamTrack,
    callbacks: BrowserSpeechCallbacks,
    _allowDefaultInputFallback: boolean,
  ) => {
    this.runs.push(callbacks);
  });
  readonly stopRecognition = vi.fn();
  readonly speak = vi.fn(async (text: string, _voiceKey: string | null, _rate: number) => {
    this.spoken.push(text);
  });
  readonly cancelSpeech = vi.fn();
  readonly close = vi.fn();
}

function makeHarness() {
  const capture = new FakeCapture();
  const browserSpeech = new FakeBrowserSpeech();
  const sentenceQueue = new StreamingSentenceQueue();
  const sentJson: Array<Record<string, unknown>> = [];
  const states: RealtimeSnapshot[] = [];
  const terminalErrors: BrowserSpeechFailure[] = [];
  const cancelLocalPlayback = vi.fn();
  const queueCancel = vi.spyOn(sentenceQueue, "cancel");
  const engine = new RealtimeVoiceEngine({
    capture: capture as unknown as MicrophoneCapture,
    browserSpeech: browserSpeech as unknown as BrowserSpeechProvider,
    sentenceQueue,
    sendJson: (event) => sentJson.push(event),
    onSnapshot: (snapshot) => states.push(snapshot),
    onTerminalError: (error) => terminalErrors.push(error),
    cancelLocalPlayback,
  });
  return {
    browserSpeech,
    cancelLocalPlayback,
    capture,
    engine,
    queueCancel,
    sentenceQueue,
    sentJson,
    states,
    terminalErrors,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("RealtimeVoiceEngine", () => {
  it("runs the browser recognition happy path and speaks matching response sentences in order", async () => {
    const { browserSpeech, engine, sentJson, states } = makeHarness();

    await engine.start(ONLINE_OPTIONS);
    expect(states).toContainEqual(expect.objectContaining({ state: "listening", provider: "browser" }));
    const recognizer = browserSpeech.runs[0];

    recognizer.onInterim("你好");
    expect(states.at(-1)?.interimText).toBe("你好");
    recognizer.onFinal("  你好声灵  ");
    expect(sentJson.at(-1)).toEqual({ type: "voice.transcript.submit", text: "你好声灵" });
    expect(states.at(-1)?.state).toBe("thinking");

    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 4, text: "你好声灵" });
    engine.handleServerEvent({
      type: "assistant.delta",
      session_id: SESSION_ID,
      turn_id: 4,
      delta: "你好！今天想聊什么？",
    });

    await vi.waitFor(() => expect(browserSpeech.spoken).toEqual(["你好！", "今天想聊什么？"]));
    expect(browserSpeech.speak).toHaveBeenNthCalledWith(1, "你好！", "zh-voice", 1);
    expect(browserSpeech.speak).toHaveBeenNthCalledWith(2, "今天想聊什么？", "zh-voice", 1);
  });

  it("interrupts only from speech-start and clears the notice after 1,200 ms", async () => {
    vi.useFakeTimers();
    const { browserSpeech, cancelLocalPlayback, engine, queueCancel, sentJson, states } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    const recognizer = browserSpeech.runs[0];
    recognizer.onFinal("开始回答");

    recognizer.onSpeechStart();

    expect(browserSpeech.cancelSpeech).toHaveBeenCalledOnce();
    expect(cancelLocalPlayback).toHaveBeenCalledOnce();
    expect(queueCancel).toHaveBeenCalledOnce();
    expect(sentJson).toContainEqual({ type: "turn.cancel" });
    expect(states.at(-1)).toMatchObject({ state: "user_speaking", notice: "interrupted" });

    await vi.advanceTimersByTimeAsync(1_199);
    expect(states.at(-1)?.notice).toBe("interrupted");
    await vi.advanceTimersByTimeAsync(1);
    expect(states.at(-1)?.notice).toBeNull();
  });

  it("falls back once for a stable browser network error code", async () => {
    const { browserSpeech, cancelLocalPlayback, capture, engine, states } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    const recognizer = browserSpeech.runs[0];

    recognizer.onError({ code: "network", recoverable: true });
    recognizer.onError({ code: "network", recoverable: true });

    expect(states).toContainEqual(expect.objectContaining({
      state: "listening",
      provider: "local",
      fallbackReason: "network",
    }));
    expect(capture.setForwardPcm).toHaveBeenCalledWith(true);
    expect(capture.setForwardPcm.mock.calls.filter(([enabled]) => enabled)).toHaveLength(1);
    expect(browserSpeech.stopRecognition).toHaveBeenCalledOnce();
    expect(cancelLocalPlayback).toHaveBeenCalledOnce();
  });

  it("treats microphone permission denial and browser not-allowed as terminal", async () => {
    const denied = makeHarness();
    denied.capture.start.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    await denied.engine.start(ONLINE_OPTIONS);
    expect(denied.terminalErrors).toEqual([
      expect.objectContaining({ code: "recognition_error", recoverable: false }),
    ]);
    expect(denied.states.at(-1)).toMatchObject({ active: false, state: "off", provider: null });

    const disallowed = makeHarness();
    await disallowed.engine.start(ONLINE_OPTIONS);
    disallowed.browserSpeech.runs[0].onError({ code: "not-allowed", recoverable: false });
    expect(disallowed.terminalErrors).toEqual([
      expect.objectContaining({ code: "not-allowed", recoverable: false }),
    ]);
    expect(disallowed.states.at(-1)).toMatchObject({ active: false, state: "off", provider: null });
  });

  it("waits for terminal capture cleanup before a later start touches the shared capture", async () => {
    const { capture, engine } = makeHarness();
    let releaseStop!: () => void;
    capture.start.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
    capture.stop.mockImplementationOnce(() => new Promise<void>((resolve) => {
      releaseStop = resolve;
    }));
    await engine.start(ONLINE_OPTIONS);

    const nextStart = engine.start({ ...ONLINE_OPTIONS, mode: "local-only" });
    await Promise.resolve();
    expect(capture.start).toHaveBeenCalledTimes(1);

    releaseStop();
    await nextStart;
    expect(capture.start).toHaveBeenCalledTimes(2);
  });

  it("uses local-only without starting browser recognition", async () => {
    const { browserSpeech, capture, engine, states } = makeHarness();

    await engine.start({ ...ONLINE_OPTIONS, mode: "local-only" });

    expect(browserSpeech.start).not.toHaveBeenCalled();
    expect(capture.setForwardPcm).toHaveBeenCalledWith(true);
    expect(states.at(-1)).toMatchObject({ active: true, state: "listening", provider: "local" });
  });

  it("restarts a naturally ended browser run after 150 ms", async () => {
    vi.useFakeTimers();
    const { browserSpeech, engine } = makeHarness();
    await engine.start(ONLINE_OPTIONS);

    browserSpeech.runs[0].onRecognitionEnd();
    await vi.advanceTimersByTimeAsync(149);
    expect(browserSpeech.start).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(browserSpeech.start).toHaveBeenCalledTimes(2);
  });

  it("resets empty runs after a final and switches only once after two consecutive empty runs", async () => {
    vi.useFakeTimers();
    const { browserSpeech, capture, engine, states } = makeHarness();
    await engine.start(ONLINE_OPTIONS);

    browserSpeech.runs[0].onRecognitionEnd();
    await vi.advanceTimersByTimeAsync(150);
    browserSpeech.runs[1].onFinal("有效结果");
    browserSpeech.runs[1].onRecognitionEnd();
    await vi.advanceTimersByTimeAsync(150);

    browserSpeech.runs[2].onRecognitionEnd();
    await vi.advanceTimersByTimeAsync(150);
    expect(browserSpeech.start).toHaveBeenCalledTimes(4);
    expect(states.at(-1)?.provider).toBe("browser");

    browserSpeech.runs[3].onRecognitionEnd();
    expect(states.at(-1)).toMatchObject({ provider: "local", fallbackReason: "empty-recognition" });
    browserSpeech.runs[3].onRecognitionEnd();
    expect(capture.setForwardPcm.mock.calls.filter(([enabled]) => enabled)).toHaveLength(1);
  });

  it("binds local turns at VAD start and ignores deltas from every other turn identity", async () => {
    const { browserSpeech, cancelLocalPlayback, engine, states } = makeHarness();
    await engine.start({ ...ONLINE_OPTIONS, mode: "local-only" });

    engine.handleServerEvent({ type: "vad.started", session_id: SESSION_ID, turn_id: 8 });
    expect(states.at(-1)?.state).toBe("user_speaking");
    engine.handleServerEvent({ type: "asr.partial", session_id: SESSION_ID, turn_id: 8, text: "本地" });
    expect(states.at(-1)?.interimText).toBe("本地");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 8, text: "本地输入" });

    engine.handleServerEvent({ type: "assistant.delta", session_id: OTHER_SESSION_ID, turn_id: 8, delta: "错会话。" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 9, delta: "错轮次。" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 8, delta: "正确。" });

    expect(browserSpeech.speak).not.toHaveBeenCalled();
    expect(states.at(-1)?.state).toBe("responding");
    expect(cancelLocalPlayback).not.toHaveBeenCalled();
  });

  it("does not let turn cancellation or an old generation rebind a late browser echo", async () => {
    const { browserSpeech, engine, sentJson, states } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    const oldRun = browserSpeech.runs[0];
    oldRun.onFinal("旧请求");
    engine.handleServerEvent({ type: "turn.cancelled", session_id: SESSION_ID, turn_id: 1 });
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 1, text: "旧请求" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 1, delta: "不应朗读。" });
    expect(browserSpeech.speak).not.toHaveBeenCalled();

    await engine.stop();
    await engine.start(ONLINE_OPTIONS);
    const sendsBeforeOldCallbacks = sentJson.length;
    oldRun.onFinal("更旧请求");
    oldRun.onError({ code: "network", recoverable: true });
    expect(sentJson).toHaveLength(sendsBeforeOldCallbacks);
    expect(states.at(-1)?.provider).toBe("browser");
  });

  it("does not let an old cancellation acknowledgement erase a newer browser echo", async () => {
    const { browserSpeech, engine } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    const recognizer = browserSpeech.runs[0];
    recognizer.onFinal("旧问题");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 10, text: "旧问题" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 10, delta: "旧回答未完成" });

    recognizer.onSpeechStart();
    recognizer.onFinal("新问题");
    engine.handleServerEvent({ type: "turn.cancelled", session_id: SESSION_ID, turn_id: 10 });
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 10, text: "迟到旧问题" });
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 11, text: "新问题" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 11, delta: "新回答。" });

    await vi.waitFor(() => expect(browserSpeech.spoken).toEqual(["新回答。"]));
  });

  it("lets a new generation speak even when an old synthesis promise never settles", async () => {
    const { browserSpeech, engine } = makeHarness();
    browserSpeech.speak.mockImplementationOnce((text: string) => {
      browserSpeech.spoken.push(text);
      return new Promise<void>(() => undefined);
    });
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[0].onFinal("旧问题");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 20, text: "旧问题" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 20, delta: "旧回答。" });
    expect(browserSpeech.speak).toHaveBeenCalledTimes(1);

    await engine.stop();
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[1].onFinal("新问题");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 21, text: "新问题" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 21, delta: "新回答。" });

    await vi.waitFor(() => expect(browserSpeech.speak).toHaveBeenCalledTimes(2));
    expect(browserSpeech.spoken).toHaveLength(2);
    expect(browserSpeech.spoken[0]).toBe("旧回答。");
    expect(browserSpeech.spoken[1]).toBe("新回答。");
  });

  it("accepts the next echo in a new call after stopping an unbound browser turn", async () => {
    const { browserSpeech, engine } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[0].onFinal("已停止问题");
    await engine.stop();

    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[1].onFinal("新问题");
    engine.handleServerEvent({ type: "asr.final", session_id: OTHER_SESSION_ID, turn_id: 1, text: "新问题" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: OTHER_SESSION_ID, turn_id: 1, delta: "新回答。" });

    await vi.waitFor(() => expect(browserSpeech.spoken).toEqual(["新回答。"]));
  });

  it("ignores completed browser turns and stale local VAD identities", async () => {
    const browser = makeHarness();
    await browser.engine.start(ONLINE_OPTIONS);
    const recognizer = browser.browserSpeech.runs[0];
    recognizer.onFinal("第一问");
    browser.engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 30, text: "第一问" });
    browser.engine.handleServerEvent({ type: "assistant.done", session_id: SESSION_ID, turn_id: 30 });
    recognizer.onFinal("第二问");
    browser.engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 30, text: "迟到第一问" });
    browser.engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 31, text: "第二问" });
    browser.engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 31, delta: "第二答。" });
    await vi.waitFor(() => expect(browser.browserSpeech.spoken).toEqual(["第二答。"]));

    const local = makeHarness();
    await local.engine.start({ ...ONLINE_OPTIONS, mode: "local-only" });
    local.engine.handleServerEvent({ type: "vad.started", session_id: SESSION_ID, turn_id: 40 });
    local.engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 40, text: "当前问题" });
    local.engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 40, delta: "当前回答" });
    local.engine.handleServerEvent({ type: "vad.started", session_id: SESSION_ID, turn_id: 39 });
    expect(local.sentJson).not.toContainEqual({ type: "turn.cancel" });
    expect(local.states.at(-1)?.state).toBe("responding");
  });

  it("falls back after browser synthesis fails, without retrying spoken sentences, and requests local speech once after done", async () => {
    const { browserSpeech, engine, queueCancel, sentJson, states } = makeHarness();
    let rejectSecond!: (reason: unknown) => void;
    browserSpeech.speak.mockImplementation((text: string) => {
      browserSpeech.spoken.push(text);
      if (text === "第二句。") {
        return new Promise<void>((_resolve, reject) => {
          rejectSecond = reject;
        });
      }
      return Promise.resolve();
    });
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[0].onFinal("请回答");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 5, text: "请回答" });
    engine.handleServerEvent({
      type: "assistant.delta",
      session_id: SESSION_ID,
      turn_id: 5,
      delta: "第一句。第二句。第三句。",
    });
    await vi.waitFor(() => expect(browserSpeech.speak).toHaveBeenCalledTimes(2));

    engine.handleServerEvent({ type: "assistant.done", session_id: SESSION_ID, turn_id: 5 });
    rejectSecond(new Error("synthesis failed"));

    await vi.waitFor(() => expect(sentJson).toContainEqual({
      type: "assistant.speak",
      turn_id: 5,
      request_id: 1,
    }));
    engine.handleServerEvent({ type: "assistant.done", session_id: SESSION_ID, turn_id: 5 });
    expect(sentJson.filter((event) => event.type === "assistant.speak")).toHaveLength(1);
    expect(browserSpeech.spoken).toHaveLength(2);
    expect(browserSpeech.spoken[0]).toBe("第一句。");
    expect(browserSpeech.spoken[1]).toBe("第二句。");
    expect(queueCancel).toHaveBeenCalledOnce();
    expect(states.at(-1)).toMatchObject({ provider: "local", state: "speaking" });
  });

  it("ignores non-matching local TTS request events", async () => {
    const { browserSpeech, engine, states } = makeHarness();
    browserSpeech.speak.mockRejectedValueOnce(new Error("synthesis failed"));
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[0].onFinal("请回答");
    engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 6, text: "请回答" });
    engine.handleServerEvent({ type: "assistant.delta", session_id: SESSION_ID, turn_id: 6, delta: "失败。" });
    await vi.waitFor(() => expect(states.at(-1)?.provider).toBe("local"));
    engine.handleServerEvent({ type: "assistant.done", session_id: SESSION_ID, turn_id: 6 });
    await vi.waitFor(() => expect(states.at(-1)?.state).toBe("speaking"));

    engine.handleServerEvent({ type: "tts.done", session_id: SESSION_ID, turn_id: 6, request_id: 2 });
    engine.handleServerEvent({ type: "tts.done", session_id: OTHER_SESSION_ID, turn_id: 6, request_id: 1 });
    expect(states.at(-1)?.state).toBe("speaking");
    engine.handleServerEvent({ type: "tts.done", session_id: SESSION_ID, turn_id: 6, request_id: 1 });
    expect(states.at(-1)?.state).toBe("listening");
  });

  it("stops recognition, both playback paths, capture, queue, and a pending turn exactly once", async () => {
    const {
      browserSpeech,
      cancelLocalPlayback,
      capture,
      engine,
      queueCancel,
      sentJson,
      states,
    } = makeHarness();
    await engine.start(ONLINE_OPTIONS);
    browserSpeech.runs[0].onFinal("待取消");

    await Promise.all([engine.stop(), engine.stop()]);

    expect(browserSpeech.stopRecognition).toHaveBeenCalledOnce();
    expect(browserSpeech.cancelSpeech).toHaveBeenCalledOnce();
    expect(cancelLocalPlayback).toHaveBeenCalledOnce();
    expect(capture.stop).toHaveBeenCalledOnce();
    expect(queueCancel).toHaveBeenCalledOnce();
    expect(sentJson.filter((event) => event.type === "turn.cancel")).toHaveLength(1);
    expect(states.at(-1)).toMatchObject({ active: false, state: "off", provider: null });
  });
});
