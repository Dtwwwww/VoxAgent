import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from voxagent.api.protocol import (
    parse_client_message,
    parse_server_message,
    validate_audio_frame,
)

FIXTURES_PATH = Path(__file__).resolve().parents[3] / "contracts" / "protocol-fixtures.json"


def test_protocol_fixtures_match_contract():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))

    client_messages = [parse_client_message(payload) for payload in fixtures["valid_client"]]
    server_messages = [parse_server_message(payload) for payload in fixtures["valid_server"]]

    assert client_messages[1].text == "hello"
    assert len(server_messages) == 12
    for payload in fixtures["invalid_client"]:
        with pytest.raises(ValidationError):
            parse_client_message(payload)
    for payload in fixtures["invalid_server"]:
        with pytest.raises(ValidationError):
            parse_server_message(payload)


def test_session_ready_publishes_fixed_microphone_contract_and_validates_frames():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    messages = [parse_server_message(payload) for payload in fixtures["valid_server"]]
    session_ready = next(message for message in messages if message.type == "session.ready")

    assert session_ready.input_audio.model_dump() == {
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration_ms": 20,
        "frame_samples": 320,
        "frame_bytes": 640,
    }
    assert validate_audio_frame(bytes(640)) == bytes(640)
    for size in (0, 639, 641):
        with pytest.raises(ValueError):
            validate_audio_frame(bytes(size))
    with pytest.raises(ValidationError):
        session_ready.input_audio.frame_bytes = 1

    preview_chunk = next(message for message in messages if message.type == "voice.preview.chunk")
    tts_chunk = next(message for message in messages if message.type == "tts.chunk")
    assert preview_chunk.sample_rate == 24000
    assert tts_chunk.sample_rate == 24000
