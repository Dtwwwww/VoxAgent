export type VoiceSpeed = 0.8 | 1.0 | 1.2;

export type ClientEvent =
  | { type: "session.start" }
  | { type: "text.submit"; text: string; speak_response?: boolean }
  | { type: "voice.transcript.submit"; text: string; request_id: number }
  | { type: "assistant.speak"; turn_id: number; request_id?: number; start_offset?: number }
  | { type: "voice.select"; voice_key: string; speed: VoiceSpeed }
  | { type: "voice.preview"; voice_key: string; speed: VoiceSpeed }
  | { type: "voice.preview.cancel" }
  | { type: "turn.cancel" }
  | { type: "tool.confirm"; confirmation_id: string }
  | { type: "tool.deny"; confirmation_id: string }
  | { type: "audio.commit" }
  | { type: "session.stop" };

export interface InputAudioFormat {
  encoding: "pcm_s16le";
  sample_rate: 16000;
  channels: 1;
  frame_duration_ms: 20;
  frame_samples: 320;
  frame_bytes: 640;
}

export interface VoiceInfo {
  voice_key: string;
  display_name: string;
  description: string;
  gender: string;
  is_default: boolean;
  previewable: boolean;
}

type TurnFields = { session_id: string; turn_id: number };

export interface ContextMemorySource {
  id: number;
  content: string;
  source_message_id: number | null;
  source_text: string | null;
  source_turn_id: number | null;
}

export interface ContextKnowledgeSource {
  chunk_id: number;
  document_id: number;
  display_name: string;
  content: string;
  page_number: number | null;
}

export type ServerEvent =
  | { type: "session.ready"; session_id: string; model_id: string; offline: true; input_audio: InputAudioFormat }
  | { type: "voices.available"; voices: VoiceInfo[] }
  | { type: "voice.selected"; voice_key: string; speed: VoiceSpeed }
  | { type: "voice.preview.chunk"; preview_id: number; sample_rate: number; mime_type: "audio/wav"; byte_length: number }
  | ({ type: "tts.started"; request_id: number } & TurnFields)
  | ({ type: "tts.done"; request_id: number } & TurnFields)
  | ({ type: "vad.started" } & TurnFields)
  | ({ type: "vad.stopped" } & TurnFields)
  | ({ type: "asr.final"; text: string; request_id?: number } & TurnFields)
  | ({ type: "asr.partial"; text: string } & TurnFields)
  | ({ type: "assistant.delta"; delta: string } & TurnFields)
  | ({ type: "assistant.done" } & TurnFields)
  | ({
      type: "tool.approval_required";
      confirmation_id: string;
      call_id: string;
      tool_name: string;
      permission: "L1" | "L2";
    } & TurnFields)
  | ({ type: "tool.started"; call_id: string; tool_name: string } & TurnFields)
  | ({ type: "tool.completed"; call_id: string; tool_name: string; user_summary: string } & TurnFields)
  | ({ type: "tool.failed"; call_id: string; tool_name: string; error_code: string } & TurnFields)
  | ({ type: "context.sources"; memories: ContextMemorySource[]; knowledge: ContextKnowledgeSource[] } & TurnFields)
  | ({
      type: "memory.proposed";
      proposal_index: number;
      source_message_id: number;
      kind: "preference" | "profile" | "habit" | "relationship" | "event";
      content: string;
      importance: number;
      requires_confirmation: boolean;
    } & TurnFields)
  | ({ type: "tts.chunk"; request_id: number; sequence: number; sample_rate: number; mime_type: "audio/wav"; byte_length: number } & TurnFields)
  | ({ type: "tts.error"; request_id: number; code: "tts_failed" | "tts_empty"; message: string; recoverable: boolean } & TurnFields)
  | ({ type: "turn.cancelled"; request_id?: number } & TurnFields)
  | { type: "error"; code: string; message: string; recoverable: boolean };

type JsonObject = Record<string, unknown>;

function objectWithExactKeys(value: unknown, required: readonly string[], optional: readonly string[] = []): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("event must be an object");
  const object = value as JsonObject;
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !(key in object)) || Object.keys(object).some((key) => !allowed.has(key))) {
    throw new TypeError("event fields do not match the protocol");
  }
  return object;
}

function string(value: unknown, name: string): string {
  if (typeof value !== "string") throw new TypeError(`${name} must be a string`);
  return value;
}

function bool(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") throw new TypeError(`${name} must be a boolean`);
  return value;
}

