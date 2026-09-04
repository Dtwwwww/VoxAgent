from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from voxagent.conversation.persistence import SqliteConversationStore
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate


@pytest.fixture
def database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runtime"
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(root))
    path = root / "data" / "voxagent.db"
    connection = open_database(path)
    migrate(connection)
    connection.close()
    return path


@pytest.mark.asyncio
async def test_persists_complete_conversation_with_stable_source_id(
    database_path: Path,
) -> None:
    store = SqliteConversationStore(database_path, asyncio.Lock())

    source_id = await store.add_user(1, "我喜欢乌龙茶", "text")
    await store.complete_assistant(1, "我记住了")

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    conversation = connection.execute("SELECT * FROM conversations").fetchone()
    messages = connection.execute("SELECT * FROM messages ORDER BY id").fetchall()
    connection.close()
    assert conversation["title"] == "我喜欢乌龙茶"
    assert [(row["turn_id"], row["role"], row["content"]) for row in messages] == [
        (1, "user", "我喜欢乌龙茶"),
        (1, "assistant", "我记住了"),
    ]
    assert source_id == messages[0]["id"]


@pytest.mark.asyncio
async def test_cancel_removes_pending_turn_and_empty_conversation(
    database_path: Path,
) -> None:
    store = SqliteConversationStore(database_path, asyncio.Lock())
    await store.add_user(1, "取消这次", "voice")

    await store.cancel_turn(1)

    connection = sqlite3.connect(database_path)
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    connection.close()


@pytest.mark.asyncio
async def test_turn_ids_are_unambiguous_across_conversations(database_path: Path) -> None:
    lock = asyncio.Lock()
    first = SqliteConversationStore(database_path, lock)
    second = SqliteConversationStore(database_path, lock)

    first_source = await first.add_user(1, "第一段会话", "text")
    second_source = await second.add_user(1, "第二段会话", "text")

    assert first_source != second_source
    connection = sqlite3.connect(database_path)
    assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2
    connection.close()


@pytest.mark.asyncio
async def test_store_starts_a_new_conversation_after_global_reset(database_path: Path) -> None:
    store = SqliteConversationStore(database_path, asyncio.Lock())
    await store.add_user(1, "清空前", "text")
    await store.complete_assistant(1, "好的")

    await store.reset()
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("DELETE FROM conversations")
    connection.commit()
    connection.close()

    await store.add_user(2, "清空后", "text")

    connection = sqlite3.connect(database_path)
    assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
    assert connection.execute("SELECT content FROM messages").fetchone()[0] == "清空后"
    connection.close()
