import { connectionPresentation } from "../presentation";
import type { VoiceSessionController } from "../useVoiceSession";
import { Icon } from "./Icon";

interface HeaderProps {
  controller: VoiceSessionController;
  onOpenVoices(): void;
}

export function Header({ controller, onOpenVoices }: HeaderProps) {
  const connection = connectionPresentation(controller.connectionStatus, controller.modelId);
  const voiceName = controller.voices.find((voice) => voice.voice_key === controller.selectedVoice?.voiceKey)?.display_name
    ?? "正在加载音色";

  return <header className="app-header">
    <div className="brand">
      <span className="brand__mark"><Icon name="spark" size={22} /></span>
      <span className="brand__copy">
        <h1>声灵</h1>
        <span>你的本地语音 AI 助手</span>
      </span>
    </div>
    <div className="app-header__actions">
      <span className="connection" data-tone={connection.tone}>
        <span className="connection__dot" aria-hidden="true" />
        {connection.label}
      </span>
      <button className="voice-trigger" type="button" onClick={onOpenVoices} aria-haspopup="dialog">
        音色：{voiceName}
        <Icon name="chevronDown" size={16} />
      </button>
      <button className="icon-button header-settings" type="button" aria-label="设置" onClick={onOpenVoices} aria-haspopup="dialog">
        <Icon name="settings" />
      </button>
    </div>
  </header>;
}
