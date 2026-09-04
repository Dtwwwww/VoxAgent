from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from voxagent.config import AppPaths
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate
from voxagent.memory.models import MemoryCandidate, MemoryKind
from voxagent.memory.repository import MemoryRepository


def _vector(*values: float) -> bytes:
    return np.asarray(values, dtype=np.float32).tobytes()


@pytest.fixture
def repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[MemoryRepository, sqlite3.Connection]:
    paths = AppPaths.from_root(tmp_path / "runtime")
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(paths.root))
    connection = open_database(paths.data / "voxagent.db")
    migrate(connection)
    yield MemoryRepository(connection), connection
    connection.close()


def test_create_and_list_return_immutable_domain_models(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, _ = repository
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    created = repo.create(
        MemoryCandidate(MemoryKind.PREFERENCE, "喜欢无糖咖啡", 0.8, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=now,
    )

    assert created.id > 0
    assert created.content == "喜欢无糖咖啡"
    assert created.created_at_utc == now
    assert repo.list(limit=100, offset=0) == (created,)
    with pytest.raises((AttributeError, TypeError)):
        created.content = "被修改"  # type: ignore[misc]


def test_create_preserves_the_source_turn_for_user_visible_attribution(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, connection = repository
    created = repo.create(
        MemoryCandidate(
            MemoryKind.PREFERENCE,
            "喜欢无糖咖啡",
            0.8,
            None,
            source_turn_id=12,
        ),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )

    assert created.source_turn_id == 12
    assert connection.execute(
        "SELECT source_turn_id FROM memories WHERE id = ?", (created.id,)
    ).fetchone()[0] == 12


def test_exact_normalized_duplicate_refreshes_instead_of_inserting(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, connection = repository
    first_time = datetime(2026, 9, 4, 12, tzinfo=UTC)
    first = repo.create(
        MemoryCandidate(MemoryKind.PREFERENCE, "喜欢 AI 助手", 0.7, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=first_time,
    )
    refreshed = repo.create(
        MemoryCandidate(MemoryKind.PREFERENCE, " 喜欢  ＡＩ\n助手 ", 0.9, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=first_time + timedelta(hours=1),
    )

    assert refreshed.id == first.id
    assert refreshed.updated_at_utc == first_time + timedelta(hours=1)
    assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_semantic_duplicate_requires_same_kind_and_score_above_threshold(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, _ = repository
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    saved = repo.create(
        MemoryCandidate(MemoryKind.HABIT, "每周三跑步", 0.8, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=now,
    )
    repo.create(
        MemoryCandidate(MemoryKind.PROFILE, "住在杭州", 0.8, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=now,
    )

    match = repo.find_duplicate(
        MemoryKind.HABIT,
        embedding=_vector(0.99, 0.01),
        embedding_dim=2,
    )

    assert match is not None
    assert match.memory.id == saved.id
    assert match.similarity > 0.94
    assert (
        repo.find_duplicate(
            MemoryKind.RELATIONSHIP,
            embedding=_vector(1, 0),
            embedding_dim=2,
        )
        is None
    )


def test_update_and_delete_write_audit_rows(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, connection = repository
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    created = repo.create(
        MemoryCandidate(MemoryKind.PREFERENCE, "喜欢茶", 0.6, None),
        embedding=_vector(1, 0),
        embedding_dim=2,
        now_utc=now,
    )
    updated = repo.update(
        created.id,
        content="喜欢乌龙茶",
        importance=0.9,
        embedding=_vector(0, 1),
        embedding_dim=2,
        now_utc=now + timedelta(minutes=1),
    )
    assert updated.content == "喜欢乌龙茶"
    assert repo.delete(created.id, now_utc=now + timedelta(minutes=2)) is True
    assert repo.list(limit=100, offset=0) == ()

    events = connection.execute(
        "SELECT event_type, detail_json FROM audit_events ORDER BY id"
    ).fetchall()
    assert [row["event_type"] for row in events] == [
        "memory.created",
        "memory.updated",
        "memory.deleted",
    ]
    assert all("乌龙茶" not in row["detail_json"] for row in events)
    assert all(isinstance(json.loads(row["detail_json"]), dict) for row in events)


def test_transaction_rolls_back_when_audit_insert_fails(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, connection = repository
    connection.execute(
        """
        CREATE TRIGGER fail_memory_audit
        BEFORE INSERT ON audit_events
        WHEN NEW.event_type = 'memory.created'
        BEGIN
            SELECT RAISE(ABORT, 'audit failed');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="audit failed"):
        repo.create(
            MemoryCandidate(MemoryKind.PREFERENCE, "喜欢茶", 0.6, None),
            embedding=_vector(1, 0),
            embedding_dim=2,
            now_utc=datetime(2026, 9, 4, 12, tzinfo=UTC),
        )

    assert connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_list_rejects_pages_larger_than_one_hundred(
    repository: tuple[MemoryRepository, sqlite3.Connection],
) -> None:
    repo, _ = repository

    with pytest.raises(ValueError, match="between 1 and 100"):
        repo.list(limit=101, offset=0)
