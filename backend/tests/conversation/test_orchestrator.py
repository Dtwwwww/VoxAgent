import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from voxagent.conversation.context import ContextAssembler, KnowledgeContext, MemoryContext
from voxagent.conversation.events import ErrorMessage
from voxagent.conversation.history import SYSTEM_INSTRUCTION, ChatMessage
from voxagent.conversation.orchestrator import (
    MAX_UTTERANCE_FRAMES,
    ConversationOrchestrator,
)
from voxagent.conversation.persona import DEFAULT_PERSONA
from voxagent.conversation.state import Phase
from voxagent.memory.models import MemoryCandidate, MemoryKind
from voxagent.memory.policy import MemoryPolicy
from voxagent.speech.asr import AsrResult
from voxagent.speech.endpoint import EndpointDecision, EndpointDetector
from voxagent.speech.tts import AudioChunk
from voxagent.speech.vad import VadDecision
from voxagent.speech.voice_catalog import VoiceCatalog, VoiceProfile

FRAME = b"\x00" * 640


class FakeVad:
    def __init__(self, decisions: list[VadDecision]) -> None:
        self.decisions = iter(decisions)

    def accept(self, frame: bytes) -> VadDecision:
        assert frame == FRAME
        return next(self.decisions)


class FakeEndpoint:
    def accept(self, decision: VadDecision, now_ms: int, partial_text: str) -> EndpointDecision:
        assert isinstance(now_ms, int)
        assert isinstance(partial_text, str)
        return (
            EndpointDecision.COMMIT
            if decision is VadDecision.STOPPED
            else EndpointDecision.CONTINUE
        )


class RecordingEndpoint(FakeEndpoint):
    def __init__(self) -> None:
        self.calls: list[tuple[VadDecision, int, str]] = []

    def accept(self, decision: VadDecision, now_ms: int, partial_text: str) -> EndpointDecision:
        self.calls.append((decision, now_ms, partial_text))
        return super().accept(decision, now_ms, partial_text)


class FakeAsr:
    model_id = "fake-asr"

    def __init__(self, texts: list[str]) -> None:
        self.texts = iter(texts)
        self.calls: list[tuple[np.ndarray, int]] = []

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        self.calls.append((samples.copy(), sample_rate))
        return AsrResult(next(self.texts), language="zh", emotion="HAPPY")


class BlockingAsr(FakeAsr):
    def __init__(self, text: str) -> None:
        super().__init__([text])
        self.started = Event()
        self.release = Event()

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().transcribe(samples, sample_rate)


