from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from voxagent.tools.schema import PermissionLevel, ToolCall

ToolRequestStatus = Literal[
    "pending",
    "awaiting_confirmation",
    "running",
    "succeeded",
    "failed",
    "denied",
    "expired",
]
TerminalToolRequestStatus = Literal["succeeded", "failed", "denied", "expired"]

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "denied", "expired"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ToolRequestRecord:
    id: int
    session_id: str
    turn_id: int
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_sha256: str
    permission: PermissionLevel
    status: ToolRequestStatus
    created_at_utc: datetime
    finished_at_utc: datetime | None


@dataclass(frozen=True)
class ConfirmationTicket:
    confirmation_id: str
    tool_request_id: int
    arguments_sha256: str
    created_at_utc: datetime
    expires_at_utc: datetime
    consumed_at_utc: datetime | None
    decision: str | None


@dataclass(frozen=True)
class ToolAuditRecord:
    id: int
    tool_request_id: int
    session_id: str
    turn_id: int
    call_id: str
    tool_name: str
    event_type: str
    detail: dict[str, Any]
    created_at_utc: datetime


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_dict(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("stored JSON value must be an object")
    return decoded


def _validate_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("arguments_sha256 must be a 64-character lowercase hex sha256")
    return value


class ToolRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def _write(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def create_request(
        self,
        session_id: str,
        turn_id: int,
        call: ToolCall,
        permission: PermissionLevel,
        arguments_sha256: str,
        now_utc: datetime,
    ) -> ToolRequestRecord:
        digest = _validate_sha256(arguments_sha256)
        timestamp = _utc_text(now_utc)
        arguments_json = _json_text(call.arguments)
        with self._write():
            result = self._connection.execute(
                """
                INSERT INTO tool_requests(
                    session_id, turn_id, call_id, tool_name, arguments_json,
                    arguments_sha256, permission, status, created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    session_id,
                    turn_id,
                    call.call_id,
                    call.name,
                    arguments_json,
                    digest,
                    permission.value,
                    timestamp,
                ),
            )
            request_id = int(result.lastrowid)
            self._audit(request_id, "request.created", {}, timestamp)
        return self._get_request(request_id)

    def create_confirmation(
        self,
        tool_request_id: int,
        arguments_sha256: str,
        now_utc: datetime,
    ) -> ConfirmationTicket:
        digest = _validate_sha256(arguments_sha256)
        timestamp = _utc_text(now_utc)
        expires_at = now_utc + timedelta(seconds=120)
        expires_at_text = _utc_text(expires_at)
        confirmation_id = str(uuid.uuid4())
        with self._write():
            result = self._connection.execute(
                """
                UPDATE tool_requests
                SET status = 'awaiting_confirmation'
                WHERE id = ?
                  AND arguments_sha256 = ?
                  AND status = 'pending'
                """,
                (tool_request_id, digest),
            )
            if result.rowcount != 1:
                raise ValueError("confirmation requires a matching pending request and hash")
            self._connection.execute(
                """
                INSERT INTO tool_confirmations(
                    confirmation_id, tool_request_id, arguments_sha256, expires_at_utc
                )
                VALUES (?, ?, ?, ?)
                """,
                (confirmation_id, tool_request_id, digest, expires_at_text),
            )
            self._audit(tool_request_id, "confirmation.requested", {}, timestamp)
        return ConfirmationTicket(
            confirmation_id=confirmation_id,
            tool_request_id=tool_request_id,
            arguments_sha256=digest,
            created_at_utc=now_utc.astimezone(UTC),
            expires_at_utc=expires_at.astimezone(UTC),
            consumed_at_utc=None,
            decision=None,
        )

    def consume_confirmation(
        self,
        confirmation_id: str,
        arguments_sha256: str,
        session_id: str,
        turn_id: int,
        now_utc: datetime,
    ) -> bool:
        digest = _validate_sha256(arguments_sha256)
        timestamp = _utc_text(now_utc)
        with self._write():
            result = self._connection.execute(
                """
                UPDATE tool_confirmations
                SET consumed_at_utc = ?, decision = 'approved'
                WHERE confirmation_id = ?
                  AND arguments_sha256 = ?
                  AND consumed_at_utc IS NULL
                  AND decision IS NULL
                  AND expires_at_utc > ?
                  AND EXISTS (
                      SELECT 1 FROM tool_requests
                      WHERE tool_requests.id = tool_confirmations.tool_request_id
                        AND tool_requests.session_id = ?
                        AND tool_requests.turn_id = ?
                        AND tool_requests.status = 'awaiting_confirmation'
                  )
                """,
                (timestamp, confirmation_id, digest, timestamp, session_id, turn_id),
            )
            if result.rowcount != 1:
                return False
            row = self._connection.execute(
                """
                SELECT tool_request_id
                FROM tool_confirmations
                WHERE confirmation_id = ?
                """,
                (confirmation_id,),
            ).fetchone()
            tool_request_id = int(row["tool_request_id"])
            self._connection.execute(
                "UPDATE tool_requests SET status = 'running' WHERE id = ?",
                (tool_request_id,),
            )
            self._audit(tool_request_id, "confirmation.approved", {}, timestamp)
        return True

    def finish_request(
        self,
        tool_request_id: int,
        status: TerminalToolRequestStatus,
        now_utc: datetime,
        *,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("status must be a terminal request status")
        timestamp = _utc_text(now_utc)
        with self._write():
            result = self._connection.execute(
                """
                UPDATE tool_requests
                SET status = ?, finished_at_utc = ?
                WHERE id = ?
                  AND status NOT IN ('succeeded','failed','denied','expired')
                """,
                (status, timestamp, tool_request_id),
            )
            if result.rowcount != 1:
                return False
            self._audit(tool_request_id, f"request.{status}", detail or {}, timestamp)
        return True

    def list_audit(self, limit: int = 50, offset: int = 0) -> tuple[ToolAuditRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        rows = self._connection.execute(
            """
            SELECT
                tool_audit.id,
                tool_audit.tool_request_id,
                tool_audit.event_type,
                tool_audit.detail_json,
                tool_audit.created_at_utc,
                tool_requests.session_id,
                tool_requests.turn_id,
                tool_requests.call_id,
                tool_requests.tool_name
            FROM tool_audit
            JOIN tool_requests ON tool_requests.id = tool_audit.tool_request_id
            ORDER BY tool_audit.created_at_utc DESC, tool_audit.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return tuple(self._audit_record(row) for row in rows)

    def recover_incomplete(self, now_utc: datetime) -> int:
        timestamp = _utc_text(now_utc)
        with self._write():
            rows = self._connection.execute(
                """
                SELECT id, status
                FROM tool_requests
                WHERE status IN ('awaiting_confirmation', 'running')
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                request_id = int(row["id"])
                status = "expired" if row["status"] == "awaiting_confirmation" else "failed"
                self._connection.execute(
                    """
                    UPDATE tool_requests
                    SET status = ?, finished_at_utc = ?
                    WHERE id = ?
                    """,
                    (status, timestamp, request_id),
                )
                self._audit(request_id, f"request.{status}", {}, timestamp)
        return len(rows)

    def _get_request(self, tool_request_id: int) -> ToolRequestRecord:
        row = self._connection.execute(
            "SELECT * FROM tool_requests WHERE id = ?", (tool_request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(tool_request_id)
        return self._request_record(row)

    def _audit(
        self,
        tool_request_id: int,
        event_type: str,
        detail: dict[str, Any],
        timestamp: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tool_audit(tool_request_id, event_type, detail_json, created_at_utc)
            VALUES (?, ?, ?, ?)
            """,
            (tool_request_id, event_type, _json_text(detail), timestamp),
        )

    @staticmethod
    def _request_record(row: sqlite3.Row) -> ToolRequestRecord:
        return ToolRequestRecord(
            id=int(row["id"]),
            session_id=row["session_id"],
            turn_id=int(row["turn_id"]),
            call_id=row["call_id"],
            tool_name=row["tool_name"],
            arguments=_json_dict(row["arguments_json"]),
            arguments_sha256=row["arguments_sha256"],
            permission=PermissionLevel(row["permission"]),
            status=row["status"],
            created_at_utc=_parse_utc(row["created_at_utc"]),
            finished_at_utc=_parse_utc(row["finished_at_utc"]),
        )

    @staticmethod
    def _audit_record(row: sqlite3.Row) -> ToolAuditRecord:
        created_at = _parse_utc(row["created_at_utc"])
        if created_at is None:
            raise ValueError("audit record is missing created_at_utc")
        return ToolAuditRecord(
            id=int(row["id"]),
            tool_request_id=int(row["tool_request_id"]),
            session_id=row["session_id"],
            turn_id=int(row["turn_id"]),
            call_id=row["call_id"],
            tool_name=row["tool_name"],
            event_type=row["event_type"],
            detail=_json_dict(row["detail_json"]),
            created_at_utc=created_at,
        )
