import { useEffect, useState } from "react";

import type { SpeechMode } from "../realtime/RealtimeVoiceEngine";
import type { VoiceSessionController } from "../useVoiceSession";
import { Icon } from "./Icon";

const SILENT_LEVEL = 0.01;

export function RealtimeVoiceControls({ controller }: { controller: VoiceSessionController }) {
  const [onlineNoticeAction, setOnlineNoticeAction] = useState<"start" | "switch" | null>(null);
  const [silentTooLong, setSilentTooLong] = useState(false);
  const active = controller.realtime.active;
  const silent = active && controller.realtime.inputLevel <= SILENT_LEVEL;
  const levelPercent = Math.round(Math.max(0, Math.min(1, controller.realtime.inputLevel)) * 100);
  const provider = controller.realtime.provider
    ?? (controller.speechMode === "local-only" ? "local" : "browser");

  useEffect(() => {
    if (!silent) {
      setSilentTooLong(false);
      return;
    }
    const timer = setTimeout(() => setSilentTooLong(true), 3_000);
    return () => clearTimeout(timer);
  }, [silent]);

  const changeMode = (mode: SpeechMode) => {
    if (mode === "online-preferred" && !controller.onlineSpeechNoticeAccepted) {
      setOnlineNoticeAction("switch");
      return;
    }
    controller.setSpeechMode(mode);
    setOnlineNoticeAction(null);
  };

  const toggleCall = () => {
    if (active) {
      void controller.stopRealtimeCall();
      return;
    }
    if (controller.speechMode === "online-preferred" && !controller.onlineSpeechNoticeAccepted) {
      setOnlineNoticeAction("start");
      return;
    }
    void controller.startRealtimeCall();
  };

  return <div className="realtime-controls">
    <div className="realtime-controls__primary">
      <button
        id="realtime-call-button"
        className={`realtime-call${active ? " realtime-call--active" : ""}`}
        type="button"
        aria-pressed={active}
        disabled={!active && controller.connectionStatus !== "connected"}
        onClick={toggleCall}
      >
        <Icon name={active ? "stop" : "microphone"} size={18} />
        {active ? "结束通话" : "开始实时通话"}
      </button>
      <span className="realtime-provider" data-provider={provider}>
        {provider === "browser" ? "浏览器在线语音" : "本地离线语音"}
      </span>
    </div>

    <div className="realtime-controls__settings">
      <label className="microphone-select">
        <span>麦克风</span>
        <select
          aria-label="麦克风"
          value={controller.selectedMicrophoneId ?? ""}
          onChange={(event) => {
            if (event.target.value) void controller.selectMicrophone(event.target.value);
          }}
        >
          {controller.selectedMicrophoneId === null && <option value="">浏览器默认麦克风</option>}
          {controller.microphones.map((device) => <option key={device.deviceId} value={device.deviceId}>
            {device.label || "未命名麦克风"}{device.virtual ? "（虚拟）" : ""}
          </option>)}
        </select>
      </label>

      <fieldset className="speech-mode" role="radiogroup" aria-label="语音模式">
        <legend>语音模式</legend>
        <label>
          <input
            type="radio"
            name="speech-mode"
            value="online-preferred"
            checked={controller.speechMode === "online-preferred"}
            onChange={() => changeMode("online-preferred")}
          />
          <span>在线优先</span>
        </label>
        <label>
          <input
            type="radio"
            name="speech-mode"
            value="local-only"
            checked={controller.speechMode === "local-only"}
            onChange={() => changeMode("local-only")}
          />
          <span>仅本地</span>
        </label>
      </fieldset>

      <div className="input-meter">
        <span>麦克风音量</span>
        <meter
          aria-label="麦克风音量"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={levelPercent}
          aria-valuetext={`${levelPercent}%`}
          min={0}
          max={1}
          low={0.08}
          high={0.55}
          optimum={0.72}
          value={controller.realtime.inputLevel}
        />
        <span className="input-meter__value" aria-label="麦克风音量数值">{levelPercent}%</span>
      </div>
    </div>

    {onlineNoticeAction !== null && <aside className="online-speech-notice" aria-label="在线语音说明">
      <p>浏览器或系统平台可能在线处理麦克风语音。项目不需要语音 API Key，文字输入仍保持本地处理。</p>
      <div>
        <button type="button" onClick={() => {
          controller.acceptOnlineSpeechNotice();
          setOnlineNoticeAction(null);
          if (onlineNoticeAction === "switch") controller.setSpeechMode("online-preferred");
          else void controller.startRealtimeCall();
        }}>{onlineNoticeAction === "switch" ? "同意并切换" : "同意并开始"}</button>
        <button type="button" onClick={() => changeMode("local-only")}>改用仅本地</button>
      </div>
    </aside>}

    {silentTooLong && <p className="microphone-diagnostic" role="status">
      3 秒未检测到声音，请切换到 Realtek 麦克风
    </p>}
  </div>;
}
