import { connectionPresentation, voiceStatusPresentation } from "../presentation";
import type { ConnectionStatus, VoiceStatus as VoiceStatusValue } from "../useVoiceSession";
import { Icon } from "./Icon";

interface VoiceStatusProps {
  connectionStatus: ConnectionStatus;
  voiceStatus: VoiceStatusValue;
}

function StatusVisual({ state }: { state: VoiceStatusValue | ConnectionStatus }) {
  if (state === "thinking") {
    return <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>;
  }
  if (state === "listening" || state === "speaking") {
    return <span className="speaking-bars" aria-hidden="true"><i /><i /><i /><i /></span>;
  }
  return <Icon name="spinner" className="status-spinner" />;
}

export function VoiceStatus({ connectionStatus, voiceStatus }: VoiceStatusProps) {
  if (connectionStatus === "connected" && voiceStatus === "idle") return null;
  if (connectionStatus !== "connected") {
    const presentation = connectionPresentation(connectionStatus, null);
    return <div className="voice-status" role="status" aria-live="polite" data-state={connectionStatus} data-tone={presentation.tone}>
      <StatusVisual state={connectionStatus} />
      <span>{presentation.label}</span>
    </div>;
  }
  const presentation = voiceStatusPresentation(voiceStatus);
  if (!presentation) return null;
  return <div className="voice-status" role="status" aria-live="polite" data-state={voiceStatus}>
    <StatusVisual state={voiceStatus} />
    <span>{presentation.label}</span>
  </div>;
}