class FailingAsr(FakeAsr):
    def transcribe(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        raise RuntimeError("recognizer failed")


class FakePartialAsr:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1

    def accept(self, samples: np.ndarray) -> SimpleNamespace:
        assert samples.shape == (320,)
        return SimpleNamespace(text="我觉得那个", updated=True)


class SequencedPartialAsr(FakePartialAsr):
    def __init__(self, texts: list[str]) -> None:
        super().__init__()
        self.texts = iter(texts)

    def accept(self, samples: np.ndarray) -> SimpleNamespace:
        assert samples.shape == (320,)
        return SimpleNamespace(text=next(self.texts), updated=True)


class BlockingPartialAsr(FakePartialAsr):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()
        self.running = False
        self.reset_while_running = False

    def reset(self) -> None:
        if self.running:
            self.reset_while_running = True
        super().reset()

    def accept(self, samples: np.ndarray) -> SimpleNamespace:
        self.running = True
        self.started.set()
        try:
            assert self.release.wait(timeout=2)
            return SimpleNamespace(text="不应写回", updated=True)
        finally:
            self.running = False


class FailingPartialAsr(FakePartialAsr):
    def accept(self, samples: np.ndarray) -> SimpleNamespace:
        raise RuntimeError("partial recognizer failed")


@dataclass
class LlmCall:
    model: str
    messages: list[dict[str, str]]


class FakeLlm:
    def __init__(self, replies: list[list[str]]) -> None:
        self.replies = iter(replies)
        self.calls: list[LlmCall] = []

    async def stream_chat(
        self, model: str, messages: tuple[ChatMessage, ...]
    ) -> AsyncIterator[str]:
        self.calls.append(
            LlmCall(model, [{"role": item.role, "content": item.content} for item in messages])
        )
        for chunk in next(self.replies):
            await asyncio.sleep(0)
            yield chunk


class FakeMemoryProposer:
    def __init__(
        self,
        candidates: tuple[MemoryCandidate, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.candidates = candidates
        self.error = error
        self.calls: list[tuple[str, str, str, int]] = []

    async def propose(
        self,
        model_id: str,
        user_text: str,
        assistant_text: str,
        *,
        source_message_id: int,
    ) -> tuple[MemoryCandidate, ...]:
        self.calls.append((model_id, user_text, assistant_text, source_message_id))
        if self.error is not None:
            raise self.error
        return self.candidates


class FakeConversationStore:
    def __init__(self, source_message_id: int = 42) -> None:
        self.source_message_id = source_message_id
        self.calls: list[tuple[object, ...]] = []

    async def add_user(self, turn_id: int, text: str, source: str) -> int:
        self.calls.append(("user", turn_id, text, source))
        return self.source_message_id

    async def complete_assistant(self, turn_id: int, text: str) -> None:
        self.calls.append(("assistant", turn_id, text))

    async def cancel_turn(self, turn_id: int) -> None:
        self.calls.append(("cancel", turn_id))

    async def reset(self) -> None:
        self.calls.append(("reset",))


@dataclass
class TtsCall:
    text: str
    voice_key: str
    speed: float


class FakeTts:
    def __init__(self, *, block: bool = False, fail: bool = False) -> None:
        self.calls: list[TtsCall] = []
        self.fail = fail
        self.started = Event()
        self.release = Event()
        if not block:
            self.release.set()

    def synthesize(self, text: str, voice_key: str, speed: float) -> AudioChunk:
        self.calls.append(TtsCall(text, voice_key, speed))
        self.started.set()
        assert self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("tts unavailable")
        return AudioChunk(b"RIFF-fake", 24000, 0.5)


class BlockingLlm(FakeLlm):
    def __init__(self) -> None:
        super().__init__([["半截"], ["新回答"]])
        self.first_delta_sent = asyncio.Event()

    async def stream_chat(
        self, model: str, messages: tuple[ChatMessage, ...]
    ) -> AsyncIterator[str]:
        self.calls.append(
            LlmCall(model, [{"role": item.role, "content": item.content} for item in messages])
        )
        if len(self.calls) == 1:
            yield "半截"
            self.first_delta_sent.set()
            await asyncio.Future()
        else:
            yield "新回答"


def catalog() -> VoiceCatalog:
    return VoiceCatalog(
        (
            VoiceProfile(
                voice_key="clear_female",
                display_name="清澈女声",
                description="明亮清晰",
                gender="female",
                engine="kokoro",
                native_voice_id=3,
                is_default=True,
                previewable=True,
            ),
            VoiceProfile(
                voice_key="private_voice",
                display_name="内部音色",
                description="不公开试听",
                gender="neutral",
                engine="kokoro",
                native_voice_id=27,
                is_default=False,
                previewable=False,
            ),
            VoiceProfile(
                voice_key="steady_male",
                display_name="沉稳男声",
                description="沉稳自然",
                gender="male",
                engine="kokoro",
                native_voice_id=13,
                is_default=False,
                previewable=True,
            ),
        )
    )


def make_orchestrator(
    *,
    vad: FakeVad | None = None,
    asr_texts: list[str] | None = None,
    replies: list[list[str]] | None = None,
    tts: FakeTts | None = None,
    context_assembler: ContextAssembler | None = None,
    memory_proposer: FakeMemoryProposer | None = None,
    conversation_store: FakeConversationStore | None = None,
    max_utterance_frames: int = MAX_UTTERANCE_FRAMES,
) -> tuple[ConversationOrchestrator, FakeLlm, FakeTts]:
    llm = FakeLlm(replies or [["回答。"]])
    tts = tts or FakeTts()
    orchestrator = ConversationOrchestrator(
        model_id="qwen-local",
        vad=vad or FakeVad([]),
        endpoint=FakeEndpoint(),
        asr=FakeAsr(asr_texts or []),
        llm=llm,
        tts=tts,
        voice_catalog=catalog(),
        clock_ms=lambda: 1234,
        max_utterance_frames=max_utterance_frames,
        context_assembler=context_assembler,
        memory_proposer=memory_proposer,
        memory_policy=MemoryPolicy() if memory_proposer is not None else None,
        conversation_store=conversation_store,
    )
    return orchestrator, llm, tts


async def collect_audio_events(orchestrator, frames):
    events = []
    for frame in frames:
        events.extend([event async for event in orchestrator.accept_audio(frame)])
    return events


@pytest.mark.asyncio
async def test_voice_turn_emits_exact_metadata_and_audio_order():
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED]),
        asr_texts=["你好"],
        replies=[["好的。"]],
    )

    outputs = await collect_audio_events(orchestrator, [FRAME, FRAME])

    assert [item.type if hasattr(item, "type") else "binary" for item in outputs] == [
        "vad.started",
        "vad.stopped",
        "asr.final",
        "assistant.delta",
        "tts.started",
        "tts.chunk",
        "binary",
        "tts.done",
        "assistant.done",
    ]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_text_and_voice_share_ordered_history_and_voice_uses_selected_tts():
    orchestrator, llm, tts = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED]),
        asr_texts=["第二问"],
        replies=[["第一答"], ["第二答。"]],
    )

    _ = [event async for event in orchestrator.submit_text("第一问", speak_response=False)]
    assert tts.calls == []
    await collect_audio_events(orchestrator, [FRAME, FRAME])

    assert llm.calls[-1].messages[-3:] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]
    assert tts.calls[-1].voice_key == "clear_female"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_browser_voice_transcript_uses_voice_reply_path_without_tts():
    store = FakeConversationStore()
    orchestrator, _, tts = make_orchestrator(
        replies=[["测试回答"]],
        context_assembler=None,
        memory_proposer=None,
        conversation_store=store,
    )

    outputs = [
        item
        async for item in orchestrator.submit_voice_transcript("浏览器识别结果", 11)
    ]

    assert [item.type for item in outputs] == [
        "asr.final",
        "assistant.delta",
        "assistant.done",
    ]
    assert outputs[0].text == "浏览器识别结果"
    assert outputs[0].request_id == 11
    assert orchestrator.history.messages_for_model()[-2:] == (
        ChatMessage("user", "浏览器识别结果"),
        ChatMessage("assistant", "测试回答"),
    )
    assert store.calls[0] == ("user", 1, "浏览器识别结果", "voice")
    assert tts.calls == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_browser_transcript_cancellation_and_echo_keep_their_request_ids():
    orchestrator, _, _ = make_orchestrator(replies=[["旧回答"], ["新回答"]])
    old = orchestrator.submit_voice_transcript("相同问题", 41)
    old_echo = await anext(old)

    replacement = orchestrator.submit_voice_transcript("相同问题", 42)
    cancelled = await anext(replacement)
    new_echo = await anext(replacement)

    assert old_echo.type == "asr.final" and old_echo.request_id == 41
    assert cancelled.type == "turn.cancelled" and cancelled.request_id == 41
    assert new_echo.type == "asr.final" and new_echo.request_id == 42
    assert [item async for item in old] == []
    _ = [item async for item in replacement]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_browser_voice_transcript_echoes_before_context_reply_and_memory():
    assembler = ContextAssembler(
        DEFAULT_PERSONA,
        lambda _query, _limit: (
            MemoryContext(2, "喜欢茶", 0.9, 8, "我喜欢喝茶", 3),
        ),
        lambda _query, _limit: (),
    )
    proposer = FakeMemoryProposer(
        (MemoryCandidate(MemoryKind.PREFERENCE, "喜欢乌龙茶", 0.8, None),)
    )
    store = FakeConversationStore(source_message_id=42)
    orchestrator, _, tts = make_orchestrator(
        replies=[["测试回答"]],
        context_assembler=assembler,
        memory_proposer=proposer,
        conversation_store=store,
    )

    outputs = [
        item async for item in orchestrator.submit_voice_transcript("浏览器识别结果", 12)
    ]

    assert [item.type for item in outputs] == [
        "asr.final",
        "context.sources",
        "assistant.delta",
        "assistant.done",
        "memory.proposed",
    ]
    assert store.calls[0] == ("user", 1, "浏览器识别结果", "voice")
    assert proposer.calls == [("qwen-local", "浏览器识别结果", "测试回答", 42)]
    assert tts.calls == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_unknown_voice_key_is_recoverable_and_selection_changes_session_only():
    orchestrator, _, _ = make_orchestrator()

    error = orchestrator.select_voice("missing", 1.0)
    selected = orchestrator.select_voice("steady_male", 1.2)

    assert isinstance(error, ErrorMessage)
    assert error.recoverable is True
    assert selected.type == "voice.selected"
    assert selected.voice_key == "steady_male"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_requires_completed_assistant_and_does_not_add_history():
    orchestrator, llm, tts = make_orchestrator(replies=[["完成回答"]])
    done = [event async for event in orchestrator.submit_text("问题", speak_response=False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")
    calls_before = len(llm.calls)

    replay = [item async for item in orchestrator.speak_message(turn_id)]
    missing = [item async for item in orchestrator.speak_message(999)]

    assert [item.type if hasattr(item, "type") else "binary" for item in replay] == [
        "tts.started",
        "tts.chunk",
        "binary",
        "tts.done",
    ]
    assert tts.calls[-1].text == "完成回答"
    assert len(llm.calls) == calls_before
    assert isinstance(missing[0], ErrorMessage) and missing[0].recoverable
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_emits_one_started_done_pair_and_splits_long_text():
    response = "第一段。" + "较长的朗读内容" * 20
    orchestrator, _, tts = make_orchestrator(replies=[[response]])
    done = [event async for event in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [item async for item in orchestrator.speak_message(turn_id)]

    event_types = [item.type if hasattr(item, "type") else "binary" for item in replay]
    assert event_types[0] == "tts.started"
    assert event_types[-1] == "tts.done"
    assert event_types.count("tts.started") == 1
    assert event_types.count("tts.done") == 1
    assert len(tts.calls) >= 2
    assert all(len(call.text) <= 120 for call in tts.calls)
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_echoes_request_id_on_every_tts_event():
    orchestrator, _, _ = make_orchestrator(replies=[["需要朗读"]])
    done = [event async for event in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [item async for item in orchestrator.speak_message(turn_id, request_id=42)]

    events = [item for item in replay if hasattr(item, "type")]
    assert {item.request_id for item in events} == {42}
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_synthesizes_only_the_server_stored_remainder():
    orchestrator, _, tts = make_orchestrator(replies=[["Hi😀。第二句。最后没有句号"]])
    done = [event async for event in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [
        item
        async for item in orchestrator.speak_message(
            turn_id,
            request_id=42,
            start_offset=len("Hi😀。"),
        )
    ]

    assert [call.text for call in tts.calls] == ["第二句。", "最后没有句号"]
    assert {item.request_id for item in replay if hasattr(item, "request_id")} == {42}
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_rejects_an_offset_outside_the_stored_assistant_text():
    orchestrator, _, tts = make_orchestrator(replies=[["短回答"]])
    done = [event async for event in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [
        item
        async for item in orchestrator.speak_message(
            turn_id, request_id=42, start_offset=999
        )
    ]

    assert [item.type for item in replay] == ["error"]
    assert replay[0].code == "invalid_speech_offset"
    assert tts.calls == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_rejects_text_that_is_empty_after_tts_cleanup():
    orchestrator, _, _ = make_orchestrator(replies=[["```python\nprint('hidden')\n```"]])
    done = [event async for event in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [item async for item in orchestrator.speak_message(turn_id)]

    assert [item.type for item in replay] == ["tts.error"]
    assert replay[0].code == "tts_empty"
    assert replay[0].recoverable is True
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_assistant_response_preserves_emoji_in_stream_history_and_strips_from_tts():
    response_text = "你好😀，欢迎来到 VoxAgent！"
    orchestrator, _, tts = make_orchestrator(replies=[[response_text]])

    outputs = [item async for item in orchestrator.submit_text("问题", speak_response=True)]
    turn_id = next(
        item.turn_id for item in outputs if getattr(item, "type", None) == "assistant.done"
    )

    assert [
        item.delta for item in outputs if getattr(item, "type", None) == "assistant.delta"
    ] == [response_text]
    assert orchestrator.history.assistant_text(turn_id) == response_text
    assert [call.text for call in tts.calls] == ["你好，欢迎来到 VoxAgent！"]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_replay_tts_strips_emoji_from_completed_assistant_message():
    response_text = "Great job 👍🏽! 继续加油。"
    orchestrator, _, tts = make_orchestrator(replies=[[response_text]])
    done = [item async for item in orchestrator.submit_text("问题", speak_response=False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = [item async for item in orchestrator.speak_message(turn_id)]

    assert [item.type if hasattr(item, "type") else "binary" for item in replay] == [
        "tts.started",
        "tts.chunk",
        "binary",
        "tts.done",
    ]
    assert tts.calls[-1].text == "Great job ! 继续加油。"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_emoji_only_assistant_response_is_displayed_but_never_spoken():
    response_text = "👩🏽‍💻 🇨🇳 1️⃣"
    orchestrator, _, tts = make_orchestrator(replies=[[response_text]])

    outputs = [item async for item in orchestrator.submit_text("问题", speak_response=True)]
    turn_id = next(
        item.turn_id for item in outputs if getattr(item, "type", None) == "assistant.done"
    )
    replay = [item async for item in orchestrator.speak_message(turn_id)]

    assert [
        item.delta for item in outputs if getattr(item, "type", None) == "assistant.delta"
    ] == [response_text]
    assert orchestrator.history.assistant_text(turn_id) == response_text
    assert [item.type if hasattr(item, "type") else "binary" for item in outputs] == [
        "assistant.delta",
        "assistant.done",
    ]
    assert [item.type for item in replay] == ["tts.error"]
    assert replay[0].code == "tts_empty"
    assert tts.calls == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_speak_message_reports_speaking_phase_while_replay_runs():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(tts=blocking_tts, replies=[["完成回答"]])
    done = [item async for item in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")

    replay = orchestrator.speak_message(turn_id)
    first_output = asyncio.create_task(anext(replay))
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    assert orchestrator.state.phase is Phase.SPEAKING
    blocking_tts.release.set()
    assert (await first_output).type == "tts.started"
    assert (await anext(replay)).type == "tts.chunk"
    assert isinstance(await anext(replay), bytes)
    assert (await anext(replay)).type == "tts.done"
    with pytest.raises(StopAsyncIteration):
        await anext(replay)
    assert orchestrator.state.phase is Phase.IDLE
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_cancelled_replay_owner_can_be_reused_before_old_generator_resumes():
    orchestrator, _, _ = make_orchestrator(replies=[["完成回答"]])
    done = [item async for item in orchestrator.submit_text("问题", False)]
    turn_id = next(item.turn_id for item in done if item.type == "assistant.done")
    old_replay = orchestrator.speak_message(turn_id)
    old_metadata = await anext(old_replay)
    assert old_metadata.type == "tts.started"

    cancelled = await orchestrator.cancel_active()
    new_replay = [item async for item in orchestrator.speak_message(turn_id)]

    assert cancelled is not None and cancelled.turn_id == turn_id
    assert [item.type if hasattr(item, "type") else "binary" for item in new_replay] == [
        "tts.started",
        "tts.chunk",
        "binary",
        "tts.done",
    ]
    assert [item async for item in old_replay] == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_preview_is_isolated_and_busy_during_conversation_reply():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(tts=blocking_tts, replies=[["会被朗读。"]])
    response = orchestrator.submit_text("问题", speak_response=True)
    assert (await anext(response)).type == "assistant.delta"
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    busy = [item async for item in orchestrator.preview_voice("clear_female", 1.0)]
    assert isinstance(busy[0], ErrorMessage) and busy[0].recoverable

    blocking_tts.release.set()
    _ = [item async for item in response]
    preview = [item async for item in orchestrator.preview_voice("clear_female", 1.0)]
    assert [item.type if hasattr(item, "type") else "binary" for item in preview] == [
        "voice.preview.chunk",
        "binary",
    ]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_text_barge_in_waits_for_old_tts_and_no_stale_output_survives():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(
        tts=blocking_tts,
        replies=[["旧回答。"], ["新回答"]],
    )
    first = orchestrator.submit_text("旧问题", speak_response=True)
    first_delta = await anext(first)
    old_turn_id = first_delta.turn_id
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    replacement_task = asyncio.create_task(
        _collect(orchestrator.submit_text("新问题", speak_response=False))
    )
    await asyncio.sleep(0.02)
    assert not replacement_task.done()
    blocking_tts.release.set()
    replacement = await replacement_task

    assert replacement[0].type == "turn.cancelled"
    assert replacement[0].turn_id == old_turn_id
    assert all(getattr(item, "turn_id", None) != old_turn_id for item in replacement[1:])
    later_old = [item async for item in first]
    assert later_old == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_voice_barge_in_atomically_replaces_old_tts_turn():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED, VadDecision.STARTED]),
        asr_texts=["旧语音"],
        replies=[["旧回答。"]],
        tts=blocking_tts,
    )
    assert [item.type async for item in orchestrator.accept_audio(FRAME)] == ["vad.started"]
    old = orchestrator.accept_audio(FRAME)
    assert (await anext(old)).type == "vad.stopped"
    assert (await anext(old)).type == "asr.final"
    old_delta = await anext(old)
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    replacement_task = asyncio.create_task(_collect(orchestrator.accept_audio(FRAME)))
    await asyncio.sleep(0.02)
    assert not replacement_task.done()
    blocking_tts.release.set()
    replacement = await replacement_task

    assert [item.type for item in replacement] == ["turn.cancelled", "vad.started"]
    assert replacement[0].turn_id == old_delta.turn_id
    assert replacement[1].turn_id == old_delta.turn_id + 1
    assert [item async for item in old] == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_cancelled_partial_reply_is_absent_from_next_model_call():
    orchestrator, _, _ = make_orchestrator(replies=[["unused"]])
    llm = BlockingLlm()
    orchestrator.llm = llm
    first = orchestrator.submit_text("旧问题", speak_response=False)
    partial = await anext(first)
    assert partial.delta == "半截"

    replacement = [item async for item in orchestrator.submit_text("新问题", False)]

    assert replacement[0].type == "turn.cancelled"
    assert llm.calls[-1].messages[-1] == {"role": "user", "content": "新问题"}
    assert all(
        message["content"] not in {"旧问题", "半截"} for message in llm.calls[-1].messages
    )
    assert [item async for item in first] == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_new_preview_waits_for_old_preview_and_does_not_consume_turn_id():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(tts=blocking_tts, replies=[["回答"]])
    first = orchestrator.preview_voice("clear_female", 1.0)
    first_next = asyncio.create_task(anext(first))
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    second_task = asyncio.create_task(
        _collect(orchestrator.preview_voice("steady_male", 1.2))
    )
    await asyncio.sleep(0.02)
    assert not second_task.done()
    blocking_tts.release.set()
    second = await second_task

    with pytest.raises(StopAsyncIteration):
        await first_next
    assert second[0].type == "voice.preview.chunk"
    assert second[0].preview_id == 2
    response = [item async for item in orchestrator.submit_text("首轮", False)]
    assert next(item.turn_id for item in response if item.type == "assistant.done") == 1
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_explicit_cancel_and_stop_await_work_then_release_history():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(
        tts=blocking_tts, replies=[["会被取消。"], ["保留"]]
    )
    response = orchestrator.submit_text("取消我", True)
    assert (await anext(response)).type == "assistant.delta"
    await asyncio.to_thread(blocking_tts.started.wait, 1)
    cancellation = asyncio.create_task(orchestrator.cancel_active())
    await asyncio.sleep(0.02)
    assert not cancellation.done()
    blocking_tts.release.set()

    cancelled = await cancellation
    assert cancelled is not None and cancelled.type == "turn.cancelled"
    assert [item async for item in response] == []
    _ = [item async for item in orchestrator.submit_text("保留问题", False)]
    await orchestrator.stop()
    assert orchestrator.history.messages_for_model() == (
        ChatMessage("system", SYSTEM_INSTRUCTION),
    )


@pytest.mark.asyncio
async def test_invalid_speed_and_non_previewable_voice_are_recoverable():
    orchestrator, _, _ = make_orchestrator()

    speed_error = orchestrator.select_voice("clear_female", 0.9)
    preview_error = [item async for item in orchestrator.preview_voice("private_voice", 1.0)]

    assert isinstance(speed_error, ErrorMessage) and speed_error.recoverable
    assert isinstance(preview_error[0], ErrorMessage) and preview_error[0].recoverable
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_tts_failure_keeps_completed_text_history_and_emits_done():
    failing_tts = FakeTts(fail=True)
    orchestrator, llm, _ = make_orchestrator(tts=failing_tts, replies=[["文字仍完整。"], ["继续"]])

    failed_speech = [item async for item in orchestrator.submit_text("第一问", True)]
    _ = [item async for item in orchestrator.submit_text("第二问", False)]

    assert [item.type for item in failed_speech] == [
        "assistant.delta",
        "tts.started",
        "tts.error",
        "assistant.done",
    ]
    assert llm.calls[-1].messages[-3:] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "文字仍完整。"},
        {"role": "user", "content": "第二问"},
    ]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_text_submission_cancels_listening_turn_and_clears_audio():
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED]), replies=[["文字回答"]]
    )
    listening = [item async for item in orchestrator.accept_audio(FRAME)]

    replacement = [item async for item in orchestrator.submit_text("改用文字", False)]

    assert listening[0].type == "vad.started"
    assert replacement[0].type == "turn.cancelled"
    assert replacement[0].turn_id == listening[0].turn_id
    assert orchestrator._audio_frames == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_done_but_undrained_old_turn_is_cancelled_but_kept_in_history():
    orchestrator, llm, _ = make_orchestrator(replies=[["旧回答"], ["新回答"]])
    old = orchestrator.submit_text("旧问题", False)
    old_delta = await anext(old)
    await asyncio.sleep(0.02)

    replacement = [item async for item in orchestrator.submit_text("新问题", False)]

    assert replacement[0].type == "turn.cancelled"
    assert replacement[0].turn_id == old_delta.turn_id
    assert [item async for item in old] == []
    assert {"旧问题", "旧回答"}.issubset(
        {message["content"] for message in llm.calls[-1].messages}
    )
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_busy_preview_yield_does_not_hold_action_lock():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(tts=blocking_tts, replies=[["回答。"]])
    response = orchestrator.submit_text("问题", True)
    assert (await anext(response)).type == "assistant.delta"
    await asyncio.to_thread(blocking_tts.started.wait, 1)
    busy = orchestrator.preview_voice("clear_female", 1.0)
    assert (await anext(busy)).type == "error"

    blocking_tts.release.set()
    cancelled = await asyncio.wait_for(orchestrator.cancel_active(), timeout=0.2)

    assert cancelled is not None
    await busy.aclose()
    assert [item async for item in response] == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_final_asr_and_text_submission_have_one_atomic_turn_boundary():
    asr = BlockingAsr("语音问题")
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED]),
        replies=[["语音回答"], ["文字回答"]],
    )
    orchestrator.asr = asr
    _ = [item async for item in orchestrator.accept_audio(FRAME)]
    voice_commit = asyncio.create_task(_collect(orchestrator.accept_audio(FRAME)))
    await asyncio.to_thread(asr.started.wait, 1)

    text = asyncio.create_task(_collect(orchestrator.submit_text("文字接管", False)))
    await asyncio.sleep(0.02)
    assert not text.done()
    asr.release.set()
    voice_outputs = await voice_commit
    text_outputs = await text

    assert [item.type for item in voice_outputs[:2]] == ["vad.stopped", "asr.final"]
    assert text_outputs[0].type == "turn.cancelled"
    assert text_outputs[-1].type == "assistant.done"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_turn_state_reports_speaking_during_tts():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(tts=blocking_tts, replies=[["回答。"]])
    response = orchestrator.submit_text("问题", True)
    assert (await anext(response)).type == "assistant.delta"
    await asyncio.to_thread(blocking_tts.started.wait, 1)

    assert orchestrator.state.phase is Phase.SPEAKING
    blocking_tts.release.set()
    _ = [item async for item in response]
    assert orchestrator.state.phase is Phase.IDLE
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_partial_asr_text_and_clock_drive_endpoint_policy():
    endpoint = RecordingEndpoint()
    partial = FakePartialAsr()
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.SPEECH, VadDecision.STOPPED]),
        asr_texts=["完整问题"],
        replies=[["回答"]],
    )
    orchestrator.endpoint = endpoint
    orchestrator.partial_asr = partial

    outputs = await collect_audio_events(orchestrator, [FRAME, FRAME, FRAME])

    assert partial.reset_calls == 2
    assert endpoint.calls[-1] == (VadDecision.STOPPED, 1234, "我觉得那个")
    assert [item.text for item in outputs if getattr(item, "type", None) == "asr.partial"] == [
        "我觉得那个"
    ]
    assert [item.text for item in outputs if getattr(item, "type", None) == "asr.final"] == [
        "完整问题"
    ]
    assert orchestrator.history.messages_for_model()[-2:] == (
        ChatMessage("user", "完整问题"),
        ChatMessage("assistant", "回答"),
    )
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_partial_asr_emits_trimmed_text_once_and_never_enters_history():
    partial = SequencedPartialAsr(["  我觉得那个  ", "我觉得那个", "   "])
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad(
            [
                VadDecision.STARTED,
                VadDecision.SPEECH,
                VadDecision.SPEECH,
                VadDecision.SPEECH,
            ]
        )
    )
    orchestrator.partial_asr = partial

    started = [item async for item in orchestrator.accept_audio(FRAME)]
    updated = [item async for item in orchestrator.accept_audio(FRAME)]
    unchanged = [item async for item in orchestrator.accept_audio(FRAME)]
    empty = [item async for item in orchestrator.accept_audio(FRAME)]

    assert [item.type for item in started] == ["vad.started"]
    assert [item.type for item in updated] == ["asr.partial"]
    assert updated[0].text == "我觉得那个"
    assert unchanged == []
    assert empty == []
    assert orchestrator.history.messages_for_model() == (
        ChatMessage("system", SYSTEM_INSTRUCTION),
    )
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_asr_failure_is_recoverable_and_does_not_enter_history():
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED])
    )
    orchestrator.asr = FailingAsr([])
    _ = [item async for item in orchestrator.accept_audio(FRAME)]

    outputs = [item async for item in orchestrator.accept_audio(FRAME)]

    assert [item.type for item in outputs] == ["vad.stopped", "error"]
    assert outputs[-1].recoverable is True
    assert orchestrator.history.messages_for_model() == (
        ChatMessage("system", SYSTEM_INSTRUCTION),
    )
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_preview_failure_is_recoverable():
    orchestrator, _, _ = make_orchestrator(tts=FakeTts(fail=True))

    outputs = [item async for item in orchestrator.preview_voice("clear_female", 1.0)]

    assert len(outputs) == 1
    assert outputs[0].type == "error" and outputs[0].recoverable
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_more_than_queue_capacity_barge_ins_do_not_leave_terminal_batches():
    orchestrator, _, _ = make_orchestrator(replies=[["回答"] for _ in range(40)])
    abandoned = []

    async def interrupt_repeatedly():
        current = orchestrator.submit_text("问题-0", False)
        assert (await anext(current)).type == "assistant.delta"
        await asyncio.sleep(0)
        for index in range(1, 40):
            abandoned.append(current)
            replacement = orchestrator.submit_text(f"问题-{index}", False)
            assert (await anext(replacement)).type == "turn.cancelled"
            assert (await anext(replacement)).type == "assistant.delta"
            current = replacement
            await asyncio.sleep(0)
        _ = [item async for item in current]

    await asyncio.wait_for(interrupt_repeatedly(), timeout=2)
    for generator in abandoned:
        await generator.aclose()
    await asyncio.wait_for(orchestrator._outputs.join(), timeout=0.2)
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_cancel_after_vad_stopped_suppresses_direct_asr_final():
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.STOPPED]),
        asr_texts=["旧语音"],
        replies=[["旧回答"], ["新回答"]],
    )
    _ = [item async for item in orchestrator.accept_audio(FRAME)]
    old = orchestrator.accept_audio(FRAME)
    stopped = await anext(old)
    assert stopped.type == "vad.stopped"

    replacement = [item async for item in orchestrator.submit_text("文字接管", False)]
    remaining_old = [item async for item in old]

    assert replacement[0].type == "turn.cancelled"
    assert all(getattr(item, "type", None) != "asr.final" for item in remaining_old)
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_cancel_between_old_turn_cancelled_and_new_vad_started_suppresses_start():
    blocking_tts = FakeTts(block=True)
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED]),
        replies=[["旧回答。"], ["文字回答"]],
        tts=blocking_tts,
    )
    old = orchestrator.submit_text("旧问题", True)
    assert (await anext(old)).type == "assistant.delta"
    await asyncio.to_thread(blocking_tts.started.wait, 1)
    audio = orchestrator.accept_audio(FRAME)
    first_audio = asyncio.create_task(anext(audio))
    blocking_tts.release.set()
    assert (await first_audio).type == "turn.cancelled"

    _ = [item async for item in orchestrator.submit_text("文字接管", False)]
    remaining_audio = [item async for item in audio]

    assert all(getattr(item, "type", None) != "vad.started" for item in remaining_audio)
    assert [item async for item in old] == []
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_stop_is_terminal_before_or_after_task_group_start():
    never_started, _, _ = make_orchestrator(vad=FakeVad([]))
    await never_started.stop()
    before_phase = never_started.state.phase
    before_history = never_started.history.messages_for_model()

    assert [item async for item in never_started.submit_text("不能提交", False)][0].code == (
        "session_stopped"
    )
    assert [item async for item in never_started.submit_text("   ", False)][0].code == (
        "session_stopped"
    )
    assert [item async for item in never_started.accept_audio(FRAME)][0].code == "session_stopped"
    assert [item async for item in never_started.speak_message(1)][0].code == "session_stopped"
    assert [item async for item in never_started.preview_voice("clear_female", 1.0)][0].code == (
        "session_stopped"
    )
    assert [item async for item in never_started.preview_voice("missing", 0.9)][0].code == (
        "session_stopped"
    )
    assert never_started.select_voice("steady_male", 1.2).code == "session_stopped"
    assert await never_started.cancel_active() is None
    assert never_started.state.phase is before_phase is Phase.STOPPED
    assert never_started.history.messages_for_model() == before_history

    started, _, _ = make_orchestrator(replies=[["完成"]])
    _ = [item async for item in started.submit_text("问题", False)]
    await started.stop()
    assert [item async for item in started.submit_text("不能重启", False)][0].code == (
        "session_stopped"
    )
    assert started.state.phase is Phase.STOPPED


