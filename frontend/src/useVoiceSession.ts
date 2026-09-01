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
  const playbackRef = useRef(new AudioPlayback());
  const pendingAudioRef = useRef<AudioMetadata | null>(null);
  const messageIdRef = useRef(0);

  const nextId = useCallback((prefix: string) => `${prefix}-${++messageIdRef.current}`, []);

  const send = useCallback((event: object) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(event));
  }, []);

  const failProtocol = useCallback(() => {
    setError({ code: "invalid_server_event", message: "服务器返回了无效数据，请重试", recoverable: true });
  }, []);

  const consumeBinary = useCallback(async (data: ArrayBuffer | Blob) => {
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
    switch (event.type) {
      case "session.ready":
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
        pendingAudioRef.current = { kind: "preview", previewId: event.preview_id, sampleRate: event.sample_rate, byteLength: event.byte_length, valid: true };
        break;
      case "tts.chunk":
        pendingAudioRef.current = { kind: "turn", turnId: event.turn_id, sequence: event.sequence, sampleRate: event.sample_rate, byteLength: event.byte_length, valid: true };
        break;
      case "vad.started":
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
          const index = current.findIndex((message) => message.role === "assistant" && message.turnId === event.turn_id);
          if (index < 0) return [...current, { id: nextId("assistant"), turnId: event.turn_id, role: "assistant", origin: "assistant", text: event.delta, status: "streaming" }];
          return current.map((message, position) => position === index ? { ...message, text: message.text + event.delta } : message);
        });
        setVoiceStatus("responding");
        break;
      case "assistant.done":
        setMessages((current) => current.map((message) => message.role === "assistant" && message.turnId === event.turn_id ? { ...message, status: "complete" } : message));
        setVoiceStatus("idle");
        break;
      case "turn.cancelled":
        if (pendingAudioRef.current?.kind === "turn" && pendingAudioRef.current.turnId === event.turn_id) pendingAudioRef.current.valid = false;
        playbackRef.current.stopTurn(event.turn_id);
        setMessages((current) => current.map((message) => message.role === "assistant" && message.turnId === event.turn_id ? { ...message, status: "cancelled" } : message));
        setVoiceStatus("idle");
        break;
      case "error":
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
      setConnectionStatus("connected");
      socket.send(JSON.stringify({ type: "session.start" }));
    };
    socket.onmessage = (message) => {
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
        const pending = pendingAudioRef.current;
        const interruptsPendingTurn = pending?.kind === "turn" && (
          (event.type === "turn.cancelled" && event.turn_id === pending.turnId)
          || event.type === "vad.started"
        );
        if (pending && !interruptsPendingTurn) {
          pending.valid = false;
          pendingAudioRef.current = null;
          failProtocol();
        }
        handleServerEvent(event);
      } catch {
        failProtocol();
      }
    };
    socket.onerror = () => setError({ code: "connection", message: "无法连接语音服务，请重试", recoverable: true });
    socket.onclose = () => {
      if (socketRef.current === socket) socketRef.current = null;
      setConnectionStatus("disconnected");
      setVoiceStatus("idle");
    };
  }, [consumeBinary, failProtocol, handleServerEvent, url]);

  const startMicrophone = useCallback(async () => {
    if (captureRef.current) return;
    const capture = new MicrophoneCapture((frame) => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) socket.send(frame);
    });
    try {
      await capture.start();
      captureRef.current = capture;
      setMicrophoneActive(true);
      setError(null);
      setVoiceStatus("listening");
    } catch {
      await capture.stop();
      setError({ code: "microphone_permission", message: "无法使用麦克风，请允许权限后重试；文字输入仍可使用", recoverable: true });
      setMicrophoneActive(false);
    }
  }, []);

  const stopMicrophone = useCallback(async () => {
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
    playbackRef.current.stopConversation();
    setMessages((current) => [...current, { id: nextId("text-user"), role: "user", origin: "text", text, status: "complete" }]);
    setVoiceStatus("responding");
    send({ type: "text.submit", text, speak_response: false });
  }, [nextId, send]);

  const speakMessage = useCallback((turnId: number) => {
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
    playbackRef.current.stopPreview();
    send({ type: "voice.preview", voice_key: voiceKey, speed });
  }, [send]);

  const cancelActive = useCallback(() => {
    if (pendingAudioRef.current) pendingAudioRef.current.valid = false;
    playbackRef.current.stopAll();
    send({ type: "turn.cancel" });
  }, [send]);

  const disconnect = useCallback(async () => {
    const capture = captureRef.current;
    captureRef.current = null;
    if (capture) await capture.stop();
    setMicrophoneActive(false);
    pendingAudioRef.current = null;
    await playbackRef.current.close();
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "session.stop" }));
    socket?.close();
    socketRef.current = null;
    setConnectionStatus("disconnected");
    setVoiceStatus("idle");
  }, []);

  useEffect(() => () => {
    void captureRef.current?.stop();
    void playbackRef.current.close();
    socketRef.current?.close();
  }, []);

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
