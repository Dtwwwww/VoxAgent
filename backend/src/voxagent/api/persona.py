from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from voxagent.api.auth import require_bearer
from voxagent.conversation.persona import DEFAULT_PERSONA, PersonaConfig
from voxagent.db.connection import open_database


class PersonaConflictError(RuntimeError):
    pass


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    config: PersonaConfig


class PersonaService(Protocol):
    async def get_persona(self) -> dict[str, object]: ...

    async def update_persona(self, request: PersonaUpdate) -> dict[str, object]: ...


def _payload(config: PersonaConfig, revision: int) -> dict[str, object]:
    return {"config": config.model_dump(mode="json"), "revision": revision}


class LocalPersonaService:
    def __init__(
        self, database_path: Path, mutation_lock: asyncio.Lock | None = None
    ) -> None:
        self._database_path = database_path
        self._write_lock = mutation_lock or asyncio.Lock()

    async def get_persona(self) -> dict[str, object]:
        return await asyncio.to_thread(self._get_persona)

    def load_config(self) -> PersonaConfig:
        return self._load()[0]

    def _get_persona(self) -> dict[str, object]:
        config, revision = self._load()
        return _payload(config, revision)

    def _load(self) -> tuple[PersonaConfig, int]:
        connection = open_database(self._database_path)
        try:
            row = connection.execute(
                "SELECT config_json, revision FROM persona_config WHERE id = 1"
            ).fetchone()
            if row is None:
                return DEFAULT_PERSONA, 0
            return PersonaConfig.model_validate_json(row["config_json"]), int(
                row["revision"]
            )
        finally:
            connection.close()

    async def update_persona(self, request: PersonaUpdate) -> dict[str, object]:
        async with self._write_lock:
            return await asyncio.to_thread(self._update_persona, request)

    def _update_persona(self, request: PersonaUpdate) -> dict[str, object]:
        connection = open_database(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM persona_config WHERE id = 1"
            ).fetchone()
            current_revision = int(row["revision"]) if row is not None else 0
            if request.expected_revision != current_revision:
                raise PersonaConflictError("人格设置已发生变化，请刷新后重试")
            revision = current_revision + 1
            timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            )
            connection.execute(
                """
                INSERT INTO persona_config(id, config_json, revision, updated_at_utc)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    config_json = excluded.config_json,
                    revision = excluded.revision,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    json.dumps(request.config.model_dump(mode="json"), ensure_ascii=False),
                    revision,
                    timestamp,
                ),
            )
            connection.commit()
            return _payload(request.config, revision)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def register_persona_routes(
    app: FastAPI, service: PersonaService, session_token: str
) -> None:
    expected_token = session_token.encode("ascii")

    @app.get("/v1/persona")
    async def get_persona(
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        return await service.get_persona()

    @app.put("/v1/persona")
    async def update_persona(
        request: PersonaUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        try:
            return await service.update_persona(request)
        except PersonaConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
