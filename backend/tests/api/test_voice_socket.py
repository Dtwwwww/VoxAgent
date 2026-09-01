from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voxagent.api.app import _dispatch_event, _forward_outputs, _SocketWriter, create_app
from voxagent.api.protocol import parse_client_message
from voxagent.conversation.events import (
    ErrorMessage,
    TtsChunk,
    TurnCancelled,
    VoicePreviewChunk,
)

SESSION_TOKEN = base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("=")


class FakeOrchestrator:
    model_id = "qwen-local"

    def __init__(self) -> None:
        self.state = SimpleNamespace(session_id=uuid4())
        self.voice_catalog = SimpleNamespace(
            public_profiles=lambda: (
                SimpleNamespace(
                    voice_key="clear_female",
                    display_name="清澈女声",
                    description="明亮清晰",
                    gender="female",
                    is_default=True,
                    previewable=True,
                ),
            )
        )
        self.stop_calls = 0
        self.history = ["private conversation"]
        self.calls: list[tuple[object, ...]] = []

    async def accept_audio(self, frame: bytes):
        self.calls.append(("accept_audio", frame))
        yield ErrorMessage(
            type="error", code="accepted_audio", message="test", recoverable=True
        )

    async def submit_text(self, text: str, speak_response: bool):
        self.calls.append(("submit_text", text, speak_response))
        yield ErrorMessage(
            type="error", code="submitted_text", message="test", recoverable=True
        )

    async def speak_message(self, turn_id: int):
        self.calls.append(("speak_message", turn_id))
        yield ErrorMessage(
            type="error", code="spoke_message", message="test", recoverable=True
        )

    def select_voice(self, voice_key: str, speed: float):
        self.calls.append(("select_voice", voice_key, speed))
        return ErrorMessage(
            type="error", code="selected_voice", message="test", recoverable=True
        )

    async def preview_voice(self, voice_key: str, speed: float):
        self.calls.append(("preview_voice", voice_key, speed))
        yield ErrorMessage(
            type="error", code="previewed_voice", message="test", recoverable=True
        )

    async def cancel_active(self):
        self.calls.append(("cancel_active",))
        return TurnCancelled(
            type="turn.cancelled",
            session_id=self.state.session_id,
            turn_id=7,
        )

    async def commit_audio(self):
        self.calls.append(("commit_audio",))
        yield ErrorMessage(
            type="error", code="committed_audio", message="test", recoverable=True
        )

    async def stop(self) -> None:
        self.stop_calls += 1
        self.history.clear()


class Factory:
    def __init__(self) -> None:
        self.instances: list[FakeOrchestrator] = []

    def __call__(self) -> FakeOrchestrator:
        instance = FakeOrchestrator()
        self.instances.append(instance)
        return instance


class FailingStopOrchestrator(FakeOrchestrator):
    async def stop(self) -> None:
        await super().stop()
        raise RuntimeError("stop failed")


class FailFirstStopFactory(Factory):
    def __call__(self) -> FakeOrchestrator:
        instance = FailingStopOrchestrator() if not self.instances else FakeOrchestrator()
        self.instances.append(instance)
        return instance


def _client(factory: Factory | None = None) -> tuple[TestClient, Factory]:
    factory = factory or Factory()
    return TestClient(create_app(factory, SESSION_TOKEN)), factory


@pytest.mark.parametrize(
    "token",
    ["", "short", "not+url_safe", base64.urlsafe_b64encode(b"x" * 31).decode("ascii")],
)
def test_create_app_requires_exactly_32_urlsafe_token_bytes(token: str):
    with pytest.raises(ValueError, match="32-byte URL-safe"):
        create_app(Factory(), token)


def test_healthz_contains_only_public_status():
    client, _ = _client()

    response = client.get("/healthz?token=must-not-echo")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "offline": True}
    serialized = response.text.lower()
    assert "token" not in serialized
    assert "path" not in serialized
    assert "qwen" not in serialized


@pytest.mark.parametrize("query", ["", "?token=wrong", "?token=中文"])
def test_missing_or_wrong_token_closes_4401(query: str):
    client, factory = _client()

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/v1/voice{query}"):
            pass

    assert closed.value.code == 4401
    assert factory.instances == []


