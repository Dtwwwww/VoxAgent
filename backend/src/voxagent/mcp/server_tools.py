from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from voxagent.mcp.capability import CapabilityError, CapabilityVerifier
from voxagent.tools.builtin import NowProvider
from voxagent.tools.knowledge_search import (
    KnowledgeSearchArgs,
    KnowledgeSource,
    build_knowledge_search_executor,
)
from voxagent.tools.reminders import (
    ReminderCompleteArgs,
    ReminderCreateArgs,
    ReminderListArgs,
    build_reminder_complete_executor,
    build_reminder_create_executor,
    build_reminder_list_executor,
)
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import ToolCall, ToolResult

ARGUMENT_MODELS = {
    "knowledge.search": KnowledgeSearchArgs,
    "reminders.list": ReminderListArgs,
    "reminders.create": ReminderCreateArgs,
    "reminders.complete": ReminderCompleteArgs,
}
WRITE_TOOLS = frozenset({"reminders.create", "reminders.complete"})
_ERROR_MESSAGES = {
    "mcp_capability_rejected": "Capability rejected.",
    "invalid_arguments": "Invalid tool arguments.",
    "reminder_not_open": "Reminder is not open.",
    "tool_execution_failed": "Tool execution failed.",
    "unknown_tool": "Unknown tool.",
}


def failure(name: str, code: str, *, call_id: str = "") -> ToolResult:
    return ToolResult(
        call_id=call_id,
        tool_name=name if name in ARGUMENT_MODELS else "unknown",
        status="denied" if code == "mcp_capability_rejected" else "failed",
        user_summary=_ERROR_MESSAGES[code],
        error_code=code,
        duration_ms=0,
    )


class LazyKnowledgeSource:
    def __init__(self, database_path: Path, model_path: Path) -> None:
        self._database_path = database_path
        self._model_path = model_path
        self._source: KnowledgeSource | None = None

    def search_knowledge(self, query: str, limit: int) -> list[object]:
        if self._source is None:
            from voxagent.conversation.context import SqliteContextSource
            from voxagent.memory.embedder import BgeSmallZhEmbedder

            embedder = BgeSmallZhEmbedder.from_path(self._model_path)
            self._source = SqliteContextSource(self._database_path, embedder)
        return list(self._source.search_knowledge(query, limit))


@dataclass(frozen=True)
class ToolServices:
    connection: sqlite3.Connection
    knowledge_source: KnowledgeSource
    now_utc: NowProvider = lambda: datetime.now(UTC)

    async def execute(
        self,
        name: str,
        arguments: dict[str, object],
        verifier: CapabilityVerifier,
        capability: str = "",
    ) -> ToolResult:
        call_id = str(uuid4())
        request_id = None
        try:
            parsed = ARGUMENT_MODELS[name].model_validate(arguments, strict=True)
            if name in WRITE_TOOLS:
                # Bind the exact JSON values received on the wire, before parsing datetimes.
                nonce = verifier.consume(capability, name, arguments, self.now_utc())
                row = self.connection.execute(
                    "SELECT tool_request_id FROM mcp_capability_nonces WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if row is None:
                    raise CapabilityError()
                request_id = int(row[0])
            executors = {
                "knowledge.search": build_knowledge_search_executor(self.knowledge_source),
                "reminders.list": build_reminder_list_executor(self.connection),
                "reminders.create": build_reminder_create_executor(self.connection, self.now_utc),
                "reminders.complete": build_reminder_complete_executor(
                    self.connection, self.now_utc
                ),
            }
            result = await executors[name](
                ToolCall(call_id=call_id, name=name, arguments=parsed.model_dump())
            )
            if result.status != "succeeded":
                code = "reminder_not_open" if result.error_code == "reminder_not_open" else (
                    "tool_execution_failed"
                )
                result = failure(name, code, call_id=call_id)
        except CapabilityError:
            return failure(name, "mcp_capability_rejected", call_id=call_id)
        except ValidationError:
            return failure(name, "invalid_arguments", call_id=call_id)
        except Exception:
            result = failure(name, "tool_execution_failed", call_id=call_id)

        if request_id is not None:
            detail: dict[str, object] = {"duration_ms": result.duration_ms}
            if result.error_code is not None:
                detail["error_code"] = result.error_code
            try:
                ToolRepository(self.connection).finish_request(
                    request_id, result.status, self.now_utc(), detail=detail
                )
            except Exception:
                return failure(name, "tool_execution_failed", call_id=call_id)
        return result
