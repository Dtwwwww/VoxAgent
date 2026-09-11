import { useEffect, useRef, useState } from "react";

import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { ErrorNotice } from "./components/ErrorNotice";
import { Header } from "./components/Header";
import { SettingsPanel } from "./components/SettingsPanel";
import { ToolActivityStatus, ToolApprovalCard } from "./components/ToolApprovalCard";
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
  const [finalizedInterim, setFinalizedInterim] = useState<string | null>(null);
  const previousMessageIdsRef = useRef(new Set(controller.messages.map((message) => message.id)));

  useEffect(() => {
    connect();
    return () => { void disconnect(); };
  }, [connect, disconnect]);

  const interimText = controller.realtime.interimText.trim();
  useEffect(() => {
    const previousIds = previousMessageIdsRef.current;
    const matchingFinalArrived = interimText.length > 0 && controller.messages.some((message) => (
      !previousIds.has(message.id)
      && message.role === "user"
      && message.status === "complete"
      && message.text.trim() === interimText
    ));
    if (matchingFinalArrived) setFinalizedInterim(interimText);
    else if (interimText.length === 0) setFinalizedInterim(null);
    previousMessageIdsRef.current = new Set(controller.messages.map((message) => message.id));
  }, [controller.messages, interimText]);

  const handleSuggestion = (suggestion: string) => {
    if (suggestion === "开始语音对话") {
      document.getElementById("realtime-call-button")?.focus();
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
      {interimText && finalizedInterim !== interimText && <article
        className="message message--right message--interim"
        data-side="right"
        aria-label="用户（识别中）"
      >
        <div className="message__meta"><span className="message__speaker">用户（识别中）</span></div>
        <div className="message__bubble"><p>{interimText}</p></div>
      </article>}
      {controller.pendingToolApproval && <ToolApprovalCard
        key={controller.pendingToolApproval.confirmationId}
        approval={controller.pendingToolApproval}
        onConfirm={() => controller.confirmTool(controller.pendingToolApproval!.confirmationId)}
        onDeny={() => controller.denyTool(controller.pendingToolApproval!.confirmationId)}
      />}
      {!controller.pendingToolApproval && <ToolActivityStatus activity={controller.recentToolActivity.at(-1)} />}
      <VoiceStatus connectionStatus={controller.connectionStatus} voiceStatus={controller.voiceStatus} realtime={controller.realtime} />
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
      onReset={controller.clearLocalData}
    />}
  </main>;
}
