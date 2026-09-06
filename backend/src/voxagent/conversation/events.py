from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

INPUT_AUDIO_ENCODING = "pcm_s16le"
INPUT_AUDIO_SAMPLE_RATE = 16000
INPUT_AUDIO_CHANNELS = 1
INPUT_AUDIO_FRAME_DURATION_MS = 20
INPUT_AUDIO_FRAME_SAMPLES = 320
INPUT_AUDIO_FRAME_BYTES = 640


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientMessage(Message):
    type: str


class SessionStart(ClientMessage):
    type: Literal["session.start"]


class TextSubmit(ClientMessage):
    type: Literal["text.submit"]
    text: str
    speak_response: bool = False

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not 1 <= len(normalized) <= 4000:
            raise ValueError("text must contain between 1 and 4000 Unicode code points")
        return normalized


class VoiceTranscriptSubmit(ClientMessage):
    type: Literal["voice.transcript.submit"]
    text: str
    request_id: StrictInt = Field(gt=0)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not 1 <= len(normalized) <= 4000:
            raise ValueError("text must contain between 1 and 4000 Unicode code points")
        return normalized


class AssistantSpeak(ClientMessage):
    type: Literal["assistant.speak"]
    turn_id: StrictInt
    request_id: StrictInt = Field(default=0, ge=0)
    start_offset: StrictInt = Field(default=0, ge=0)


class VoiceSelect(ClientMessage):
    type: Literal["voice.select"]
    voice_key: str
    speed: Literal[0.8, 1.0, 1.2]


class VoicePreview(ClientMessage):
    type: Literal["voice.preview"]
    voice_key: str
    speed: Literal[0.8, 1.0, 1.2]


class VoicePreviewCancel(ClientMessage):
    type: Literal["voice.preview.cancel"]


class TurnCancel(ClientMessage):
    type: Literal["turn.cancel"]


class AudioCommit(ClientMessage):
    type: Literal["audio.commit"]


class SessionStop(ClientMessage):
    type: Literal["session.stop"]


type ClientEvent = Annotated[
    SessionStart
    | TextSubmit
    | VoiceTranscriptSubmit
    | AssistantSpeak
    | VoiceSelect
    | VoicePreview
    | VoicePreviewCancel
    | TurnCancel
    | AudioCommit
    | SessionStop,
    Field(discriminator="type"),
]


class ServerMessage(Message):
    type: str


class InputAudioFormat(Message):
    model_config = ConfigDict(extra="forbid", frozen=True)

    encoding: Literal[INPUT_AUDIO_ENCODING]
    sample_rate: Literal[INPUT_AUDIO_SAMPLE_RATE]
    channels: Literal[INPUT_AUDIO_CHANNELS]
    frame_duration_ms: Literal[INPUT_AUDIO_FRAME_DURATION_MS]
    frame_samples: Literal[INPUT_AUDIO_FRAME_SAMPLES]
    frame_bytes: Literal[INPUT_AUDIO_FRAME_BYTES]


INPUT_AUDIO_FORMAT = InputAudioFormat(
    encoding=INPUT_AUDIO_ENCODING,
    sample_rate=INPUT_AUDIO_SAMPLE_RATE,
    channels=INPUT_AUDIO_CHANNELS,
    frame_duration_ms=INPUT_AUDIO_FRAME_DURATION_MS,
    frame_samples=INPUT_AUDIO_FRAME_SAMPLES,
    frame_bytes=INPUT_AUDIO_FRAME_BYTES,
)


class SessionReady(ServerMessage):
    type: Literal["session.ready"]
    session_id: UUID
    model_id: str
    offline: Literal[True]
    input_audio: InputAudioFormat


class VoiceInfo(Message):
    voice_key: str
    display_name: str
    description: str
    gender: str
    is_default: bool
    previewable: bool


class VoicesAvailable(ServerMessage):
    type: Literal["voices.available"]
    voices: list[VoiceInfo]


