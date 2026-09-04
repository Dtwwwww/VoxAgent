import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { VoiceSessionController } from "../useVoiceSession";

afterEach(cleanup);

function controller(overrides: Partial<VoiceSessionController> = {}): VoiceSessionController {
  return {
    messages: [
      { id: "a", turnId: 3, role: "assistant", origin: "assistant", text: "你好", status: "complete" },
      { id: "u", role: "user", origin: "voice", text: "语音内容", status: "complete" },
      { id: "c", turnId: 4, role: "assistant", origin: "assistant", text: "中断", status: "cancelled" },
      { id: "s", turnId: 5, role: "assistant", origin: "assistant", text: "流式", status: "streaming" },
    ],
    voices: [{ voice_key: "default_voice", display_name: "声灵默认音色", description: "自然清晰，适合日常对话", gender: "neutral", is_default: true, previewable: true }],
    selectedVoice: { voiceKey: "default_voice", speed: 1 },
    connectionStatus: "connected",
    voiceStatus: "idle",
    error: null,
    isMicrophoneActive: false,
    modelId: "qwen",
    offline: true,
    speakingTurnId: null,
    previewingVoiceKey: null,
    connect: vi.fn(),
    disconnect: vi.fn(async () => undefined),
    startMicrophone: vi.fn(async () => undefined),
    stopMicrophone: vi.fn(async () => undefined),
    submitText: vi.fn(),
    speakMessage: vi.fn(),
    stopSpeaking: vi.fn(),
    selectVoice: vi.fn(),
    previewVoice: vi.fn(),
    stopVoicePreview: vi.fn(),
    cancelActive: vi.fn(),
    ...overrides,
  };
}

describe("App", () => {
  it("renders the approved shell and message identities", () => {
    const session = controller();
    render(<App controller={session} />);

    expect(screen.getByRole("heading", { name: "声灵" })).toBeVisible();
    expect(screen.getByText("你的本地语音 AI 助手")).toBeVisible();
    expect(screen.getByText("本地运行 · qwen")).toBeVisible();
    expect(screen.getAllByLabelText("声灵消息")[0]).toHaveAttribute("data-side", "left");
    expect(screen.getByLabelText("用户消息")).toHaveAttribute("data-side", "right");
    expect(screen.getByText("用户")).toBeVisible();
    expect(screen.getByText("语音输入")).toBeVisible();
    expect(screen.getByText("已停止")).toBeVisible();
    expect(screen.getByRole("button", { name: /音色：/ })).toBeVisible();
  });

  it("shows the welcome state and submits a text suggestion silently", () => {
    const session = controller({ messages: [] });
    render(<App controller={session} />);

    expect(screen.getByText("你好，我是声灵")).toBeVisible();
    expect(screen.getByText("可以输入文字，或者点击麦克风和我说话。")).toBeVisible();
    expect(screen.getByRole("button", { name: "介绍一下你自己" })).toBeVisible();
    expect(screen.getByRole("button", { name: "帮我整理今天的计划" })).toBeVisible();
    expect(screen.getByRole("button", { name: "开始语音对话" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "介绍一下你自己" }));
    expect(session.submitText).toHaveBeenCalledWith("介绍一下你自己");
    expect(session.speakMessage).not.toHaveBeenCalled();
  });

  it("supports message copy and repeatable manual speech", () => {
    const session = controller();
    render(<App controller={session} />);

    expect(screen.getAllByRole("button", { name: "复制" })).toHaveLength(4);
    const speak = screen.getByRole("button", { name: "朗读" });
    fireEvent.click(speak);
    fireEvent.click(speak);
    expect(session.speakMessage).toHaveBeenNthCalledWith(1, 3);
    expect(session.speakMessage).toHaveBeenNthCalledWith(2, 3);
  });

  it("preserves manual scroll position and offers a return-to-bottom action", () => {
    const session = controller();
    render(<App controller={session} />);
    const conversation = screen.getByLabelText("对话记录");
    Object.defineProperties(conversation, {
      scrollHeight: { configurable: true, value: 600 },
      clientHeight: { configurable: true, value: 200 },
      scrollTop: { configurable: true, value: 0, writable: true },
    });

    fireEvent.scroll(conversation);
    expect(screen.getByRole("button", { name: "回到底部" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "回到底部" }));
    expect(conversation.scrollTop).toBe(600);
    expect(screen.queryByRole("button", { name: "回到底部" })).toBeNull();
  });

  it("keeps text controls usable", () => {
    const session = controller();
    render(<App controller={session} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(session.submitText).toHaveBeenCalledWith("hello");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(session.cancelActive).toHaveBeenCalledOnce();
    expect(screen.queryByRole("checkbox", { name: "文字回复自动朗读" })).toBeNull();
  });

  it("keeps composition input intact and disables an empty send action", () => {
    const session = controller();
    render(<App controller={session} />);
    const input = screen.getByRole("textbox");

    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: "输入中" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(session.submitText).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(session.submitText).toHaveBeenCalledWith("输入中");
  });

  it("shows active voice state immediately above the composer", () => {
    render(<App controller={controller({ voiceStatus: "thinking" })} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("声灵正在思考");
    expect(status.compareDocumentPosition(screen.getByLabelText("消息输入")) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });

  it("opens and closes the accessible voice dialog", () => {
    render(<App controller={controller()} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));
    expect(screen.getByRole("dialog", { name: "选择音色" })).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "选择音色" })).toBeNull();
  });

  it("offers only valid recovery actions for connection and microphone errors", () => {
    const connection = controller({ error: { code: "connection", message: "raw", recoverable: true } });
    const view = render(<App controller={connection} />);
    expect(screen.getByText("本地服务未启动，请启动后重试。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试连接" }));
    expect(connection.connect).toHaveBeenCalledTimes(2);

    const microphone = controller({ error: { code: "microphone_permission", message: "raw", recoverable: true } });
    view.rerender(<App controller={microphone} />);
    expect(screen.getByText("无法使用麦克风，请在浏览器地址栏中允许麦克风权限。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试麦克风" }));
    expect(microphone.startMicrophone).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "重试连接" })).toBeNull();
  });

  it("keeps voice previews mutually exclusive", () => {
    const voices = [
      ...controller().voices,
      { voice_key: "engine-002", display_name: "清亮音色", description: "清晰明快", gender: "female", is_default: false, previewable: true },
    ];
    const session = controller({ voices, previewingVoiceKey: "default_voice" });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));

    fireEvent.click(screen.getByRole("button", { name: "停止试听 声灵默认音色" }));
    expect(session.stopVoicePreview).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "试听 清亮音色" })).toBeDisabled();
  });

  it("exposes recording and message playback stop actions", () => {
    const session = controller({ isMicrophoneActive: true, voiceStatus: "listening", speakingTurnId: 3 });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: "结束录音" }));
    expect(session.stopMicrophone).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "停止朗读" }));
    expect(session.stopSpeaking).toHaveBeenCalledWith(3);
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
