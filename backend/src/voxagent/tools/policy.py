from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition

PolicyAction = Literal["execute", "confirm", "deny"]


@dataclass(frozen=True)
class PolicyContext:
    session_id: str
    turn_id: int
    authorized_roots: Sequence[Path]
    tool_call_count: int
    node_visit_count: int
    cancelled: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorized_roots", tuple(self.authorized_roots))


@dataclass(frozen=True)
class AuthorizationDecision:
    action: PolicyAction
    code: str


class ToolPolicy:
    @staticmethod
    def authorize(
        definition: ToolDefinition | None,
        call: ToolCall,
        context: PolicyContext,
    ) -> AuthorizationDecision:
        if definition is None:
            return _deny("unknown_tool")
        if call.name != definition.name:
            return _deny("tool_mismatch")
        if context.cancelled:
            return _deny("turn_cancelled")
        if context.tool_call_count >= 3:
            return _deny("tool_call_limit_exceeded")
        if context.node_visit_count >= 8:
            return _deny("node_visit_limit_exceeded")
        if definition.name == "files.search_authorized" and not context.authorized_roots:
            return _deny("authorized_roots_required")
        try:
            ToolCall.from_untrusted(definition, call.call_id, call.arguments)
        except ValidationError:
            return _deny("invalid_arguments")

        if definition.permission is PermissionLevel.L0:
            return AuthorizationDecision("execute", "tool_execute")
        return AuthorizationDecision("confirm", "tool_confirm")


def _deny(code: str) -> AuthorizationDecision:
    return AuthorizationDecision("deny", code)

