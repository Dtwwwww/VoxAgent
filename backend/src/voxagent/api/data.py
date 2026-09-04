from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import threading
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from voxagent.api.auth import require_bearer
from voxagent.db.backup import DailyBackupManager

RESET_PHRASE = "删除声灵全部本地数据"
_EXPORT_TABLES = (
    "conversations",
    "messages",
    "memories",
    "documents",
    "document_chunks",
    "audit_events",
    "persona_config",
)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["删除声灵全部本地数据"]


class DataService(Protocol):
    async def create_export(self) -> dict[str, object]: ...

    def consume_export(self, download_id: str) -> Path | None: ...

    async def reset_all(self) -> None: ...

    def list_backups(self) -> tuple[dict[str, object], ...]: ...

    def delete_backup(self, filename: str) -> bool: ...


class LocalDataService:
    def __init__(
        self,
        database_path: Path,
        data_directory: Path,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._database_path = database_path
        self._data_directory = data_directory
        self._exports_directory = data_directory / "exports"
        self._backup_manager = DailyBackupManager(data_directory / "backups")
        self._tickets: dict[str, tuple[Path, datetime]] = {}
        self._tickets_lock = threading.Lock()
        self._write_lock = mutation_lock or asyncio.Lock()
        if self._exports_directory.exists():
            for orphan in self._exports_directory.glob("export-*.zip"):
                orphan.unlink(missing_ok=True)

    async def create_export(self) -> dict[str, object]:
        async with self._write_lock:
            return await asyncio.to_thread(self._create_export)

    def _create_export(self) -> dict[str, object]:
        self._exports_directory.mkdir(parents=True, exist_ok=True)
        download_id = secrets.token_urlsafe(24)
        archive_path = self._exports_directory / f"export-{download_id}.zip"
        payload = self._export_payload()
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "voxagent-export.json",
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        expires = datetime.now(UTC) + timedelta(minutes=10)
        with self._tickets_lock:
            self._discard_expired_locked(datetime.now(UTC))
            self._tickets[download_id] = (archive_path, expires)
        return {
            "download_id": download_id,
            "expires_at_utc": expires.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }

    def _export_payload(self) -> dict[str, object]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            version = int(
                connection.execute("SELECT version FROM schema_version").fetchone()[0]
            )
            payload: dict[str, object] = {
                "schema_version": version,
                "exported_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            for table in _EXPORT_TABLES:
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                payload[table] = [
                    {
                        key: row[key]
                        for key in row.keys()
                        if key not in {"embedding", "embedding_dim"}
                    }
                    for row in rows
                ]
            connection.commit()
            return payload
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def consume_export(self, download_id: str) -> Path | None:
        now = datetime.now(UTC)
        with self._tickets_lock:
            self._discard_expired_locked(now)
            ticket = self._tickets.pop(download_id, None)
        if ticket is None or ticket[1] <= now or not ticket[0].is_file():
            return None
        return ticket[0]

    def _discard_expired_locked(self, now: datetime) -> None:
        expired = [key for key, (_, expiry) in self._tickets.items() if expiry <= now]
        for key in expired:
            path, _ = self._tickets.pop(key)
            path.unlink(missing_ok=True)

    async def reset_all(self) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._reset_all)

    def _reset_all(self) -> None:
        connection = sqlite3.connect(self._database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "document_chunks",
                "documents",
                "memories",
                "messages",
                "conversations",
                "audit_events",
                "persona_config",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.commit()
            remaining = sum(
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in _EXPORT_TABLES
            )
            if remaining:
                raise RuntimeError("local data reset verification failed")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._backup_manager.purge_all()
        if self._exports_directory.exists():
            for archive in self._exports_directory.glob("export-*.zip"):
                archive.unlink()
        with self._tickets_lock:
            self._tickets.clear()

    def list_backups(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "filename": path.name,
                "date": path.stem.removeprefix("voxagent-"),
                "size_bytes": path.stat().st_size,
            }
            for path in self._backup_manager.list()
        )

    def delete_backup(self, filename: str) -> bool:
        return self._backup_manager.delete(filename)


def _delete_download(path: Path) -> None:
    path.unlink(missing_ok=True)


def register_data_routes(
    app: FastAPI,
    service: DataService,
    session_token: str,
    on_reset: Callable[[], Awaitable[None]] | None = None,
    on_reset_complete: Callable[[], Awaitable[None]] | None = None,
) -> None:
    expected_token = session_token.encode("ascii")
    reset_lock = asyncio.Lock()

    @app.post("/v1/data/export", status_code=status.HTTP_201_CREATED)
    async def create_export(
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        return await service.create_export()

    @app.get("/v1/data/export/{download_id}")
    async def download_export(
        download_id: str,
        authorization: str | None = Header(default=None),
    ) -> FileResponse:
        require_bearer(authorization, expected_token)
        path = service.consume_export(download_id)
        if path is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            path,
            media_type="application/zip",
            filename="voxagent-export.zip",
            background=BackgroundTask(_delete_download, path),
        )

    @app.delete("/v1/data", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_all(
        _request: ResetRequest,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_bearer(authorization, expected_token)
        async with reset_lock:
            try:
                if on_reset is not None:
                    await on_reset()
                await service.reset_all()
            finally:
                if on_reset_complete is not None:
                    await on_reset_complete()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/backups")
    async def list_backups(
        authorization: str | None = Header(default=None),
    ) -> tuple[dict[str, object], ...]:
        require_bearer(authorization, expected_token)
        return service.list_backups()

    @app.delete("/v1/backups/{filename}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_backup(
        filename: str,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_bearer(authorization, expected_token)
        try:
            deleted = service.delete_backup(filename)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
