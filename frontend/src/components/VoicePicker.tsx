import { useEffect, useRef, useState } from "react";

import { BrowserSpeechProvider } from "../audio/webSpeech";
import type { VoiceSpeed } from "../protocol";
import type { VoiceSessionController } from "../useVoiceSession";
import { Icon } from "./Icon";

const speeds: Array<{ value: VoiceSpeed; label: string }> = [
  { value: 0.8, label: "舒缓" },
  { value: 1.0, label: "自然" },
  { value: 1.2, label: "稍快" },
];

const BROWSER_PREVIEW_TEXT = "你好，我是声灵，很高兴认识你。";

interface VoicePickerProps {
  controller: VoiceSessionController;
  open: boolean;
  onClose(): void;
}

export function VoicePicker({ controller, open, onClose }: VoicePickerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const browserSpeechRef = useRef<BrowserSpeechProvider | null>(null);
  const [previewingBrowserVoiceKey, setPreviewingBrowserVoiceKey] = useState<string | null>(null);
  if (!browserSpeechRef.current) browserSpeechRef.current = new BrowserSpeechProvider();

  useEffect(() => () => browserSpeechRef.current?.close(), []);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      browserSpeechRef.current?.cancelSpeech();
      previousFocusRef.current?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  const localFallback = controller.voices.find((voice) => voice.is_default) ?? controller.voices[0] ?? null;
  const selectedVoiceKey = localFallback?.voice_key === controller.selectedVoice?.voiceKey
    ? controller.selectedVoice.voiceKey
    : localFallback?.voice_key ?? null;
  const selectedVoice = localFallback?.voice_key === selectedVoiceKey ? localFallback : null;
  const selectedBrowserVoice = controller.browserVoices.find((voice) => voice.key === controller.selectedBrowserVoiceKey);
  const speed = controller.selectedVoice?.speed ?? 1;
  const previewBusy = controller.previewingVoiceKey !== null || previewingBrowserVoiceKey !== null;
  const sessionBusy = controller.voiceStatus !== "idle" || controller.realtime.active;

  const stopBrowserPreview = () => {
    browserSpeechRef.current?.cancelSpeech();
    setPreviewingBrowserVoiceKey(null);
  };

  const previewBrowserVoice = (voiceKey: string) => {
    if (previewingBrowserVoiceKey === voiceKey) {
      stopBrowserPreview();
      return;
    }
    browserSpeechRef.current?.cancelSpeech();
    if (controller.previewingVoiceKey !== null) controller.stopVoicePreview();
    controller.selectBrowserVoice(voiceKey);
    setPreviewingBrowserVoiceKey(voiceKey);
    void browserSpeechRef.current?.speak(BROWSER_PREVIEW_TEXT, voiceKey, speed)
      .catch(() => undefined)
      .finally(() => setPreviewingBrowserVoiceKey((current) => current === voiceKey ? null : current));
  };

  return <div className="voice-picker-layer" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="voice-picker" role="dialog" aria-modal="true" aria-labelledby="voice-picker-title">
      <div className="voice-picker__header">
        <div>
          <h2 id="voice-picker-title">选择音色</h2>
          <p>当前：{selectedBrowserVoice?.name ?? selectedVoice?.display_name ?? "正在加载音色"}</p>
        </div>
        <button ref={closeRef} className="icon-button" type="button" aria-label="关闭音色选择" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>

      <div className="voice-list">
        {controller.browserVoices.map((voice) => {
          const selected = voice.key === controller.selectedBrowserVoiceKey;
          const previewing = voice.key === previewingBrowserVoiceKey;
          return <article className="voice-card" key={voice.key} data-selected={selected} data-provider="browser">
            <button
              className="voice-card__select"
              type="button"
              aria-pressed={selected}
              onClick={() => controller.selectBrowserVoice(voice.key)}
            >
              <span className="voice-card__title">{voice.name}{selected && <span className="voice-card__check">已选择</span>}</span>
              <span className="voice-card__description">{voice.lang}</span>
              <span className="voice-card__provider">在线/系统</span>
            </button>
            <button
              className="preview-button"
              type="button"
              aria-label={`${previewing ? "停止试听" : "试听"} ${voice.name}`}
              disabled={sessionBusy || (previewBusy && !previewing)}
              onClick={() => previewBrowserVoice(voice.key)}
            >
              <Icon name={previewing ? "stop" : "volume"} size={16} />
              {previewing ? "停止试听" : "试听"}
            </button>
          </article>;
        })}
        {localFallback && (() => {
          const voice = localFallback;
          const selected = voice.voice_key === selectedVoiceKey;
          const previewing = voice.voice_key === controller.previewingVoiceKey;
          return <article className="voice-card" key={voice.voice_key} data-selected={selected} data-provider="local">
            <button
              className="voice-card__select"
              type="button"
              aria-pressed={selected}
              onClick={() => controller.selectVoice(voice.voice_key, speed)}
            >
              <span className="voice-card__title">{voice.display_name}{selected && <span className="voice-card__check">已选择</span>}</span>
              <span className="voice-card__description">{voice.description}</span>
              <span className="voice-card__provider">本地·生成较慢</span>
            </button>
            <button
              className="preview-button"
              type="button"
              aria-label={`${previewing ? "停止试听" : "试听"} ${voice.display_name}`}
              disabled={!voice.previewable || sessionBusy || (previewBusy && !previewing)}
              onClick={() => {
                stopBrowserPreview();
                if (previewing) controller.stopVoicePreview();
                else controller.previewVoice(voice.voice_key, speed);
              }}
            >
              <Icon name={previewing ? "stop" : "volume"} size={16} />
              {previewing ? "停止试听" : "试听"}
            </button>
          </article>;
        })()}
      </div>

      <fieldset className="speed-picker">
        <legend>语速</legend>
        <div className="speed-options">
          {speeds.map((item) => <button
            type="button"
            key={item.value}
            disabled={selectedVoiceKey === null}
            aria-pressed={speed === item.value}
            onClick={() => {
              if (selectedVoiceKey !== null) controller.selectVoice(selectedVoiceKey, item.value);
            }}
          >{item.label}</button>)}
        </div>
      </fieldset>
    </section>
  </div>;
}
