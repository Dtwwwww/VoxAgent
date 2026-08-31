import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from voxagent.conversation.events import ErrorMessage
from voxagent.conversation.history import SYSTEM_INSTRUCTION, ChatMessage
from voxagent.conversation.orchestrator import (
    MAX_UTTERANCE_FRAMES,
    ConversationOrchestrator,
)
from voxagent.conversation.state import Phase
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
        "tts.chunk",
        "binary",
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
        "tts.chunk",
        "binary",
    ]
    assert tts.calls[-1].text == "完成回答"
    assert len(llm.calls) == calls_before
    assert isinstance(missing[0], ErrorMessage) and missing[0].recoverable
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
    assert (await first_output).type == "tts.chunk"
    assert isinstance(await anext(replay), bytes)
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
    assert old_metadata.type == "tts.chunk"

    cancelled = await orchestrator.cancel_active()
    new_replay = [item async for item in orchestrator.speak_message(turn_id)]

    assert cancelled is not None and cancelled.turn_id == turn_id
    assert [item.type if hasattr(item, "type") else "binary" for item in new_replay] == [
        "tts.chunk",
        "binary",
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
        "error",
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
async def test_done_but_undrained_old_turn_is_cancelled_and_purged():
    orchestrator, llm, _ = make_orchestrator(replies=[["旧回答"], ["新回答"]])
    old = orchestrator.submit_text("旧问题", False)
    old_delta = await anext(old)
    await asyncio.sleep(0.02)

    replacement = [item async for item in orchestrator.submit_text("新问题", False)]

    assert replacement[0].type == "turn.cancelled"
    assert replacement[0].turn_id == old_delta.turn_id
    assert [item async for item in old] == []
    assert all(
        message["content"] not in {"旧问题", "旧回答"} for message in llm.calls[-1].messages
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

    await collect_audio_events(orchestrator, [FRAME, FRAME, FRAME])

    assert partial.reset_calls == 1
    assert endpoint.calls[-1] == (VadDecision.STOPPED, 1234, "我觉得那个")
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


async def _collect(iterator):
    return [item async for item in iterator]