@pytest.mark.asyncio
async def test_vad_restart_before_endpoint_threshold_continues_same_turn_and_audio():
    clock = iter([0, 500, 600, 2000])
    asr = FakeAsr(["连续问题"])
    llm = FakeLlm([["回答"]])
    tts = FakeTts()
    orchestrator = ConversationOrchestrator(
        model_id="qwen-local",
        vad=FakeVad(
            [
                VadDecision.STARTED,
                VadDecision.STOPPED,
                VadDecision.STARTED,
                VadDecision.STOPPED,
            ]
        ),
        endpoint=EndpointDetector("natural"),
        asr=asr,
        llm=llm,
        tts=tts,
        voice_catalog=catalog(),
        clock_ms=lambda: next(clock),
    )

    outputs = await collect_audio_events(orchestrator, [FRAME, FRAME, FRAME, FRAME])

    assert [item.type if hasattr(item, "type") else "binary" for item in outputs].count(
        "vad.started"
    ) == 1
    assert "turn.cancelled" not in [getattr(item, "type", None) for item in outputs]
    assert asr.calls[0][0].shape == (640,)
    await orchestrator.stop()


async def _start_blocked_partial(orchestrator, partial):
    _ = [item async for item in orchestrator.accept_audio(FRAME)]
    pending = asyncio.create_task(_collect(orchestrator.accept_audio(FRAME)))
    await asyncio.to_thread(partial.started.wait, 1)
    return pending


