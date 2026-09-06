from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Event

import numpy as np
from fastapi.testclient import TestClient

from voxagent.api.app import create_app
from voxagent.conversation.history import ChatMessage
from voxagent.conversation.orchestrator import PREVIEW_TEXT, ConversationOrchestrator
from voxagent.speech.asr import AsrResult
from voxagent.speech.endpoint import EndpointDetector
from voxagent.speech.tts import AudioChunk
from voxagent.speech.vad import VadDecision
from voxagent.speech.voice_catalog import VoiceCatalog, VoiceProfile

FRAME = b"\x00" * 640
SESSION_TOKEN = base64.urlsafe_b64encode(b"p" * 32).decode("ascii").rstrip("=")


class ScriptedVad:
    def __init__(self) -> None:
        self._speaking = False

    def accept(self, frame: bytes) -> VadDecision:
        assert frame == FRAME
        if not self._speaking:
            self._speaking = True
            return VadDecision.STARTED
        return VadDecision.SPEECH


class ScriptedAsr:
    model_id = "e2e-asr"

    def __init__(self, *texts: str) -> None:
        self._texts = iter(texts)
        self.calls: list[tuple[np.ndarray, int]] = []

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        self.calls.append((samples.copy(), sample_rate))
        return AsrResult(next(self._texts), language="zh")


@dataclass(frozen=True)
class LlmCall:
    model: str
    messages: tuple[tuple[str, str], ...]


class ScriptedLlm:
    def __init__(self, *replies: tuple[str, ...]) -> None:
        self._replies = iter(replies)
        self.calls: list[LlmCall] = []

    async def stream_chat(
        self, model: str, messages: tuple[ChatMessage, ...]
    ) -> AsyncIterator[str]:
        self.calls.append(
            LlmCall(model, tuple((message.role, message.content) for message in messages))
        )
        for chunk in next(self._replies):
            await asyncio.sleep(0)
            yield chunk


@dataclass(frozen=True)
class TtsCall:
    text: str
    voice_key: str
    speed: float


class RecordingTts:
    def __init__(self) -> None:
        self.calls: list[TtsCall] = []

    def synthesize(self, text: str, voice_key: str, speed: float) -> AudioChunk:
        self.calls.append(TtsCall(text, voice_key, speed))
        return AudioChunk(b"RIFF-e2e", 24000, 0.25)


class RecordingConversationStore:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def add_user(self, turn_id: int, text: str, source: str) -> int:
        self.calls.append(("user", turn_id, text, source))
        return len(self.calls)

    async def complete_assistant(self, turn_id: int, text: str) -> None:
        self.calls.append(("assistant", turn_id, text))

    async def cancel_turn(self, turn_id: int) -> None:
        self.calls.append(("cancel", turn_id))

    async def reset(self) -> None:
        self.calls.append(("reset",))


class BlockingFirstTts(RecordingTts):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def synthesize(self, text: str, voice_key: str, speed: float) -> AudioChunk:
        self.calls.append(TtsCall(text, voice_key, speed))
        if len(self.calls) == 1:
            self.started.set()
            assert self.release.wait(timeout=2)
        return AudioChunk(b"RIFF-e2e", 24000, 0.25)


def catalog() -> VoiceCatalog:
    return VoiceCatalog(
        (
            VoiceProfile(
                voice_key="default_voice",
                display_name="声灵默认音色",
                description="自然清晰，适合日常对话",
                gender="neutral",
                engine="kokoro",
                native_voice_id=3,
                is_default=True,
                previewable=True,
            ),
        )
    )


class OrchestratorFactory:
    def __init__(
        self,
        *,
        llm: ScriptedLlm,
        tts: RecordingTts,
        asr: ScriptedAsr,
        conversation_store: RecordingConversationStore | None = None,
    ) -> None:
        self.llm = llm
        self.tts = tts
        self.asr = asr
        self.conversation_store = conversation_store
        self.instances: list[ConversationOrchestrator] = []

    def __call__(self) -> ConversationOrchestrator:
        instance = ConversationOrchestrator(
            model_id="qwen-local-e2e",
            vad=ScriptedVad(),
            endpoint=EndpointDetector("natural"),
            asr=self.asr,
            llm=self.llm,
            tts=self.tts,
            voice_catalog=catalog(),
            conversation_store=self.conversation_store,
        )
        self.instances.append(instance)
        return instance


def start_session(socket) -> list[dict[str, object]]:
    socket.send_json({"type": "session.start"})
    return [socket.receive_json(), socket.receive_json()]


def receive_turn(socket) -> tuple[list[dict[str, object]], list[bytes]]:
    events: list[dict[str, object]] = []
    audio: list[bytes] = []
    while True:
        event = socket.receive_json()
        events.append(event)
        if event["type"] == "tts.chunk":
            audio.append(socket.receive_bytes())
        if event["type"] == "assistant.done":
            return events, audio


