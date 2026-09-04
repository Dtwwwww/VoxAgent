import type { ConnectionStatus, SessionError, VoiceStatus } from "./useVoiceSession";

export type Tone = "neutral" | "success" | "warning" | "danger";
export type RecoveryAction = "none" | "connect" | "microphone";

export function connectionPresentation(status: ConnectionStatus, modelId: string | null) {
  if (status === "connected") {
    return { tone: "success" as const, label: `本地运行 · ${modelId ?? "本地模型"}` };
  }
  if (status === "connecting") {
    return { tone: "warning" as const, label: "正在连接本地服务" };
  }
  return { tone: "danger" as const, label: "本地服务未连接" };
}

export function voiceStatusPresentation(status: VoiceStatus) {
  const values = {
    listening: { icon: "wave", label: "正在聆听，点击停止" },
    transcribing: { icon: "spinner", label: "正在识别你的语音" },
    thinking: { icon: "thinking", label: "声灵正在思考" },
    speaking: { icon: "wave", label: "正在回复" },
  } as const;
  return status === "idle" ? null : values[status];
}

export function errorPresentation(error: SessionError) {
  if (error.code === "connection") {
    return { label: "本地服务未启动，请启动后重试。", action: "connect" as const };
  }
  if (error.code === "microphone_permission") {
    return {
      label: "无法使用麦克风，请在浏览器地址栏中允许麦克风权限。",
      action: "microphone" as const,
    };
  }
  if (error.code === "audio_playback") {
    return { label: "浏览器阻止了自动播放，请点击朗读。", action: "none" as const };
  }
  if (error.code === "preview_failed") {
    return { label: "音色试听失败，请选择其他音色。", action: "none" as const };
  }
  return {
    label: error.message,
    action: error.recoverable ? "connect" as const : "none" as const,
  };
}