@pytest.mark.asyncio
@pytest.mark.parametrize("takeover", ["text", "voice", "stop"])
async def test_partial_asr_is_awaited_before_reset_on_takeover(takeover):
    decisions = [VadDecision.STARTED, VadDecision.SPEECH]
    if takeover == "voice":
        decisions.append(VadDecision.STARTED)
    partial = BlockingPartialAsr()
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad(decisions), replies=[["回答"]]
    )
    orchestrator.partial_asr = partial
    pending_partial = await _start_blocked_partial(orchestrator, partial)

    if takeover == "text":
        takeover_task = asyncio.create_task(_collect(orchestrator.submit_text("接管", False)))
    elif takeover == "voice":
        takeover_task = asyncio.create_task(_collect(orchestrator.accept_audio(FRAME)))
    else:
        takeover_task = asyncio.create_task(orchestrator.stop())
    await asyncio.sleep(0.02)
    assert not takeover_task.done()
    partial.release.set()
    await pending_partial
    result = await takeover_task

    assert partial.reset_while_running is False
    assert orchestrator._partial_text != "不应写回"
    if takeover != "stop":
        assert result[0].type == "turn.cancelled"
        await orchestrator.stop()
    else:
        assert orchestrator.state.phase is Phase.STOPPED


@pytest.mark.asyncio
async def test_raw_audio_limit_errors_at_boundary_and_releases_frames():
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.SPEECH]),
        max_utterance_frames=2,
    )
    first = [item async for item in orchestrator.accept_audio(FRAME)]
    boundary = [item async for item in orchestrator.accept_audio(FRAME)]

    assert first[0].type == "vad.started"
    assert [item.type for item in boundary] == ["vad.stopped", "error"]
    assert boundary[-1].code == "utterance_too_long"
    assert boundary[-1].recoverable is True
    assert orchestrator._audio_frames == []
    assert orchestrator.state.phase is Phase.IDLE
    await orchestrator.stop()


