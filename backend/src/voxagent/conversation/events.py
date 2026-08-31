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


class AssistantSpeak(ClientMessage):
    type: Literal["assistant.speak"]
    turn_id: StrictInt


class VoiceSelect(ClientMessage):
    type: Literal["voice.select"]
    voice_key: str
    speed: Literal[0.8, 1.0, 1.2]


class VoicePreview(ClientMessage):
    type: Literal["voice.preview"]
    voice_key: str
    speed: Literal[0.8, 1.0, 1.2]


class TurnCancel(ClientMessage):
    type: Literal["turn.cancel"]


class AudioCommit(ClientMessage):
    type: Literal["audio.commit"]


class SessionStop(ClientMessage):
    type: Literal["session.stop"]


type ClientEvent = Annotated[
    SessionStart
    | TextSubmit
    | AssistantSpeak
    | VoiceSelect
    | VoicePreview
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
    turn_id: StrictInt


class VadStarted(TurnServerMessage):
    type: Literal["vad.started"]


class VadStopped(TurnServerMessage):
    type: Literal["vad.stopped"]


class AsrFinal(TurnServerMessage):
    type: Literal["asr.final"]
    text: str


class AssistantDelta(TurnServerMessage):
    type: Literal["assistant.delta"]
    delta: str


class AssistantDone(TurnServerMessage):
    type: Literal["assistant.done"]


class TtsChunk(TurnServerMessage):
    type: Literal["tts.chunk"]
    sequence: StrictInt = Field(ge=0)
    sample_rate: StrictInt = Field(gt=0)
    mime_type: Literal["audio/wav"]
    byte_length: StrictInt = Field(gt=0)


class TurnCancelled(TurnServerMessage):
    type: Literal["turn.cancelled"]


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
    | AssistantDelta
    | AssistantDone
    | TtsChunk
    | TurnCancelled
    | ErrorMessage,
    Field(discriminator="type"),
]
