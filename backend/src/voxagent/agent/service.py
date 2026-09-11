from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.types import Command

from voxagent.agent.events import (
    AgentEvent,
    AgentTextDelta,
    ToolApprovalRequired,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    TurnDone,
)
from voxagent.agent.graph import AgentModel, AgentWorkflow, build_graph
from voxagent.conversation.history import ChatMessage, TrustedSystemMessage
from voxagent.llm.ollama import ModelMessage
from voxagent.tools.confirmation import ConfirmationError, ConfirmationService
from voxagent.tools.registry import ToolRegistry
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel


class AgentService:
    def __init__(
        self,
        *,
        model_name: str,
        model: AgentModel,
        registry: ToolRegistry,
        repository: ToolRepository,
        confirmation: ConfirmationService,
        checkpointer: object,
        now_utc: Callable[[], datetime],
    ) -> None:
        self._cancelled: set[tuple[str, int]] = set()
        workflow = AgentWorkflow(
            model_name=model_name,
            model=model,
            registry=registry,
            repository=repository,
            confirmation=confirmation,
            now_utc=now_utc,
            is_cancelled=lambda session_id, turn_id: (
                (
                    session_id,
                    turn_id,
                )
                in self._cancelled
            ),
        )
        self.graph = build_graph(workflow, checkpointer)
        self._repository = repository
        self._confirmation = confirmation
        self._now_utc = now_utc

    async def start_turn(
        self,
        session_id: str,
        turn_id: int,
        messages: Sequence[ModelMessage],
        authorized_roots: Sequence[Path] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        key = (session_id, turn_id)
        roots = (
            tuple(authorized_roots)
            if authorized_roots is not None
            else self._repository.list_authorized_roots()
        )
        state = {
            "session_id": session_id,
            "turn_id": turn_id,
            "messages": _checkpoint_messages(messages),
            "authorized_roots": [str(path) for path in roots],
            "tool_call_count": 0,
            "node_visit_count": 0,
            "cancelled": key in self._cancelled,
            "pending_calls": [],
            "current_call": None,
            "tool_request_id": None,
            "confirmation_id": None,
            "permission": None,
            "next_action": "route",
            "outbox": [],
        }
        async for event in self._run(state, session_id, turn_id):
            yield event

    async def resume_confirmation(
        self,
        confirmation_id: str,
        session_id: str,
        turn_id: int,
        *,
        approved: bool,
    ) -> AsyncIterator[AgentEvent]:
        try:
            _ticket, record = self._repository.get_confirmation_request(confirmation_id)
        except KeyError:
            yield ToolFailed("", "", "confirmation_unavailable")
            return
        if (session_id, turn_id) in self._cancelled:
            yield ToolFailed(record.call_id, record.tool_name, "turn_cancelled")
            return
        try:
            if approved:
                approved_call = self._confirmation.approve(
                    confirmation_id,
                    session_id,
                    turn_id,
                    self._now(),
                )
            else:
                self._confirmation.deny(
                    confirmation_id,
                    session_id,
                    turn_id,
                    self._now(),
                )
        except ConfirmationError as error:
            yield ToolFailed(record.call_id, record.tool_name, error.code)
            return
        command = Command(
            resume={
                "approved": approved,
                "call": (approved_call.model_dump(mode="json") if approved else None),
                "tool_request_id": record.id,
            }
        )
        async for event in self._run(command, session_id, turn_id):
            yield event

    def cancel(self, session_id: str, turn_id: int) -> None:
        self._cancelled.add((session_id, turn_id))

    def _now(self) -> datetime:
        return self._now_utc()

    async def _run(
        self,
        graph_input: dict[str, Any] | Command,
        session_id: str,
        turn_id: int,
    ) -> AsyncIterator[AgentEvent]:
        config = {
            "configurable": {"thread_id": f"{session_id}:{turn_id}"},
            "recursion_limit": 24,
        }
        async for mode, update in self.graph.astream(
            graph_input,
            config,
            stream_mode=["custom", "updates"],
        ):
            if mode == "custom":
                if isinstance(update, Mapping):
                    yield _event(update)
                continue
            if not isinstance(update, dict):
                continue
            for value in update.values():
                if not isinstance(value, dict):
                    continue
                for item in value.get("outbox", []):
                    yield _event(item)


def _event(item: Mapping[str, Any]) -> AgentEvent:
    kind = item.get("kind")
    if kind == "text":
        return AgentTextDelta(str(item["delta"]))
    if kind == "approval":
        return ToolApprovalRequired(
            str(item["confirmation_id"]),
            str(item["call_id"]),
            str(item["tool_name"]),
            PermissionLevel(str(item["permission"])),
        )
    if kind == "started":
        return ToolStarted(str(item["call_id"]), str(item["tool_name"]))
    if kind == "completed":
        return ToolCompleted(
            str(item["call_id"]),
            str(item["tool_name"]),
            str(item["user_summary"]),
        )
    if kind == "failed":
        return ToolFailed(
            str(item.get("call_id", "")),
            str(item.get("tool_name", "")),
            str(item["error_code"]),
        )
    if kind == "done":
        return TurnDone()
    raise ValueError("unknown persisted agent event")


def _checkpoint_messages(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, TrustedSystemMessage):
            items.append({"kind": "trusted_system", "content": message.content})
        elif isinstance(message, ChatMessage):
            items.append({"role": message.role, "content": message.content})
        elif isinstance(message, Mapping):
            items.append(
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                }
            )
        else:
            raise ValueError("a new turn accepts only chat and trusted system messages")
    return items
