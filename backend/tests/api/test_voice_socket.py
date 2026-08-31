from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voxagent.api.app import _SocketWriter, create_app
from voxagent.conversation.events import ErrorMessage

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
    task = asyncio.create_task(writer.run())
    await writer.send(
        ErrorMessage(type="error", code="test", message="test", recoverable=True)
    )
    with pytest.raises(RuntimeError, match="send failed"):
        await task

    await asyncio.wait_for(writer.close(task), timeout=0.1)


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
    task = asyncio.create_task(writer.run())
    payload = ErrorMessage(type="error", code="test", message="test", recoverable=True)
    await writer.send(payload)
    await socket.entered.wait()
    for _ in range(32):
        await writer.send(payload)

    closing = asyncio.create_task(writer.close(task))
    await asyncio.sleep(0)
    socket.release.set()

    await asyncio.wait_for(closing, timeout=0.1)
    assert writer._queue.empty()
    await asyncio.wait_for(writer._queue.join(), timeout=0.1)
