import { useEffect, useState } from "react";

import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { Header } from "./components/Header";
import { VoicePicker } from "./components/VoicePicker";
import { voiceStatusPresentation } from "./presentation";
import type { VoiceSessionController } from "./useVoiceSession";

export function App({ controller }: { controller: VoiceSessionController }) {
  const { connect, disconnect } = controller;
  const [voicePickerOpen, setVoicePickerOpen] = useState(false);

  useEffect(() => {
    connect();
    return () => { void disconnect(); };
  }, [connect, disconnect]);

  const status = voiceStatusPresentation(controller.voiceStatus);
  const handleSuggestion = (suggestion: string) => {
    if (suggestion === "开始语音对话") {
      document.getElementById("microphone-button")?.focus();
      return;
    }
    controller.submitText(suggestion);
  };

  return <main className="app-shell">
    <Header controller={controller} onOpenVoices={() => setVoicePickerOpen((open) => !open)} />
    <Conversation
      messages={controller.messages}
      speakingTurnId={controller.speakingTurnId}
      onSpeak={controller.speakMessage}
      onStopSpeaking={controller.stopSpeaking}
      onSuggestion={handleSuggestion}
    />
    <div className="composer-region">
      {controller.error && <p className="session-error" aria-live="polite">{controller.error.message}</p>}
      {status && <div className="voice-status" role="status" data-state={controller.voiceStatus}>{status.label}</div>}
      <Composer controller={controller} />
    </div>
    {voicePickerOpen && <div className="voice-picker-layer"><VoicePicker controller={controller} /></div>}
  </main>;
}