def test_session_ready_is_followed_by_public_voice_catalog():
    client, factory = _client()

    with client.websocket_connect(f"/v1/voice?token={SESSION_TOKEN}") as socket:
        socket.send_json({"type": "session.start"})
        ready = socket.receive_json()
        voices = socket.receive_json()

    assert ready["type"] == "session.ready"
    UUID(ready["session_id"])
    assert ready["model_id"] == "qwen-local"
    assert ready["offline"] is True
    assert ready["input_audio"] == {
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration_ms": 20,
        "frame_samples": 320,
        "frame_bytes": 640,
    }
    assert voices == {
        "type": "voices.available",
        "voices": [
            {
                "voice_key": "clear_female",
                "display_name": "清澈女声",
                "description": "明亮清晰",
                "gender": "female",
                "is_default": True,
                "previewable": True,
            }
        ],
    }
    assert "native" not in str(voices).lower()
    assert factory.instances[0].stop_calls == 1
    assert factory.instances[0].history == []


def test_second_concurrent_session_closes_4409_and_stop_releases_slot():
    client, factory = _client()
    url = f"/v1/voice?token={SESSION_TOKEN}"

    with client.websocket_connect(url) as first:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(url):
                pass
        assert closed.value.code == 4409
        first.send_json({"type": "session.stop"})
        with pytest.raises(WebSocketDisconnect) as stopped:
            first.receive_json()
        assert stopped.value.code == 1000

    with client.websocket_connect(url) as replacement:
        replacement.send_json({"type": "session.start"})
        assert replacement.receive_json()["type"] == "session.ready"

    assert len(factory.instances) == 2
    assert all(instance.stop_calls == 1 for instance in factory.instances)


def test_invalid_binary_frame_closes_4400_and_releases_slot():
    client, factory = _client()
    url = f"/v1/voice?token={SESSION_TOKEN}"

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(url) as socket:
            socket.send_bytes(b"not-a-640-byte-frame")
            socket.receive_json()

    assert closed.value.code == 4400
    assert factory.instances[0].stop_calls == 1
    with client.websocket_connect(url):
        pass


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "text.submit", "text": "   ", "speak_response": False},
        {"type": "text.submit", "text": "字" * 4001, "speak_response": False},
        {"type": "voice.select", "voice_key": "clear_female", "speed": 0.9},
        {"type": "voice.preview", "voice_key": "clear_female", "speed": 0.9},
    ],
)
def test_recoverable_validation_error_does_not_close_socket(payload: dict[str, object]):
    client, _ = _client()
    url = f"/v1/voice?token={SESSION_TOKEN}"

    with client.websocket_connect(url) as socket:
        socket.send_json(payload)
        error = socket.receive_json()
        socket.send_json({"type": "session.start"})
        ready = socket.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "invalid_event"
    assert error["recoverable"] is True
    assert ready["type"] == "session.ready"


@pytest.mark.asyncio
async def test_every_task_1_client_event_dispatches_to_exact_orchestrator_operation():
    class RecordingSocket:
        def __init__(self) -> None:
            self.frames: list[tuple[str, object]] = []

        async def send_json(self, payload: dict[str, object]) -> None:
            self.frames.append(("json", payload))

        async def send_bytes(self, payload: bytes) -> None:
            self.frames.append(("bytes", payload))

    orchestrator = FakeOrchestrator()
    socket = RecordingSocket()
    writer = _SocketWriter(socket)
    writer.start()
    frame = b"\x00" * 640

    payloads = (
        {"type": "session.start"},
        {"type": "text.submit", "text": "  你好  ", "speak_response": True},
        {"type": "assistant.speak", "turn_id": 3},
        {"type": "voice.select", "voice_key": "clear_female", "speed": 1.2},
        {"type": "voice.preview", "voice_key": "clear_female", "speed": 0.8},
        {"type": "turn.cancel"},
        {"type": "audio.commit"},
    )
    for payload in payloads:
        should_stop = await _dispatch_event(
            parse_client_message(payload), orchestrator, writer
        )
        assert should_stop is False
    await _forward_outputs(orchestrator.accept_audio(frame), writer)
    should_stop = await _dispatch_event(
        parse_client_message({"type": "session.stop"}), orchestrator, writer
    )
    await writer._queue.join()
    await writer.close()

    assert should_stop is True
    assert orchestrator.calls == [
        ("submit_text", "你好", True),
        ("speak_message", 3),
        ("select_voice", "clear_female", 1.2),
        ("preview_voice", "clear_female", 0.8),
        ("cancel_active",),
        ("commit_audio",),
        ("accept_audio", frame),
    ]
    assert orchestrator.stop_calls == 1
    assert [
        payload["type"] for kind, payload in socket.frames if kind == "json"
    ][:2] == ["session.ready", "voices.available"]


