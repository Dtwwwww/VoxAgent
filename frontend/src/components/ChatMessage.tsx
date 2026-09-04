import type { ConversationMessage } from "../useVoiceSession";
import { Icon } from "./Icon";

interface ChatMessageProps {
  message: ConversationMessage;
  speaking: boolean;
  onSpeak(turnId: number): void;
  onStopSpeaking(turnId: number): void;
}

export function ChatMessage({ message, speaking, onSpeak, onStopSpeaking }: ChatMessageProps) {
  const isAssistant = message.role === "assistant";
  const side = isAssistant ? "left" : "right";
  const copy = async () => { await navigator.clipboard?.writeText(message.text); };
  return <article className={`message message--${side}`} data-side={side} aria-label={isAssistant ? "声灵消息" : "用户消息"}>
    <div className="message__meta">
      {isAssistant && <span className="message__avatar"><Icon name="spark" size={14} /></span>}
      <span className="message__speaker">{isAssistant ? "声灵" : "用户"}</span>
      {message.origin === "voice" && <span className="message__tag">语音输入</span>}
      {message.status === "cancelled" && <span className="message__tag">已停止</span>}
    </div>
    <div className="message__bubble"><p>{message.text}</p></div>
    <div className="message__actions">
      <button className="message-action" type="button" aria-label="复制" onClick={() => void copy()}><Icon name="copy" size={16} /><span>复制</span></button>
      {isAssistant && message.status === "complete" && message.turnId !== undefined && (speaking
        ? <button className="message-action message-action--danger" type="button" aria-label="停止朗读" onClick={() => onStopSpeaking(message.turnId!)}><Icon name="stop" size={14} /><span>停止朗读</span></button>
        : <button className="message-action" type="button" aria-label="朗读" onClick={() => onSpeak(message.turnId!)}><Icon name="volume" size={16} /><span>朗读</span></button>)}
    </div>
  </article>;
}
