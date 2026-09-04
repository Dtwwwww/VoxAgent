from __future__ import annotations

import sqlite3
from pathlib import Path

from voxagent.config import AppPaths, resolve_data_root


def _resolved_database_path(path: Path) -> Path:
    data_directory = AppPaths.from_root(resolve_data_root(None)).data.resolve(strict=False)
    data_directory.mkdir(parents=True, exist_ok=True)
    candidate = path.expanduser().resolve(strict=False)
    if not candidate.is_relative_to(data_directory):
        raise ValueError("database path must remain inside the configured data directory")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def open_database(path: Path) -> sqlite3.Connection:
    """Open one local database with the project's required safety settings."""
    database_path = _resolved_database_path(path)
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        connection.close()
        raise
    return connection
