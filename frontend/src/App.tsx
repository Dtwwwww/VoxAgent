import { useEffect, useState } from "react";

import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ErrorNotice } from "./components/ErrorNotice";
import { Header } from "./components/Header";
import { VoiceStatus } from "./components/VoiceStatus";
import { VoicePicker } from "./components/VoicePicker";
import type { VoiceSessionController } from "./useVoiceSession";

export function App({ controller }: { controller: VoiceSessionController }) {
  const { connect, disconnect } = controller;
  const [voicePickerOpen, setVoicePickerOpen] = useState(false);

  useEffect(() => {
    connect();
    return () => { void disconnect(); };
  }, [connect, disconnect]);

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
      {controller.error && <ErrorNotice error={controller.error} onConnect={controller.connect} onMicrophone={() => { void controller.startMicrophone(); }} />}
      <VoiceStatus connectionStatus={controller.connectionStatus} voiceStatus={controller.voiceStatus} />
      <Composer controller={controller} />
    </div>
    <VoicePicker controller={controller} open={voicePickerOpen} onClose={() => setVoicePickerOpen(false)} />
  </main>;
}
