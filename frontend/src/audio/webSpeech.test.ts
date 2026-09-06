import { describe, expect, it, vi } from "vitest";

import { BrowserSpeechProvider } from "./webSpeech";

type FakeRecognitionResult = {
  0: { transcript: string };
  isFinal: boolean;
  length: 1;
};

type FakeRecognitionResults = { length: number; [index: number]: FakeRecognitionResult };
type RecognitionHandler = ((event: { resultIndex: number; results: FakeRecognitionResults }) => void) | null;

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  onresult: RecognitionHandler = null;
  onspeechstart: (() => void) | null = null;
  onspeechend: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((event: { error: string; message?: string }) => void) | null = null;
  readonly start = vi.fn<(track?: MediaStreamTrack) => void>();
  readonly stop = vi.fn();

  emitInterim(text: string): void {
    this.emitResult(0, false, text);
  }

  emitFinal(text: string): void {
    this.emitResult(0, true, text);
  }

  emitResult(index: number, isFinal: boolean, text: string): void {
    this.onresult?.({
      resultIndex: index,
      results: { [index]: { 0: { transcript: text }, isFinal, length: 1 }, length: index + 1 },
    });
  }
}

function callbacks() {
  return {
    onInterim: vi.fn(),
    onFinal: vi.fn(),
    onSpeechStart: vi.fn(),
    onSpeechEnd: vi.fn(),
    onRecognitionEnd: vi.fn(),
    onError: vi.fn(),
  };
}

class FakeUtterance {
  lang = "";
  rate = 1;
  voice: SpeechSynthesisVoice | null = null;
  onend: (() => void) | null = null;
  onerror: ((event: SpeechSynthesisErrorEvent) => void) | null = null;

  constructor(public readonly text: string) {}
}

function fakeWindow(recognition: FakeRecognition, voices: SpeechSynthesisVoice[] = []) {
  const utterances: FakeUtterance[] = [];
  let currentVoices = voices;
  const voiceChangeListeners = new Set<EventListenerOrEventListenerObject>();
  const synthesis = {
    getVoices: () => currentVoices,
    speak: vi.fn(),
    cancel: vi.fn(),
    addEventListener: vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "voiceschanged") voiceChangeListeners.add(listener);
    }),
    removeEventListener: vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "voiceschanged") voiceChangeListeners.delete(listener);
    }),
  };
  const scope = {
    SpeechRecognition: class { constructor() { return recognition; } },
    speechSynthesis: synthesis,
    SpeechSynthesisUtterance: class {
      constructor(text: string) {
        const utterance = new FakeUtterance(text);
        utterances.push(utterance);
        return utterance;
      }
    },
  } as unknown as Window;
  return {
    scope,
    synthesis,
    utterances,
    setVoices(nextVoices: SpeechSynthesisVoice[]) {
      currentVoices = nextVoices;
    },
    emitVoicesChanged() {
      const event = new Event("voiceschanged");
      for (const listener of voiceChangeListeners) {
        if (typeof listener === "function") listener(event);
        else listener.handleEvent(event);
      }
    },
  };
}

