from __future__ import annotations

import asyncio
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status

from voxagent.api.auth import require_bearer
from voxagent.db.connection import open_database
from voxagent.knowledge.ingest import ImportCancelled, KnowledgeIngestor
from voxagent.memory.embedder import Embedder

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx"}


class KnowledgeService(Protocol):
    async def list_documents(self) -> tuple[dict[str, object], ...]: ...

    async def import_upload(
        self, filename: str, content: bytes
    ) -> dict[str, object]: ...

    async def delete_document(self, document_id: int) -> bool: ...

    async def cancel_import(self) -> bool: ...

    async def list_chunks(
        self, document_id: int, limit: int, offset: int
    ) -> tuple[dict[str, object], ...]: ...


class LocalKnowledgeService:
    def __init__(
        self,
        database_path: Path,
        temp_directory: Path,
        embedder: Embedder,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._database_path = database_path
        self._temp_directory = temp_directory
        self._embedder = embedder
        self._write_lock = mutation_lock or asyncio.Lock()
        self._active_import: threading.Event | None = None
        self._import_state_lock = threading.Lock()

    async def list_documents(self) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self._list_documents)

    def _list_documents(self) -> tuple[dict[str, object], ...]:
        connection = open_database(self._database_path)
        try:
            rows = connection.execute(
                """
                SELECT documents.id, documents.display_name, documents.sha256,
                       documents.mime_type, documents.imported_at_utc,
                       COUNT(document_chunks.id) AS chunk_count
                FROM documents
                LEFT JOIN document_chunks
                  ON document_chunks.document_id = documents.id
                GROUP BY documents.id
                ORDER BY documents.imported_at_utc DESC, documents.id DESC
                """
            ).fetchall()
            return tuple(
                {
                    "id": int(row["id"]),
                    "display_name": row["display_name"],
                    "sha256": row["sha256"],
                    "mime_type": row["mime_type"],
                    "imported_at_utc": row["imported_at_utc"],
                    "chunk_count": int(row["chunk_count"]),
                }
                for row in rows
            )
        finally:
            connection.close()

    async def import_upload(self, filename: str, content: bytes) -> dict[str, object]:
        safe_name = self._validate_filename(filename)
        if not content:
            raise ValueError("文档内容为空")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文档不能超过 20 MB")
        cancellation = threading.Event()
        with self._import_state_lock:
            if self._active_import is not None:
                raise ValueError("已有文档正在导入，请等待或先取消")
            self._active_import = cancellation
        try:
            async with self._write_lock:
                if cancellation.is_set():
                    raise ImportCancelled("knowledge import cancelled")
                return await asyncio.to_thread(
                    self._import_upload, safe_name, content, cancellation
                )
        finally:
            with self._import_state_lock:
                if self._active_import is cancellation:
                    self._active_import = None

    def _import_upload(
        self, filename: str, content: bytes, cancellation: threading.Event
    ) -> dict[str, object]:
        self._temp_directory.mkdir(parents=True, exist_ok=True)
        upload_directory = Path(tempfile.mkdtemp(prefix="voxagent-", dir=self._temp_directory))
        upload_path = upload_directory / filename
        try:
            upload_path.write_bytes(content)
            connection = open_database(self._database_path)
            try:
                result = KnowledgeIngestor(connection, self._embedder).import_file(
                    upload_path,
                    now_utc=datetime.now(UTC),
                    source_label=f"browser-upload:{filename}",
                    is_cancelled=cancellation.is_set,
                )
            finally:
                connection.close()
            return {
                "document_id": result.document_id,
                "display_name": filename,
                "created": result.created,
                "chunk_count": result.chunk_count,
                "sha256": result.sha256,
            }
        finally:
            upload_path.unlink(missing_ok=True)
            upload_directory.rmdir()

    async def delete_document(self, document_id: int) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(self._delete_document, document_id)

    def _delete_document(self, document_id: int) -> bool:
        connection = open_database(self._database_path)
        try:
            return KnowledgeIngestor(connection, self._embedder).delete_document(
                document_id, now_utc=datetime.now(UTC)
            )
        finally:
            connection.close()

    async def cancel_import(self) -> bool:
        with self._import_state_lock:
            cancellation = self._active_import
            if cancellation is None:
                return False
            cancellation.set()
            return True

    async def list_chunks(
        self, document_id: int, limit: int, offset: int
    ) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self._list_chunks, document_id, limit, offset)

    def _list_chunks(
        self, document_id: int, limit: int, offset: int
    ) -> tuple[dict[str, object], ...]:
        connection = open_database(self._database_path)
        try:
            rows = connection.execute(
                """
                SELECT id, ordinal, page_number, content
                FROM document_chunks
                WHERE document_id = ?
                ORDER BY ordinal
                LIMIT ? OFFSET ?
                """,
                (document_id, limit, offset),
            ).fetchall()
            return tuple(
                {
                    "id": int(row["id"]),
                    "ordinal": int(row["ordinal"]),
                    "page_number": row["page_number"],
                    "content": row["content"],
                }
                for row in rows
            )
        finally:
            connection.close()

    @staticmethod
    def _validate_filename(filename: str) -> str:
        name = filename.strip()
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("文件名无效")
        if Path(name).suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError("仅支持 TXT、Markdown、PDF 和 DOCX 文档")
        return name


def register_knowledge_routes(
    app: FastAPI, service: KnowledgeService, session_token: str
) -> None:
    expected_token = session_token.encode("ascii")

    @app.get("/v1/knowledge")
    async def list_documents(
        authorization: str | None = Header(default=None),
    ) -> tuple[dict[str, object], ...]:
        require_bearer(authorization, expected_token)
        return await service.list_documents()

    @app.post("/v1/knowledge", status_code=status.HTTP_201_CREATED)
    async def import_document(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        require_bearer(authorization, expected_token)
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from None
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        try:
            return await service.import_upload(filename, bytes(body))
        except ImportCancelled as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="文档导入已取消",
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.delete("/v1/knowledge/import", status_code=status.HTTP_204_NO_CONTENT)
    async def cancel_import(
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_bearer(authorization, expected_token)
        await service.cancel_import()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.delete("/v1/knowledge/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(
        document_id: int,
        authorization: str | None = Header(default=None),
    ) -> Response:
        require_bearer(authorization, expected_token)
        if not await service.delete_document(document_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/knowledge/{document_id}/chunks")
    async def list_chunks(
        document_id: int,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ) -> tuple[dict[str, object], ...]:
        require_bearer(authorization, expected_token)
        return await service.list_chunks(document_id, limit, offset)
