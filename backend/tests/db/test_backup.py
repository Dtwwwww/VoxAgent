from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from voxagent.db.backup import DailyBackupManager


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE IF NOT EXISTS facts(value TEXT NOT NULL)")
    connection.commit()
    return connection


def test_creates_at_most_one_integrity_checked_backup_per_day(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    source.execute("INSERT INTO facts VALUES ('仅保存在本地')")
    source.commit()
    manager = DailyBackupManager(tmp_path / "backups")

    created = manager.create(source, date(2026, 9, 4))
    duplicate = manager.create(source, date(2026, 9, 4))

    assert created == tmp_path / "backups" / "voxagent-2026-09-04.db"
    assert duplicate is None
    copy = sqlite3.connect(created)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT value FROM facts").fetchone()[0] == "仅保存在本地"
    copy.close()
    source.close()


def test_rotates_to_the_newest_seven_daily_backups(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    manager = DailyBackupManager(tmp_path / "backups")

    start = date(2026, 8, 28)
    for offset in range(9):
        manager.create(source, start + timedelta(days=offset))

    assert [item.name for item in manager.list()] == [
        "voxagent-2026-09-05.db",
        "voxagent-2026-09-04.db",
        "voxagent-2026-09-03.db",
        "voxagent-2026-09-02.db",
        "voxagent-2026-09-01.db",
        "voxagent-2026-08-31.db",
        "voxagent-2026-08-30.db",
    ]
    source.close()


def test_integrity_failure_removes_the_incomplete_backup(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    manager = DailyBackupManager(tmp_path / "backups", integrity_check=lambda _: False)

    created = manager.create(source, date(2026, 9, 4))

    assert created is None
    assert manager.list() == ()
    assert not tuple((tmp_path / "backups").glob("*.tmp"))
    source.close()


def test_backup_exception_removes_the_temporary_database(tmp_path: Path) -> None:
    class FailingSource:
        def backup(self, _destination: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("backup failed")

    manager = DailyBackupManager(tmp_path / "backups")

    with pytest.raises(sqlite3.OperationalError, match="backup failed"):
        manager.create(FailingSource(), date(2026, 9, 4))  # type: ignore[arg-type]

    assert not tuple((tmp_path / "backups").glob("*.tmp"))


def test_purge_all_removes_valid_corrupt_and_temporary_backups(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    (directory / "voxagent-2026-09-04.db").write_bytes(b"database")
    (directory / ".voxagent-2026-09-05.db.tmp").write_bytes(b"temporary private data")
    manager = DailyBackupManager(directory)

    manager.purge_all()

    assert not tuple(directory.iterdir())


def test_replaces_a_corrupt_existing_same_day_backup(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    directory = tmp_path / "backups"
    directory.mkdir()
    target = directory / "voxagent-2026-09-04.db"
    target.write_bytes(b"not sqlite")

    created = DailyBackupManager(directory).create(source, date(2026, 9, 4))

    assert created == target
    copy = sqlite3.connect(target)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copy.close()
    source.close()


def test_list_hides_a_backup_that_became_corrupt_after_creation(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    manager = DailyBackupManager(tmp_path / "backups")
    created = manager.create(source, date(2026, 9, 4))
    assert created is not None
    created.write_bytes(b"corrupt after creation")

    assert manager.list() == ()
    source.close()


def test_new_backup_reflects_memory_deletions_in_the_source(tmp_path: Path) -> None:
    source = _database(tmp_path / "voxagent.db")
    source.execute("INSERT INTO facts VALUES ('待删除')")
    source.commit()
    manager = DailyBackupManager(tmp_path / "backups")
    manager.create(source, date(2026, 9, 4))
    source.execute("DELETE FROM facts")
    source.commit()

    latest = manager.create(source, date(2026, 9, 5))

    copy = sqlite3.connect(latest)
    assert copy.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    copy.close()
    source.close()