describe("BrowserSpeechProvider", () => {
  it("starts recognition with the selected track and forwards interim and final text", async () => {
    const recognition = new FakeRecognition();
    const { scope } = fakeWindow(recognition);
    const provider = new BrowserSpeechProvider(scope);
    const selectedTrack = {} as MediaStreamTrack;
    const handlers = callbacks();

    expect(BrowserSpeechProvider.isSupported(scope)).toBe(true);
    await provider.start(selectedTrack, handlers, false);
    expect(recognition.start).toHaveBeenCalledWith(selectedTrack);

    recognition.emitInterim("今天天气");
    expect(handlers.onInterim).toHaveBeenCalledWith("今天天气");
    recognition.emitFinal("今天天气怎么样");
    expect(handlers.onFinal).toHaveBeenCalledWith("今天天气怎么样");
  });

  it("trims recognition transcripts before forwarding them", async () => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.emitInterim("  今天天气  ");
    recognition.emitFinal("\n今天天气怎么样\t");

    expect(handlers.onInterim).toHaveBeenCalledWith("今天天气");
    expect(handlers.onFinal).toHaveBeenCalledWith("今天天气怎么样");
  });

  it("does not forward whitespace-only recognition transcripts", async () => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.emitInterim(" \n\t ");
    recognition.emitFinal("  \n ");

    expect(handlers.onInterim).not.toHaveBeenCalled();
    expect(handlers.onFinal).not.toHaveBeenCalled();
  });

  it("forwards each final recognition result index only once", async () => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.emitResult(3, true, "重复结果");
    recognition.emitResult(3, true, "重复结果");

    expect(handlers.onFinal).toHaveBeenCalledTimes(1);
    expect(handlers.onFinal).toHaveBeenCalledWith("重复结果");
  });

  it("allows matching final text from distinct recognition result indexes", async () => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.emitResult(0, true, "可以重复");
    recognition.emitResult(1, true, "可以重复");

    expect(handlers.onFinal).toHaveBeenNthCalledWith(1, "可以重复");
    expect(handlers.onFinal).toHaveBeenNthCalledWith(2, "可以重复");
  });

  it("does not use an unknown default microphone after a selected-track TypeError", async () => {
    const recognition = new FakeRecognition();
    recognition.start.mockImplementation(() => { throw new TypeError("track unsupported"); });
    const provider = new BrowserSpeechProvider(fakeWindow(recognition).scope);
    const handlers = callbacks();

    await provider.start({} as MediaStreamTrack, handlers, false);

    expect(recognition.start).toHaveBeenCalledTimes(1);
    expect(handlers.onError).toHaveBeenCalledWith({ code: "track_not_supported", recoverable: true });
  });

  it("uses exactly one parameterless fallback only when the selected input matches the browser default", async () => {
    const recognition = new FakeRecognition();
    recognition.start.mockImplementationOnce(() => { throw new TypeError("track unsupported"); });
    const provider = new BrowserSpeechProvider(fakeWindow(recognition).scope);

    await provider.start({} as MediaStreamTrack, callbacks(), true);

    expect(recognition.start).toHaveBeenNthCalledWith(1, expect.anything());
    expect(recognition.start).toHaveBeenNthCalledWith(2);
    expect(recognition.start).toHaveBeenCalledTimes(2);
  });

  it("reports selected-track support failure for every unsuccessful recognition start", async () => {
    const nonTypeErrorRecognition = new FakeRecognition();
    nonTypeErrorRecognition.start.mockImplementation(() => { throw new Error("start failed"); });
    const nonTypeErrorCallbacks = callbacks();

    await new BrowserSpeechProvider(fakeWindow(nonTypeErrorRecognition).scope).start({} as MediaStreamTrack, nonTypeErrorCallbacks, false);
    expect(nonTypeErrorCallbacks.onError).toHaveBeenCalledWith({ code: "track_not_supported", recoverable: true });

    const fallbackRecognition = new FakeRecognition();
    fallbackRecognition.start.mockImplementationOnce(() => { throw new TypeError("track unsupported"); });
    fallbackRecognition.start.mockImplementationOnce(() => { throw new Error("fallback failed"); });
    const fallbackCallbacks = callbacks();

    await new BrowserSpeechProvider(fakeWindow(fallbackRecognition).scope).start({} as MediaStreamTrack, fallbackCallbacks, true);
    expect(fallbackCallbacks.onError).toHaveBeenCalledWith({ code: "track_not_supported", recoverable: true });
  });

  it.each([
    ["NotAllowedError", "not-allowed"],
    ["SecurityError", "not-allowed"],
    ["NotFoundError", "device-not-found"],
    ["DevicesNotFoundError", "device-not-found"],
  ] as const)("maps synchronous %s startup failures to terminal %s", async (name, code) => {
    const recognition = new FakeRecognition();
    recognition.start.mockImplementation(() => {
      throw new DOMException("startup failed", name);
    });
    const handlers = callbacks();

    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start(
      {} as MediaStreamTrack,
      handlers,
      false,
    );

    expect(handlers.onError).toHaveBeenCalledWith({
      code,
      recoverable: false,
      message: "startup failed",
    });
  });

  it.each([
    ["NotAllowedError", "not-allowed"],
    ["NotFoundError", "device-not-found"],
  ] as const)("maps parameterless retry %s failures to terminal %s", async (name, code) => {
    const recognition = new FakeRecognition();
    recognition.start.mockImplementationOnce(() => {
      throw new TypeError("selected track unsupported");
    });
    recognition.start.mockImplementationOnce(() => {
      throw new DOMException("fallback failed", name);
    });
    const handlers = callbacks();

    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start(
      {} as MediaStreamTrack,
      handlers,
      true,
    );

    expect(handlers.onError).toHaveBeenCalledWith({
      code,
      recoverable: false,
      message: "fallback failed",
    });
  });

  it("exposes only Chinese voices", async () => {
    const recognition = new FakeRecognition();
    const voices = [
      { voiceURI: "zh-cn", name: "Microsoft Xiaoxiao", lang: "zh-CN", localService: true },
      { voiceURI: "zh-tw", name: "Traditional", lang: "zh-TW", localService: false },
      { voiceURI: "en", name: "English", lang: "en-US", localService: true },
    ] as SpeechSynthesisVoice[];
    const { scope, synthesis, utterances } = fakeWindow(recognition, voices);
    const provider = new BrowserSpeechProvider(scope);

    expect(provider.voices().map((voice) => voice.lang)).toEqual(["zh-CN", "zh-TW"]);

    const spoken = provider.speak("你好", "zh-tw", 1.2);
    expect(synthesis.speak).toHaveBeenCalledWith(utterances[0]);
    expect(utterances[0]).toMatchObject({ text: "你好", lang: "zh-CN", rate: 1.2, voice: voices[1] });
    utterances[0].onend?.();
    await expect(spoken).resolves.toBeUndefined();
  });

  it("subscribes to delayed browser voices and removes the listener", () => {
    const recognition = new FakeRecognition();
    const browser = fakeWindow(recognition);
    const provider = new BrowserSpeechProvider(browser.scope);
    const onVoicesChanged = vi.fn();
    const unsubscribe = provider.subscribeVoices(onVoicesChanged);
    const chineseVoice = {
      voiceURI: "zh-xiaoxiao",
      name: "Microsoft Xiaoxiao",
      lang: "zh-CN",
      localService: false,
    } as SpeechSynthesisVoice;

    browser.setVoices([chineseVoice]);
    browser.emitVoicesChanged();
    expect(onVoicesChanged).toHaveBeenCalledWith([
      { key: "zh-xiaoxiao", name: "Microsoft Xiaoxiao", lang: "zh-CN", localService: false },
    ]);

    unsubscribe();
    browser.emitVoicesChanged();
    expect(onVoicesChanged).toHaveBeenCalledTimes(1);
    expect(browser.synthesis.removeEventListener).toHaveBeenCalledWith("voiceschanged", expect.any(Function));
  });

  it("keeps speech-end and natural recognition-end callbacks distinct", async () => {
    const recognition = new FakeRecognition();
    const provider = new BrowserSpeechProvider(fakeWindow(recognition).scope);
    const handlers = callbacks();

    await provider.start({} as MediaStreamTrack, handlers, false);
    expect(recognition).toMatchObject({ lang: "zh-CN", continuous: true, interimResults: true, maxAlternatives: 1 });
    recognition.onspeechend?.();
    recognition.onend?.();

    expect(handlers.onSpeechEnd).toHaveBeenCalledTimes(1);
    expect(handlers.onRecognitionEnd).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["network", true],
    ["language-not-supported", true],
    ["service-not-allowed", false],
    ["not-allowed", false],
  ] as const)("preserves the stable recognition error code %s", async (code, recoverable) => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.onerror?.({ error: code, message: "browser detail" } as SpeechRecognitionErrorEvent);

    expect(handlers.onError).toHaveBeenCalledWith({ code, recoverable, message: "browser detail" });
  });

  it("normalizes unknown recognition error codes", async () => {
    const recognition = new FakeRecognition();
    const handlers = callbacks();
    await new BrowserSpeechProvider(fakeWindow(recognition).scope).start({} as MediaStreamTrack, handlers, false);

    recognition.onerror?.({ error: "audio-capture", message: "capture failed" } as SpeechRecognitionErrorEvent);

    expect(handlers.onError).toHaveBeenCalledWith({
      code: "recognition_error",
      recoverable: false,
      message: "capture failed",
    });
  });

  it("rejects a cancelled utterance and ignores its later completion callback", async () => {
    const recognition = new FakeRecognition();
    const { scope, utterances } = fakeWindow(recognition);
    const provider = new BrowserSpeechProvider(scope);
    const speaking = provider.speak("会被取消", null, 1);

    provider.cancelSpeech();
    utterances[0].onend?.();

    await expect(speaking).rejects.toMatchObject({ name: "AbortError" });
  });
});
