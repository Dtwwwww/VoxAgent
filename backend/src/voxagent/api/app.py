from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware

from voxagent.api.data import DataService, register_data_routes
from voxagent.api.knowledge import KnowledgeService, register_knowledge_routes
from voxagent.api.memory import MemoryService, register_memory_routes
from voxagent.api.persona import PersonaService, register_persona_routes
from voxagent.api.protocol import parse_client_message, validate_audio_frame
from voxagent.conversation.events import (
    INPUT_AUDIO_FORMAT,
    ErrorMessage,
    SessionReady,
    TtsChunk,
    VoiceInfo,
    VoicePreviewChunk,
    VoicesAvailable,
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class _State(Protocol):
    session_id: object


class _VoiceCatalog(Protocol):
    def public_profiles(self) -> tuple[object, ...]: ...


class Orchestrator(Protocol):
    model_id: str
    state: _State
    voice_catalog: _VoiceCatalog

    def accept_audio(self, frame: bytes) -> AsyncIterator[_Output]: ...

    def submit_text(self, text: str, speak_response: bool) -> AsyncIterator[_Output]: ...

    def submit_voice_transcript(self, text: str) -> AsyncIterator[_Output]: ...

    def speak_message(
        self, turn_id: int, request_id: int = 0
    ) -> AsyncIterator[_Output]: ...

    def select_voice(self, voice_key: str, speed: float) -> _Output: ...

    def preview_voice(self, voice_key: str, speed: float) -> AsyncIterator[_Output]: ...

    async def cancel_active(self) -> _Output | None: ...

    def commit_audio(self) -> AsyncIterator[_Output]: ...

    async def stop(self) -> None: ...

    async def reset_conversation(self) -> None: ...

    async def resume_after_reset(self) -> None: ...


OrchestratorFactory = Callable[[], Orchestrator]
_Output = object | bytes
_MICROPHONE_QUEUE_CAPACITY = 32


def _validate_session_token(token: str) -> str:
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError("session token must be a 32-byte URL-safe value without padding")
    try:
        decoded = base64.urlsafe_b64decode(token + "=")
    except (binascii.Error, ValueError) as error:
        message = "session token must be a 32-byte URL-safe value without padding"
        raise ValueError(message) from error
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if len(decoded) != 32 or not hmac.compare_digest(token, canonical):
        raise ValueError("session token must be a 32-byte URL-safe value without padding")
    return token


class _SocketWriter:
    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._queue: asyncio.Queue[tuple[_Output, ...]] = asyncio.Queue(maxsize=32)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None:
            raise RuntimeError("socket writer already started")
        self._task = asyncio.create_task(self.run(), name="voice-socket-writer")
        return self._task

    async def run(self) -> None:
        while True:
            batch = await self._queue.get()
            try:
                for item in batch:
                    if isinstance(item, bytes):
                        await self._socket.send_bytes(item)
                    else:
                        await self._socket.send_json(item.model_dump(mode="json"))
            finally:
                self._queue.task_done()

    async def send(self, *items: _Output) -> None:
        task = self._task
        if task is None:
            raise RuntimeError("socket writer has not started")
        if task.done():
            await self._raise_writer_result(task)
        pending_put = asyncio.create_task(self._queue.put(tuple(items)))
        try:
            done, _ = await asyncio.wait(
                (pending_put, task), return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            pending_put.cancel()
            await asyncio.gather(pending_put, return_exceptions=True)
            raise
        if task in done:
            if not pending_put.done():
                pending_put.cancel()
                await asyncio.gather(pending_put, return_exceptions=True)
            await self._raise_writer_result(task)
        await pending_put
        if task.done():
            await self._raise_writer_result(task)

    @staticmethod
    async def _raise_writer_result(task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError as error:
            raise RuntimeError("socket writer stopped") from error
        raise RuntimeError("socket writer stopped unexpectedly")

    async def close(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()


def _public_voices(orchestrator: Orchestrator) -> VoicesAvailable:
    voices = [
        VoiceInfo(
            voice_key=profile.voice_key,
            display_name=profile.display_name,
            description=profile.description,
            gender=profile.gender,
            is_default=profile.is_default,
            previewable=profile.previewable,
        )
        for profile in orchestrator.voice_catalog.public_profiles()
    ]
    return VoicesAvailable(type="voices.available", voices=voices)


async def _forward_outputs(
    outputs: AsyncIterator[_Output], writer: _SocketWriter
) -> None:
    iterator = aiter(outputs)
    async for item in iterator:
        if isinstance(item, (TtsChunk, VoicePreviewChunk)):
            audio = await anext(iterator)
            if not isinstance(audio, bytes) or len(audio) != item.byte_length:
                raise RuntimeError("audio metadata must be followed by matching WAV bytes")
            await writer.send(item, audio)
        else:
            await writer.send(item)


class _MicrophoneWorker:
    def __init__(
        self,
        orchestrator: Orchestrator,
        writer: _SocketWriter,
        track_producer: Callable[[asyncio.Task[object]], object],
        *,
        max_pending: int = _MICROPHONE_QUEUE_CAPACITY,
    ) -> None:
        if max_pending < 1:
            raise ValueError("microphone queue capacity must be positive")
        self._orchestrator = orchestrator
        self._writer = writer
        self._track_producer = track_producer
        self._queue: asyncio.Queue[tuple[str, bytes | None]] = asyncio.Queue(
            maxsize=max_pending
        )
        self._task: asyncio.Task[object] | None = None

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def task(self) -> asyncio.Task[object] | None:
        return self._task

    def start(self) -> asyncio.Task[object]:
        if self._task is not None:
            raise RuntimeError("microphone worker already started")
        self._task = asyncio.create_task(self.run(), name="voice-microphone-worker")
        return self._task

    async def submit_frame(self, frame: bytes) -> None:
        await self._submit(("frame", frame))

    async def submit_commit(self) -> None:
        await self._submit(("commit", None))

    async def _submit(self, command: tuple[str, bytes | None]) -> None:
        task = self._task
        if task is None:
            raise RuntimeError("microphone worker has not started")
        if task.done():
            await self._raise_worker_result(task)
        pending_put = asyncio.create_task(self._queue.put(command))
        try:
            done, _ = await asyncio.wait(
                (pending_put, task), return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            pending_put.cancel()
            await asyncio.gather(pending_put, return_exceptions=True)
            raise
        if task in done:
            if not pending_put.done():
                pending_put.cancel()
                await asyncio.gather(pending_put, return_exceptions=True)
            await self._raise_worker_result(task)
        await pending_put
        if task.done():
            await self._raise_worker_result(task)

    @staticmethod
    async def _raise_worker_result(task: asyncio.Task[object]) -> None:
        try:
            await task
        except asyncio.CancelledError as error:
            raise RuntimeError("microphone worker stopped") from error
        raise RuntimeError("microphone worker stopped unexpectedly")

    async def run(self) -> None:
        while True:
            kind, frame = await self._queue.get()
            try:
                outputs = (
                    self._orchestrator.accept_audio(frame)
                    if kind == "frame" and frame is not None
                    else self._orchestrator.commit_audio()
                )
                await self._prime(outputs)
            finally:
                self._queue.task_done()

    async def _prime(self, outputs: AsyncIterator[_Output]) -> None:
        iterator = aiter(outputs)
        try:
            first = await anext(iterator)
        except StopAsyncIteration:
            return
        if isinstance(first, (TtsChunk, VoicePreviewChunk)):
            audio = await anext(iterator)
            if not isinstance(audio, bytes) or len(audio) != first.byte_length:
                raise RuntimeError("audio metadata must be followed by matching WAV bytes")
            await self._writer.send(first, audio)
        else:
            await self._writer.send(first)
        remainder = asyncio.create_task(
            _forward_outputs(iterator, self._writer),
            name="voice-microphone-output",
        )
        self._track_producer(remainder)

    async def join(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()


async def _dispatch_event(
    event: object, orchestrator: Orchestrator, writer: _SocketWriter
) -> bool:
    event_type = getattr(event, "type", None)
    if event_type == "session.start":
        ready = SessionReady(
            type="session.ready",
            session_id=orchestrator.state.session_id,
            model_id=orchestrator.model_id,
            offline=True,
            input_audio=INPUT_AUDIO_FORMAT,
        )
        await writer.send(ready, _public_voices(orchestrator))
    elif event_type == "text.submit":
        await _forward_outputs(
            orchestrator.submit_text(event.text, event.speak_response), writer
        )
    elif event_type == "voice.transcript.submit":
        await _forward_outputs(orchestrator.submit_voice_transcript(event.text), writer)
    elif event_type == "assistant.speak":
        await _forward_outputs(
            orchestrator.speak_message(event.turn_id, event.request_id), writer
        )
    elif event_type == "voice.select":
        await writer.send(orchestrator.select_voice(event.voice_key, event.speed))
    elif event_type == "voice.preview":
        await _forward_outputs(
            orchestrator.preview_voice(event.voice_key, event.speed), writer
        )
    elif event_type == "turn.cancel":
        cancelled = await orchestrator.cancel_active()
        if cancelled is not None:
            await writer.send(cancelled)
    elif event_type == "audio.commit":
        await _forward_outputs(orchestrator.commit_audio(), writer)
    elif event_type == "session.stop":
        await orchestrator.stop()
        return True
    else:
        raise RuntimeError(f"unsupported validated client event: {event_type}")
    return False


def create_app(
    orchestrator_factory: OrchestratorFactory,
    session_token: str,
    *,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
    knowledge_service: KnowledgeService | None = None,
    memory_service: MemoryService | None = None,
    persona_service: PersonaService | None = None,
    data_service: DataService | None = None,
) -> FastAPI:
    expected_token = _validate_session_token(session_token)
    expected_token_bytes = expected_token.encode("ascii")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if on_shutdown is not None:
                await on_shutdown()

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    active_orchestrators: set[Orchestrator] = set()

    async def reset_active_conversations() -> None:
        if knowledge_service is not None:
            await knowledge_service.cancel_import()
        for orchestrator in tuple(active_orchestrators):
            await orchestrator.reset_conversation()

    async def resume_active_conversations() -> None:
        for orchestrator in tuple(active_orchestrators):
            await orchestrator.resume_after_reset()

    if knowledge_service is not None:
        register_knowledge_routes(app, knowledge_service, expected_token)
    if memory_service is not None:
        register_memory_routes(app, memory_service, expected_token)
    if persona_service is not None:
        register_persona_routes(app, persona_service, expected_token)
    if data_service is not None:
        register_data_routes(
            app,
            data_service,
            expected_token,
            on_reset=reset_active_conversations,
            on_reset_complete=resume_active_conversations,
        )
    slot_lock = asyncio.Lock()
    session_active = False

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "offline": True}

    @app.websocket("/v1/voice")
    async def voice_socket(socket: WebSocket) -> None:
        nonlocal session_active
        candidate = socket.query_params.get("token", "")
        try:
            candidate_bytes = candidate.encode("ascii")
        except UnicodeEncodeError:
            authenticated = False
        else:
            authenticated = hmac.compare_digest(candidate_bytes, expected_token_bytes)
        if not authenticated:
            await socket.close(code=4401)
            return
        async with slot_lock:
            if session_active:
                await socket.close(code=4409)
                return
            session_active = True

        orchestrator: Orchestrator | None = None
        writer: _SocketWriter | None = None
        microphone: _MicrophoneWorker | None = None
        receive_task: asyncio.Task[dict[str, object]] | None = None
        producers: set[asyncio.Task[object]] = set()
        orchestrator_stopped = False
        try:
            await socket.accept()
            try:
                orchestrator = await asyncio.to_thread(orchestrator_factory)
            except Exception:
                await socket.send_json(
                    ErrorMessage(
                        type="error",
                        code="speech_runtime_startup",
                        message="本地语音模型初始化失败，请重启服务后再试",
                        recoverable=True,
                    ).model_dump(mode="json")
                )
                await socket.close(code=1011)
                return
            active_orchestrators.add(orchestrator)
            writer = _SocketWriter(socket)
            writer.start()
            microphone = _MicrophoneWorker(orchestrator, writer, producers.add)
            producers.add(microphone.start())
            while True:
                if receive_task is None:
                    receive_task = asyncio.create_task(
                        socket.receive(), name="voice-socket-receive"
                    )
                done, _ = await asyncio.wait(
                    {receive_task, *producers}, return_when=asyncio.FIRST_COMPLETED
                )
                completed_producers = done.intersection(producers)
                for producer in completed_producers:
                    producers.remove(producer)
                    await producer
                if receive_task not in done:
                    continue
                message = receive_task.result()
                receive_task = None
                if message["type"] == "websocket.disconnect":
                    break
                raw = message.get("bytes")
                if raw is not None:
                    try:
                        validate_audio_frame(raw)
                    except (TypeError, ValueError):
                        await socket.close(code=4400)
                        break
                    await microphone.submit_frame(raw)
                    continue
                try:
                    decoded = json.loads(message.get("text") or "")
                except (json.JSONDecodeError, TypeError):
                    await socket.close(code=4400)
                    break
                try:
                    event = parse_client_message(decoded)
                except (ValidationError, ValueError, TypeError):
                    await writer.send(
                        ErrorMessage(
                            type="error",
                            code="invalid_event",
                            message="请求内容无效，请修改后重试",
                            recoverable=True,
                        )
                    )
                    continue
                if event.type == "audio.commit":
                    await microphone.submit_commit()
                elif event.type in {
                    "text.submit",
                    "voice.transcript.submit",
                    "assistant.speak",
                    "voice.preview",
                }:
                    producer = asyncio.create_task(
                        _dispatch_event(event, orchestrator, writer),
                        name=f"voice-socket-{event.type}",
                    )
                    producers.add(producer)
                elif await _dispatch_event(event, orchestrator, writer):
                    orchestrator_stopped = True
                    await socket.close(code=1000)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            try:
                try:
                    if microphone is not None:
                        worker_task = microphone.task
                        await microphone.close()
                        if worker_task is not None:
                            producers.discard(worker_task)
                    pending = [*producers]
                    if receive_task is not None:
                        pending.append(receive_task)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if orchestrator is not None and not orchestrator_stopped:
                        await orchestrator.stop()
                except Exception:
                    pass
                finally:
                    if writer is not None:
                        await writer.close()
                    if orchestrator is not None:
                        active_orchestrators.discard(orchestrator)
            finally:
                async with slot_lock:
                    session_active = False

    return app