@pytest.mark.asyncio
async def test_concurrent_reply_and_preview_producers_keep_each_wav_pair_adjacent():
    class RecordingSocket:
        def __init__(self) -> None:
            self.frames: list[tuple[str, object]] = []

        async def send_json(self, payload: dict[str, object]) -> None:
            self.frames.append(("json", payload))
            await asyncio.sleep(0)

        async def send_bytes(self, payload: bytes) -> None:
            self.frames.append(("bytes", payload))
            await asyncio.sleep(0)

    release = asyncio.Event()
    session_id = uuid4()

    async def reply_outputs():
        yield TtsChunk(
            type="tts.chunk",
            session_id=session_id,
            turn_id=1,
            sequence=0,
            sample_rate=24000,
            mime_type="audio/wav",
            byte_length=9,
        )
        await release.wait()
        yield b"reply-wav"

    async def preview_outputs():
        yield VoicePreviewChunk(
            type="voice.preview.chunk",
            preview_id=1,
            sample_rate=44100,
            mime_type="audio/wav",
            byte_length=11,
        )
        await release.wait()
        yield b"preview-wav"

    socket = RecordingSocket()
    writer = _SocketWriter(socket)
    writer.start()
    producers = [
        asyncio.create_task(_forward_outputs(reply_outputs(), writer)),
        asyncio.create_task(_forward_outputs(preview_outputs(), writer)),
    ]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*producers)
    await writer._queue.join()
    await writer.close()

    assert len(socket.frames) == 4
    pairs = [socket.frames[index : index + 2] for index in (0, 2)]
    assert {
        (pair[0][1]["type"], pair[1][1])
        for pair in pairs
    } == {
        ("tts.chunk", b"reply-wav"),
        ("voice.preview.chunk", b"preview-wav"),
    }


def test_stop_exception_cannot_strand_single_session_slot():
    factory = FailFirstStopFactory()
    client, _ = _client(factory)
    url = f"/v1/voice?token={SESSION_TOKEN}"

    with client.websocket_connect(url):
        pass
    with client.websocket_connect(url) as replacement:
        replacement.send_json({"type": "session.start"})
        assert replacement.receive_json()["type"] == "session.ready"

    assert len(factory.instances) == 2


@pytest.mark.asyncio
async def test_failed_writer_shutdown_never_enqueues_into_dead_queue():
    class FailingSocket:
        async def send_json(self, _payload: object) -> None:
            raise RuntimeError("send failed")

        async def send_bytes(self, _payload: bytes) -> None:
            raise RuntimeError("send failed")

    writer = _SocketWriter(FailingSocket())
    task = writer.start()
    with pytest.raises(RuntimeError, match="send failed"):
        await writer.send(
            ErrorMessage(type="error", code="test", message="test", recoverable=True)
        )
    assert task.done()

    await asyncio.wait_for(writer.close(), timeout=0.1)


@pytest.mark.asyncio
async def test_writer_failure_while_full_shutdown_cannot_block_slot_release():
    class BlockingFailSocket:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def send_json(self, _payload: object) -> None:
            self.entered.set()
            await self.release.wait()
            raise RuntimeError("send failed after close started")

        async def send_bytes(self, _payload: bytes) -> None:
            raise AssertionError("unexpected bytes")

    socket = BlockingFailSocket()
    writer = _SocketWriter(socket)
    writer.start()
    payload = ErrorMessage(type="error", code="test", message="test", recoverable=True)
    await writer.send(payload)
    await socket.entered.wait()
    for _ in range(32):
        await writer.send(payload)

    closing = asyncio.create_task(writer.close())
    await asyncio.sleep(0)
    socket.release.set()

    await asyncio.wait_for(closing, timeout=0.1)
    assert writer._queue.empty()
    await asyncio.wait_for(writer._queue.join(), timeout=0.1)


@pytest.mark.asyncio
async def test_failed_writer_wakes_producer_before_more_than_queue_capacity_hangs():
    class FailingSocket:
        async def send_json(self, _payload: object) -> None:
            raise RuntimeError("first send failed")

        async def send_bytes(self, _payload: bytes) -> None:
            raise AssertionError("unexpected bytes")

    async def many_outputs():
        for index in range(40):
            yield ErrorMessage(
                type="error",
                code=f"test-{index}",
                message="test",
                recoverable=True,
            )

    writer = _SocketWriter(FailingSocket())
    writer.start()
    try:
        with pytest.raises(RuntimeError, match="first send failed"):
            await asyncio.wait_for(_forward_outputs(many_outputs(), writer), timeout=0.1)
    finally:
        await writer.close()

    assert writer._queue.empty()
    await asyncio.wait_for(writer._queue.join(), timeout=0.1)