def test_raw_audio_default_limit_is_120_seconds():
    assert MAX_UTTERANCE_FRAMES == 6000


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["tts", "partial"])
async def test_stop_publishes_terminal_state_before_waiting_for_native_work(blocked):
    blocking_tts = FakeTts(block=blocked == "tts")
    partial = BlockingPartialAsr()
    if blocked == "tts":
        orchestrator, _, _ = make_orchestrator(
            tts=blocking_tts, replies=[["回答。"]]
        )
        active = orchestrator.submit_text("问题", True)
        assert (await anext(active)).type == "assistant.delta"
        await asyncio.to_thread(blocking_tts.started.wait, 1)
    else:
        orchestrator, _, _ = make_orchestrator(
            vad=FakeVad([VadDecision.STARTED, VadDecision.SPEECH])
        )
        orchestrator.partial_asr = partial
        active = await _start_blocked_partial(orchestrator, partial)
    old_settings = (orchestrator._voice_key, orchestrator._speed)

    stopping = asyncio.create_task(orchestrator.stop())
    await asyncio.sleep(0.02)
    selected = orchestrator.select_voice("steady_male", 1.2)

    assert selected.type == "error" and selected.code == "session_stopped"
    assert (orchestrator._voice_key, orchestrator._speed) == old_settings
    if blocked == "tts":
        blocking_tts.release.set()
    else:
        partial.release.set()
    await active.aclose() if blocked == "tts" else await active
    await stopping
    assert orchestrator.state.phase is Phase.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["normal", "asr_failed", "asr_empty", "too_long"])
