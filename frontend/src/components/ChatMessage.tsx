import type { ConversationMessage } from "../useVoiceSession";

interface ChatMessageProps {
  message: ConversationMessage;
  onSpeak(turnId: number): void;
}

export function ChatMessage({ message, onSpeak }: ChatMessageProps) {
  const isAssistant = message.role === "assistant";
  const side = isAssistant ? "left" : "right";
  const copy = async () => { await navigator.clipboard?.writeText(message.text); };
  return <article className={`message message--${side}`} data-side={side} aria-label={isAssistant ? "Agent（声灵）消息" : "用户消息"}>
    <div className="message__speaker">{isAssistant ? "Agent（声灵）" : "用户"}</div>
    {message.origin === "voice" && <span className="message__tag">语音输入</span>}
    {message.status === "cancelled" && <span className="message__tag">已停止</span>}
    <p>{message.text}</p>
    <div className="message__actions">
      <button type="button" onClick={() => void copy()}>复制</button>
      {isAssistant && message.status === "complete" && message.turnId !== undefined && <button type="button" onClick={() => onSpeak(message.turnId!)}>朗读</button>}
    </div>
  </article>;
}