class VoiceSelected(ServerMessage):
    type: Literal["voice.selected"]
    voice_key: str
    speed: Literal[0.8, 1.0, 1.2]


class VoicePreviewChunk(ServerMessage):
    type: Literal["voice.preview.chunk"]
    preview_id: StrictInt = Field(ge=1)
    sample_rate: StrictInt = Field(gt=0)
    mime_type: Literal["audio/wav"]
    byte_length: StrictInt = Field(gt=0)


class TurnServerMessage(ServerMessage):
    session_id: UUID
    turn_id: StrictInt = Field(gt=0)


class VadStarted(TurnServerMessage):
    type: Literal["vad.started"]


class VadStopped(TurnServerMessage):
    type: Literal["vad.stopped"]


class AsrFinal(TurnServerMessage):
    type: Literal["asr.final"]
    text: str
    request_id: StrictInt | None = Field(
        default=None, gt=0, exclude_if=lambda value: value is None
    )


class AsrPartial(TurnServerMessage):
    type: Literal["asr.partial"]
    text: str

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("partial transcript must be non-empty")
        return value


class AssistantDelta(TurnServerMessage):
    type: Literal["assistant.delta"]
    delta: str


class AssistantDone(TurnServerMessage):
    type: Literal["assistant.done"]


class MemorySource(Message):
    id: StrictInt = Field(ge=1)
    content: str
    source_message_id: StrictInt | None = Field(default=None, ge=1)
    source_text: str | None = None
    source_turn_id: StrictInt | None = Field(default=None, ge=1)


class KnowledgeSource(Message):
    chunk_id: StrictInt = Field(ge=1)
    document_id: StrictInt = Field(ge=1)
    display_name: str
    content: str
    page_number: StrictInt | None = Field(default=None, ge=1)


class ContextSources(TurnServerMessage):
    type: Literal["context.sources"]
    memories: list[MemorySource]
    knowledge: list[KnowledgeSource]


class MemoryProposed(TurnServerMessage):
    type: Literal["memory.proposed"]
    proposal_index: StrictInt = Field(ge=0)
    source_message_id: StrictInt = Field(ge=1)
    kind: Literal["preference", "profile", "habit", "relationship", "event"]
    content: str = Field(min_length=1, max_length=500)
    importance: float = Field(ge=0, le=1)
    requires_confirmation: bool


class TtsChunk(TurnServerMessage):
    type: Literal["tts.chunk"]
    request_id: StrictInt = Field(default=0, ge=0)
    sequence: StrictInt = Field(ge=0)
    sample_rate: StrictInt = Field(gt=0)
    mime_type: Literal["audio/wav"]
    byte_length: StrictInt = Field(gt=0)


class TtsStarted(TurnServerMessage):
    type: Literal["tts.started"]
    request_id: StrictInt = Field(default=0, ge=0)


class TtsDone(TurnServerMessage):
    type: Literal["tts.done"]
    request_id: StrictInt = Field(default=0, ge=0)


class TtsError(TurnServerMessage):
    type: Literal["tts.error"]
    request_id: StrictInt = Field(default=0, ge=0)
    code: Literal["tts_failed", "tts_empty"]
    message: str
    recoverable: bool


class TurnCancelled(TurnServerMessage):
    type: Literal["turn.cancelled"]
    request_id: StrictInt | None = Field(
        default=None, gt=0, exclude_if=lambda value: value is None
    )


class ErrorMessage(ServerMessage):
    type: Literal["error"]
    code: str
    message: str
    recoverable: bool


type ServerEvent = Annotated[
    SessionReady
    | VoicesAvailable
    | VoiceSelected
    | VoicePreviewChunk
    | VadStarted
    | VadStopped
    | AsrFinal
    | AsrPartial
    | AssistantDelta
    | AssistantDone
    | ContextSources
    | MemoryProposed
    | TtsChunk
    | TtsStarted
    | TtsDone
    | TtsError
    | TurnCancelled
    | ErrorMessage,
    Field(discriminator="type"),
]
