import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { VoiceSessionController } from "../useVoiceSession";
import { RealtimeVoiceControls } from "./RealtimeVoiceControls";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function controller(overrides: Partial<VoiceSessionController> = {}): VoiceSessionController {
  return {
    messages: [],
    voices: [],
    selectedVoice: null,
    connectionStatus: "connected",
    voiceStatus: "idle",
    error: null,
    isMicrophoneActive: false,
    modelId: "qwen",
    offline: true,
    speakingTurnId: null,
    previewingVoiceKey: null,
    memoryProposals: [],
    realtime: {
      active: false,
      state: "off",
      provider: "browser",
      interimText: "",
      inputLevel: 0.42,
      fallbackReason: null,
      notice: null,
    },
    microphones: [
      { deviceId: "realtek", groupId: "physical", label: "麦克风阵列 (Realtek(R) Audio)", virtual: false, preferred: true, browserDefault: false },
      { deviceId: "todesk", groupId: "virtual", label: "ToDesk Virtual Audio", virtual: true, preferred: false, browserDefault: false },
    ],
    selectedMicrophoneId: "realtek",
    speechMode: "online-preferred",
    browserVoices: [],
    selectedBrowserVoiceKey: null,
    onlineSpeechNoticeAccepted: true,
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

describe("RealtimeVoiceControls", () => {
  it("exposes accessible call, device, mode, level, and provider controls", () => {
    const session = controller();
    const view = render(<RealtimeVoiceControls controller={session} />);

    expect(screen.getByRole("button", { name: "开始实时通话" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "麦克风" })).toHaveValue("realtek");
    expect(screen.getByRole("option", { name: /ToDesk.*虚拟/ })).toBeVisible();
    expect(screen.getByRole("radiogroup", { name: "语音模式" })).toBeVisible();
    expect(screen.getByRole("meter", { name: "麦克风音量" })).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText("浏览器在线语音")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "开始实时通话" }));
    expect(session.startRealtimeCall).toHaveBeenCalledOnce();
    expect(session.stopRealtimeCall).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("combobox", { name: "麦克风" }), { target: { value: "todesk" } });
    expect(session.selectMicrophone).toHaveBeenCalledWith("todesk");
    fireEvent.click(screen.getByRole("radio", { name: "仅本地" }));
    expect(session.setSpeechMode).toHaveBeenCalledWith("local-only");

    const activeSession = controller({
      realtime: { ...session.realtime, active: true, state: "listening", provider: "local" },
    });
    view.rerender(<RealtimeVoiceControls controller={activeSession} />);
    fireEvent.click(screen.getByRole("button", { name: "结束通话" }));
    expect(activeSession.stopRealtimeCall).toHaveBeenCalledOnce();
    expect(activeSession.startRealtimeCall).not.toHaveBeenCalled();
    expect(screen.getByText("本地离线语音")).toBeVisible();
  });

  it("requires online acknowledgement once and keeps local-only opt-out stopped", () => {
    const session = controller({ onlineSpeechNoticeAccepted: false });
    const view = render(<RealtimeVoiceControls controller={session} />);

    fireEvent.click(screen.getByRole("button", { name: "开始实时通话" }));
    expect(screen.getByText(/浏览器或系统平台可能在线处理麦克风语音/)).toBeVisible();
    expect(session.startRealtimeCall).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "改用仅本地" }));
    expect(session.setSpeechMode).toHaveBeenCalledWith("local-only");
    expect(session.acceptOnlineSpeechNotice).not.toHaveBeenCalled();
    expect(session.startRealtimeCall).not.toHaveBeenCalled();

    view.rerender(<RealtimeVoiceControls controller={session} />);
    fireEvent.click(screen.getByRole("button", { name: "开始实时通话" }));
    fireEvent.click(screen.getByRole("button", { name: "同意并开始" }));
    expect(session.acceptOnlineSpeechNotice).toHaveBeenCalledOnce();
    expect(session.startRealtimeCall).toHaveBeenCalledOnce();
  });

  it("starts local-only calls without an online notice", () => {
    const session = controller({ speechMode: "local-only", onlineSpeechNoticeAccepted: false });
    render(<RealtimeVoiceControls controller={session} />);

    fireEvent.click(screen.getByRole("button", { name: "开始实时通话" }));
    expect(session.startRealtimeCall).toHaveBeenCalledOnce();
    expect(screen.queryByText(/在线处理麦克风语音/)).toBeNull();
  });

  it("switches an accepted active call immediately", () => {
    const session = controller({
      speechMode: "local-only",
      realtime: { active: true, state: "listening", provider: "local", interimText: "", inputLevel: 0.2, fallbackReason: null, notice: null },
    });
    render(<RealtimeVoiceControls controller={session} />);

    fireEvent.click(screen.getByRole("radio", { name: "在线优先" }));

    expect(session.setSpeechMode).toHaveBeenCalledWith("online-preferred");
    expect(session.startRealtimeCall).not.toHaveBeenCalled();
  });

  it("requires online acknowledgement before switching an active local call", () => {
    const session = controller({
      speechMode: "local-only",
      onlineSpeechNoticeAccepted: false,
      realtime: { active: true, state: "listening", provider: "local", interimText: "", inputLevel: 0.2, fallbackReason: null, notice: null },
    });
    render(<RealtimeVoiceControls controller={session} />);

    fireEvent.click(screen.getByRole("radio", { name: "在线优先" }));
    expect(session.setSpeechMode).not.toHaveBeenCalled();
    expect(screen.getByText(/浏览器或系统平台可能在线处理麦克风语音/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "同意并切换" }));
    expect(session.acceptOnlineSpeechNotice).toHaveBeenCalledOnce();
    expect(session.setSpeechMode).toHaveBeenCalledWith("online-preferred");
    expect(session.startRealtimeCall).not.toHaveBeenCalled();
  });

  it("shows a non-blocking Realtek diagnostic after three silent seconds", () => {
    vi.useFakeTimers();
    const session = controller({
      realtime: {
        active: true,
        state: "listening",
        provider: "browser",
        interimText: "",
        inputLevel: 0,
        fallbackReason: null,
        notice: null,
      },
    });
    render(<RealtimeVoiceControls controller={session} />);

    expect(screen.queryByText("3 秒未检测到声音，请切换到 Realtek 麦克风")).toBeNull();
    act(() => vi.advanceTimersByTime(3_000));
    expect(screen.getByText("3 秒未检测到声音，请切换到 Realtek 麦克风")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "麦克风" })).toBeEnabled();
    fireEvent.change(screen.getByRole("combobox", { name: "麦克风" }), { target: { value: "todesk" } });
    expect(session.selectMicrophone).toHaveBeenCalledWith("todesk");
    expect(screen.getByRole("button", { name: "结束通话" })).toBeEnabled();
  });

  it("counts continuous below-threshold level fluctuations as one silent period", () => {
    vi.useFakeTimers();
    const session = controller({
      realtime: {
        active: true,
        state: "listening",
        provider: "browser",
        interimText: "",
        inputLevel: 0.001,
        fallbackReason: null,
        notice: null,
      },
    });
    const view = render(<RealtimeVoiceControls controller={session} />);

    act(() => vi.advanceTimersByTime(1_000));
    view.rerender(<RealtimeVoiceControls controller={{
      ...session,
      realtime: { ...session.realtime, inputLevel: 0.004 },
    }} />);
    act(() => vi.advanceTimersByTime(1_000));
    view.rerender(<RealtimeVoiceControls controller={{
      ...session,
      realtime: { ...session.realtime, inputLevel: 0.002 },
    }} />);
    act(() => vi.advanceTimersByTime(1_000));

    expect(screen.getByText("3 秒未检测到声音，请切换到 Realtek 麦克风")).toBeVisible();
  });
});
