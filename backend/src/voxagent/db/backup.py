from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path


class DailyBackupManager:
    def __init__(
        self,
        directory: Path,
        *,
        retention: int = 7,
        integrity_check: Callable[[Path], bool] | None = None,
    ) -> None:
        if retention < 1:
            raise ValueError("backup retention must be positive")
        self._directory = directory
        self._retention = retention
        self._integrity_check = integrity_check or self._has_valid_integrity

    def create(self, source: sqlite3.Connection, local_day: date) -> Path | None:
        self._directory.mkdir(parents=True, exist_ok=True)
        target = self._directory / f"voxagent-{local_day.isoformat()}.db"
        if target.exists():
            if self._is_valid(target):
                return None
            target.unlink()
        temporary = self._directory / f".{target.name}.tmp"
        temporary.unlink(missing_ok=True)
        try:
            destination = sqlite3.connect(temporary)
            try:
                source.backup(destination)
            finally:
                destination.close()
            if not self._is_valid(temporary):
                return None
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        self._rotate()
        return target

    def list(self) -> tuple[Path, ...]:
        if not self._directory.exists():
            return ()
        return tuple(
            path
            for path in sorted(
                self._directory.glob("voxagent-????-??-??.db"), reverse=True
            )
            if self._is_valid(path)
        )

    def delete(self, filename: str) -> bool:
        if not filename.startswith("voxagent-") or not filename.endswith(".db"):
            raise ValueError("invalid backup filename")
        candidate = (self._directory / filename).resolve(strict=False)
        directory = self._directory.resolve(strict=False)
        if candidate.parent != directory:
            raise ValueError("backup path must remain inside the backup directory")
        if not candidate.exists():
            return False
        candidate.unlink()
        return True

    def purge_all(self) -> None:
        if not self._directory.exists():
            return
        for candidate in self._directory.glob("voxagent-????-??-??.db"):
            candidate.unlink(missing_ok=True)
        for temporary in self._directory.glob(".voxagent-????-??-??.db.tmp"):
            temporary.unlink(missing_ok=True)

    def _rotate(self) -> None:
        candidates = tuple(
            sorted(self._directory.glob("voxagent-????-??-??.db"), reverse=True)
        )
        valid: list[Path] = []
        for candidate in candidates:
            if self._is_valid(candidate):
                valid.append(candidate)
            else:
                candidate.unlink(missing_ok=True)
        for expired in valid[self._retention :]:
            expired.unlink()

    def _is_valid(self, path: Path) -> bool:
        try:
            return self._integrity_check(path)
        except (OSError, sqlite3.DatabaseError):
            return False

    @staticmethod
    def _has_valid_integrity(path: Path) -> bool:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            connection.close()
