from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import numpy as np

from voxagent.memory.models import (
    DuplicateMatch,
    MemoryCandidate,
    MemoryKind,
    MemoryRecord,
)
from voxagent.memory.policy import normalize_memory_text


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _validate_embedding(embedding: bytes, embedding_dim: int) -> bytes:
    value = bytes(embedding)
    if embedding_dim < 1 or len(value) != embedding_dim * np.dtype(np.float32).itemsize:
        raise ValueError("embedding must contain embedding_dim float32 values")
    return value


class MemoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def _write(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def create(
        self,
        candidate: MemoryCandidate,
        *,
        embedding: bytes,
        embedding_dim: int,
        now_utc: datetime,
    ) -> MemoryRecord:
        vector = _validate_embedding(embedding, embedding_dim)
        normalized = normalize_memory_text(candidate.content)
        timestamp = _utc_text(now_utc)
        with self._write():
            existing = self._connection.execute(
                "SELECT id FROM memories WHERE normalized_content = ? ORDER BY id LIMIT 1",
                (normalized,),
            ).fetchone()
            if existing is not None:
                memory_id = int(existing["id"])
                self._connection.execute(
                    "UPDATE memories SET updated_at_utc = ? WHERE id = ?",
                    (timestamp, memory_id),
                )
                self._audit(
                    "memory.refreshed",
                    memory_id,
                    {"kind": candidate.kind.value},
                    timestamp,
                )
            else:
                result = self._connection.execute(
                    """
                    INSERT INTO memories(
                        kind, content, normalized_content, importance,
                        source_message_id, embedding, embedding_dim,
                        created_at_utc, updated_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.kind.value,
                        candidate.content.strip(),
                        normalized,
                        candidate.importance,
                        candidate.source_message_id,
                        vector,
                        embedding_dim,
                        timestamp,
                        timestamp,
                    ),
                )
                memory_id = int(result.lastrowid)
                self._audit("memory.created", memory_id, {"kind": candidate.kind.value}, timestamp)
        return self.get(memory_id)

    def get(self, memory_id: int) -> MemoryRecord:
        row = self._connection.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return self._record(row)

    def list(self, *, limit: int, offset: int) -> tuple[MemoryRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        rows = self._connection.execute(
            "SELECT * FROM memories ORDER BY updated_at_utc DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return tuple(self._record(row) for row in rows)

    def update(
        self,
        memory_id: int,
        *,
        content: str,
        importance: float,
        embedding: bytes,
        embedding_dim: int,
        now_utc: datetime,
    ) -> MemoryRecord:
        vector = _validate_embedding(embedding, embedding_dim)
        normalized = normalize_memory_text(content)
        if not normalized:
            raise ValueError("memory content must not be empty")
        timestamp = _utc_text(now_utc)
        with self._write():
            result = self._connection.execute(
                """
                UPDATE memories
                SET content = ?, normalized_content = ?, importance = ?,
                    embedding = ?, embedding_dim = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (
                    content.strip(),
                    normalized,
                    importance,
                    vector,
                    embedding_dim,
                    timestamp,
                    memory_id,
                ),
            )
            if result.rowcount != 1:
                raise KeyError(memory_id)
            self._audit("memory.updated", memory_id, {}, timestamp)
        return self.get(memory_id)

    def delete(self, memory_id: int, *, now_utc: datetime) -> bool:
        timestamp = _utc_text(now_utc)
        with self._write():
            result = self._connection.execute(
                "DELETE FROM memories WHERE id = ?", (memory_id,)
            )
            if result.rowcount == 0:
                return False
            self._audit("memory.deleted", memory_id, {}, timestamp)
        return True

    def find_duplicate(
        self,
        kind: MemoryKind,
        *,
        embedding: bytes,
        embedding_dim: int,
        threshold: float = 0.94,
    ) -> DuplicateMatch | None:
        query = np.frombuffer(
            _validate_embedding(embedding, embedding_dim), dtype=np.float32
        ).astype(np.float64)
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            return None
        rows = self._connection.execute(
            """
            SELECT * FROM memories
            WHERE kind = ? AND embedding IS NOT NULL AND embedding_dim = ?
            ORDER BY id LIMIT 2000
            """,
            (kind.value, embedding_dim),
        ).fetchall()
        best: DuplicateMatch | None = None
        for row in rows:
            candidate = np.frombuffer(row["embedding"], dtype=np.float32).astype(np.float64)
            denominator = query_norm * float(np.linalg.norm(candidate))
            similarity = float(np.dot(query, candidate) / denominator) if denominator else 0.0
            if similarity <= threshold:
                continue
            match = DuplicateMatch(self._record(row), similarity)
            if best is None or (-match.similarity, match.memory.id) < (
                -best.similarity,
                best.memory.id,
            ):
                best = match
        return best

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
            )
            VALUES (?, 'memory', ?, ?, ?)
            """,
            (event_type, entity_id, json.dumps(detail, sort_keys=True), timestamp),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        embedding = row["embedding"]
        return MemoryRecord(
            id=int(row["id"]),
            kind=MemoryKind(row["kind"]),
            content=row["content"],
            normalized_content=row["normalized_content"],
            importance=float(row["importance"]),
            source_message_id=row["source_message_id"],
            embedding=bytes(embedding) if embedding is not None else None,
            embedding_dim=row["embedding_dim"],
            created_at_utc=_parse_utc(row["created_at_utc"]),
            updated_at_utc=_parse_utc(row["updated_at_utc"]),
        )
