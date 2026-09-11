from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxagent.tools.schema import ToolCall, ToolResult


class KnowledgeSource(Protocol):
    def search_knowledge(self, query: str, limit: int) -> list[object]: ...


class KnowledgeSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=4, ge=1, le=10)


def build_knowledge_search_executor(
    knowledge_source: KnowledgeSource,
) -> Callable[[ToolCall], Awaitable[ToolResult]]:
    async def execute(call: ToolCall) -> ToolResult:
        started = perf_counter()
        results = knowledge_source.search_knowledge(
            call.arguments["query"],
            call.arguments["limit"],
        )
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"results": [_safe_result(item) for item in results]},
            user_summary="知识库查询完成。",
            duration_ms=_duration_ms(started),
        )

    return execute


def _safe_result(item: object) -> dict[str, Any]:
    return {
        "chunk_id": _value(item, "chunk_id"),
        "document_id": _value(item, "document_id"),
        "display_name": _value(item, "display_name"),
        "content": _value(item, "content"),
        "page_number": _value(item, "page_number"),
        "score": _value(item, "score"),
    }


def _value(item: object, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
