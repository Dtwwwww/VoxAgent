from __future__ import annotations

import sqlite3

LATEST_SCHEMA_VERSION = 2

_UTC_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

_MIGRATION_001 = (
    f"""
    CREATE TABLE conversations (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        created_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW}),
        updated_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW})
    )
    """,
    f"""
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        turn_id INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
        content TEXT NOT NULL,
        created_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW})
    )
    """,
    f"""
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        normalized_content TEXT NOT NULL,
        importance REAL NOT NULL CHECK (importance >= 0 AND importance <= 1),
        source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
        embedding BLOB,
        embedding_dim INTEGER,
        created_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW}),
        updated_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW}),
        CHECK ((embedding IS NULL AND embedding_dim IS NULL) OR
               (embedding IS NOT NULL AND embedding_dim > 0))
    )
    """,
    f"""
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        display_name TEXT NOT NULL,
        source_path TEXT NOT NULL,
        sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
        mime_type TEXT NOT NULL,
        imported_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW})
    )
    """,
    """
    CREATE TABLE document_chunks (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        content TEXT NOT NULL,
        page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
        embedding BLOB NOT NULL,
        embedding_dim INTEGER NOT NULL CHECK (embedding_dim > 0),
        UNIQUE (document_id, ordinal)
    )
    """,
    f"""
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY,
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER,
        detail_json TEXT NOT NULL DEFAULT '{{}}',
        created_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW})
    )
    """,
    "CREATE INDEX idx_messages_turn_id ON messages(turn_id)",
    "CREATE INDEX idx_memories_normalized_content ON memories(normalized_content)",
    "CREATE UNIQUE INDEX idx_documents_sha256 ON documents(sha256)",
    """
    CREATE INDEX idx_document_chunks_parent_ordinal
    ON document_chunks(document_id, ordinal)
    """,
)

_MIGRATION_002 = (
    "ALTER TABLE memories ADD COLUMN source_turn_id INTEGER",
    f"""
    CREATE TABLE persona_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        config_json TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at_utc TEXT NOT NULL DEFAULT ({_UTC_NOW})
    )
    """,
)


def migrate(connection: sqlite3.Connection) -> int:
    """Atomically migrate a database to the latest supported schema."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL CHECK (version >= 0))"
        )
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_version(version) VALUES (0)")
            current_version = 0
        else:
            current_version = int(row[0])
        if current_version > LATEST_SCHEMA_VERSION:
            raise RuntimeError(f"database schema version {current_version} is unsupported")
        if current_version == 0:
            for statement in _MIGRATION_001:
                connection.execute(statement)
            current_version = 1
            connection.execute(
                "UPDATE schema_version SET version = ?", (current_version,)
            )
        if current_version == 1:
            for statement in _MIGRATION_002:
                connection.execute(statement)
            current_version = 2
            connection.execute(
                "UPDATE schema_version SET version = ?", (current_version,)
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return current_version
