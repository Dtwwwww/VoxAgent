import { useState } from "react";

import type { VoiceSessionController } from "../useVoiceSession";

export function Composer({ controller }: { controller: VoiceSessionController }) {
  const [text, setText] = useState("");
  const [validation, setValidation] = useState("");
  const count = [...text].length;
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
    {(controller.isMicrophoneActive || controller.voiceStatus === "listening") && <p className="listening" role="status">● 正在聆听</p>}
    <label htmlFor="message-input">输入消息</label>
    <textarea id="message-input" rows={2} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => {
      if (event.key === "Escape") { event.preventDefault(); controller.cancelActive(); }
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
    }} placeholder="输入消息…" />
    <div className="composer__controls">
      <button type="button" onClick={() => controller.isMicrophoneActive ? void controller.stopMicrophone() : void controller.startMicrophone()}>{controller.isMicrophoneActive ? "结束聆听" : "麦克风"}</button>
      <button type="button" onClick={controller.cancelActive}>停止</button>
      <button type="button" onClick={submit}>发送</button>
    </div>
    <p className="visually-live" aria-live="polite">{validation}</p>
  </section>;
}
