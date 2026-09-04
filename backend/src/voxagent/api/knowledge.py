from __future__ import annotations

import asyncio
import hmac
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status

from voxagent.db.connection import open_database
from voxagent.knowledge.ingest import KnowledgeIngestor
from voxagent.memory.embedder import Embedder

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx"}


class KnowledgeService(Protocol):
    async def list_documents(self) -> tuple[dict[str, object], ...]: ...

    async def import_upload(
        self, filename: str, content: bytes
    ) -> dict[str, object]: ...

    async def delete_document(self, document_id: int) -> bool: ...


class LocalKnowledgeService:
    def __init__(
        self, database_path: Path, temp_directory: Path, embedder: Embedder
    ) -> None:
        self._database_path = database_path
        self._temp_directory = temp_directory
        self._embedder = embedder
        self._write_lock = asyncio.Lock()

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
        async with self._write_lock:
            return await asyncio.to_thread(self._import_upload, safe_name, content)

    def _import_upload(self, filename: str, content: bytes) -> dict[str, object]:
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

    @staticmethod
    def _validate_filename(filename: str) -> str:
        name = filename.strip()
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("文件名无效")
        if Path(name).suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ValueError("仅支持 TXT、Markdown、PDF 和 DOCX 文档")
        return name


def _authorize(authorization: str | None, expected_token: bytes) -> None:
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        candidate = authorization[len(prefix) :].encode("ascii")
    except UnicodeEncodeError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error
    if not hmac.compare_digest(candidate, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def register_knowledge_routes(
    app: FastAPI, service: KnowledgeService, session_token: str
) -> None:
    expected_token = session_token.encode("ascii")

    @app.get("/v1/knowledge")
    async def list_documents(
        authorization: str | None = Header(default=None),
    ) -> tuple[dict[str, object], ...]:
        _authorize(authorization, expected_token)
        return await service.list_documents()

    @app.post("/v1/knowledge", status_code=status.HTTP_201_CREATED)
    async def import_document(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        _authorize(authorization, expected_token)
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
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.delete("/v1/knowledge/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(
        document_id: int,
        authorization: str | None = Header(default=None),
    ) -> Response:
        _authorize(authorization, expected_token)
        if not await service.delete_document(document_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
