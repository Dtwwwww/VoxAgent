from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from voxagent.conversation.events import INPUT_AUDIO_FORMAT, ClientEvent, ServerEvent

_client_adapter = TypeAdapter(ClientEvent)
_server_adapter = TypeAdapter(ServerEvent)


def parse_client_message(payload: Any) -> ClientEvent:
    """Validate one JSON client event with the protocol discriminator."""
    return _client_adapter.validate_python(payload)


def parse_server_message(payload: Any) -> ServerEvent:
    """Validate one JSON server event with the protocol discriminator."""
    return _server_adapter.validate_python(payload)


def validate_audio_frame(frame: bytes) -> bytes:
    """Validate one fixed-format microphone frame by its 20 ms byte length.

    Raw bytes carry no header, so encoding, channel count, sample rate, and
    endianness are fixed by the public session-ready input_audio contract.
    """
    if not isinstance(frame, bytes):
        raise TypeError("audio frame must be bytes")
    if len(frame) != INPUT_AUDIO_FORMAT.frame_bytes:
        raise ValueError(
            f"audio frame must be exactly {INPUT_AUDIO_FORMAT.frame_bytes} bytes "
            f"({INPUT_AUDIO_FORMAT.frame_duration_ms} ms PCM16 mono)"
        )
    return frame
