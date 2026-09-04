import { useEffect, useState } from "react";

import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ErrorNotice } from "./components/ErrorNotice";
import { Header } from "./components/Header";
import { SettingsPanel } from "./components/SettingsPanel";
import { VoiceStatus } from "./components/VoiceStatus";
import { VoicePicker } from "./components/VoicePicker";
import { KnowledgePanel } from "./knowledge/KnowledgePanel";
import type { KnowledgeClient } from "./knowledge/client";
import type { LocalApiClient } from "./localApi";
import { MemoryProposalNotice } from "./memory/MemoryPanel";
import type { VoiceSessionController } from "./useVoiceSession";

interface AppProps {
  controller: VoiceSessionController;
  knowledgeClient?: KnowledgeClient;
  localApiClient?: LocalApiClient;
}

export function App({ controller, knowledgeClient, localApiClient }: AppProps) {
  const { connect, disconnect } = controller;
  const [voicePickerOpen, setVoicePickerOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

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
    <Header
      controller={controller}
      onOpenKnowledge={knowledgeClient ? () => { setSettingsOpen(false); setKnowledgeOpen(true); } : undefined}
      onOpenSettings={localApiClient ? () => { setKnowledgeOpen(false); setSettingsOpen(true); } : undefined}
      onOpenVoices={() => setVoicePickerOpen((open) => !open)}
    />
    <Conversation
      messages={controller.messages}
      speakingTurnId={controller.speakingTurnId}
      onSpeak={controller.speakMessage}
      onStopSpeaking={controller.stopSpeaking}
      onSuggestion={handleSuggestion}
    />
    <div className="composer-region">
      {localApiClient && controller.memoryProposals.map((proposal) => <MemoryProposalNotice
        key={proposal.id}
        proposal={proposal}
        client={localApiClient}
        onDismiss={controller.dismissMemoryProposal}
      />)}
      {controller.error && <ErrorNotice error={controller.error} onConnect={controller.connect} onMicrophone={() => { void controller.startMicrophone(); }} />}
      <VoiceStatus connectionStatus={controller.connectionStatus} voiceStatus={controller.voiceStatus} />
      <Composer controller={controller} />
    </div>
    <VoicePicker controller={controller} open={voicePickerOpen} onClose={() => setVoicePickerOpen(false)} />
    {knowledgeClient && <KnowledgePanel
      client={knowledgeClient}
      open={knowledgeOpen}
      onClose={() => setKnowledgeOpen(false)}
    />}
    {localApiClient && <SettingsPanel
      client={localApiClient}
      open={settingsOpen}
      onClose={() => setSettingsOpen(false)}
    />}
  </main>;
}
