from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from voxagent.memory.embedder import l2_normalize_rows

MAX_MEMORY_ROWS = 2_000
MAX_DOCUMENT_CHUNKS = 20_000


@dataclass(frozen=True, slots=True)
class RankedHit:
    id: int
    score: float


class SqliteVectorRetriever:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def search_memories(
        self,
        query: np.ndarray,
        *,
        top_k: int = 5,
        minimum_score: float = 0.55,
    ) -> tuple[RankedHit, ...]:
        return self._search(
            query,
            """
            SELECT id, embedding FROM memories
            WHERE embedding IS NOT NULL AND embedding_dim = ?
            ORDER BY id DESC LIMIT ?
            """,
            MAX_MEMORY_ROWS,
            top_k,
            minimum_score,
        )

    def search_document_chunks(
        self,
        query: np.ndarray,
        *,
        top_k: int = 5,
        minimum_score: float = 0.55,
    ) -> tuple[RankedHit, ...]:
        return self._search(
            query,
            """
            SELECT id, embedding FROM document_chunks
            WHERE embedding IS NOT NULL AND embedding_dim = ?
            ORDER BY id LIMIT ?
            """,
            MAX_DOCUMENT_CHUNKS,
            top_k,
            minimum_score,
        )

    def _search(
        self,
        query: np.ndarray,
        statement: str,
        maximum_rows: int,
        top_k: int,
        minimum_score: float,
    ) -> tuple[RankedHit, ...]:
        query_vector = np.asarray(query, dtype=np.float32)
        if query_vector.ndim != 1:
            raise ValueError("query embedding must be one-dimensional")
        rows = self._connection.execute(
            statement, (query_vector.shape[0], maximum_rows)
        ).fetchall()
        if not rows:
            return ()
        ids = tuple(int(row["id"]) for row in rows)
        vectors = tuple(bytes(row["embedding"]) for row in rows)
        expected_bytes = query_vector.shape[0] * np.dtype(np.float32).itemsize
        if any(len(vector) != expected_bytes for vector in vectors):
            raise ValueError("stored embedding byte length does not match its dimension")
        matrix = np.frombuffer(b"".join(vectors), dtype=np.float32).reshape(
            len(vectors), query_vector.shape[0]
        )
        return cosine_top_k(query_vector, matrix, ids, top_k, minimum_score)


def cosine_top_k(
    query: np.ndarray,
    matrix: np.ndarray,
    ids: Sequence[int],
    k: int = 5,
    minimum_score: float = 0.55,
) -> tuple[RankedHit, ...]:
    query_vector = np.asarray(query, dtype=np.float32)
    candidate_matrix = np.asarray(matrix, dtype=np.float32)
    if query_vector.ndim != 1:
        raise ValueError("query embedding must be one-dimensional")
    if candidate_matrix.ndim != 2:
        raise ValueError("candidate embedding matrix must be two-dimensional")
    if candidate_matrix.shape[1] != query_vector.shape[0]:
        raise ValueError("query and candidate embedding dimensions must match")
    if candidate_matrix.shape[0] != len(ids):
        raise ValueError("candidate rows and IDs must have equal lengths")
    if candidate_matrix.shape[0] == 0 or k <= 0:
        return ()

    normalized_query = l2_normalize_rows(query_vector.reshape(1, -1))[0]
    normalized_matrix = l2_normalize_rows(candidate_matrix)
    scores = normalized_matrix @ normalized_query
    hits = (
        RankedHit(int(ids[index]), float(score))
        for index, score in enumerate(scores)
        if float(score) >= minimum_score
    )
    ordered = sorted(hits, key=lambda hit: (-hit.score, hit.id))
    return tuple(ordered[: min(k, len(ordered))])
