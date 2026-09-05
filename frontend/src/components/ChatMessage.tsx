import { useEffect, useRef, useState } from "react";

import type { ConversationMessage } from "../useVoiceSession";
import { Icon } from "./Icon";

interface ChatMessageProps {
  message: ConversationMessage;
  speaking: boolean;
  onSpeak(turnId: number): void;
  onStopSpeaking(turnId: number): void;
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Browser permissions can reject the modern API; use the local fallback.
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand?.("copy") ?? false;
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}

export function ChatMessage({ message, speaking, onSpeak, onStopSpeaking }: ChatMessageProps) {
  const isAssistant = message.role === "assistant";
  const side = isAssistant ? "left" : "right";
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyTimer.current !== null) clearTimeout(copyTimer.current);
  }, []);
  const copy = async () => {
    const copied = await copyText(message.text);
    setCopyState(copied ? "copied" : "failed");
    if (copyTimer.current !== null) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(
      () => setCopyState("idle"),
      copied ? 1500 : 2500,
    );
  };
  const copyLabel = copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制";
  return <article className={`message message--${side}`} data-side={side} aria-label={isAssistant ? "声灵消息" : "用户消息"}>
    <div className="message__meta">
      {isAssistant && <span className="message__avatar"><Icon name="spark" size={14} /></span>}
      <span className="message__speaker">{isAssistant ? "声灵" : "用户"}</span>
      {message.origin === "voice" && <span className="message__tag">语音输入</span>}
      {message.status === "cancelled" && <span className="message__tag">已停止</span>}
    </div>
    <div className="message__bubble"><p>{message.text}</p></div>
    {isAssistant && message.sources && message.sources.length > 0 && <details className="message-sources">
      <summary>查看引用来源（{message.sources.length}）</summary>
      <div className="message-sources__list">
        {message.sources.map((source) => source.kind === "memory"
          ? <section key={`memory-${source.id}`}>
              <strong>长期记忆</strong>
              <p>{source.content}</p>
              {source.sourceText && <blockquote>原话：{source.sourceText}</blockquote>}
            </section>
          : <section key={`knowledge-${source.chunkId}`}>
              <strong>{source.displayName}{source.pageNumber ? ` · 第 ${source.pageNumber} 页` : ""}</strong>
              <p>{source.content}</p>
            </section>)}
      </div>
    </details>}
    <div className="message__actions">
      <button className="message-action" type="button" aria-label={copyLabel} data-state={copyState} onClick={() => void copy()}><Icon name="copy" size={16} /><span>{copyLabel}</span></button>
      {isAssistant && message.status === "complete" && message.turnId !== undefined && (speaking
        ? <button className="message-action message-action--danger" type="button" aria-label="停止朗读" onClick={() => onStopSpeaking(message.turnId!)}><Icon name="stop" size={14} /><span>停止朗读</span></button>
        : <button className="message-action" type="button" aria-label="朗读" onClick={() => onSpeak(message.turnId!)}><Icon name="volume" size={16} /><span>朗读</span></button>)}
    </div>
  </article>;
}
