from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from voxagent.config import AppPaths
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.knowledge.ingest import ImportCancelled, KnowledgeIngestor


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def encode(self, texts: tuple[str, ...]) -> np.ndarray:
        self.calls.append(texts)
        output = np.zeros((len(texts), 512), dtype=np.float32)
        output[:, 0] = 1
        return output


@pytest.fixture
def database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> sqlite3.Connection:
    paths = AppPaths.from_root(tmp_path / "runtime")
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(paths.root))
    connection = open_database(paths.data / "voxagent.db")
    migrate(connection)
    yield connection
    connection.close()


def test_import_is_atomic_and_stores_traceable_source(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "knowledge.txt"
    path.write_text("本地知识内容", encoding="utf-8")
    result = KnowledgeIngestor(database, FakeEmbedder()).import_file(
        path, now_utc=datetime(2026, 9, 4, tzinfo=UTC)
    )

    assert result.created is True
    assert result.chunk_count == 1
    row = database.execute("SELECT * FROM documents WHERE id = ?", (result.document_id,)).fetchone()
    assert row["source_path"] == str(path.resolve())
    assert database.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 1


def test_identical_hash_returns_existing_document_without_reembedding(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "same.txt"
    path.write_text("相同内容", encoding="utf-8")
    embedder = FakeEmbedder()
    ingestor = KnowledgeIngestor(database, embedder)
    now = datetime(2026, 9, 4, tzinfo=UTC)

    first = ingestor.import_file(path, now_utc=now)
    second = ingestor.import_file(path, now_utc=now)

    assert second.document_id == first.document_id
    assert second.created is False
    assert len(embedder.calls) == 1
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_changed_content_creates_a_new_document(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "changing.txt"
    path.write_text("版本一", encoding="utf-8")
    ingestor = KnowledgeIngestor(database, FakeEmbedder())
    now = datetime(2026, 9, 4, tzinfo=UTC)
    first = ingestor.import_file(path, now_utc=now)
    path.write_text("版本二", encoding="utf-8")

    second = ingestor.import_file(path, now_utc=now)

    assert second.document_id != first.document_id
    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_cancellation_between_embedding_batches_leaves_no_rows(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    path = tmp_path / "long.txt"
    path.write_text("知识段落。" * 500, encoding="utf-8")
    checks = iter((False, True))

    with pytest.raises(ImportCancelled):
        KnowledgeIngestor(database, FakeEmbedder(), batch_size=1).import_file(
            path,
            now_utc=datetime(2026, 9, 4, tzinfo=UTC),
            is_cancelled=lambda: next(checks, True),
        )

    assert database.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 0


def test_deleting_one_document_removes_only_its_chunks(
    database: sqlite3.Connection, tmp_path: Path
) -> None:
    first_path = tmp_path / "first.txt"
    second_path = tmp_path / "second.txt"
    first_path.write_text("第一份资料", encoding="utf-8")
    second_path.write_text("第二份资料", encoding="utf-8")
    ingestor = KnowledgeIngestor(database, FakeEmbedder())
    now = datetime(2026, 9, 4, tzinfo=UTC)
    first = ingestor.import_file(first_path, now_utc=now)
    second = ingestor.import_file(second_path, now_utc=now)

    assert ingestor.delete_document(first.document_id, now_utc=now) is True

    remaining = database.execute("SELECT document_id FROM document_chunks").fetchall()
    assert [row["document_id"] for row in remaining] == [second.document_id]
