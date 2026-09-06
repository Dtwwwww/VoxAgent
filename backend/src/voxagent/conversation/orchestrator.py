from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, TypeVar

import numpy as np

from voxagent.conversation.context import ContextAssembler, MemoryProposalService
from voxagent.conversation.events import (
    AsrFinal,
    AsrPartial,
    AssistantDelta,
    AssistantDone,
    ContextSources,
    ErrorMessage,
    MemoryProposed,
    ServerMessage,
    TtsChunk,
    TtsDone,
    TtsError,
    TtsStarted,
    TurnCancelled,
    VadStarted,
    VadStopped,
    VoicePreviewChunk,
    VoiceSelected,
)
from voxagent.conversation.history import ConversationHistory
from voxagent.conversation.persistence import ConversationStore
from voxagent.conversation.sentence_chunker import SentenceChunker
from voxagent.conversation.state import Phase, TurnState, TurnToken
from voxagent.llm.ollama import OllamaClient
from voxagent.memory.models import PolicyStatus
from voxagent.memory.policy import MemoryPolicy
from voxagent.speech.asr import AsrEngine, PartialAsrEngine
from voxagent.speech.endpoint import EndpointDecision, EndpointDetector
from voxagent.speech.text_normalization import normalize_tts_text, split_tts_text, strip_emoji
from voxagent.speech.tts import PUBLIC_TTS_SPEEDS, TtsEngine
from voxagent.speech.vad import SAMPLE_RATE, VadDecision, VadDetector
from voxagent.speech.voice_catalog import VoiceCatalog, VoiceCatalogError

PREVIEW_TEXT = "你好，我是声灵，很高兴陪你一起聊天。"
EMPTY_VISIBLE_REPLY = "我暂时没有生成有效回答，请再试一次。"
MAX_UTTERANCE_FRAMES = 6000
Output = ServerMessage | bytes
_Owner = tuple[str, int]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _OutputBatch:
    owner: _Owner
    items: tuple[Output, ...]
    terminal: bool = False


