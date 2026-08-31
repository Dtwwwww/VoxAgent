from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

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

    async def stop(self) -> None: ...


OrchestratorFactory = Callable[[], Orchestrator]
_Output = object | bytes


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


def create_app(orchestrator_factory: OrchestratorFactory, session_token: str) -> FastAPI:
    expected_token = _validate_session_token(session_token)
    expected_token_bytes = expected_token.encode("ascii")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
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
        try:
            orchestrator = orchestrator_factory()
            await socket.accept()
            writer = _SocketWriter(socket)
            writer.start()
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                raw = message.get("bytes")
                if raw is not None:
                    try:
                        validate_audio_frame(raw)
                    except (TypeError, ValueError):
                        await socket.close(code=4400)
                        break
                    accept_audio = getattr(orchestrator, "accept_audio", None)
                    if accept_audio is not None:
                        await _forward_outputs(accept_audio(raw), writer)
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
                if event.type == "session.start":
                    ready = SessionReady(
                        type="session.ready",
                        session_id=orchestrator.state.session_id,
                        model_id=orchestrator.model_id,
                        offline=True,
                        input_audio=INPUT_AUDIO_FORMAT,
                    )
                    await writer.send(ready, _public_voices(orchestrator))
                elif event.type == "session.stop":
                    await socket.close(code=1000)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            try:
                try:
                    if orchestrator is not None:
                        await orchestrator.stop()
                except Exception:
                    pass
                finally:
                    if writer is not None:
                        await writer.close()
            finally:
                async with slot_lock:
                    session_active = False

    return app
