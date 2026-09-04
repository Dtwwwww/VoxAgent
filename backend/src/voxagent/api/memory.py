from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from voxagent.api.auth import require_bearer
from voxagent.db.connection import open_database
from voxagent.memory.embedder import EMBEDDING_DIMENSION, Embedder
from voxagent.memory.models import MemoryCandidate, MemoryKind, MemoryRecord, PolicyStatus
from voxagent.memory.policy import MemoryPolicy
from voxagent.memory.repository import MemoryRepository


class MemoryConflictError(RuntimeError):
    pass


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    content: str = Field(min_length=1, max_length=500)
    importance: float = Field(ge=0, le=1)
    source_turn_id: int | None = Field(default=None, ge=1)
    confirmed: bool = False


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)
    importance: float = Field(ge=0, le=1)
    expected_updated_at_utc: str = Field(min_length=1, max_length=40)


class MemoryService(Protocol):
    async def list_memories(self, limit: int, offset: int) -> tuple[dict[str, object], ...]: ...

    async def create_memory(self, request: MemoryCreate) -> dict[str, object]: ...

    async def update_memory(
        self, memory_id: int, request: MemoryUpdate
    ) -> dict[str, object]: ...

    async def delete_memory(self, memory_id: int) -> bool: ...


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _public_memory(record: MemoryRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "kind": record.kind.value,
        "content": record.content,
        "importance": record.importance,
        "source_turn_id": record.source_turn_id,
        "created_at_utc": _utc_text(record.created_at_utc),
        "updated_at_utc": _utc_text(record.updated_at_utc),
    }


class LocalMemoryService:
    def __init__(self, database_path: Path, embedder: Embedder) -> None:
        self._database_path = database_path
        self._embedder = embedder
        self._policy = MemoryPolicy()
        self._write_lock = asyncio.Lock()

    async def list_memories(
        self, limit: int, offset: int
    ) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self._list_memories, limit, offset)

    def _list_memories(self, limit: int, offset: int) -> tuple[dict[str, object], ...]:
        connection = open_database(self._database_path)
        try:
            return tuple(
                _public_memory(item)
                for item in MemoryRepository(connection).list(limit=limit, offset=offset)
            )
        finally:
            connection.close()

    async def create_memory(self, request: MemoryCreate) -> dict[str, object]:
        candidate = MemoryCandidate(
            request.kind,
            request.content,
            request.importance,
            None,
            user_explicit=request.confirmed,
            source_turn_id=request.source_turn_id,
        )
        decision = self._policy.evaluate(candidate)
        if decision.status is PolicyStatus.REJECT:
            raise ValueError("该内容不能保存为长期记忆")
        if decision.status is PolicyStatus.REQUIRES_CONFIRMATION and not request.confirmed:
            raise MemoryConflictError("该记忆需要用户明确确认")
        async with self._write_lock:
            return await asyncio.to_thread(self._create_memory, candidate)

    def _create_memory(self, candidate: MemoryCandidate) -> dict[str, object]:
        vector = np.asarray(self._embedder.encode((candidate.content,))[0], dtype=np.float32)
        connection = open_database(self._database_path)
        try:
            record = MemoryRepository(connection).create(
                candidate,
                embedding=vector.tobytes(),
                embedding_dim=EMBEDDING_DIMENSION,
                now_utc=datetime.now(UTC),
            )
            return _public_memory(record)
        finally:
            connection.close()

    async def update_memory(
        self, memory_id: int, request: MemoryUpdate
    ) -> dict[str, object]:
        async with self._write_lock:
            return await asyncio.to_thread(self._update_memory, memory_id, request)

    def _update_memory(self, memory_id: int, request: MemoryUpdate) -> dict[str, object]:
        connection = open_database(self._database_path)
        try:
            repository = MemoryRepository(connection)
            current = repository.get(memory_id)
            if _utc_text(current.updated_at_utc) != request.expected_updated_at_utc:
                raise MemoryConflictError("记忆已发生变化，请刷新后重试")
            candidate = MemoryCandidate(
                current.kind,
                request.content,
                request.importance,
                current.source_message_id,
                user_explicit=True,
                source_turn_id=current.source_turn_id,
            )
            if self._policy.evaluate(candidate).status is PolicyStatus.REJECT:
                raise ValueError("该内容不能保存为长期记忆")
            vector = np.asarray(
                self._embedder.encode((request.content,))[0], dtype=np.float32
            )
            return _public_memory(
                repository.update(
                    memory_id,
                    content=request.content,
                    importance=request.importance,
                    embedding=vector.tobytes(),
                    embedding_dim=EMBEDDING_DIMENSION,
                    now_utc=datetime.now(UTC),
                )
            )
        except KeyError as error:
            raise LookupError(memory_id) from error
        finally:
            connection.close()

    async def delete_memory(self, memory_id: int) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(self._delete_memory, memory_id)

    def _delete_memory(self, memory_id: int) -> bool:
        connection = open_database(self._database_path)
        try:
            return MemoryRepository(connection).delete(memory_id, now_utc=datetime.now(UTC))
        finally:
            connection.close()


def register_memory_routes(app: FastAPI, service: MemoryService, session_token: str) -> None:
    expected_token = session_token.encode("ascii")

    @app.get("/v1/memories")
    async def list_memories(
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ) -> tuple[dict[str, object], ...]:
        require_bearer(authorization, expected_token)
        return await service.list_memories(limit, offset)

    @app.post("/v1/memories", status_code=status.HTTP_201_CREATED)
    async def create_memory(
        request: MemoryCreate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        try:
            return await service.create_memory(request)
        except MemoryConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.patch("/v1/memories/{memory_id}")
    async def update_memory(
        memory_id: int,
        request: MemoryUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        try:
            return await service.update_memory(memory_id, request)
        except MemoryConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error

    @app.delete("/v1/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(
        memory_id: int,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_bearer(authorization, expected_token)
        if not await service.delete_memory(memory_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
