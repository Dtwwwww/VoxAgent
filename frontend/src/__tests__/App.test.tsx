import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import type { KnowledgeClient } from "../knowledge/client";
import type { LocalApiClient } from "../localApi";
import type { VoiceSessionController } from "../useVoiceSession";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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
    memoryProposals: [],
    pendingToolApproval: null,
    recentToolActivity: [],
    realtime: { active: false, state: "off", provider: null, interimText: "", inputLevel: 0, fallbackReason: null, notice: null },
    microphones: [],
    selectedMicrophoneId: null,
    speechMode: "online-preferred",
    browserVoices: [],
    selectedBrowserVoiceKey: null,
    onlineSpeechNoticeAccepted: false,
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
    dismissMemoryProposal: vi.fn(),
    confirmTool: vi.fn(),
    denyTool: vi.fn(),
    clearLocalData: vi.fn(),
    startRealtimeCall: vi.fn(async () => undefined),
    stopRealtimeCall: vi.fn(async () => undefined),
    setSpeechMode: vi.fn(),
    selectMicrophone: vi.fn(),
    selectBrowserVoice: vi.fn(),
    acceptOnlineSpeechNotice: vi.fn(),
    ...overrides,
  };
}

describe("App", () => {
  it("loads sanitized tool audit only on demand and retries failures", async () => {
    const listToolAudit = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([{
        id: 9,
        request_id: 4,
        session_id: "session-safe",
        turn_id: 2,
        call_id: "call-safe",
        tool_name: "reminders.create",
        event_type: "tool.completed",
        detail: { duration_ms: 12, error_code: null, recovered_from: "retry", arguments: "SECRET" },
        created_at_utc: "2026-09-11T00:00:00Z",
      }]);
    const localApiClient = {
      getPersona: vi.fn(async () => ({ revision: 0, config: {
        name: "声灵", user_address: "用户", background: "本地伙伴", traits: "温和",
        relationship: "陪伴与助手", style: "简洁", initiative: "适度主动",
        boundaries: "尊重用户", default_reply_length: "两到四句",
      } })),
      listToolAudit,
    } as unknown as LocalApiClient;
    render(<App controller={controller()} localApiClient={localApiClient} />);

    expect(listToolAudit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(listToolAudit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "工具审计" }));
    expect(await screen.findByText("加载工具审计失败。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("reminders.create")).toBeVisible();
    expect(screen.getByText("12 ms")).toBeVisible();
    expect(screen.getByText("恢复自 retry")).toBeVisible();
    expect(document.body).not.toHaveTextContent("SECRET");
    expect(listToolAudit).toHaveBeenCalledTimes(2);
  });

  it("shows a one-shot tool approval without exposing arguments", () => {
    const session = controller({
      pendingToolApproval: {
        confirmationId: "00000000-0000-4000-8000-000000000002",
        turnId: 4,
        callId: "call-1",
        toolName: "reminders.create",
        permission: "L1",
        status: "pending",
      },
      recentToolActivity: [{
        turnId: 3,
        callId: "call-0",
        toolName: "knowledge.search",
        status: "completed",
        summary: "arguments: private path",
      }],
    });
    render(<App controller={session} />);

    const card = screen.getByRole("region", { name: "工具执行确认" });
    expect(card).toHaveTextContent("创建提醒");
    expect(card).toHaveTextContent("reminders.create");
    expect(card).toHaveTextContent("L1");
    expect(card).toHaveTextContent("仅允许本次执行");
    expect(card).not.toHaveTextContent("private path");
    fireEvent.click(within(card).getByRole("button", { name: "允许一次" }));
    expect(session.confirmTool).toHaveBeenCalledWith("00000000-0000-4000-8000-000000000002");
    expect(within(card).getByRole("button", { name: "允许一次" })).toBeDisabled();
    expect(within(card).getByRole("button", { name: "拒绝" })).toBeDisabled();
  });

  it("opens lazy settings tabs and keeps them separate from the voice picker", async () => {
    const localApiClient = {
      getPersona: vi.fn(async () => ({ revision: 0, config: {
        name: "声灵", user_address: "用户", background: "本地伙伴", traits: "温和",
        relationship: "陪伴与助手", style: "简洁", initiative: "适度主动",
        boundaries: "尊重用户", default_reply_length: "两到四句",
      } })),
      listMemories: vi.fn(async () => []),
    } as unknown as LocalApiClient;
    render(<App controller={controller()} localApiClient={localApiClient} />);

    expect(localApiClient.getPersona).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(await screen.findByRole("dialog", { name: "声灵设置" })).toBeVisible();
    expect(localApiClient.getPersona).toHaveBeenCalledOnce();
    expect(localApiClient.listMemories).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "记忆" }));
    expect(await screen.findByText("还没有长期记忆。")).toBeVisible();
    expect(localApiClient.listMemories).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog", { name: "选择音色" })).toBeNull();
  });

  it("exposes a lazy-loaded local knowledge entry", async () => {
    const knowledgeClient: KnowledgeClient = {
      listDocuments: vi.fn(async () => []),
      importDocument: vi.fn(),
      cancelImport: vi.fn(async () => undefined),
      deleteDocument: vi.fn(),
      listChunks: vi.fn(),
    };
    render(<App controller={controller()} knowledgeClient={knowledgeClient} />);

    expect(knowledgeClient.listDocuments).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "知识库" }));

    expect(await screen.findByRole("dialog", { name: "本地知识库" })).toBeVisible();
    expect(knowledgeClient.listDocuments).toHaveBeenCalledOnce();
  });

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

  it("keeps text input visible during a realtime call", () => {
    render(<App controller={controller({
      realtime: { active: true, state: "listening", provider: "browser", interimText: "", inputLevel: 0.2, fallbackReason: null, notice: null },
    })} />);

    expect(screen.getByRole("textbox", { name: "输入消息" })).toBeVisible();
    expect(screen.getByRole("button", { name: "结束通话" })).toBeVisible();
    expect(document.body).not.toHaveTextContent("松开后");
  });

  it("renders one right-side interim user bubble and removes it for a matching final message", () => {
    const interim = controller({
      messages: [],
      realtime: { active: true, state: "transcribing", provider: "browser", interimText: "今天天气", inputLevel: 0.2, fallbackReason: null, notice: null },
    });
    const view = render(<App controller={interim} />);

    const bubble = screen.getByLabelText("用户（识别中）");
    expect(bubble).toHaveAttribute("data-side", "right");
    expect(bubble).toHaveClass("message--interim");
    expect(screen.getAllByText("今天天气")).toHaveLength(1);

    view.rerender(<App controller={{
      ...interim,
      messages: [{ id: "final", role: "user", origin: "voice", text: "今天天气", status: "complete" }],
    }} />);
    expect(screen.queryByLabelText("用户（识别中）")).toBeNull();
    expect(screen.getByLabelText("用户消息")).toBeVisible();
  });

  it("shows realtime status immediately above the composer", () => {
    render(<App controller={controller({
      realtime: { active: true, state: "user_speaking", provider: "browser", interimText: "", inputLevel: 0.2, fallbackReason: null, notice: "interrupted" },
    })} />);

    const status = screen.getByRole("status");
    const composer = screen.getByLabelText("消息输入");
    expect(status).toHaveTextContent("你已打断声灵");
    expect(status.nextElementSibling).toBe(composer);
  });

  it("keeps the local fallback announcement after the engine immediately resumes listening", () => {
    const fallback = controller({
      realtime: {
        active: true,
        state: "fallback",
        provider: "local",
        interimText: "",
        inputLevel: 0.2,
        fallbackReason: "network",
        notice: null,
      },
    });
    const view = render(<App controller={fallback} />);
    expect(screen.getByRole("status")).toHaveTextContent("已切换到本地语音");

    view.rerender(<App controller={{
      ...fallback,
      realtime: { ...fallback.realtime, state: "listening" },
    }} />);

    expect(screen.getByRole("status")).toHaveTextContent("正在监听");
    expect(screen.getByRole("status")).toHaveTextContent("已切换到本地语音");
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
    expect(screen.getByRole("main")).toHaveClass("app-shell");
    expect(screen.getByLabelText("对话记录")).toHaveClass("conversation");
    expect(screen.getByLabelText("消息输入")).toHaveClass("composer");
    expect(status).toHaveAttribute("data-state", "thinking");
    expect(status).toHaveTextContent("声灵正在思考");
    expect(status.compareDocumentPosition(screen.getByLabelText("消息输入")) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  });

  it("shows local model initialization and keeps voice input unavailable until ready", () => {
    render(<App controller={controller({ connectionStatus: "initializing" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在初始化本地语音模型…");
    expect(screen.getAllByText("正在初始化本地语音模型…")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "开始实时通话" })).toBeDisabled();
    expect(screen.queryByText(/本地运行/)).toBeNull();
  });

  it("never renders or submits a private voice identifier before the catalog loads", () => {
    const session = controller({ voices: [], selectedVoice: { voiceKey: "voice-005", speed: 1 } });
    render(<App controller={session} />);

    expect(document.body).not.toHaveTextContent("voice-005");
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));
    for (const button of screen.getAllByRole("button", { name: /舒缓|自然|稍快/ })) {
      expect(button).toBeDisabled();
    }
    expect(session.selectVoice).not.toHaveBeenCalled();
  });

  it("opens and closes the accessible voice dialog", () => {
    render(<App controller={controller()} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));
    expect(screen.getByRole("dialog", { name: "选择音色" })).toBeVisible();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "选择音色" })).toBeNull();
  });

  it("lists browser voices first, routes their previews through the controller, and exposes one local fallback", () => {
    const session = controller({
      browserVoices: [
        { key: "browser-xiaoxiao", name: "微软晓晓", lang: "zh-CN", localService: false },
        { key: "browser-huihui", name: "系统慧慧", lang: "zh-TW", localService: true },
      ],
      selectedBrowserVoiceKey: "browser-xiaoxiao",
      voices: [
        ...controller().voices,
        { voice_key: "melo-native-0", display_name: "Melo 内部音色 0", description: "不可公开", gender: "neutral", is_default: false, previewable: true },
      ],
    });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));

    const dialog = screen.getByRole("dialog", { name: "选择音色" });
    const cards = within(dialog).getAllByRole("article");
    expect(cards).toHaveLength(3);
    expect(cards[0]).toHaveTextContent("微软晓晓");
    expect(cards[0]).toHaveTextContent("在线/系统");
    expect(cards[1]).toHaveTextContent("系统慧慧");
    expect(cards[2]).toHaveTextContent("声灵默认音色");
    expect(cards[2]).toHaveTextContent("本地·实时可用");
    expect(dialog).not.toHaveTextContent("Melo");
    expect(dialog).not.toHaveTextContent("melo-native-0");

    fireEvent.click(within(dialog).getByRole("button", { name: "试听 微软晓晓" }));
    expect(session.selectBrowserVoice).toHaveBeenCalledWith("browser-xiaoxiao");
    expect(session.previewVoice).toHaveBeenCalledWith("browser-xiaoxiao", 1, "browser");
  });

  it("shows the Taiwanese BreezyVoice as a selectable high-quality local voice", () => {
    const session = controller({
      voices: [
        ...controller().voices,
        {
          voice_key: "breezy_tw_female",
          display_name: "台湾腔女声",
          description: "台湾普通话女声；高质量朗读（需配置 BreezyVoice），生成较慢",
          gender: "female",
          is_default: false,
          previewable: false,
        },
      ],
    });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));

    const dialog = screen.getByRole("dialog", { name: "选择音色" });
    const card = within(dialog).getByRole("article", { name: /台湾腔女声/ });
    expect(card).toHaveTextContent("高质量朗读");
    expect(card).toHaveTextContent("本地·高质量朗读（非实时）");
    expect(within(card).getByRole("button", { name: "试听 台湾腔女声" })).toBeDisabled();
    fireEvent.click(within(card).getByRole("button", { name: /选择台湾腔女声/ }));
    expect(session.selectVoice).toHaveBeenCalledWith("breezy_tw_female", 1);
  });

  it("asks the controller to stop only its active preview when the picker closes", () => {
    const session = controller({
      browserVoices: [
        { key: "browser-xiaoxiao", name: "微软晓晓", lang: "zh-CN", localService: false },
      ],
      selectedBrowserVoiceKey: "browser-xiaoxiao",
      previewingVoiceKey: "browser-xiaoxiao",
    });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));
    fireEvent.keyDown(document, { key: "Escape" });

    expect(session.stopVoicePreview).toHaveBeenCalledOnce();
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
    const session = controller({
      browserVoices: [{ key: "browser-online", name: "在线中文音色", lang: "zh-CN", localService: false }],
      previewingVoiceKey: "default_voice",
    });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));

    fireEvent.click(screen.getByRole("button", { name: "停止试听 声灵默认音色" }));
    expect(session.stopVoicePreview).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "试听 在线中文音色" })).toBeDisabled();
  });

  it("disables voice previews while a conversation reply is playing", () => {
    const session = controller({ voiceStatus: "speaking", speakingTurnId: 3 });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: /音色：/ }));

    expect(screen.getByRole("button", { name: "试听 声灵默认音色" })).toBeDisabled();
  });

  it("exposes realtime call and message playback stop actions", () => {
    const session = controller({
      isMicrophoneActive: true,
      voiceStatus: "listening",
      speakingTurnId: 3,
      realtime: { active: true, state: "listening", provider: "browser", interimText: "", inputLevel: 0.2, fallbackReason: null, notice: null },
    });
    render(<App controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: "结束通话" }));
    expect(session.stopRealtimeCall).toHaveBeenCalledOnce();
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
