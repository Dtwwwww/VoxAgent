from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from voxagent.db.connection import open_database

InputSource = Literal["text", "voice"]


class ConversationStore(Protocol):
    async def add_user(self, turn_id: int, text: str, source: InputSource) -> int: ...

    async def complete_assistant(self, turn_id: int, text: str) -> None: ...

    async def cancel_turn(self, turn_id: int) -> None: ...

    async def reset(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SqliteConversationStore:
    """Persist one WebSocket session while sharing the application mutation gate."""

    def __init__(self, database_path: Path, mutation_lock: asyncio.Lock) -> None:
        self._database_path = database_path
        self._mutation_lock = mutation_lock
        self._conversation_id: int | None = None

    async def add_user(self, turn_id: int, text: str, source: InputSource) -> int:
        async with self._mutation_lock:
            return await asyncio.to_thread(self._add_user, turn_id, text, source)

    def _add_user(self, turn_id: int, text: str, source: InputSource) -> int:
        normalized = text.strip()
        if turn_id < 1 or not normalized or source not in {"text", "voice"}:
            raise ValueError("invalid persisted user message")
        timestamp = _utc_now()
        connection = open_database(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            conversation_id = self._conversation_id
            if conversation_id is None:
                result = connection.execute(
                    """
                    INSERT INTO conversations(title, summary, created_at_utc, updated_at_utc)
                    VALUES (?, '', ?, ?)
                    """,
                    (normalized[:80], timestamp, timestamp),
                )
                conversation_id = int(result.lastrowid)
            result = connection.execute(
                """
                INSERT INTO messages(conversation_id, turn_id, role, content, created_at_utc)
                VALUES (?, ?, 'user', ?, ?)
                """,
                (conversation_id, turn_id, normalized, timestamp),
            )
            connection.execute(
                "UPDATE conversations SET updated_at_utc = ? WHERE id = ?",
                (timestamp, conversation_id),
            )
            connection.commit()
            self._conversation_id = conversation_id
            return int(result.lastrowid)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def complete_assistant(self, turn_id: int, text: str) -> None:
        async with self._mutation_lock:
            await asyncio.to_thread(self._complete_assistant, turn_id, text)

    def _complete_assistant(self, turn_id: int, text: str) -> None:
        normalized = text.strip()
        conversation_id = self._conversation_id
        if conversation_id is None or not normalized:
            raise ValueError("assistant completion has no persisted user turn")
        timestamp = _utc_now()
        connection = open_database(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute(
                """
                SELECT id FROM messages
                WHERE conversation_id = ? AND turn_id = ? AND role = 'user'
                """,
                (conversation_id, turn_id),
            ).fetchone()
            if user is None:
                raise ValueError("assistant completion has no persisted user turn")
            connection.execute(
                """
                INSERT INTO messages(conversation_id, turn_id, role, content, created_at_utc)
                VALUES (?, ?, 'assistant', ?, ?)
                """,
                (conversation_id, turn_id, normalized, timestamp),
            )
            connection.execute(
                "UPDATE conversations SET updated_at_utc = ? WHERE id = ?",
                (timestamp, conversation_id),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def cancel_turn(self, turn_id: int) -> None:
        async with self._mutation_lock:
            await asyncio.to_thread(self._cancel_turn, turn_id)

    async def reset(self) -> None:
        async with self._mutation_lock:
            self._conversation_id = None

    def _cancel_turn(self, turn_id: int) -> None:
        conversation_id = self._conversation_id
        if conversation_id is None:
            return
        connection = open_database(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND turn_id = ?",
                (conversation_id, turn_id),
            )
            remaining = connection.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ? LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "DELETE FROM conversations WHERE id = ?", (conversation_id,)
                )
            connection.commit()
            if remaining is None:
                self._conversation_id = None
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