def test_real_socket_keeps_text_and_voice_in_one_history_and_hides_native_voice_id():
    llm = ScriptedLlm(("文字回答。",), ("语音回答。",))
    tts = RecordingTts()
    asr = ScriptedAsr("语音问题")
    factory = OrchestratorFactory(llm=llm, tts=tts, asr=asr)

    with TestClient(create_app(factory, SESSION_TOKEN)) as client:
        with client.websocket_connect(f"/v1/voice?token={SESSION_TOKEN}") as socket:
            ready, voices = start_session(socket)
            assert ready["type"] == "session.ready"
            assert voices["type"] == "voices.available"
            assert "native" not in str(voices).lower()

            socket.send_json(
                {"type": "text.submit", "text": "文字问题", "speak_response": False}
            )
            text_events, text_audio = receive_turn(socket)
            assert [event["type"] for event in text_events] == [
                "assistant.delta",
                "assistant.done",
            ]
            assert text_audio == []

            socket.send_json(
                {"type": "voice.select", "voice_key": "default_voice", "speed": 1.2}
            )
            assert socket.receive_json() == {
                "type": "voice.selected",
                "voice_key": "default_voice",
                "speed": 1.2,
            }
            socket.send_json(
                {"type": "voice.preview", "voice_key": "default_voice", "speed": 1.2}
            )
            preview = socket.receive_json()
            preview_audio = socket.receive_bytes()
            assert preview["type"] == "voice.preview.chunk"
            assert preview["byte_length"] == len(preview_audio)
            assert "native" not in str(preview).lower()

            socket.send_bytes(FRAME)
            assert socket.receive_json()["type"] == "vad.started"
            socket.send_json({"type": "audio.commit"})
            voice_events, voice_audio = receive_turn(socket)

    assert [event["type"] for event in voice_events] == [
        "vad.stopped",
        "asr.final",
        "assistant.delta",
        "tts.started",
        "tts.chunk",
        "tts.done",
        "assistant.done",
    ]
    assert voice_events[1]["text"] == "语音问题"
    assert len(voice_audio) == 1
    assert voice_events[4]["byte_length"] == len(voice_audio[0])
    assert llm.calls[1].messages[-3:] == (
        ("user", "文字问题"),
        ("assistant", "文字回答。"),
        ("user", "语音问题"),
    )
    assert tts.calls == [
        TtsCall(PREVIEW_TEXT, "default_voice", 1.2),
        TtsCall("语音回答。", "default_voice", 1.2),
    ]
    assert asr.calls[0][1] == 16000


def test_browser_transcript_then_local_microphone_share_ordered_voice_history():
    llm = ScriptedLlm(("浏览器回答。",), ("本地回答。",))
    tts = RecordingTts()
    asr = ScriptedAsr("本地麦克风转写")
    store = RecordingConversationStore()
    factory = OrchestratorFactory(
        llm=llm,
        tts=tts,
        asr=asr,
        conversation_store=store,
    )

    with TestClient(create_app(factory, SESSION_TOKEN)) as client:
        with client.websocket_connect(f"/v1/voice?token={SESSION_TOKEN}") as socket:
            start_session(socket)
            socket.send_json(
                {
                    "type": "voice.transcript.submit",
                    "text": "浏览器最终转写",
                    "request_id": 41,
                }
            )
            browser_events, browser_audio = receive_turn(socket)

            assert [event["type"] for event in browser_events] == [
                "asr.final",
                "assistant.delta",
                "assistant.done",
            ]
            assert browser_audio == []
            assert browser_events[0]["text"] == "浏览器最终转写"
            assert browser_events[0]["request_id"] == 41

            socket.send_bytes(FRAME)
            assert socket.receive_json()["type"] == "vad.started"
            socket.send_json({"type": "audio.commit"})
            local_events, local_audio = receive_turn(socket)

    assert [event["type"] for event in local_events] == [
        "vad.stopped",
        "asr.final",
        "assistant.delta",
        "tts.started",
        "tts.chunk",
        "tts.done",
        "assistant.done",
    ]
    assert local_events[1]["text"] == "本地麦克风转写"
    assert len(local_audio) == 1
    assert llm.calls[1].messages[-3:] == (
        ("user", "浏览器最终转写"),
        ("assistant", "浏览器回答。"),
        ("user", "本地麦克风转写"),
    )
    assert store.calls == [
        ("user", 1, "浏览器最终转写", "voice"),
        ("assistant", 1, "浏览器回答。"),
        ("user", 2, "本地麦克风转写", "voice"),
        ("assistant", 2, "本地回答。"),
    ]


def test_text_barge_in_cancels_a_turn_blocked_in_tts_without_stale_audio():
    llm = ScriptedLlm(("旧回答。",), ("新回答",))
    tts = BlockingFirstTts()
    factory = OrchestratorFactory(llm=llm, tts=tts, asr=ScriptedAsr("未使用"))

    try:
        with TestClient(create_app(factory, SESSION_TOKEN)) as client:
            with client.websocket_connect(f"/v1/voice?token={SESSION_TOKEN}") as socket:
                start_session(socket)
                socket.send_json(
                    {"type": "text.submit", "text": "旧问题", "speak_response": True}
                )
                old_delta = socket.receive_json()
                assert old_delta["type"] == "assistant.delta"
                assert tts.started.wait(timeout=1)

                socket.send_json(
                    {"type": "text.submit", "text": "新问题", "speak_response": False}
                )
                replacement_events, replacement_audio = receive_turn(socket)

                assert [event["type"] for event in replacement_events[:2]] == [
                    "tts.started",
                    "turn.cancelled",
                ]
                assert replacement_events[1]["turn_id"] == old_delta["turn_id"]
                assert [event["type"] for event in replacement_events[2:]] == [
                    "assistant.delta",
                    "assistant.done",
                ]
                assert replacement_audio == []
                assert all(
                    event.get("turn_id") != old_delta["turn_id"]
                    or event["type"] in {"tts.started", "turn.cancelled"}
                    for event in replacement_events
                )
    finally:
        tts.release.set()