function integer(value: unknown, name: string, minimum?: number): number {
  if (!Number.isInteger(value) || (minimum !== undefined && (value as number) < minimum)) {
    throw new TypeError(`${name} must be an integer`);
  }
  return value as number;
}

function positiveInteger(value: unknown, name: string): number {
  return integer(value, name, 1);
}

function boundedNumber(value: unknown, name: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw new TypeError(`${name} must be between ${minimum} and ${maximum}`);
  }
  return value;
}

function speed(value: unknown): VoiceSpeed {
  if (value !== 0.8 && value !== 1.0 && value !== 1.2) throw new TypeError("speed is unsupported");
  return value;
}

function uuid(value: unknown): string {
  const candidate = string(value, "session_id");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu.test(candidate)) {
    throw new TypeError("session_id must be a UUID");
  }
  return candidate;
}

function transcriptText(value: unknown): string {
  const text = string(value, "text").trim();
  const length = [...text].length;
  if (length < 1 || length > 4000) throw new TypeError("text length is invalid");
  return text;
}

function turnObject(value: unknown, extra: readonly string[] = [], optional: readonly string[] = []): JsonObject {
  const object = objectWithExactKeys(value, ["type", "session_id", "turn_id", ...extra], optional);
  uuid(object.session_id);
  positiveInteger(object.turn_id, "turn_id");
  return object;
}

export function parseClientEvent(value: unknown): ClientEvent {
  const base = objectWithExactKeys(value, ["type"], Object.keys(value as object).filter((key) => key !== "type"));
  const type = string(base.type, "type");
  switch (type) {
    case "session.start":
    case "turn.cancel":
    case "voice.preview.cancel":
    case "audio.commit":
    case "session.stop":
      objectWithExactKeys(value, ["type"]);
      return { type };
    case "text.submit": { // The server defaults speak_response to false when omitted.
      const object = objectWithExactKeys(value, ["type", "text"], ["speak_response"]);
      const text = transcriptText(object.text);
      if (object.speak_response !== undefined) bool(object.speak_response, "speak_response");
      return object.speak_response === undefined
        ? { type, text }
        : { type, text, speak_response: object.speak_response as boolean };
    }
    case "voice.transcript.submit": {
      const object = objectWithExactKeys(value, ["type", "text", "request_id"]);
      return {
        type,
        text: transcriptText(object.text),
        request_id: positiveInteger(object.request_id, "request_id"),
      };
    }
    case "assistant.speak": {
      const object = objectWithExactKeys(value, ["type", "turn_id"], ["request_id", "start_offset"]);
      const turnId = integer(object.turn_id, "turn_id");
      const requestId = object.request_id === undefined
        ? undefined
        : integer(object.request_id, "request_id", 0);
      const startOffset = object.start_offset === undefined
        ? undefined
        : integer(object.start_offset, "start_offset", 0);
      return {
        type,
        turn_id: turnId,
        ...(requestId === undefined ? {} : { request_id: requestId }),
        ...(startOffset === undefined ? {} : { start_offset: startOffset }),
      };
    }
    case "voice.select":
    case "voice.preview": {
      const object = objectWithExactKeys(value, ["type", "voice_key", "speed"]);
      return { type, voice_key: string(object.voice_key, "voice_key"), speed: speed(object.speed) };
    }
    case "tool.confirm":
    case "tool.deny": {
      const object = objectWithExactKeys(value, ["type", "confirmation_id"]);
      return { type, confirmation_id: uuid(object.confirmation_id) };
    }
    default:
      throw new TypeError(`unknown client event: ${type}`);
  }
}

const STRICT_INTEGER_TOKEN = /("(?:id|turn_id|request_id|start_offset|source_turn_id|source_message_id|document_id|chunk_id|page_number|proposal_index|preview_id|sequence|sample_rate|byte_length|frame_samples|frame_bytes)"\s*:\s*)(-?(?:(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+))(?=\s*[,}])/gu;

function parseProtocolJson(raw: string): unknown {
  if (typeof raw !== "string") throw new TypeError("event JSON must be a string");
  return JSON.parse(raw.replace(STRICT_INTEGER_TOKEN, "$1\"$2\""));
}

export function parseClientEventJson(raw: string): ClientEvent {
  return parseClientEvent(parseProtocolJson(raw));
}

