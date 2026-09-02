import { useEffect } from "react";

import { ChatMessage } from "./components/ChatMessage";
import { Composer } from "./components/Composer";
import { VoicePicker } from "./components/VoicePicker";
import type { VoiceSessionController } from "./useVoiceSession";

const statusLabels = { idle: "空闲", listening: "正在聆听", transcribing: "正在转写", thinking: "正在思考", speaking: "正在朗读" };

export function App({ controller }: { controller: VoiceSessionController }) {
  const { connect, disconnect } = controller;
  useEffect(() => {
    connect();
    return () => { void disconnect(); };
  }, [connect, disconnect]);
  const voiceName = controller.voices.find((voice) => voice.voice_key === controller.selectedVoice?.voiceKey)?.display_name;
  return <main className="app-shell">
    <header><div><h1>Agent（声灵）</h1><p>本地运行 · {controller.modelId ?? "正在连接"}</p></div><p role="status">{statusLabels[controller.voiceStatus]}</p>{voiceName && <p className="current-voice">{voiceName}</p>}</header>
    <VoicePicker controller={controller} />
    <section className="conversation" aria-label="对话记录">
      {controller.messages.map((message) => <ChatMessage key={message.id} message={message} onSpeak={controller.speakMessage} />)}
    </section>
    {controller.error && <p className="session-error" aria-live="polite">{controller.error.message}</p>}
    <Composer controller={controller} />
  </main>;
}
