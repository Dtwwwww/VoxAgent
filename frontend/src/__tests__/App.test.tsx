import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { VoiceSessionController } from "../useVoiceSession";

function controller(): VoiceSessionController {
  return {
    messages: [
      { id: "a", turnId: 3, role: "assistant", origin: "assistant", text: "你好", status: "complete" },
      { id: "u", role: "user", origin: "voice", text: "语音内容", status: "complete" },
      { id: "c", turnId: 4, role: "assistant", origin: "assistant", text: "中断", status: "cancelled" },
      { id: "s", turnId: 5, role: "assistant", origin: "assistant", text: "流式", status: "streaming" },
    ],
    voices: [{ voice_key: "default_voice", display_name: "声灵默认音色", description: "自然清晰，适合日常对话", gender: "neutral", is_default: true, previewable: true }],
    selectedVoice: { voiceKey: "default_voice", speed: 1 }, connectionStatus: "connected", voiceStatus: "idle", error: null,
    isMicrophoneActive: false, modelId: "qwen", offline: true, speakTextReplies: false,
    connect: vi.fn(), disconnect: vi.fn(async () => undefined), startMicrophone: vi.fn(async () => undefined), stopMicrophone: vi.fn(async () => undefined),
    submitText: vi.fn(), speakMessage: vi.fn(), selectVoice: vi.fn(), previewVoice: vi.fn(), cancelActive: vi.fn(), setSpeakTextReplies: vi.fn(),
  };
}

describe("App", () => {
  it("renders accessible conversation labels, settings, and usable text controls", () => {
    const session = controller();
    render(<App controller={session} />);
    expect(screen.getAllByText("Agent（声灵）")[1].closest("article")?.getAttribute("data-side")).toBe("left");
    expect(screen.getByText("用户").closest("article")?.getAttribute("data-side")).toBe("right");
    expect(screen.getByText("语音输入")).not.toBeNull();
    expect(screen.getByText("已停止")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: "复制" })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: "朗读" })).toHaveLength(1);
    expect(screen.getByText("本地运行 · qwen")).not.toBeNull();
    expect(screen.getAllByText("声灵默认音色")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "慢速 0.8×" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "试听" })).not.toBeNull();
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(session.submitText).toHaveBeenCalledWith("hello");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(session.cancelActive).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("checkbox", { name: "文字回复自动朗读" }));
    expect(session.setSpeakTextReplies).toHaveBeenCalledWith(true);
  });

  it("keeps a session connected when controller state creates a new object", () => {
    const session = controller();
    const view = render(<App controller={session} />);
    expect(session.connect).toHaveBeenCalledOnce();
    view.rerender(<App controller={{ ...session, messages: [...session.messages] }} />);
    expect(session.connect).toHaveBeenCalledOnce();
    expect(session.disconnect).not.toHaveBeenCalled();
    view.unmount();
    expect(session.disconnect).toHaveBeenCalledOnce();
  });
});
