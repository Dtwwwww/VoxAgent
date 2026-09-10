from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from voxagent.tools.policy import PolicyContext, ToolPolicy
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition


class QueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


class ArgsWithModelPermission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    permission: str


def definition(
    *,
    name: str = "knowledge.search",
    permission: PermissionLevel = PermissionLevel.L0,
    arguments_model: type[BaseModel] = QueryArgs,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Search safely",
        permission=permission,
        arguments_model=arguments_model,
        timeout_seconds=1.0,
        provider="native",
    )


def context(
    roots: tuple[Path, ...] = (Path.cwd(),),
    *,
    tool_call_count: int = 0,
    node_visit_count: int = 0,
    cancelled: bool = False,
) -> PolicyContext:
    return PolicyContext(
        session_id="session-1",
        turn_id=7,
        authorized_roots=roots,
        tool_call_count=tool_call_count,
        node_visit_count=node_visit_count,
        cancelled=cancelled,
    )


def call(name: str = "knowledge.search", arguments: dict[str, object] | None = None) -> ToolCall:
    return ToolCall(call_id="call-1", name=name, arguments=arguments or {"query": "声灵"})


@pytest.mark.parametrize(
    ("permission", "action"),
    [
        (PermissionLevel.L0, "execute"),
        (PermissionLevel.L1, "confirm"),
        (PermissionLevel.L2, "confirm"),
    ],
)
def test_permission_matrix(permission: PermissionLevel, action: str) -> None:
    decision = ToolPolicy.authorize(definition(permission=permission), call(), context())

    assert decision.action == action
    assert decision.code == f"tool_{action}"


@pytest.mark.parametrize(
    ("tool_definition", "tool_call", "policy_context", "code"),
    [
        (None, call(), context(), "unknown_tool"),
        (definition(name="knowledge.search"), call("knowledge.other"), context(), "tool_mismatch"),
        (definition(), call(arguments={"query": 1}), context(), "invalid_arguments"),
        (definition(), call(), context(tool_call_count=3), "tool_call_limit_exceeded"),
        (definition(), call(), context(node_visit_count=8), "node_visit_limit_exceeded"),
        (definition(), call(), context(cancelled=True), "turn_cancelled"),
        (
            definition(name="files.search_authorized"),
            call("files.search_authorized"),
            context(roots=()),
            "authorized_roots_required",
        ),
    ],
)
def test_deny_conditions(
    tool_definition: ToolDefinition | None,
    tool_call: ToolCall,
    policy_context: PolicyContext,
    code: str,
) -> None:
    decision = ToolPolicy.authorize(tool_definition, tool_call, policy_context)

    assert decision.action == "deny"
    assert decision.code == code


def test_policy_uses_trusted_definition_permission_not_model_arguments() -> None:
    tool_definition = definition(
        permission=PermissionLevel.L2,
        arguments_model=ArgsWithModelPermission,
    )
    tool_call = call(arguments={"query": "声灵", "permission": "L0"})

    decision = ToolPolicy.authorize(tool_definition, tool_call, context())

    assert decision.action == "confirm"
    assert decision.code == "tool_confirm"

