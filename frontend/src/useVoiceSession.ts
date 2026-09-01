import { useCallback, useEffect, useRef, useState } from "react";

import { MicrophoneCapture } from "./audio/capture";
import { AudioPlayback } from "./audio/playback";
import { type ServerEvent, type VoiceInfo, type VoiceSpeed, parseServerEventJson } from "./protocol";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";
export type VoiceStatus = "idle" | "listening" | "responding" | "speaking";
export type MessageStatus = "streaming" | "complete" | "cancelled";

export interface ConversationMessage {
  id: string;
  turnId?: number;
  role: "user" | "assistant";
  origin: "text" | "voice" | "assistant";
  text: string;
  status: MessageStatus;
}

export interface SessionError {
  message: string;
  recoverable: boolean;
  code?: string;
}

export interface SelectedVoice {
  voiceKey: string;
  speed: VoiceSpeed;
}

export interface VoiceSessionOptions {
  url: string;
}

export interface VoiceSessionController {
  messages: ConversationMessage[];
  voices: VoiceInfo[];
  selectedVoice: SelectedVoice | null;
  connectionStatus: ConnectionStatus;
  voiceStatus: VoiceStatus;
  error: SessionError | null;
  isMicrophoneActive: boolean;
  connect(): void;
  disconnect(): Promise<void>;
  startMicrophone(): Promise<void>;
  stopMicrophone(): Promise<void>;
  submitText(text: string): void;
  speakMessage(turnId: number): void;
  selectVoice(voiceKey: string, speed: VoiceSpeed): void;
  previewVoice(voiceKey: string, speed: VoiceSpeed): void;
  cancelActive(): void;
}

type AudioMetadata =
  | { kind: "turn"; turnId: number; sequence: number; sampleRate: number; byteLength: number; valid: boolean }
  | { kind: "preview"; previewId: number; sampleRate: number; byteLength: number; valid: boolean };

const VOICE_STORAGE_KEY = "voxagent.voice";

function storedVoice(): SelectedVoice | null {
  try {
    const value = JSON.parse(localStorage.getItem(VOICE_STORAGE_KEY) ?? "null") as Partial<SelectedVoice> | null;
    if (value && typeof value.voiceKey === "string" && (value.speed === 0.8 || value.speed === 1 || value.speed === 1.2)) {
      return { voiceKey: value.voiceKey, speed: value.speed };
    }
  } catch { /* Ignore damaged local preferences. */ }
  return null;
}

function binaryData(value: unknown): value is ArrayBuffer | Blob {
  return value instanceof ArrayBuffer || value instanceof Blob;
}

