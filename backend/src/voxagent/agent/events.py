from __future__ import annotations

from dataclasses import dataclass

from voxagent.tools.schema import PermissionLevel


@dataclass(frozen=True, slots=True)
class AgentTextDelta:
    delta: str


@dataclass(frozen=True, slots=True)
class ToolApprovalRequired:
    confirmation_id: str
    call_id: str
    tool_name: str
    permission: PermissionLevel


@dataclass(frozen=True, slots=True)
class ToolStarted:
    call_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    call_id: str
    tool_name: str
    user_summary: str


@dataclass(frozen=True, slots=True)
class ToolFailed:
    call_id: str
    tool_name: str
    error_code: str


@dataclass(frozen=True, slots=True)
class TurnDone:
    pass


AgentEvent = (
    AgentTextDelta | ToolApprovalRequired | ToolStarted | ToolCompleted | ToolFailed | TurnDone
)
