import { describe, expect, it } from "vitest";

import {
  connectionPresentation,
  errorPresentation,
  realtimeVoiceStatusPresentation,
  voiceStatusPresentation,
} from "../presentation";

describe("presentation", () => {
  it("describes local connection states", () => {
    expect(connectionPresentation("connected", "qwen2.5:7b").label).toBe("本地运行 · qwen2.5:7b");
    expect(connectionPresentation("connecting", null).label).toBe("正在连接本地服务");
    expect(connectionPresentation("initializing", null).label).toBe("正在初始化本地语音模型…");
    expect(connectionPresentation("disconnected", null).label).toBe("本地服务未连接");
  });

  it("keeps idle visually empty and maps active voice states", () => {
    expect(voiceStatusPresentation("idle")).toBeNull();
    expect(voiceStatusPresentation("listening")?.label).toBe("正在聆听，点击停止");
    expect(voiceStatusPresentation("thinking")?.label).toBe("声灵正在思考");
  });

  it("maps every realtime voice state and gives interruption priority", () => {
    expect(realtimeVoiceStatusPresentation("off")).toBeNull();
    expect(realtimeVoiceStatusPresentation("connecting")?.label).toBe("正在连接");
    expect(realtimeVoiceStatusPresentation("listening")?.label).toBe("正在监听");
    expect(realtimeVoiceStatusPresentation("user_speaking")?.label).toBe("检测到你在说话");
    expect(realtimeVoiceStatusPresentation("transcribing")?.label).toBe("正在识别");
    expect(realtimeVoiceStatusPresentation("thinking")?.label).toBe("声灵正在思考");
    expect(realtimeVoiceStatusPresentation("responding")?.label).toBe("声灵正在回复");
    expect(realtimeVoiceStatusPresentation("speaking")?.label).toBe("声灵正在朗读");
    expect(realtimeVoiceStatusPresentation("fallback")?.label).toBe("已切换到本地语音");
    expect(realtimeVoiceStatusPresentation("user_speaking", "interrupted")?.label).toBe("你已打断声灵");
  });

  it("classifies connection, microphone, and playback errors", () => {
    expect(errorPresentation({ code: "connection", message: "raw", recoverable: true })).toMatchObject({
      action: "connect",
      label: "本地服务未启动，请启动后重试。",
    });
    expect(errorPresentation({ code: "microphone_permission", message: "raw", recoverable: true })).toMatchObject({
      action: "microphone",
      label: "无法使用麦克风，请在浏览器地址栏中允许麦克风权限。",
    });
    expect(errorPresentation({ code: "audio_playback", message: "raw", recoverable: true })).toMatchObject({
      action: "none",
      label: "浏览器阻止了自动播放，请点击朗读。",
    });
  });
});