class ConversationOrchestrator:
    """Coordinate one interruptible, session-scoped local conversation."""

    def __init__(
        self,
        *,
        model_id: str,
        vad: VadDetector,
        endpoint: EndpointDetector,
        asr: AsrEngine,
        llm: OllamaClient,
        tts: TtsEngine,
        voice_catalog: VoiceCatalog,
        partial_asr: PartialAsrEngine | None = None,
        state: TurnState | None = None,
        history: ConversationHistory | None = None,
        clock_ms: Callable[[], int] | None = None,
        max_utterance_frames: int = MAX_UTTERANCE_FRAMES,
        context_assembler: ContextAssembler | None = None,
        memory_proposer: MemoryProposalService | None = None,
        memory_policy: MemoryPolicy | None = None,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        if not 2 <= max_utterance_frames <= MAX_UTTERANCE_FRAMES:
            raise ValueError(
                f"max_utterance_frames must be between 2 and {MAX_UTTERANCE_FRAMES}"
            )
        self.model_id = model_id
        self.vad = vad
        self.endpoint = endpoint
        self.asr = asr
        self.partial_asr = partial_asr
        self.llm = llm
        self.tts = tts
        self.voice_catalog = voice_catalog
        self.state = state or TurnState()
        self.history = history or ConversationHistory()
        self.context_assembler = context_assembler
        self.memory_proposer = memory_proposer
        self.memory_policy = memory_policy or (
            MemoryPolicy() if memory_proposer is not None else None
        )
        self.conversation_store = conversation_store
        self._source_message_ids: dict[int, int] = {}
        self._voice_transcript_request_ids: dict[int, int] = {}
        self._clock_ms = clock_ms or (lambda: int(monotonic() * 1000))
        default = next(profile for profile in voice_catalog.public_profiles() if profile.is_default)
        self._voice_key = default.voice_key
        self._speed = 1.0
        self._audio_frames: list[bytes] = []
        self._partial_text = ""
        self._waiting_silence = False
        self._max_utterance_frames = max_utterance_frames
        self._partial_task: asyncio.Task[tuple[bool, object]] | None = None
        self._partial_token: TurnToken | None = None

        self._outputs: asyncio.Queue[_OutputBatch] = asyncio.Queue(maxsize=32)
        self._output_changed = asyncio.Condition()
        self._task_group = asyncio.TaskGroup()
        self._task_group_entered = False
        self._lifecycle_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._active_task: asyncio.Task[None] | None = None
        self._active_token: TurnToken | None = None
        self._active_has_pending_history = False
        self._preview_task: asyncio.Task[None] | None = None
        self._preview_cancel: asyncio.Event | None = None
        self._preview_id = 0
        self._stopping = False
        self._stopped = False
        self._reset_paused = False

    async def accept_audio(self, frame: bytes) -> AsyncIterator[Output]:
        async with self._action_lock:
            stopped = self._is_closed()
            decision = None if stopped else self.vad.accept(frame)
        if stopped:
            yield self._stopped_error()
            return
        assert decision is not None
        if decision is VadDecision.STARTED:
            async with self._action_lock:
                if self._is_closed():
                    stopped = True
                    continuation = False
                    cancelled = None
                    token = None
                    partial_task = None
                    too_long = False
                elif (
                    self._active_token is not None
                    and self.state.phase is Phase.LISTENING
                    and self._waiting_silence
                ):
                    stopped = False
                    continuation = True
                    cancelled = None
                    token = self._active_token
                    self._waiting_silence = False
                    self._audio_frames.append(frame)
                    self.endpoint.accept(decision, self._clock_ms(), self._partial_text)
                    too_long = len(self._audio_frames) >= self._max_utterance_frames
                    partial_task = await self._start_partial_locked(token, frame)
                else:
                    stopped = False
                    continuation = False
                    cancelled = await self._cancel_conversation()
                    await self._cancel_preview()
                    token = self.state.begin_user_speech()
                    self._audio_frames = [frame]
                    self._partial_text = ""
                    self._waiting_silence = False
                    if self.partial_asr is not None:
                        self.partial_asr.reset()
                    self.endpoint.accept(decision, self._clock_ms(), self._partial_text)
                    self._active_token = token
                    self._active_task = None
                    self._active_has_pending_history = False
                    partial_task = None
                    too_long = False
            if stopped or token is None:
                yield self._stopped_error()
                return
            if continuation:
                if partial_task is not None:
                    partial_outcome = await self._finish_partial(token, partial_task)
                    if isinstance(partial_outcome, ErrorMessage):
                        async for output in self._terminate_voice_turn(
                            token, partial_outcome
                        ):
                            yield output
                        return
                    if isinstance(partial_outcome, AsrPartial):
                        yield partial_outcome
                if too_long:
                    async for output in self._limit_utterance(token):
                        yield output
                return
            if cancelled is not None:
                yield cancelled
            if not self._owns_live_turn(token):
                return
            yield VadStarted(
                type="vad.started", session_id=token.session_id, turn_id=token.turn_id
            )
            return

        async with self._action_lock:
            if self._is_closed():
                stopped = True
                token = None
                partial_task = None
                too_long = False
                endpoint = EndpointDecision.CONTINUE
            else:
                stopped = False
                token = self._active_token
                if token is None or self.state.phase is not Phase.LISTENING:
                    return
                if decision is VadDecision.SPEECH:
                    self._audio_frames.append(frame)
                    too_long = len(self._audio_frames) >= self._max_utterance_frames
                    partial_task = await self._start_partial_locked(token, frame)
                else:
                    too_long = False
                    partial_task = None
                endpoint = self.endpoint.accept(
                    decision, self._clock_ms(), self._partial_text
                )
                self._waiting_silence = (
                    decision in {VadDecision.STOPPED, VadDecision.SILENCE}
                    and endpoint is EndpointDecision.CONTINUE
                )
        if stopped or token is None:
            yield self._stopped_error()
            return
        if partial_task is not None:
            partial_outcome = await self._finish_partial(token, partial_task)
            if isinstance(partial_outcome, ErrorMessage):
                async for output in self._terminate_voice_turn(
                    token, partial_outcome
                ):
                    yield output
                return
            if isinstance(partial_outcome, AsrPartial):
                yield partial_outcome
        if too_long:
            async for output in self._limit_utterance(token):
                yield output
            return
        if endpoint is not EndpointDecision.COMMIT:
            return

        async for output in self._commit_voice_token(token):
            yield output

    async def commit_audio(self) -> AsyncIterator[Output]:
        async with self._action_lock:
            token = self._active_token
            if (
                self._is_closed()
                or token is None
                or self.state.phase is not Phase.LISTENING
                or not self._audio_frames
            ):
                return
            partial_task = self._partial_task if self._partial_token is token else None
        if partial_task is not None:
            partial_outcome = await self._finish_partial(token, partial_task)
            if isinstance(partial_outcome, ErrorMessage):
                async for output in self._terminate_voice_turn(token, partial_outcome):
                    yield output
                return
            if isinstance(partial_outcome, AsrPartial):
                yield partial_outcome
        async for output in self._commit_voice_token(token):
            yield output

    async def _commit_voice_token(self, token: TurnToken) -> AsyncIterator[Output]:
        async with self._action_lock:
            if (
                not self._owns_live_turn(token)
                or self.state.phase is not Phase.LISTENING
                or not self._audio_frames
            ):
                return
            self.state.finish_user_speech(token)
            samples = self._all_samples()
            self._reset_partial_locked()
            try:
                result = await self._await_sync(self.asr.transcribe, samples, SAMPLE_RATE)
            except Exception:
                yield_error = self._error(
                    "asr_failed", "语音识别失败，请重试或改用文字输入"
                )
                result = None
            if result is None:
                prefix: tuple[Output, ...] = (
                    VadStopped(
                        type="vad.stopped",
                        session_id=token.session_id,
                        turn_id=token.turn_id,
                    ),
                    yield_error,
                )
            else:
                text = result.text.strip()
                if not text:
                    prefix = (
                        VadStopped(
                            type="vad.stopped",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                        ),
                        self._error("asr_empty", "没有识别到有效语音，请重试"),
                    )
                else:
                    if self.conversation_store is not None:
                        self._source_message_ids[token.turn_id] = (
                            await self.conversation_store.add_user(
                                token.turn_id, text, "voice"
                            )
                        )
                    self.history.add_user(token.turn_id, text, "voice")
                    self.state.begin_reply(token)
                    await self._start_reply(token, speak_response=True, pending_history=True)
                    prefix = (
                        VadStopped(
                            type="vad.stopped",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                        ),
                        AsrFinal(
                            type="asr.final",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                            text=text,
                        ),
                    )
        for item in prefix:
            if not self._owns_live_turn(token):
                return
            yield item
        if len(prefix) < 2 or not isinstance(prefix[1], AsrFinal):
            async with self._action_lock:
                if self._owns_live_turn(token):
                    self.state.complete_turn(token)
                    self._active_token = None
            return
        async for output in self._drain(("turn", token.turn_id), token.cancelled):
            yield output
        self._finish_active(token)

    async def submit_text(self, text: str, speak_response: bool) -> AsyncIterator[Output]:
        async for item in self._submit_text_turn(
            text, origin="text", speak_response=speak_response, echo_asr=False
        ):
            yield item

    async def submit_voice_transcript(
        self, text: str, request_id: int
    ) -> AsyncIterator[Output]:
        async for item in self._submit_text_turn(
            text,
            origin="voice",
            speak_response=False,
            echo_asr=True,
            transcript_request_id=request_id,
        ):
            yield item

    async def _submit_text_turn(
        self,
        text: str,
        *,
        origin: Literal["text", "voice"],
        speak_response: bool,
        echo_asr: bool,
        transcript_request_id: int | None = None,
    ) -> AsyncIterator[Output]:
        normalized = text.strip()
        async with self._action_lock:
            if self._is_closed():
                error = self._stopped_error()
                cancelled = None
                token = None
            elif not normalized:
                error = self._error("invalid_text", "文字内容不能为空")
                cancelled = None
                token = None
            else:
                error = None
                cancelled = await self._cancel_conversation()
                await self._cancel_preview()
                token = self.state.begin_text_turn()
                if transcript_request_id is not None:
                    self._voice_transcript_request_ids[token.turn_id] = (
                        transcript_request_id
                    )
                if self.conversation_store is not None:
                    self._source_message_ids[token.turn_id] = (
                        await self.conversation_store.add_user(
                            token.turn_id, normalized, origin
                        )
                    )
                self.history.add_user(token.turn_id, normalized, origin)
                await self._start_reply(
                    token, speak_response=speak_response, pending_history=True
                )
        if error is not None or token is None:
            yield error or self._stopped_error()
            return
        if cancelled is not None:
            yield cancelled
        if echo_asr:
            if not self._owns_live_turn(token):
                return
            yield AsrFinal(
                type="asr.final",
                session_id=token.session_id,
                turn_id=token.turn_id,
                text=normalized,
                request_id=transcript_request_id,
            )
        async for output in self._drain(("turn", token.turn_id), token.cancelled):
            yield output
        self._finish_active(token)

    def select_voice(self, voice_key: str, speed: float) -> VoiceSelected | ErrorMessage:
        if self._is_closed():
            return self._stopped_error()
        error = self._validate_voice(voice_key, speed, require_previewable=False)
        if error is not None:
            return error
        self._voice_key = voice_key
        self._speed = float(speed)
        return VoiceSelected(type="voice.selected", voice_key=voice_key, speed=float(speed))

    async def speak_message(
        self, turn_id: int, request_id: int = 0
    ) -> AsyncIterator[Output]:
        async with self._action_lock:
            if self._is_closed():
                error = self._stopped_error()
                token = None
                cancelled = None
            else:
                try:
                    text = self.history.assistant_text(turn_id)
                except KeyError:
                    error = self._error(
                        "assistant_not_completed",
                        "只能朗读当前会话中已完成的助手消息",
                    )
                    token = None
                    cancelled = None
                else:
                    error = None
                    cancelled = await self._cancel_conversation()
                    await self._cancel_preview()
                    token = TurnToken(self.state.session_id, turn_id)
                    await self._ensure_task_group()
                    self._active_token = token
                    self._active_has_pending_history = False
                    self.state.phase = Phase.SPEAKING
                    self._active_task = self._task_group.create_task(
                        self._speech_worker(token, text, request_id),
                        name=f"replay-{turn_id}-{request_id}",
                    )
        if error is not None or token is None:
            yield error or self._stopped_error()
            return
        if cancelled is not None:
            yield cancelled
        async for output in self._drain(("turn", turn_id), token.cancelled):
            yield output
        self._finish_active(token)

    async def preview_voice(self, voice_key: str, speed: float) -> AsyncIterator[Output]:
        busy = False
        async with self._action_lock:
            if self._is_closed():
                stopped = True
                error = None
            else:
                stopped = False
                error = self._validate_voice(
                    voice_key, speed, require_previewable=True
                )
                if error is None:
                    if self._active_token is not None:
                        busy = True
                    else:
                        await self._cancel_preview()
                        await self._ensure_task_group()
                        self._preview_id += 1
                        preview_id = self._preview_id
                        cancel = asyncio.Event()
                        self._preview_cancel = cancel
                        self._preview_task = self._task_group.create_task(
                            self._preview_worker(
                                preview_id, cancel, voice_key, float(speed)
                            ),
                            name=f"preview-{preview_id}",
                        )
        if stopped:
            yield self._stopped_error()
            return
        if error is not None:
            yield error
            return
        if busy:
            yield self._error("conversation_busy", "正在回答或朗读，请稍后再试听")
            return
        async for output in self._drain(("preview", preview_id), cancel):
            yield output
        if self._preview_id == preview_id:
            self._preview_task = None
            self._preview_cancel = None

    async def cancel_active(self) -> TurnCancelled | None:
        async with self._action_lock:
            if self._is_closed():
                return None
            cancelled = await self._cancel_conversation()
            await self._cancel_preview()
            return cancelled

    async def reset_conversation(self) -> None:
        async with self._action_lock:
            if self._is_closed():
                return
            await self._cancel_conversation()
            await self._cancel_preview()
            if self.conversation_store is not None:
                await self.conversation_store.reset()
            self.history.clear()
            self._source_message_ids.clear()
            self._audio_frames.clear()
            self._partial_text = ""
            self._waiting_silence = False
            self._reset_paused = True
            await self._clear_all_outputs()

    async def resume_after_reset(self) -> None:
        async with self._action_lock:
            if not self._stopped and not self._stopping:
                self._reset_paused = False

    async def stop(self) -> None:
        if self._stopped:
            return
        async with self._action_lock:
            if self._stopped:
                return
            self._stopping = True
            await self._cancel_conversation()
            await self._cancel_preview()
            self.state.stop()
            self.history.clear()
            self._source_message_ids.clear()
            self._audio_frames.clear()
            self._partial_text = ""
            self._waiting_silence = False
            self._stopped = True
            await self._clear_all_outputs()
        if self._task_group_entered:
            await self._task_group.__aexit__(None, None, None)
        self._stopping = False

    async def _start_reply(
        self, token: TurnToken, *, speak_response: bool, pending_history: bool
    ) -> None:
        await self._ensure_task_group()
        self._active_token = token
        self._active_has_pending_history = pending_history
        self._active_task = self._task_group.create_task(
            self._reply_worker(token, speak_response), name=f"reply-{token.turn_id}"
        )

    async def _reply_worker(self, token: TurnToken, speak_response: bool) -> None:
        owner = ("turn", token.turn_id)
        chunker = SentenceChunker()
        answer: list[str] = []
        sequence = 0
        tts_failed = False
        tts_started = False
        try:
            history_messages = self.history.messages_for_model()
            user_text = history_messages[-1].content
            model_messages = history_messages
            context_sources = ContextSources(
                type="context.sources",
                session_id=token.session_id,
                turn_id=token.turn_id,
                memories=[],
                knowledge=[],
            )
            if self.context_assembler is not None:
                try:
                    bundle = await self._await_sync(
                        self.context_assembler.build, user_text, history_messages
                    )
                    model_messages = bundle.messages
                    context_sources = ContextSources(
                        type="context.sources",
                        session_id=token.session_id,
                        turn_id=token.turn_id,
                        memories=[
                            {
                                "id": item.id,
                                "content": item.content,
                                "source_message_id": item.source_message_id,
                                "source_text": item.source_text,
                                "source_turn_id": item.source_turn_id,
                            }
                            for item in bundle.memory_sources
                        ],
                        knowledge=[
                            {
                                "chunk_id": item.chunk_id,
                                "document_id": item.document_id,
                                "display_name": item.display_name,
                                "content": item.content,
                                "page_number": item.page_number,
                            }
                            for item in bundle.knowledge_sources
                        ],
                    )
                except Exception:
                    model_messages = (history_messages[0], history_messages[-1])
            if context_sources.memories or context_sources.knowledge:
                await self._emit(_OutputBatch(owner, (context_sources,)))
            async for delta in self.llm.stream_chat(self.model_id, model_messages):
                if token.cancelled.is_set():
                    return
                visible_delta = strip_emoji(delta)
                if not visible_delta.strip():
                    continue
                answer.append(visible_delta)
                await self._emit(
                    _OutputBatch(
                        owner,
                        (
                            AssistantDelta(
                                type="assistant.delta",
                                session_id=token.session_id,
                                turn_id=token.turn_id,
                                delta=visible_delta,
                            ),
                        ),
                    )
                )
                if speak_response:
                    for sentence in chunker.feed(visible_delta):
                        sequence, tts_failed, tts_started = await self._synthesize_reply_sentence(
                            token, sentence, sequence, tts_failed, tts_started
                        )
            if not answer:
                answer.append(EMPTY_VISIBLE_REPLY)
                await self._emit(
                    _OutputBatch(
                        owner,
                        (
                            AssistantDelta(
                                type="assistant.delta",
                                session_id=token.session_id,
                                turn_id=token.turn_id,
                                delta=EMPTY_VISIBLE_REPLY,
                            ),
                        ),
                    )
                )
                if speak_response:
                    for sentence in chunker.feed(EMPTY_VISIBLE_REPLY):
                        sequence, tts_failed, tts_started = await self._synthesize_reply_sentence(
                            token, sentence, sequence, tts_failed, tts_started
                        )
            if speak_response:
                for sentence in chunker.flush():
                    sequence, tts_failed, tts_started = await self._synthesize_reply_sentence(
                        token, sentence, sequence, tts_failed, tts_started
                    )
            if token.cancelled.is_set():
                return
            if speak_response and tts_started and not tts_failed:
                await self._emit(
                    _OutputBatch(
                        owner,
                        (
                            TtsDone(
                                type="tts.done",
                                session_id=token.session_id,
                                turn_id=token.turn_id,
                                request_id=0,
                            ),
                        ),
                    )
                )
            assistant_text = "".join(answer)
            if self.conversation_store is not None:
                await self.conversation_store.complete_assistant(
                    token.turn_id, assistant_text
                )
            self.history.complete_assistant(token.turn_id, assistant_text)
            # From here onward the completed pair must survive cancellation of
            # the optional, slower memory-proposal follow-up.
            self._active_has_pending_history = False
            await self._emit(
                _OutputBatch(
                    owner,
                    (
                        AssistantDone(
                            type="assistant.done",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                        ),
                    ),
                )
            )
            memory_events = await self._memory_proposal_events(
                token, user_text, assistant_text
            )
            if token.cancelled.is_set():
                return
            await self._emit(
                _OutputBatch(
                    owner,
                    memory_events,
                    terminal=True,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._discard_pending_turn(token.turn_id)
            await self._emit(
                _OutputBatch(
                    owner,
                    (self._error("reply_failed", "本地回答生成失败，请重试"),),
                    terminal=True,
                )
            )

    async def _memory_proposal_events(
        self,
        token: TurnToken,
        user_text: str,
        assistant_text: str,
    ) -> tuple[MemoryProposed, ...]:
        if self.memory_proposer is None or self.memory_policy is None:
            return ()
        try:
            source_message_id = self._source_message_ids.get(token.turn_id, token.turn_id)
            async with asyncio.timeout(15):
                candidates = await self.memory_proposer.propose(
                    self.model_id,
                    user_text,
                    assistant_text,
                    source_message_id=source_message_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            return ()
        events: list[MemoryProposed] = []
        for index, candidate in enumerate(candidates):
            decision = self.memory_policy.evaluate(candidate)
            if decision.status is PolicyStatus.REJECT:
                continue
            events.append(
                MemoryProposed(
                    type="memory.proposed",
                    session_id=token.session_id,
                    turn_id=token.turn_id,
                    proposal_index=index,
                    source_message_id=source_message_id,
                    kind=candidate.kind.value,
                    content=candidate.content,
                    importance=candidate.importance,
                    requires_confirmation=(
                        decision.status is PolicyStatus.REQUIRES_CONFIRMATION
                    ),
                )
            )
        return tuple(events)

    async def _synthesize_reply_sentence(
        self,
        token: TurnToken,
        text: str,
        sequence: int,
        already_failed: bool,
        already_started: bool,
    ) -> tuple[int, bool, bool]:
        if already_failed:
            return sequence, True, already_started
        speech_text = normalize_tts_text(text)
        if not speech_text:
            return sequence, False, already_started
        try:
            self.state.begin_speaking(token)
            if not already_started:
                await self._emit(
                    _OutputBatch(
                        ("turn", token.turn_id),
                        (
                            TtsStarted(
                                type="tts.started",
                                session_id=token.session_id,
                                turn_id=token.turn_id,
                                request_id=0,
                            ),
                        ),
                    )
                )
                already_started = True
            sequence = await self._synthesize_turn(
                token, speech_text, sequence, request_id=0
            )
            return sequence, False, already_started
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit(
                _OutputBatch(
                    ("turn", token.turn_id),
                    (
                        TtsError(
                            type="tts.error",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                            request_id=0,
                            code="tts_failed",
                            message="朗读失败，文字回答仍然可用",
                            recoverable=True,
                        ),
                    ),
                )
            )
            return sequence, True, already_started

    async def _synthesize_turn(
        self,
        token: TurnToken,
        text: str,
        sequence: int,
        *,
        request_id: int,
    ) -> int:
        speech_text = normalize_tts_text(text)
        for sentence in split_tts_text(speech_text):
            audio = await self._await_sync(
                self.tts.synthesize, sentence, self._voice_key, self._speed
            )
            if token.cancelled.is_set():
                return sequence
            event = TtsChunk(
                type="tts.chunk",
                session_id=token.session_id,
                turn_id=token.turn_id,
                request_id=request_id,
                sequence=sequence,
                sample_rate=audio.sample_rate,
                mime_type="audio/wav",
                byte_length=len(audio.wav_bytes),
            )
            await self._emit(
                _OutputBatch(("turn", token.turn_id), (event, audio.wav_bytes))
            )
            sequence += 1
        return sequence

    async def _speech_worker(
        self, token: TurnToken, text: str, request_id: int
    ) -> None:
        owner = ("turn", token.turn_id)
        try:
            speech_text = normalize_tts_text(text)
            if not split_tts_text(speech_text):
                await self._emit(
                    _OutputBatch(
                        owner,
                        (
                            TtsError(
                                type="tts.error",
                                session_id=token.session_id,
                                turn_id=token.turn_id,
                                request_id=request_id,
                                code="tts_empty",
                                message="这条回答没有可朗读的文字",
                                recoverable=True,
                            ),
                        ),
                        terminal=True,
                    )
                )
                return
            await self._emit(
                _OutputBatch(
                    owner,
                    (
                        TtsStarted(
                            type="tts.started",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                            request_id=request_id,
                        ),
                    ),
                )
            )
            await self._synthesize_turn(
                token, speech_text, 0, request_id=request_id
            )
            if token.cancelled.is_set():
                return
            await self._emit(
                _OutputBatch(
                    owner,
                    (
                        TtsDone(
                            type="tts.done",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                            request_id=request_id,
                        ),
                    ),
                    terminal=True,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit(
                _OutputBatch(
                    owner,
                    (
                        TtsError(
                            type="tts.error",
                            session_id=token.session_id,
                            turn_id=token.turn_id,
                            request_id=request_id,
                            code="tts_failed",
                            message="朗读失败，文字回答仍然可用",
                            recoverable=True,
                        ),
                    ),
                    terminal=True,
                )
            )

    async def _preview_worker(
        self, preview_id: int, cancel: asyncio.Event, voice_key: str, speed: float
    ) -> None:
        owner = ("preview", preview_id)
        try:
            audio = await self._await_sync(self.tts.synthesize, PREVIEW_TEXT, voice_key, speed)
            if cancel.is_set():
                return
            event = VoicePreviewChunk(
                type="voice.preview.chunk",
                preview_id=preview_id,
                sample_rate=audio.sample_rate,
                mime_type="audio/wav",
                byte_length=len(audio.wav_bytes),
            )
            await self._emit(_OutputBatch(owner, (event, audio.wav_bytes), terminal=True))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit(
                _OutputBatch(
                    owner,
                    (self._error("preview_failed", "音色试听失败，请选择其他音色"),),
                    terminal=True,
                )
            )

    async def _start_partial_locked(
        self, token: TurnToken, frame: bytes
    ) -> asyncio.Task[tuple[bool, object]] | None:
        if self.partial_asr is None:
            return None
        await self._ensure_task_group()
        samples = self._frame_samples(frame)
        async def capture_partial() -> tuple[bool, object]:
            try:
                return True, await self._await_sync(self.partial_asr.accept, samples)
            except Exception as error:
                return False, error

        task = self._task_group.create_task(
            capture_partial(), name=f"partial-asr-{token.turn_id}"
        )
        self._partial_task = task
        self._partial_token = token
        return task

    async def _finish_partial(
        self, token: TurnToken, task: asyncio.Task[tuple[bool, object]]
    ) -> AsrPartial | Literal[False] | ErrorMessage:
        try:
            succeeded, partial = await asyncio.shield(task)
        except asyncio.CancelledError:
            if token.cancelled.is_set() or self._is_closed():
                return False
            raise
        async with self._action_lock:
            if self._partial_task is task:
                self._partial_task = None
                self._partial_token = None
            if not self._owns_live_turn(token):
                return False
            if not succeeded:
                return self._error(
                    "partial_asr_failed",
                    "实时语音识别失败，请重试或改用文字输入",
                )
            text = str(getattr(partial, "text", "")).strip()
            if not text or text == self._partial_text:
                return False
            self._partial_text = text
            return AsrPartial(
                type="asr.partial",
                session_id=token.session_id,
                turn_id=token.turn_id,
                text=text,
            )

    async def _limit_utterance(self, token: TurnToken) -> AsyncIterator[Output]:
        error = self._error(
            "utterance_too_long",
            "单次语音已达到两分钟上限，请分成更短的问题重试",
        )
        async for output in self._terminate_voice_turn(token, error):
            yield output

    async def _terminate_voice_turn(
        self, token: TurnToken, error: ErrorMessage
    ) -> AsyncIterator[Output]:
        async with self._action_lock:
            if not self._owns_live_turn(token):
                return
            self._audio_frames.clear()
            self._reset_partial_locked()
            prefix: tuple[Output, ...] = (
                VadStopped(
                    type="vad.stopped",
                    session_id=token.session_id,
                    turn_id=token.turn_id,
                ),
                error,
            )
        for item in prefix:
            if not self._owns_live_turn(token):
                return
            yield item
        async with self._action_lock:
            if self._owns_live_turn(token):
                self.state.complete_turn(token)
                self._active_token = None

    def _owns_live_turn(self, token: TurnToken) -> bool:
        return (
            not self._is_closed()
            and not token.cancelled.is_set()
            and self._active_token is token
        )

    async def _cancel_conversation(self) -> TurnCancelled | None:
        task = self._active_task
        token = self._active_token
        if token is None:
            self._active_task = None
            self._active_has_pending_history = False
            return None
        token.cancelled.set()
        self.state.cancel_active_turn()
        partial_task = self._partial_task if self._partial_token is token else None
        if partial_task is not None and not partial_task.done():
            partial_task.cancel()
            try:
                await partial_task
            except asyncio.CancelledError:
                pass
        if self._partial_task is partial_task:
            self._partial_task = None
            self._partial_token = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._active_has_pending_history:
            await self._discard_pending_turn(token.turn_id)
        self._audio_frames.clear()
        self._reset_partial_locked()
        owner = ("turn", token.turn_id)
        await self._purge_owner(owner)
        self._active_task = None
        self._active_token = None
        self._active_has_pending_history = False
        request_id = self._voice_transcript_request_ids.pop(token.turn_id, None)
        return TurnCancelled(
            type="turn.cancelled",
            session_id=token.session_id,
            turn_id=token.turn_id,
            request_id=request_id,
        )

    async def _discard_pending_turn(self, turn_id: int) -> None:
        self.history.cancel_turn(turn_id)
        self._source_message_ids.pop(turn_id, None)
        if self.conversation_store is not None:
            await self.conversation_store.cancel_turn(turn_id)

    async def _cancel_preview(self) -> None:
        task = self._preview_task
        cancel = self._preview_cancel
        if task is None:
            return
        if cancel is not None:
            cancel.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        owner = ("preview", self._preview_id)
        await self._purge_owner(owner)
        self._preview_task = None
        self._preview_cancel = None

    async def _await_sync(self, function: Callable[..., _T], *args: object) -> _T:
        await self._ensure_task_group()
        async def capture_result() -> tuple[bool, _T | Exception]:
            try:
                return True, await asyncio.to_thread(function, *args)
            except Exception as error:
                return False, error

        thread_task = self._task_group.create_task(capture_result())
        try:
            succeeded, result = await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            try:
                await thread_task
            except asyncio.CancelledError:
                pass
            raise
        if not succeeded:
            assert isinstance(result, Exception)
            raise result
        return result

    async def _ensure_task_group(self) -> None:
        if self._task_group_entered:
            return
        async with self._lifecycle_lock:
            if not self._task_group_entered:
                await self._task_group.__aenter__()
                self._task_group_entered = True

    async def _emit(self, batch: _OutputBatch) -> None:
        await self._outputs.put(batch)
        async with self._output_changed:
            self._output_changed.notify_all()

    async def _drain(
        self, owner: _Owner, cancelled: asyncio.Event
    ) -> AsyncIterator[Output]:
        while True:
            async with self._output_changed:
                if self._is_closed() or cancelled.is_set():
                    return
                batch = self._remove_first_owner_batch(owner)
                while batch is None:
                    await self._output_changed.wait()
                    if self._is_closed() or cancelled.is_set():
                        return
                    batch = self._remove_first_owner_batch(owner)
            try:
                for item in batch.items:
                    if self._is_closed() or cancelled.is_set():
                        return
                    yield item
            finally:
                self._outputs.task_done()
            if batch.terminal:
                return

    def _remove_first_owner_batch(self, owner: _Owner) -> _OutputBatch | None:
        selected: _OutputBatch | None = None
        retained: list[_OutputBatch] = []
        for _ in range(self._outputs.qsize()):
            try:
                batch = self._outputs.get_nowait()
            except asyncio.QueueEmpty:
                break
            if selected is None and batch.owner == owner:
                selected = batch
            else:
                self._outputs.task_done()
                retained.append(batch)
        for batch in retained:
            self._outputs.put_nowait(batch)
        return selected

    async def _purge_owner(self, owner: _Owner) -> None:
        async with self._output_changed:
            retained: list[_OutputBatch] = []
            for _ in range(self._outputs.qsize()):
                try:
                    batch = self._outputs.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._outputs.task_done()
                if batch.owner != owner:
                    retained.append(batch)
            for batch in retained:
                self._outputs.put_nowait(batch)
            self._output_changed.notify_all()

    async def _clear_all_outputs(self) -> None:
        async with self._output_changed:
            while True:
                try:
                    self._outputs.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._outputs.task_done()
            self._output_changed.notify_all()

    def _finish_active(self, token: TurnToken) -> None:
        self._voice_transcript_request_ids.pop(token.turn_id, None)
        if self._active_token is token:
            self._active_task = None
            self._active_token = None
            self._active_has_pending_history = False
            if self.state.active_turn is token:
                self.state.complete_turn(token)
            else:
                self.state.phase = Phase.IDLE

    def _validate_voice(
        self, voice_key: str, speed: float, *, require_previewable: bool
    ) -> ErrorMessage | None:
        if isinstance(speed, bool) or speed not in PUBLIC_TTS_SPEEDS:
            return self._error("invalid_voice_speed", "语速只能是 0.8、1.0 或 1.2")
        try:
            profile = self.voice_catalog.get(voice_key)
        except VoiceCatalogError:
            return self._error("unknown_voice", "没有找到这个音色")
        if require_previewable and not profile.previewable:
            return self._error("voice_not_previewable", "这个音色暂不支持试听")
        return None

    def _frame_samples(self, frame: bytes) -> np.ndarray:
        return np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0

    def _all_samples(self) -> np.ndarray:
        frames, self._audio_frames = self._audio_frames, []
        return np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float32) / 32768.0

    def _reset_partial_locked(self) -> None:
        if self._partial_task is not None and not self._partial_task.done():
            raise RuntimeError("partial ASR cannot reset while work is running")
        self._partial_task = None
        self._partial_token = None
        self._partial_text = ""
        self._waiting_silence = False
        if self.partial_asr is not None:
            self.partial_asr.reset()

    @staticmethod
    def _error(code: str, message: str) -> ErrorMessage:
        return ErrorMessage(type="error", code=code, message=message, recoverable=True)

    def _is_closed(self) -> bool:
        return self._stopping or self._stopped or self._reset_paused

    @staticmethod
    def _stopped_error() -> ErrorMessage:
        return ConversationOrchestrator._error("session_stopped", "会话已经结束")
