import { errorPresentation } from "../presentation";
import type { SessionError } from "../useVoiceSession";

interface ErrorNoticeProps {
  error: SessionError;
  onConnect(): void;
  onMicrophone(): void;
}

export function ErrorNotice({ error, onConnect, onMicrophone }: ErrorNoticeProps) {
  const presentation = errorPresentation(error);
  const action = presentation.action === "connect"
    ? <button type="button" onClick={onConnect}>重试连接</button>
    : presentation.action === "microphone"
      ? <button type="button" onClick={onMicrophone}>重试麦克风</button>
      : null;

  return <div className="error-notice" role="alert" data-action={presentation.action}>
    <span className="error-notice__mark" aria-hidden="true">!</span>
    <span className="error-notice__message">{presentation.label}</span>
    {action}
  </div>;
}
