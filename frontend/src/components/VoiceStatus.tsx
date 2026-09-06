import { connectionPresentation, realtimeVoiceStatusPresentation, voiceStatusPresentation } from "../presentation";
import type { RealtimeSnapshot } from "../realtime/RealtimeVoiceEngine";
import type { ConnectionStatus, VoiceStatus as VoiceStatusValue } from "../useVoiceSession";
import { Icon } from "./Icon";

interface VoiceStatusProps {
  connectionStatus: ConnectionStatus;
  voiceStatus: VoiceStatusValue;
  realtime: RealtimeSnapshot;
}

function StatusVisual({ icon }: { icon: "thinking" | "spinner" | "wave" }) {
  if (icon === "thinking") {
    return <span className="thinking-dots" aria-hidden="true"><i /><i /><i /></span>;
  }
  if (icon === "wave") {
    return <span className="speaking-bars" aria-hidden="true"><i /><i /><i /><i /></span>;
  }
  return <Icon name="spinner" className="status-spinner" />;
}

export function VoiceStatus({ connectionStatus, voiceStatus, realtime }: VoiceStatusProps) {
  if (realtime.state !== "off" || realtime.notice !== null || realtime.fallbackReason !== null) {
    const fallbackPresentation = realtime.fallbackReason === null
      ? null
      : realtimeVoiceStatusPresentation("fallback", null);
    const presentation = realtimeVoiceStatusPresentation(realtime.state, realtime.notice)
      ?? fallbackPresentation;
    if (!presentation) return null;
    return <div className="voice-status" role="status" aria-live="polite" data-state={realtime.state}>
      <StatusVisual icon={presentation.icon} />
      <span>{presentation.label}</span>
      {fallbackPresentation && realtime.state !== "fallback" && <span className="voice-status__fallback">
        {fallbackPresentation.label}
      </span>}
    </div>;
  }
  if (connectionStatus === "connected" && voiceStatus === "idle") return null;
  if (connectionStatus !== "connected") {
    const presentation = connectionPresentation(connectionStatus, null);
    return <div className="voice-status" role="status" aria-live="polite" data-state={connectionStatus} data-tone={presentation.tone}>
      <StatusVisual icon="spinner" />
      <span>{presentation.label}</span>
    </div>;
  }
  const presentation = voiceStatusPresentation(voiceStatus);
  if (!presentation) return null;
  return <div className="voice-status" role="status" aria-live="polite" data-state={voiceStatus}>
    <StatusVisual icon={presentation.icon} />
    <span>{presentation.label}</span>
  </div>;
}
