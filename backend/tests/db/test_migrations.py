from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from voxagent.config import AppPaths
from voxagent.db.connection import open_database
from voxagent.db.migrations import migrate


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sqlite3.Connection:
    paths = AppPaths.from_root(tmp_path / "runtime")
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(paths.root))
    connection = open_database(paths.data / "voxagent.db")
    yield connection
    connection.close()


def test_new_database_migrates_to_latest_version_idempotently(
    database: sqlite3.Connection,
) -> None:
    assert migrate(database) == 4
    assert migrate(database) == 4
    assert database.execute("SELECT version FROM schema_version").fetchone()[0] == 4

    tables = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "schema_version",
        "conversations",
        "messages",
        "memories",
        "documents",
        "document_chunks",
        "audit_events",
        "persona_config",
        "authorized_roots",
        "reminders",
        "tool_requests",
        "tool_confirmations",
        "tool_audit",
    } <= tables

    memory_columns = {
        row[1] for row in database.execute("PRAGMA table_info(memories)").fetchall()
    }
    assert "source_turn_id" in memory_columns

    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO schema_version(version) VALUES (4)")


def test_persona_table_enforces_exactly_one_versioned_row(
    database: sqlite3.Connection,
) -> None:
    migrate(database)

    database.execute(
        "INSERT INTO persona_config(id, config_json, revision) VALUES (1, '{}', 1)"
    )

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO persona_config(id, config_json, revision) VALUES (2, '{}', 1)"
        )


def test_connection_uses_safe_sqlite_settings(database: sqlite3.Connection) -> None:
    assert database.row_factory is sqlite3.Row
    assert database.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert database.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert database.execute("PRAGMA synchronous").fetchone()[0] == 1
    assert database.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000


def test_database_path_must_remain_inside_configured_data_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = AppPaths.from_root(tmp_path / "runtime")
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(paths.root))

    with pytest.raises(ValueError, match="configured data directory"):
        open_database(tmp_path / "outside.db")


def test_schema_uses_text_and_explicit_vector_dimensions(
    database: sqlite3.Connection,
) -> None:
    migrate(database)

    tables = (
        "conversations",
        "messages",
        "memories",
        "documents",
        "document_chunks",
        "audit_events",
    )
    for table in tables:
        columns = {
            row[1]: row for row in database.execute(f"PRAGMA table_info({table})")
        }
        for name, column in columns.items():
            if name.endswith("_at_utc") or name in {
                "title",
                "summary",
                "role",
                "content",
                "kind",
                "normalized_content",
                "display_name",
                "source_path",
                "sha256",
                "mime_type",
                "event_type",
                "entity_type",
                "detail_json",
            }:
                assert column[2] == "TEXT", f"{table}.{name} must be TEXT"

    memories = {row[1]: row[2] for row in database.execute("PRAGMA table_info(memories)")}
    chunks = {row[1]: row[2] for row in database.execute("PRAGMA table_info(document_chunks)")}
    assert memories["embedding"] == "BLOB"
    assert memories["embedding_dim"] == "INTEGER"
    assert chunks["embedding"] == "BLOB"
    assert chunks["embedding_dim"] == "INTEGER"


def test_default_timestamps_are_utc_iso_8601(database: sqlite3.Connection) -> None:
    migrate(database)
    row = database.execute(
        """
        INSERT INTO conversations(title, summary) VALUES (?, ?)
        RETURNING created_at_utc, updated_at_utc
        """,
        ("测试", ""),
    ).fetchone()

    assert row["created_at_utc"].endswith("Z")
    assert "T" in row["created_at_utc"]
    assert row["updated_at_utc"].endswith("Z")


def test_deleting_document_cascades_to_chunks(database: sqlite3.Connection) -> None:
    migrate(database)
    document_id = database.execute(
        """
        INSERT INTO documents(display_name, source_path, sha256, mime_type)
        VALUES (?, ?, ?, ?)
        RETURNING id
        """,
        ("资料.txt", "D:/资料.txt", "a" * 64, "text/plain"),
    ).fetchone()[0]
    database.execute(
        """
        INSERT INTO document_chunks(
            document_id, ordinal, content, page_number, embedding, embedding_dim
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (document_id, 0, "内容", None, b"\x00" * 8, 2),
    )

    database.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    assert database.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 0


def test_required_indexes_exist(database: sqlite3.Connection) -> None:
    migrate(database)
    indexes = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert {
        "idx_messages_turn_id",
        "idx_memories_normalized_content",
        "idx_documents_sha256",
        "idx_document_chunks_parent_ordinal",
        "idx_messages_conversation_turn_role",
        "idx_schema_version_singleton",
    } <= indexes


def test_tool_schema_constraints_are_enforced(database: sqlite3.Connection) -> None:
    migrate(database)
    now = "2026-09-10T12:00:00.000Z"

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO tool_requests(
                session_id, turn_id, call_id, tool_name, arguments_json,
                arguments_sha256, permission, status, created_at_utc
            )
            VALUES ('session-1', 1, 'call-1', 'knowledge.search', '{}',
                    ?, 'L1', 'pending', ?)
            """,
            ("a" * 63, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO tool_requests(
                session_id, turn_id, call_id, tool_name, arguments_json,
                arguments_sha256, permission, status, created_at_utc
            )
            VALUES ('session-1', 1, 'call-1', 'knowledge.search', '{}',
                    ?, 'L3', 'pending', ?)
            """,
            ("a" * 64, now),
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO tool_requests(
                session_id, turn_id, call_id, tool_name, arguments_json,
                arguments_sha256, permission, status, created_at_utc
            )
            VALUES ('session-1', 1, 'call-1', 'knowledge.search', '{}',
                    ?, 'L1', 'paused', ?)
            """,
            ("a" * 64, now),
        )


def test_authorized_roots_are_unique(database: sqlite3.Connection) -> None:
    migrate(database)
    database.execute(
        """
        INSERT INTO authorized_roots(display_name, canonical_path, created_at_utc)
        VALUES ('Workspace', 'D:/Agent_protect/VoxAgent', '2026-09-10T12:00:00.000Z')
        """
    )

    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            """
            INSERT INTO authorized_roots(display_name, canonical_path, created_at_utc)
            VALUES (
                'Duplicate',
                'D:/Agent_protect/VoxAgent',
                '2026-09-10T12:00:01.000Z'
            )
            """
        )


def test_tool_confirmations_cascade_with_request(database: sqlite3.Connection) -> None:
    migrate(database)
    request_id = database.execute(
        """
        INSERT INTO tool_requests(
            session_id, turn_id, call_id, tool_name, arguments_json,
            arguments_sha256, permission, status, created_at_utc
        )
        VALUES ('session-1', 1, 'call-1', 'knowledge.search', '{}',
                ?, 'L1', 'awaiting_confirmation', '2026-09-10T12:00:00.000Z')
        RETURNING id
        """,
        ("a" * 64,),
    ).fetchone()[0]
    database.execute(
        """
        INSERT INTO tool_confirmations(
            confirmation_id, tool_request_id, arguments_sha256, expires_at_utc
        )
        VALUES ('confirmation-1', ?, ?, '2026-09-10T12:02:00.000Z')
        """,
        (request_id, "a" * 64),
    )

    database.execute("DELETE FROM tool_requests WHERE id = ?", (request_id,))

    assert database.execute("SELECT COUNT(*) FROM tool_confirmations").fetchone()[0] == 0
