import { useRef, useState } from "react";

import type { VoiceSessionController } from "../useVoiceSession";
import { Icon } from "./Icon";
import { RealtimeVoiceControls } from "./RealtimeVoiceControls";

export function Composer({ controller }: { controller: VoiceSessionController }) {
  const [text, setText] = useState("");
  const [validation, setValidation] = useState("");
  const composing = useRef(false);
  const count = [...text].length;
  const canSubmit = Boolean(text.trim()) && count <= 4000;
  const canCancel = controller.voiceStatus === "thinking" || controller.voiceStatus === "speaking";

  const submit = () => {
    if (!text.trim() || count > 4000) {
      setValidation("请输入 1 到 4000 个字符");
      return;
    }
    controller.submitText(text);
    setText("");
    setValidation("");
  };

  return <section className="composer" aria-label="消息输入">
    <label className="sr-only" htmlFor="message-input">输入消息</label>
    <textarea
      id="message-input"
      rows={1}
      value={text}
      aria-describedby="composer-mode composer-validation"
      aria-invalid={Boolean(validation)}
      onChange={(event) => {
        setText(event.target.value);
        if (validation) setValidation("");
      }}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          controller.cancelActive();
        }
        if (event.key === "Enter" && !event.shiftKey && !composing.current) {
          event.preventDefault();
          submit();
        }
      }}
      placeholder="输入消息，或开始实时通话"
    />
    <RealtimeVoiceControls controller={controller} />
    <div className="composer__footer">
      <span id="composer-mode" className="composer__mode">
        {controller.realtime.active ? "实时通话中，文字仍可发送" : "文字消息仅回复文字"}
      </span>
      <div className="composer__actions">
        {canCancel && <button className="stop-generation" type="button" onClick={controller.cancelActive}>
          <Icon name="stop" size={14} />
          停止生成
        </button>}
        <button className="send-button" type="button" aria-label="发送" disabled={!canSubmit} onClick={submit}>
          <Icon name="send" />
        </button>
      </div>
    </div>
    <div className="composer__feedback">
      <span id="composer-validation" className="validation" aria-live="polite">{validation}</span>
      {count > 3600 && <span className="character-count">{count}/4000</span>}
    </div>
  </section>;
}
