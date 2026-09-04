import { useEffect, useRef } from "react";

import type { VoiceSpeed } from "../protocol";
import type { VoiceSessionController } from "../useVoiceSession";
import { Icon } from "./Icon";

const speeds: Array<{ value: VoiceSpeed; label: string }> = [
  { value: 0.8, label: "舒缓" },
  { value: 1.0, label: "自然" },
  { value: 1.2, label: "稍快" },
];

interface VoicePickerProps {
  controller: VoiceSessionController;
  open: boolean;
  onClose(): void;
}

export function VoicePicker({ controller, open, onClose }: VoicePickerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

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
      previousFocusRef.current?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  const selectedVoiceKey = controller.selectedVoice?.voiceKey ?? "voice-005";
  const selectedVoice = controller.voices.find((voice) => voice.voice_key === selectedVoiceKey);
  const speed = controller.selectedVoice?.speed ?? 1;
  const previewBusy = controller.previewingVoiceKey !== null;
  const sessionBusy = controller.voiceStatus === "transcribing" || controller.voiceStatus === "thinking";

  return <div className="voice-picker-layer" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="voice-picker" role="dialog" aria-modal="true" aria-labelledby="voice-picker-title">
      <div className="voice-picker__header">
        <div>
          <h2 id="voice-picker-title">选择音色</h2>
          <p>当前：{selectedVoice?.display_name ?? selectedVoiceKey}</p>
        </div>
        <button ref={closeRef} className="icon-button" type="button" aria-label="关闭音色选择" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>

      <div className="voice-list">
        {controller.voices.map((voice) => {
          const selected = voice.voice_key === selectedVoiceKey;
          const previewing = voice.voice_key === controller.previewingVoiceKey;
          return <article className="voice-card" key={voice.voice_key} data-selected={selected}>
            <button
              className="voice-card__select"
              type="button"
              aria-pressed={selected}
              onClick={() => controller.selectVoice(voice.voice_key, speed)}
            >
              <span className="voice-card__title">{voice.display_name}{selected && <span className="voice-card__check">已选择</span>}</span>
              <span className="voice-card__description">{voice.description}</span>
            </button>
            <button
              className="preview-button"
              type="button"
              aria-label={`${previewing ? "停止试听" : "试听"} ${voice.display_name}`}
              disabled={!voice.previewable || sessionBusy || (previewBusy && !previewing)}
              onClick={() => previewing
                ? controller.stopVoicePreview()
                : controller.previewVoice(voice.voice_key, speed)}
            >
              <Icon name={previewing ? "stop" : "volume"} size={16} />
              {previewing ? "停止试听" : "试听"}
            </button>
          </article>;
        })}
      </div>

      <fieldset className="speed-picker">
        <legend>语速</legend>
        <div className="speed-options">
          {speeds.map((item) => <button
            type="button"
            key={item.value}
            aria-pressed={speed === item.value}
            onClick={() => controller.selectVoice(selectedVoiceKey, item.value)}
          >{item.label}</button>)}
        </div>
      </fieldset>
    </section>
  </div>;
}
