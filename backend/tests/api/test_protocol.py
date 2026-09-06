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
    assert client_messages[2].type == "voice.transcript.submit"
    assert client_messages[2].text == "你好，声灵"
    assert len(server_messages) == 16
    assert next(
        message.text for message in server_messages if message.type == "asr.partial"
    ) == "你好"
    assert {
        message.turn_id for message in server_messages if message.type == "tts.started"
    } == {1}
    assert {
        message.turn_id for message in server_messages if message.type == "tts.done"
    } == {1}
    assert {
        message.request_id
        for message in server_messages
        if message.type in {"tts.started", "tts.chunk", "tts.done", "tts.error"}
    } == {7}
    for payload in fixtures["invalid_client"]:
        with pytest.raises(ValidationError):
            parse_client_message(payload)
    for payload in fixtures["invalid_server"]:
        with pytest.raises(ValidationError):
            parse_server_message(payload)


def test_browser_transcript_request_ids_are_strict_and_echo_on_turn_events():
    submission = parse_client_message(
        {"type": "voice.transcript.submit", "text": "你好", "request_id": 9}
    )
    final = parse_server_message(
        {
            "type": "asr.final",
            "session_id": "00000000-0000-4000-8000-000000000001",
            "turn_id": 3,
            "text": "你好",
            "request_id": 9,
        }
    )
    cancelled = parse_server_message(
        {
            "type": "turn.cancelled",
            "session_id": "00000000-0000-4000-8000-000000000001",
            "turn_id": 3,
            "request_id": 9,
        }
    )

    assert submission.request_id == final.request_id == cancelled.request_id == 9
    with pytest.raises(ValidationError):
        parse_client_message({"type": "voice.transcript.submit", "text": "你好"})
    with pytest.raises(ValidationError):
        parse_client_message(
            {"type": "voice.transcript.submit", "text": "你好", "request_id": 1.0}
        )


def test_assistant_speech_remainder_offset_is_strict_and_cannot_supply_text():
    message = parse_client_message(
        {"type": "assistant.speak", "turn_id": 7, "request_id": 11, "start_offset": 4}
    )

    assert (message.turn_id, message.request_id, message.start_offset) == (7, 11, 4)
    for start_offset in (-1, 1.0, True):
        with pytest.raises(ValidationError):
            parse_client_message(
                {
                    "type": "assistant.speak",
                    "turn_id": 7,
                    "request_id": 11,
                    "start_offset": start_offset,
                }
            )
    with pytest.raises(ValidationError):
        parse_client_message(
            {
                "type": "assistant.speak",
                "turn_id": 7,
                "request_id": 11,
                "start_offset": 4,
                "text": "客户端不能注入朗读文本",
            }
        )

    assert parse_client_message({"type": "voice.preview.cancel"}).type == "voice.preview.cancel"
    with pytest.raises(ValidationError):
        parse_client_message({"type": "voice.preview.cancel", "turn_id": 7})


def test_shared_astral_fixture_enforces_4000_code_point_boundary():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    boundary = fixtures["astral_text_boundaries"][0]
    assert len(boundary["scalar"]) == 1

    valid = {
        "type": boundary["event_type"],
        "text": boundary["scalar"] * boundary["valid_count"],
        "request_id": boundary["request_id"],
    }
    invalid = {
        **valid,
        "text": boundary["scalar"] * boundary["invalid_count"],
    }

    assert len(parse_client_message(valid).text) == 4000
    with pytest.raises(ValidationError):
        parse_client_message(invalid)


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


def test_input_audio_contract_rejects_empty_and_partial_wire_objects():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    incomplete_contracts = [
        payload["input_audio"]
        for payload in fixtures["invalid_server"]
        if payload.get("type") == "session.ready" and "input_audio" in payload
    ]

    assert incomplete_contracts == [{}, {"encoding": "pcm_s16le"}]
    for input_audio in incomplete_contracts:
        payload = {
            "type": "session.ready",
            "session_id": "00000000-0000-4000-8000-000000000001",
            "model_id": "qwen2.5:7b",
            "offline": True,
            "input_audio": input_audio,
        }
        with pytest.raises(ValidationError):
            parse_server_message(payload)
