import { useEffect, useRef, useState } from "react";

import type { ConversationMessage } from "../useVoiceSession";
import { ChatMessage } from "./ChatMessage";
import { Icon } from "./Icon";

interface ConversationProps {
  messages: ConversationMessage[];
  speakingTurnId: number | null;
  onSpeak(turnId: number): void;
  onStopSpeaking(turnId: number): void;
  onSuggestion(suggestion: string): void;
}

const suggestions = ["介绍一下你自己", "帮我整理今天的计划", "开始语音对话"];

export function Conversation({ messages, speakingTurnId, onSpeak, onStopSpeaking, onSuggestion }: ConversationProps) {
  const scrollRef = useRef<HTMLElement>(null);
  const nearBottomRef = useRef(true);
  const [showReturn, setShowReturn] = useState(false);

  const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
    const element = scrollRef.current;
    if (!element) return;
    if (typeof element.scrollTo === "function") element.scrollTo({ top: element.scrollHeight, behavior });
    else element.scrollTop = element.scrollHeight;
    nearBottomRef.current = true;
    setShowReturn(false);
  };

  useEffect(() => {
    if (nearBottomRef.current) scrollToBottom("auto");
    else setShowReturn(true);
  }, [messages]);

  const handleScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
    nearBottomRef.current = nearBottom;
    setShowReturn(!nearBottom);
  };

  return <section className="conversation" aria-label="对话记录" ref={scrollRef} onScroll={handleScroll}>
    {messages.length === 0 ? <div className="welcome">
      <span className="welcome__mark"><Icon name="spark" size={28} /></span>
      <h2>你好，我是声灵</h2>
      <p>可以输入文字，或者点击麦克风和我说话。</p>
      <div className="welcome__suggestions" aria-label="对话建议">
        {suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}
      </div>
    </div> : <div className="message-list">
      {messages.map((message) => <ChatMessage
        key={message.id}
        message={message}
        speaking={message.turnId !== undefined && message.turnId === speakingTurnId}
        onSpeak={onSpeak}
        onStopSpeaking={onStopSpeaking}
      />)}
    </div>}
    {showReturn && <button className="return-bottom" type="button" onClick={() => scrollToBottom()}>
      <Icon name="arrowDown" />
      回到底部
    </button>}
  </section>;
}
