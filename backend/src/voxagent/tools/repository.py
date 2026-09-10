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
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FINISH_DETAIL_KEYS = frozenset({"duration_ms", "error_code", "recovered_from"})
_MAX_DURATION_MS = 300_000
_RECOVERED_FROM_VALUES = frozenset({"awaiting_confirmation", "running"})
_SENSITIVE_DETAIL_TERMS = frozenset(
    {
        "api_key",
        "password",
        "path",
        "prompt",
        "secret",
        "stack",
        "token",
        "traceback",
    }
)


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


def _required_utc(value: str) -> datetime:
    parsed = _parse_utc(value)
    if parsed is None:
        raise ValueError("stored UTC datetime is missing")
    return parsed


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


def _validate_finish_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if detail is None:
        return {}
    unknown_keys = set(detail) - _FINISH_DETAIL_KEYS
    if unknown_keys:
        raise ValueError("detail contains unsupported audit fields")

    safe_detail: dict[str, Any] = {}
    if "duration_ms" in detail:
        duration_ms = detail["duration_ms"]
        if type(duration_ms) is not int or not 0 <= duration_ms <= _MAX_DURATION_MS:
            raise ValueError("detail.duration_ms must be a bounded non-negative integer")
        safe_detail["duration_ms"] = duration_ms
    if "error_code" in detail:
        error_code = detail["error_code"]
        if not isinstance(error_code, str) or not _ERROR_CODE_RE.fullmatch(error_code):
            raise ValueError("detail.error_code must be a stable machine code")
        if any(term in error_code for term in _SENSITIVE_DETAIL_TERMS):
            raise ValueError("detail.error_code must not contain sensitive terms")
        safe_detail["error_code"] = error_code
    if "recovered_from" in detail:
        recovered_from = detail["recovered_from"]
        if recovered_from not in _RECOVERED_FROM_VALUES:
            raise ValueError("detail.recovered_from must be a known runtime state")
        safe_detail["recovered_from"] = recovered_from
    return safe_detail


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
                        AND tool_requests.arguments_sha256 = ?
                  )
                """,
                (timestamp, confirmation_id, digest, timestamp, session_id, turn_id, digest),
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

    def get_confirmation_request(
        self,
        confirmation_id: str,
    ) -> tuple[ConfirmationTicket, ToolRequestRecord]:
        row = self._connection.execute(
            """
            SELECT
                tool_confirmations.confirmation_id,
                tool_confirmations.tool_request_id,
                tool_confirmations.arguments_sha256 AS confirmation_arguments_sha256,
                tool_confirmations.expires_at_utc,
                tool_confirmations.consumed_at_utc,
                tool_confirmations.decision,
                tool_requests.*
            FROM tool_confirmations
            JOIN tool_requests ON tool_requests.id = tool_confirmations.tool_request_id
            WHERE tool_confirmations.confirmation_id = ?
            """,
            (confirmation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(confirmation_id)

        created_at = self._confirmation_created_at(int(row["tool_request_id"]))
        ticket = ConfirmationTicket(
            confirmation_id=row["confirmation_id"],
            tool_request_id=int(row["tool_request_id"]),
            arguments_sha256=row["confirmation_arguments_sha256"],
            created_at_utc=created_at,
            expires_at_utc=_required_utc(row["expires_at_utc"]),
            consumed_at_utc=_parse_utc(row["consumed_at_utc"]),
            decision=row["decision"],
        )
        return ticket, self._request_record(row)

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
        safe_detail = _validate_finish_detail(detail)
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
            self._audit(tool_request_id, f"request.{status}", safe_detail, timestamp)
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
                recovered_from = row["status"]
                status = "expired" if recovered_from == "awaiting_confirmation" else "failed"
                self._connection.execute(
                    """
                    UPDATE tool_requests
                    SET status = ?, finished_at_utc = ?
                    WHERE id = ?
                    """,
                    (status, timestamp, request_id),
                )
                self._audit(
                    request_id,
                    f"request.{status}",
                    {"recovered_from": recovered_from},
                    timestamp,
                )
        return len(rows)

    def _get_request(self, tool_request_id: int) -> ToolRequestRecord:
        row = self._connection.execute(
            "SELECT * FROM tool_requests WHERE id = ?", (tool_request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(tool_request_id)
        return self._request_record(row)

    def _confirmation_created_at(self, tool_request_id: int) -> datetime:
        row = self._connection.execute(
            """
            SELECT created_at_utc
            FROM tool_audit
            WHERE tool_request_id = ?
              AND event_type = 'confirmation.requested'
            ORDER BY id DESC
            LIMIT 1
            """,
            (tool_request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(tool_request_id)
        return _required_utc(row["created_at_utc"])

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