export function parseServerEvent(value: unknown): ServerEvent {
  const base = objectWithExactKeys(value, ["type"], Object.keys(value as object).filter((key) => key !== "type"));
  const type = string(base.type, "type");
  switch (type) {
    case "session.ready": {
      const object = objectWithExactKeys(value, ["type", "session_id", "model_id", "offline", "input_audio"]);
      const audio = objectWithExactKeys(object.input_audio, ["encoding", "sample_rate", "channels", "frame_duration_ms", "frame_samples", "frame_bytes"]);
      if (audio.encoding !== "pcm_s16le" || audio.sample_rate !== 16000 || audio.channels !== 1 || audio.frame_duration_ms !== 20 || audio.frame_samples !== 320 || audio.frame_bytes !== 640) {
        throw new TypeError("input_audio does not match the fixed microphone contract");
      }
      if (object.offline !== true) throw new TypeError("session must be offline");
      return { type, session_id: uuid(object.session_id), model_id: string(object.model_id, "model_id"), offline: true, input_audio: audio as unknown as InputAudioFormat };
    }
    case "voices.available": {
      const object = objectWithExactKeys(value, ["type", "voices"]);
      if (!Array.isArray(object.voices)) throw new TypeError("voices must be an array");
      const voices = object.voices.map((item) => {
        const voice = objectWithExactKeys(item, ["voice_key", "display_name", "description", "gender", "is_default", "previewable"]);
        return {
          voice_key: string(voice.voice_key, "voice_key"),
          display_name: string(voice.display_name, "display_name"),
          description: string(voice.description, "description"),
          gender: string(voice.gender, "gender"),
          is_default: bool(voice.is_default, "is_default"),
          previewable: bool(voice.previewable, "previewable"),
        };
      });
      return { type, voices };
    }
    case "voice.selected": {
      const object = objectWithExactKeys(value, ["type", "voice_key", "speed"]);
      return { type, voice_key: string(object.voice_key, "voice_key"), speed: speed(object.speed) };
    }
    case "voice.preview.chunk": {
      const object = objectWithExactKeys(value, ["type", "preview_id", "sample_rate", "mime_type", "byte_length"]);
      if (object.mime_type !== "audio/wav") throw new TypeError("preview payload must be WAV");
      return { type, preview_id: positiveInteger(object.preview_id, "preview_id"), sample_rate: positiveInteger(object.sample_rate, "sample_rate"), mime_type: "audio/wav", byte_length: positiveInteger(object.byte_length, "byte_length") };
    }
    case "vad.started":
    case "vad.stopped":
    case "assistant.done": {
      const object = turnObject(value);
      return { type, session_id: object.session_id as string, turn_id: object.turn_id as number };
    }
    case "tool.approval_required": {
      const object = turnObject(value, ["confirmation_id", "call_id", "tool_name", "permission"]);
      if (object.permission !== "L1" && object.permission !== "L2") {
        throw new TypeError("tool permission must be L1 or L2");
      }
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        confirmation_id: uuid(object.confirmation_id),
        call_id: string(object.call_id, "call_id"),
        tool_name: string(object.tool_name, "tool_name"),
        permission: object.permission,
      };
    }
    case "tool.started": {
      const object = turnObject(value, ["call_id", "tool_name"]);
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        call_id: string(object.call_id, "call_id"),
        tool_name: string(object.tool_name, "tool_name"),
      };
    }
    case "tool.completed": {
      const object = turnObject(value, ["call_id", "tool_name", "user_summary"]);
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        call_id: string(object.call_id, "call_id"),
        tool_name: string(object.tool_name, "tool_name"),
        user_summary: string(object.user_summary, "user_summary"),
      };
    }
    case "tool.failed": {
      const object = turnObject(value, ["call_id", "tool_name", "error_code"]);
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        call_id: string(object.call_id, "call_id"),
        tool_name: string(object.tool_name, "tool_name"),
        error_code: string(object.error_code, "error_code"),
      };
    }
    case "turn.cancelled": {
      const object = turnObject(value, [], ["request_id"]);
      return object.request_id === undefined
        ? { type, session_id: object.session_id as string, turn_id: object.turn_id as number }
        : {
            type,
            session_id: object.session_id as string,
            turn_id: object.turn_id as number,
            request_id: positiveInteger(object.request_id, "request_id"),
          };
    }
    case "tts.started":
    case "tts.done": {
      const object = turnObject(value, [], ["request_id"]);
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        request_id: object.request_id === undefined ? 0 : integer(object.request_id, "request_id", 0),
      };
    }
    case "asr.final": {
      const object = turnObject(value, ["text"], ["request_id"]);
      return object.request_id === undefined
        ? { type, session_id: object.session_id as string, turn_id: object.turn_id as number, text: string(object.text, "text") }
        : {
            type,
            session_id: object.session_id as string,
            turn_id: object.turn_id as number,
            text: string(object.text, "text"),
            request_id: positiveInteger(object.request_id, "request_id"),
          };
    }
    case "asr.partial": {
      const object = turnObject(value, ["text"]);
      const text = string(object.text, "text");
      if (!text.trim()) throw new TypeError("partial transcript must be non-empty");
      return { type, session_id: object.session_id as string, turn_id: object.turn_id as number, text };
    }
    case "assistant.delta": {
      const object = turnObject(value, ["delta"]);
      return { type, session_id: object.session_id as string, turn_id: object.turn_id as number, delta: string(object.delta, "delta") };
    }
    case "context.sources": {
      const object = turnObject(value, ["memories", "knowledge"]);
      if (!Array.isArray(object.memories) || !Array.isArray(object.knowledge)) {
        throw new TypeError("context sources must be arrays");
      }
      const memories = object.memories.map((item) => {
        const source = objectWithExactKeys(item, ["id", "content", "source_message_id", "source_text", "source_turn_id"]);
        if (source.source_message_id !== null) positiveInteger(source.source_message_id, "source_message_id");
        if (source.source_turn_id !== null) positiveInteger(source.source_turn_id, "source_turn_id");
        if (source.source_text !== null) string(source.source_text, "source_text");
        return {
          id: positiveInteger(source.id, "id"),
          content: string(source.content, "content"),
          source_message_id: source.source_message_id as number | null,
          source_text: source.source_text as string | null,
          source_turn_id: source.source_turn_id as number | null,
        };
      });
      const knowledge = object.knowledge.map((item) => {
        const source = objectWithExactKeys(item, ["chunk_id", "document_id", "display_name", "content", "page_number"]);
        if (source.page_number !== null) positiveInteger(source.page_number, "page_number");
        return {
          chunk_id: positiveInteger(source.chunk_id, "chunk_id"),
          document_id: positiveInteger(source.document_id, "document_id"),
          display_name: string(source.display_name, "display_name"),
          content: string(source.content, "content"),
          page_number: source.page_number as number | null,
        };
      });
      return { type, session_id: object.session_id as string, turn_id: object.turn_id as number, memories, knowledge };
    }
    case "memory.proposed": {
      const object = turnObject(value, ["proposal_index", "source_message_id", "kind", "content", "importance", "requires_confirmation"]);
      const kind = string(object.kind, "kind");
      if (!["preference", "profile", "habit", "relationship", "event"].includes(kind)) {
        throw new TypeError("memory kind is unsupported");
      }
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        proposal_index: integer(object.proposal_index, "proposal_index", 0),
        source_message_id: positiveInteger(object.source_message_id, "source_message_id"),
        kind: kind as "preference" | "profile" | "habit" | "relationship" | "event",
        content: string(object.content, "content"),
        importance: boundedNumber(object.importance, "importance", 0, 1),
        requires_confirmation: bool(object.requires_confirmation, "requires_confirmation"),
      };
    }
    case "tts.chunk": {
      const object = turnObject(value, ["sequence", "sample_rate", "mime_type", "byte_length"], ["request_id"]);
      if (object.mime_type !== "audio/wav") throw new TypeError("TTS payload must be WAV");
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        request_id: object.request_id === undefined ? 0 : integer(object.request_id, "request_id", 0),
        sequence: integer(object.sequence, "sequence", 0),
        sample_rate: positiveInteger(object.sample_rate, "sample_rate"),
        mime_type: "audio/wav",
        byte_length: positiveInteger(object.byte_length, "byte_length"),
      };
    }
    case "tts.error": {
      const object = turnObject(value, ["code", "message", "recoverable"], ["request_id"]);
      const code = string(object.code, "code");
      if (code !== "tts_failed" && code !== "tts_empty") throw new TypeError("unknown TTS error code");
      return {
        type,
        session_id: object.session_id as string,
        turn_id: object.turn_id as number,
        request_id: object.request_id === undefined ? 0 : integer(object.request_id, "request_id", 0),
        code,
        message: string(object.message, "message"),
        recoverable: bool(object.recoverable, "recoverable"),
      };
    }
    case "error": {
      const object = objectWithExactKeys(value, ["type", "code", "message", "recoverable"]);
      return { type, code: string(object.code, "code"), message: string(object.message, "message"), recoverable: bool(object.recoverable, "recoverable") };
    }
    default:
      throw new TypeError(`unknown server event: ${type}`);
  }
}

export function parseServerEventJson(raw: string): ServerEvent {
  return parseServerEvent(parseProtocolJson(raw));
}
