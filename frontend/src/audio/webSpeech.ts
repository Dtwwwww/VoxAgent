export interface BrowserSpeechCallbacks {
  onInterim(text: string): void;
  onFinal(text: string): void;
  onSpeechStart(): void;
  onSpeechEnd(): void;
  onRecognitionEnd(): void;
  onError(error: BrowserSpeechFailure): void;
}

export interface BrowserVoice {
  key: string;
  name: string;
  lang: string;
  localService: boolean;
}

export interface BrowserSpeechFailure {
  code: "unsupported" | "track_not_supported" | "recognition_error";
  recoverable: boolean;
  message?: string;
}

const CHINESE_VOICE_NAME = /Chinese|中文|Xiaoxiao|Yunxi|Huihui|Yaoyao/i;

function defaultScope(): Window | undefined {
  return typeof window === "undefined" ? undefined : window;
}

function recognitionConstructor(scope: Window): SpeechRecognitionConstructor | undefined {
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
}

export class BrowserSpeechProvider {
  private recognition: SpeechRecognition | null = null;
  private recognitionVersion = 0;
  private speechVersion = 0;
  private readonly activeUtterances = new Map<SpeechSynthesisUtterance, (reason: DOMException) => void>();

  constructor(private readonly scope: Window = defaultScope() as Window) {}

  static isSupported(scope: Window | undefined = defaultScope()): boolean {
    return Boolean(scope && recognitionConstructor(scope) && scope.speechSynthesis && scope.SpeechSynthesisUtterance);
  }

  async start(
    track: MediaStreamTrack,
    callbacks: BrowserSpeechCallbacks,
    allowDefaultInputFallback: boolean,
  ): Promise<void> {
    if (!BrowserSpeechProvider.isSupported(this.scope)) {
      callbacks.onError({ code: "unsupported", recoverable: false });
      return;
    }

    this.stopRecognition();
    const Recognition = recognitionConstructor(this.scope);
    if (!Recognition) {
      callbacks.onError({ code: "unsupported", recoverable: false });
      return;
    }

    const recognition = new Recognition();
    const version = ++this.recognitionVersion;
    this.recognition = recognition;
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      if (version !== this.recognitionVersion) return;
      const finalText: string[] = [];
      const interimText: string[] = [];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) finalText.push(transcript);
        else interimText.push(transcript);
      }
      if (interimText.length > 0) callbacks.onInterim(interimText.join(""));
      if (finalText.length > 0) callbacks.onFinal(finalText.join(""));
    };
    recognition.onspeechstart = () => {
      if (version === this.recognitionVersion) callbacks.onSpeechStart();
    };
    recognition.onspeechend = () => {
      if (version === this.recognitionVersion) callbacks.onSpeechEnd();
    };
    recognition.onend = () => {
      if (version === this.recognitionVersion) callbacks.onRecognitionEnd();
    };
    recognition.onerror = (event) => {
      if (version !== this.recognitionVersion) return;
      callbacks.onError({
        code: "recognition_error",
        recoverable: event.error !== "not-allowed" && event.error !== "service-not-allowed",
        message: event.message || event.error,
      });
    };

    try {
      recognition.start(track);
    } catch (error) {
      if (!(error instanceof TypeError)) {
        callbacks.onError({ code: "track_not_supported", recoverable: true });
        return;
      }
      if (!allowDefaultInputFallback) {
        callbacks.onError({ code: "track_not_supported", recoverable: true });
        return;
      }
      try {
        recognition.start();
      } catch {
        callbacks.onError({ code: "track_not_supported", recoverable: true });
      }
    }
  }

  stopRecognition(): void {
    this.recognitionVersion += 1;
    const recognition = this.recognition;
    this.recognition = null;
    if (!recognition) return;
    recognition.onresult = null;
    recognition.onspeechstart = null;
    recognition.onspeechend = null;
    recognition.onend = null;
    recognition.onerror = null;
    try {
      recognition.stop();
    } catch {
      // Some implementations throw when recognition has not started.
    }
  }

  voices(): BrowserVoice[] {
    if (!this.scope?.speechSynthesis) return [];
    return this.scope.speechSynthesis.getVoices()
      .filter((voice) => voice.lang.toLowerCase().startsWith("zh") || CHINESE_VOICE_NAME.test(voice.name))
      .map((voice) => ({
        key: voice.voiceURI || `${voice.name}:${voice.lang}`,
        name: voice.name,
        lang: voice.lang,
        localService: voice.localService,
      }));
  }

  speak(text: string, voiceKey: string | null, rate: number): Promise<void> {
    if (!this.scope?.speechSynthesis || !this.scope.SpeechSynthesisUtterance) {
      return Promise.reject(new Error("Speech synthesis is not supported"));
    }
    const voices = this.scope.speechSynthesis.getVoices();
    const selectedVoice = voiceKey === null
      ? undefined
      : voices.find((voice) => (voice.voiceURI || `${voice.name}:${voice.lang}`) === voiceKey && this.isChineseVoice(voice));
    const utterance = new this.scope.SpeechSynthesisUtterance(text);
    const version = this.speechVersion;
    utterance.lang = "zh-CN";
    utterance.rate = rate;
    if (selectedVoice) utterance.voice = selectedVoice;
    return new Promise<void>((resolve, reject) => {
      this.activeUtterances.set(utterance, reject);
      const finish = (complete: () => void) => {
        if (version !== this.speechVersion || !this.activeUtterances.has(utterance)) return;
        this.activeUtterances.delete(utterance);
        utterance.onend = null;
        utterance.onerror = null;
        complete();
      };
      utterance.onend = () => finish(resolve);
      utterance.onerror = (event) => finish(() => reject(new Error(event.error || "Speech synthesis failed")));
      this.scope.speechSynthesis.speak(utterance);
    });
  }

  cancelSpeech(): void {
    this.speechVersion += 1;
    for (const [utterance, reject] of this.activeUtterances) {
      utterance.onend = null;
      utterance.onerror = null;
      reject(new DOMException("Speech synthesis was cancelled", "AbortError"));
    }
    this.activeUtterances.clear();
    this.scope?.speechSynthesis?.cancel();
  }

  close(): void {
    this.stopRecognition();
    this.cancelSpeech();
  }

  private isChineseVoice(voice: SpeechSynthesisVoice): boolean {
    return voice.lang.toLowerCase().startsWith("zh") || CHINESE_VOICE_NAME.test(voice.name);
  }
}
