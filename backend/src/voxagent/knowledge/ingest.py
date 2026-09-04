from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from voxagent.knowledge.extract import chunk_document, extract_document
from voxagent.memory.embedder import EMBEDDING_DIMENSION, Embedder


class ImportCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImportResult:
    document_id: int
    created: bool
    chunk_count: int
    sha256: str


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class KnowledgeIngestor:
    def __init__(
        self,
        connection: sqlite3.Connection,
        embedder: Embedder,
        *,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._connection = connection
        self._embedder = embedder
        self._batch_size = batch_size

    @contextmanager
    def _write(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def import_file(
        self,
        path: Path,
        *,
        now_utc: datetime,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> ImportResult:
        source = path.expanduser().resolve(strict=True)
        digest = _sha256(source)
        existing = self._existing(digest)
        if existing is not None:
            return ImportResult(existing["id"], False, existing["chunk_count"], digest)

        document = extract_document(source)
        chunks = chunk_document(document)
        if not chunks:
            raise ValueError("文档中没有可索引的文字")
        vectors: list[np.ndarray] = []
        for start in range(0, len(chunks), self._batch_size):
            if is_cancelled():
                raise ImportCancelled("knowledge import cancelled")
            batch = chunks[start : start + self._batch_size]
            encoded = np.asarray(
                self._embedder.encode(tuple(chunk.content for chunk in batch)),
                dtype=np.float32,
            )
            if encoded.shape != (len(batch), EMBEDDING_DIMENSION):
                raise ValueError("embedder returned an unexpected matrix shape")
            vectors.extend(encoded)
        if is_cancelled():
            raise ImportCancelled("knowledge import cancelled")

        timestamp = _utc_text(now_utc)
        with self._write():
            concurrent = self._existing(digest)
            if concurrent is not None:
                return ImportResult(
                    concurrent["id"], False, concurrent["chunk_count"], digest
                )
            result = self._connection.execute(
                """
                INSERT INTO documents(
                    display_name, source_path, sha256, mime_type, imported_at_utc
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (document.display_name, str(source), digest, document.mime_type, timestamp),
            )
            document_id = int(result.lastrowid)
            self._connection.executemany(
                """
                INSERT INTO document_chunks(
                    document_id, ordinal, content, page_number, embedding, embedding_dim
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        document_id,
                        chunk.ordinal,
                        chunk.content,
                        chunk.page_number,
                        np.asarray(vector, dtype=np.float32).tobytes(),
                        EMBEDDING_DIMENSION,
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ),
            )
            self._audit("knowledge.imported", document_id, {"sha256": digest}, timestamp)
        return ImportResult(document_id, True, len(chunks), digest)

    def delete_document(self, document_id: int, *, now_utc: datetime) -> bool:
        timestamp = _utc_text(now_utc)
        with self._write():
            result = self._connection.execute(
                "DELETE FROM documents WHERE id = ?", (document_id,)
            )
            if result.rowcount == 0:
                return False
            self._audit("knowledge.deleted", document_id, {}, timestamp)
        return True

    def _existing(self, digest: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT documents.id, COUNT(document_chunks.id) AS chunk_count
            FROM documents
            LEFT JOIN document_chunks ON document_chunks.document_id = documents.id
            WHERE documents.sha256 = ?
            GROUP BY documents.id
            """,
            (digest,),
        ).fetchone()

    def _audit(
        self,
        event_type: str,
        entity_id: int,
        detail: dict[str, object],
        timestamp: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events(
                event_type, entity_type, entity_id, detail_json, created_at_utc
            ) VALUES (?, 'document', ?, ?, ?)
            """,
            (event_type, entity_id, json.dumps(detail, sort_keys=True), timestamp),
        )