export function useVoiceSession({ url }: VoiceSessionOptions): VoiceSessionController {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [voices, setVoices] = useState<VoiceInfo[]>([]);
  const [selectedVoice, setSelectedVoice] = useState<SelectedVoice | null>(() => storedVoice());
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("disconnected");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>("idle");
  const [error, setError] = useState<SessionError | null>(null);
  const [isMicrophoneActive, setMicrophoneActive] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const captureRef = useRef<MicrophoneCapture | null>(null);
  const captureStartRef = useRef<Promise<void> | null>(null);
  const captureLifecycleRef = useRef(0);
  const playbackRef = useRef(new AudioPlayback());
  const pendingAudioRef = useRef<AudioMetadata | null>(null);
  const discardedPayloadsRef = useRef(0);
  const assistantDraftsRef = useRef(new Map<number, string>());
  const sessionIdRef = useRef<string | null>(null);
  const maximumTurnIdRef = useRef(0);
  const blockedThroughTurnRef = useRef(0);
  const cancelledTurnsRef = useRef(new Set<number>());
  const allowedReplayTurnsRef = useRef(new Set<number>());
  const previewRequestsRef = useRef<Array<{ valid: boolean }>>([]);
  const messageIdRef = useRef(0);

  const nextId = useCallback((prefix: string) => `${prefix}-${++messageIdRef.current}`, []);

  const send = useCallback((event: object): boolean => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(event));
    return true;
  }, []);

  const resetSessionTracking = useCallback(() => {
    pendingAudioRef.current = null;
    discardedPayloadsRef.current = 0;
    assistantDraftsRef.current.clear();
    sessionIdRef.current = null;
    maximumTurnIdRef.current = 0;
    blockedThroughTurnRef.current = 0;
    cancelledTurnsRef.current.clear();
    allowedReplayTurnsRef.current.clear();
    previewRequestsRef.current = [];
  }, []);

  const stopLocalResources = useCallback(async () => {
    captureLifecycleRef.current += 1;
    setMicrophoneActive(false);
    setVoiceStatus("idle");
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) await capture.stop();
    await playbackRef.current.close();
    resetSessionTracking();
  }, [resetSessionTracking]);

  const failProtocol = useCallback(() => {
    setError({ code: "invalid_server_event", message: "服务器返回了无效数据，请重试", recoverable: true });
  }, []);

  const consumeBinary = useCallback(async (data: ArrayBuffer | Blob) => {
    if (discardedPayloadsRef.current > 0) {
      discardedPayloadsRef.current -= 1;
      return;
    }
    const metadata = pendingAudioRef.current;
    pendingAudioRef.current = null;
    if (!metadata) {
      failProtocol();
      return;
    }
    const bytes = data instanceof Blob ? await data.arrayBuffer() : data;
    if (bytes.byteLength !== metadata.byteLength) {
      failProtocol();
      return;
    }
    if (!metadata.valid) return;
    try {
      if (metadata.kind === "turn") {
        await playbackRef.current.enqueue({ kind: "turn", turnId: metadata.turnId, sequence: metadata.sequence, sampleRate: metadata.sampleRate }, bytes);
      } else {
        await playbackRef.current.enqueue({ kind: "preview", previewId: metadata.previewId, sampleRate: metadata.sampleRate }, bytes);
      }
      setVoiceStatus("speaking");
    } catch {
      setError({ code: "audio_playback", message: "音频播放失败，请重试", recoverable: true });
    }
  }, [failProtocol]);

  const handleServerEvent = useCallback((event: ServerEvent) => {
    if ("turn_id" in event) maximumTurnIdRef.current = Math.max(maximumTurnIdRef.current, event.turn_id);
    switch (event.type) {
      case "session.ready":
        sessionIdRef.current = event.session_id;
        assistantDraftsRef.current.clear();
        maximumTurnIdRef.current = 0;
        blockedThroughTurnRef.current = 0;
        cancelledTurnsRef.current.clear();
        allowedReplayTurnsRef.current.clear();
        previewRequestsRef.current = [];
        setConnectionStatus("connected");
        break;
      case "voices.available": {
        setVoices(event.voices);
        setSelectedVoice((current) => current && event.voices.some((voice) => voice.voice_key === current.voiceKey)
          ? current
          : (() => {
              const fallback = event.voices.find((voice) => voice.is_default) ?? event.voices[0];
              return fallback ? { voiceKey: fallback.voice_key, speed: 1 } : null;
            })());
        break;
      }
      case "voice.selected":
        setSelectedVoice({ voiceKey: event.voice_key, speed: event.speed });
        break;
      case "voice.preview.chunk":
        pendingAudioRef.current = {
          kind: "preview",
          previewId: event.preview_id,
          sampleRate: event.sample_rate,
          byteLength: event.byte_length,
          valid: previewRequestsRef.current.shift()?.valid ?? true,
        };
        break;
      case "tts.chunk": {
        const valid = allowedReplayTurnsRef.current.has(event.turn_id)
          || (event.turn_id > blockedThroughTurnRef.current && !cancelledTurnsRef.current.has(event.turn_id));
        pendingAudioRef.current = { kind: "turn", turnId: event.turn_id, sequence: event.sequence, sampleRate: event.sample_rate, byteLength: event.byte_length, valid };
        break;
      }
      case "vad.started":
        blockedThroughTurnRef.current = Math.max(blockedThroughTurnRef.current, event.turn_id - 1);
        allowedReplayTurnsRef.current.clear();
        if (pendingAudioRef.current?.kind === "turn") pendingAudioRef.current.valid = false;
        playbackRef.current.stopConversation();
        setVoiceStatus("listening");
        break;
      case "vad.stopped":
        setVoiceStatus("responding");
        break;
      case "asr.final":
        setMessages((current) => [...current, { id: nextId("voice-user"), turnId: event.turn_id, role: "user", origin: "voice", text: event.text, status: "complete" }]);
        setVoiceStatus("responding");
        break;
      case "assistant.delta":
        setMessages((current) => {
          const draftId = assistantDraftsRef.current.get(event.turn_id);
          const index = draftId ? current.findIndex((message) => message.id === draftId) : -1;
          if (index < 0) {
            const id = nextId("assistant");
            assistantDraftsRef.current.set(event.turn_id, id);
            return [...current, { id, turnId: event.turn_id, role: "assistant", origin: "assistant", text: event.delta, status: "streaming" }];
          }
          return current.map((message, position) => position === index ? { ...message, text: message.text + event.delta } : message);
        });
        setVoiceStatus("responding");
        break;
      case "assistant.done":
        {
          const draftId = assistantDraftsRef.current.get(event.turn_id);
          setMessages((current) => current.map((message) => message.id === draftId ? { ...message, status: "complete" } : message));
        }
        assistantDraftsRef.current.delete(event.turn_id);
        setVoiceStatus("idle");
        break;
      case "turn.cancelled":
        cancelledTurnsRef.current.add(event.turn_id);
        allowedReplayTurnsRef.current.delete(event.turn_id);
        if (pendingAudioRef.current?.kind === "turn" && pendingAudioRef.current.turnId === event.turn_id) pendingAudioRef.current.valid = false;
        playbackRef.current.stopTurn(event.turn_id);
        {
          const draftId = assistantDraftsRef.current.get(event.turn_id);
          setMessages((current) => current.map((message) => message.id === draftId ? { ...message, status: "cancelled" } : message));
        }
        assistantDraftsRef.current.delete(event.turn_id);
        setVoiceStatus("idle");
        break;
      case "error":
        if (previewRequestsRef.current.length > 0) previewRequestsRef.current.shift();
        setError({ code: event.code, message: event.message, recoverable: event.recoverable });
        break;
    }
  }, [nextId]);

  const connect = useCallback(() => {
    if (socketRef.current && socketRef.current.readyState < WebSocket.CLOSING) return;
    setError(null);
    setConnectionStatus("connecting");
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;
    socket.onopen = () => {
      if (socketRef.current !== socket) return;
      setConnectionStatus("connected");
      socket.send(JSON.stringify({ type: "session.start" }));
    };
    socket.onmessage = (message) => {
      if (socketRef.current !== socket) return;
      if (binaryData(message.data)) {
        void consumeBinary(message.data);
        return;
      }
      if (typeof message.data !== "string") {
        failProtocol();
        return;
      }
      try {
        const event = parseServerEventJson(message.data);
        if ("session_id" in event && sessionIdRef.current !== null && event.session_id !== sessionIdRef.current) {
          failProtocol();
          return;
        }
        const pending = pendingAudioRef.current;
        const audioMetadata = event.type === "tts.chunk" || event.type === "voice.preview.chunk";
        if (discardedPayloadsRef.current > 0) {
          if (audioMetadata) discardedPayloadsRef.current += 1;
          failProtocol();
          return;
        }
        const interruptsPendingTurn = pending?.kind === "turn" && (
          (event.type === "turn.cancelled" && event.turn_id === pending.turnId)
          || event.type === "vad.started"
        );
        if (pending && audioMetadata) {
          pending.valid = false;
          pendingAudioRef.current = null;
          discardedPayloadsRef.current = 2;
          failProtocol();
          return;
        }
        if (pending && !interruptsPendingTurn) {
          pending.valid = false;
          pendingAudioRef.current = null;
          discardedPayloadsRef.current = 1;
          failProtocol();
        }
        handleServerEvent(event);
      } catch {
        failProtocol();
      }
    };
    socket.onerror = () => {
      if (socketRef.current === socket) setError({ code: "connection", message: "无法连接语音服务，请重试", recoverable: true });
    };
    socket.onclose = () => {
      if (socketRef.current !== socket) return;
      socketRef.current = null;
      void stopLocalResources();
      setConnectionStatus("disconnected");
    };
  }, [consumeBinary, failProtocol, handleServerEvent, stopLocalResources, url]);

  const startMicrophone = useCallback((): Promise<void> => {
    if (captureRef.current) return Promise.resolve();
    if (captureStartRef.current) return captureStartRef.current;
    const lifecycle = captureLifecycleRef.current;
    const capture = new MicrophoneCapture((frame) => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) socket.send(frame);
    });
    let operation!: Promise<void>;
    operation = (async () => {
      try {
        await capture.start();
        if (lifecycle !== captureLifecycleRef.current) {
          await capture.stop();
          return;
        }
        captureRef.current = capture;
        setMicrophoneActive(true);
        setError(null);
        setVoiceStatus("listening");
      } catch {
        await capture.stop();
        if (lifecycle === captureLifecycleRef.current) {
          setError({ code: "microphone_permission", message: "无法使用麦克风，请允许权限后重试；文字输入仍可使用", recoverable: true });
          setMicrophoneActive(false);
        }
      } finally {
        if (captureStartRef.current === operation) captureStartRef.current = null;
      }
    })();
    captureStartRef.current = operation;
    return operation;
  }, []);

  const stopMicrophone = useCallback(async () => {
    captureLifecycleRef.current += 1;
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) await capture.stop();
    setMicrophoneActive(false);
    setVoiceStatus("responding");
    send({ type: "audio.commit" });
  }, [send]);

  const submitText = useCallback((rawText: string) => {
    const text = rawText.trim();
    const length = [...text].length;
    if (length < 1 || length > 4000) {
      setError({ code: "invalid_text", message: "请输入 1 到 4000 个字符", recoverable: true });
      return;
    }
    if (pendingAudioRef.current?.kind === "turn") pendingAudioRef.current.valid = false;
    blockedThroughTurnRef.current = Math.max(blockedThroughTurnRef.current, maximumTurnIdRef.current);
    allowedReplayTurnsRef.current.clear();
    playbackRef.current.stopConversation();
    setMessages((current) => [...current, { id: nextId("text-user"), role: "user", origin: "text", text, status: "complete" }]);
    setVoiceStatus("responding");
    send({ type: "text.submit", text, speak_response: false });
  }, [nextId, send]);

  const speakMessage = useCallback((turnId: number) => {
    allowedReplayTurnsRef.current.add(turnId);
    send({ type: "assistant.speak", turn_id: turnId });
  }, [send]);

  const selectVoice = useCallback((voiceKey: string, speed: VoiceSpeed) => {
    const selection = { voiceKey, speed };
    setSelectedVoice(selection);
    localStorage.setItem(VOICE_STORAGE_KEY, JSON.stringify(selection));
    send({ type: "voice.select", voice_key: voiceKey, speed });
  }, [send]);

  const previewVoice = useCallback((voiceKey: string, speed: VoiceSpeed) => {
    if (pendingAudioRef.current?.kind === "preview") pendingAudioRef.current.valid = false;
    for (const request of previewRequestsRef.current) request.valid = false;
    playbackRef.current.stopPreview();
    if (send({ type: "voice.preview", voice_key: voiceKey, speed })) previewRequestsRef.current.push({ valid: true });
  }, [send]);

  const cancelActive = useCallback(() => {
    if (pendingAudioRef.current) pendingAudioRef.current.valid = false;
    blockedThroughTurnRef.current = Math.max(blockedThroughTurnRef.current, maximumTurnIdRef.current);
    allowedReplayTurnsRef.current.clear();
    playbackRef.current.stopAll();
    send({ type: "turn.cancel" });
  }, [send]);

  const disconnect = useCallback(async () => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "session.stop" }));
    socketRef.current = null;
    socket?.close();
    await stopLocalResources();
    setConnectionStatus("disconnected");
  }, [stopLocalResources]);

  useEffect(() => () => {
    captureLifecycleRef.current += 1;
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
    void captureRef.current?.stop();
    void playbackRef.current.close();
    resetSessionTracking();
  }, [resetSessionTracking]);

  return {
    messages,
    voices,
    selectedVoice,
    connectionStatus,
    voiceStatus,
    error,
    isMicrophoneActive,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
    submitText,
    speakMessage,
    selectVoice,
    previewVoice,
    cancelActive,
  };
}
