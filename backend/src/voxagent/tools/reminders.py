from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from voxagent.tools.schema import ToolCall, ToolResult

NowProvider = Callable[[], datetime]


class ReminderListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReminderCreateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    due_at_utc: AwareDatetime | None = None

    @field_validator("due_at_utc", mode="before")
    @classmethod
    def parse_wire_datetime(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("due_at_utc must be an ISO-8601 datetime") from error


class ReminderCompleteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reminder_id: int = Field(gt=0)


def build_reminder_list_executor(
    connection: sqlite3.Connection,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        rows = connection.execute(
            """
            SELECT id, title, due_at_utc, status, created_at_utc, completed_at_utc
            FROM reminders
            WHERE status = 'open'
            ORDER BY id
            """
        ).fetchall()
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"reminders": [_reminder(row) for row in rows]},
            user_summary="提醒列表已读取。",
            duration_ms=_duration_ms(started),
        )

    return execute


def build_reminder_create_executor(
    connection: sqlite3.Connection,
    now_utc: NowProvider,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        now_text = _utc_text(now_utc())
        due_at = call.arguments.get("due_at_utc")
        due_at_text = _utc_text(due_at) if due_at is not None else None
        with _write(connection):
            result = connection.execute(
                """
                INSERT INTO reminders(title, due_at_utc, status, created_at_utc)
                VALUES (?, ?, 'open', ?)
                """,
                (call.arguments["title"], due_at_text, now_text),
            )
            reminder_id = int(result.lastrowid)
        row = connection.execute(
            """
            SELECT id, title, due_at_utc, status, created_at_utc, completed_at_utc
            FROM reminders
            WHERE id = ?
            """,
            (reminder_id,),
        ).fetchone()
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"reminder": _reminder(row)},
            user_summary="提醒已创建。",
            duration_ms=_duration_ms(started),
        )

    return execute


def build_reminder_complete_executor(
    connection: sqlite3.Connection,
    now_utc: NowProvider,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        with _write(connection):
            result = connection.execute(
                """
                UPDATE reminders
                SET status = 'completed', completed_at_utc = ?
                WHERE id = ? AND status = 'open'
                """,
                (_utc_text(now_utc()), call.arguments["reminder_id"]),
            )
            if result.rowcount != 1:
                return ToolResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status="failed",
                    data={},
                    user_summary="提醒不存在或已完成。",
                    error_code="reminder_not_open",
                    duration_ms=_duration_ms(started),
                )
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"reminder_id": call.arguments["reminder_id"]},
            user_summary="提醒已完成。",
            duration_ms=_duration_ms(started),
        )

    return execute


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _reminder(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "due_at_utc": row["due_at_utc"],
        "status": row["status"],
        "created_at_utc": row["created_at_utc"],
        "completed_at_utc": row["completed_at_utc"],
    }


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
