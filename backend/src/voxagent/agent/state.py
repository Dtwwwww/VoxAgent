from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    session_id: str
    turn_id: int
    messages: list[dict[str, Any]]
    authorized_roots: list[str]
    tool_call_count: int
    node_visit_count: int
    cancelled: bool
    pending_calls: list[dict[str, Any]]
    current_call: dict[str, Any] | None
    tool_request_id: int | None
    confirmation_id: str | None
    permission: str | None
    next_action: str
    outbox: list[dict[str, Any]]
