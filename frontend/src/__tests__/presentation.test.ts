import { describe, expect, it } from "vitest";

import {
  connectionPresentation,
  errorPresentation,
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