async def test_voice_turn_end_immediately_resets_partial_state(ending):
    partial = FakePartialAsr()
    decisions = [VadDecision.STARTED, VadDecision.SPEECH]
    max_frames = MAX_UTTERANCE_FRAMES
    if ending != "too_long":
        decisions.append(VadDecision.STOPPED)
    else:
        max_frames = 2
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad(decisions),
        asr_texts=["" if ending == "asr_empty" else "问题"],
        replies=[["回答"]],
        max_utterance_frames=max_frames,
    )
    orchestrator.partial_asr = partial
    if ending == "asr_failed":
        orchestrator.asr = FailingAsr([])

    _ = await collect_audio_events(orchestrator, [FRAME] * len(decisions))

    assert partial.reset_calls >= 2
    assert orchestrator._partial_task is None
    assert orchestrator._partial_token is None
    assert orchestrator._partial_text == ""
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_partial_asr_exception_is_recoverable_and_text_remains_usable():
    partial = FailingPartialAsr()
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.SPEECH]),
        replies=[["文字回答"]],
    )
    orchestrator.partial_asr = partial
    _ = [item async for item in orchestrator.accept_audio(FRAME)]

    failed = [item async for item in orchestrator.accept_audio(FRAME)]
    text = [item async for item in orchestrator.submit_text("改用文字", False)]

    assert [item.type for item in failed] == ["vad.stopped", "error"]
    assert failed[1].code == "partial_asr_failed" and failed[1].recoverable
    assert orchestrator._audio_frames == []
    assert orchestrator._partial_text == ""
    assert orchestrator.state.phase is Phase.IDLE
    assert text[-1].type == "assistant.done"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_commit_audio_without_live_voice_is_noop():
    orchestrator, _, _ = make_orchestrator()

    outputs = [item async for item in orchestrator.commit_audio()]

    assert outputs == []
    assert orchestrator.state.phase is Phase.IDLE
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_context_assembler_supplies_trusted_persona_to_next_reply():
    assembler = ContextAssembler(
        DEFAULT_PERSONA,
        lambda _query, _limit: (),
        lambda _query, _limit: (),
    )
    orchestrator, llm, _ = make_orchestrator(
        replies=[["有上下文的回答"]], context_assembler=assembler
    )

    outputs = [item async for item in orchestrator.submit_text("问题", False)]

    assert outputs[-1].type == "assistant.done"
    assert llm.calls[0].messages[0]["role"] == "system"
    assert "<persona_data>" in llm.calls[0].messages[0]["content"]
    assert llm.calls[0].messages[-1] == {"role": "user", "content": "问题"}
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_reply_emits_the_exact_retrieved_sources_before_answer():
    assembler = ContextAssembler(
        DEFAULT_PERSONA,
        lambda _query, _limit: (
            MemoryContext(2, "喜欢茶", 0.9, 8, "我喜欢喝茶", 3),
        ),
        lambda _query, _limit: (
            KnowledgeContext(7, 4, "手册.pdf", "八十度水温", 5, 0.8),
        ),
    )
    orchestrator, _, _ = make_orchestrator(
        replies=[["有来源的回答"]], context_assembler=assembler
    )

    outputs = [item async for item in orchestrator.submit_text("怎么泡茶", False)]

    source_event = outputs[0]
    assert source_event.type == "context.sources"
    assert source_event.memories[0].source_text == "我喜欢喝茶"
    assert source_event.knowledge[0].display_name == "手册.pdf"
    assert source_event.knowledge[0].page_number == 5
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_reply_emits_only_policy_accepted_memory_proposals() -> None:
    proposer = FakeMemoryProposer(
        (
            MemoryCandidate(MemoryKind.PREFERENCE, "喜欢乌龙茶", 0.8, 1),
            MemoryCandidate(MemoryKind.PROFILE, "密码是 abc123456", 1, 1),
        )
    )
    orchestrator, _, _ = make_orchestrator(
        replies=[["已经记下候选。"]], memory_proposer=proposer
    )

    outputs = [item async for item in orchestrator.submit_text("记住我喜欢乌龙茶", False)]

    assert [item.type for item in outputs] == [
        "assistant.delta",
        "assistant.done",
        "memory.proposed",
    ]
    assert outputs[2].content == "喜欢乌龙茶"
    assert outputs[2].kind == "preference"
    assert outputs[2].requires_confirmation is False
    assert proposer.calls == [("qwen-local", "记住我喜欢乌龙茶", "已经记下候选。", 1)]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_reply_persists_messages_and_anchors_proposal_to_database_source() -> None:
    store = FakeConversationStore(source_message_id=42)
    proposer = FakeMemoryProposer(
        (MemoryCandidate(MemoryKind.PREFERENCE, "喜欢乌龙茶", 0.8, None),)
    )
    orchestrator, _, _ = make_orchestrator(
        replies=[["已记录"]],
        memory_proposer=proposer,
        conversation_store=store,
    )

    outputs = [item async for item in orchestrator.submit_text("我喜欢乌龙茶", False)]

    assert store.calls == [
        ("user", 1, "我喜欢乌龙茶", "text"),
        ("assistant", 1, "已记录"),
    ]
    assert proposer.calls == [("qwen-local", "我喜欢乌龙茶", "已记录", 42)]
    proposal = next(item for item in outputs if item.type == "memory.proposed")
    assert proposal.source_message_id == 42
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_memory_proposal_failure_never_changes_successful_reply() -> None:
    proposer = FakeMemoryProposer(error=ValueError("invalid memory proposal payload"))
    orchestrator, _, _ = make_orchestrator(
        replies=[["回答仍然成功"]], memory_proposer=proposer
    )

    outputs = [item async for item in orchestrator.submit_text("问题", False)]

    assert [item.type for item in outputs] == ["assistant.delta", "assistant.done"]
    assert orchestrator.history.assistant_text(1) == "回答仍然成功"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_new_turn_after_done_keeps_completed_history_while_proposal_is_slow() -> None:
    class SlowProposer:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def propose(self, *_args, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()

    proposer = SlowProposer()
    orchestrator, llm, _ = make_orchestrator(
        replies=[["第一答"], ["第二答"]], memory_proposer=proposer
    )
    first = orchestrator.submit_text("第一问", False)
    assert (await anext(first)).type == "assistant.delta"
    assert (await anext(first)).type == "assistant.done"
    await proposer.started.wait()
    orchestrator.memory_proposer = None

    second = [item async for item in orchestrator.submit_text("第二问", False)]

    assert second[-1].type == "assistant.done"
    assert llm.calls[1].messages[-3:] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
    ]
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_global_reset_pauses_new_turns_until_database_clear_finishes() -> None:
    orchestrator, _, _ = make_orchestrator(replies=[["恢复后回答"]])

    await orchestrator.reset_conversation()
    paused = [item async for item in orchestrator.submit_text("不能插入", False)]
    await orchestrator.resume_after_reset()
    resumed = [item async for item in orchestrator.submit_text("可以继续", False)]

    assert paused[0].type == "error"
    assert resumed[-1].type == "assistant.done"
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_commit_audio_finishes_buffered_turn_through_normal_reply_path():
    orchestrator, llm, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED]),
        asr_texts=["手动停止录音"],
        replies=[["已收到"]],
    )
    started = [item async for item in orchestrator.accept_audio(FRAME)]

    outputs = [item async for item in orchestrator.commit_audio()]

    assert [item.type for item in started] == ["vad.started"]
    assert [getattr(item, "type", "binary") for item in outputs] == [
        "vad.stopped",
        "asr.final",
        "assistant.delta",
        "tts.started",
        "tts.chunk",
        "binary",
        "tts.done",
        "assistant.done",
    ]
    assert outputs[1].text == "手动停止录音"
    assert llm.calls[0].messages[-1] == {"role": "user", "content": "手动停止录音"}
    assert orchestrator._audio_frames == []
    assert orchestrator.state.phase is Phase.IDLE
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_concurrent_commits_waiting_on_partial_asr_claim_live_turn_once():
    partial = BlockingPartialAsr()
    final_asr = FakeAsr(["只能提交一次"])
    orchestrator, _, _ = make_orchestrator(
        vad=FakeVad([VadDecision.STARTED, VadDecision.SPEECH]),
        replies=[["单次回答"]],
    )
    orchestrator.partial_asr = partial
    orchestrator.asr = final_asr
    _ = [item async for item in orchestrator.accept_audio(FRAME)]
    pending_frame = asyncio.create_task(_collect(orchestrator.accept_audio(FRAME)))
    await asyncio.to_thread(partial.started.wait, 1)

    commits = [
        asyncio.create_task(_collect(orchestrator.commit_audio())),
        asyncio.create_task(_collect(orchestrator.commit_audio())),
    ]
    await asyncio.sleep(0)
    partial.release.set()
    await pending_frame
    results = await asyncio.gather(*commits)

    assert len(final_asr.calls) == 1
    assert sum(
        item.type == "asr.final"
        for result in results
        for item in result
        if hasattr(item, "type")
    ) == 1
    assert orchestrator.history.messages_for_model()[-2:] == (
        ChatMessage("user", "只能提交一次"),
        ChatMessage("assistant", "单次回答"),
    )
    await orchestrator.stop()


async def _collect(iterator):
    return [item async for item in iterator]
