import type { VoiceSpeed } from "../protocol";
import type { VoiceSessionController } from "../useVoiceSession";

const speeds: Array<{ value: VoiceSpeed; label: string }> = [
  { value: 0.8, label: "慢速 0.8×" }, { value: 1.0, label: "正常 1.0×" }, { value: 1.2, label: "稍快 1.2×" },
];

export function VoicePicker({ controller }: { controller: VoiceSessionController }) {
  const busy = controller.voiceStatus === "transcribing" || controller.voiceStatus === "thinking" || controller.voiceStatus === "speaking";
  const speed = controller.selectedVoice?.speed ?? 1.0;
  return <section className="voice-picker" aria-label="音色设置">
    <h2>音色</h2>
    {controller.voices.map((voice) => {
      const selected = controller.selectedVoice?.voiceKey === voice.voice_key;
      return <article className="voice-card" key={voice.voice_key} aria-selected={selected}>
        <h3>{voice.display_name}</h3><p>{voice.description}</p>
        <div className="speed-options">{speeds.map((item) => <button type="button" key={item.value} aria-pressed={selected && speed === item.value} onClick={() => controller.selectVoice(voice.voice_key, item.value)}>{item.label}</button>)}</div>
        <button type="button" disabled={busy || !voice.previewable} onClick={() => controller.previewVoice(voice.voice_key, speed)}>试听</button>
      </article>;
    })}
    {controller.error?.code === "preview_failed" && <p aria-live="polite">试听失败，可选择其他音色</p>}
  </section>;
}
