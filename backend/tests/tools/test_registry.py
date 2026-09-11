from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from voxagent.tools.registry import (
    DuplicateToolError,
    FrozenToolRegistryError,
    ToolRegistry,
    UnknownToolError,
)
from voxagent.tools.schema import (
    PermissionLevel,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class QueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def make_definition(name: str = "knowledge.search", timeout: float = 1.0) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Run {name}",
        permission=PermissionLevel.L0,
        arguments_model=QueryArgs,
        timeout_seconds=timeout,
        provider="native",
    )


async def successful_executor(call: ToolCall) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_name=call.name,
        status="succeeded",
        data={"query": call.arguments["query"]},
        user_summary="查询完成",
        duration_ms=1,
    )


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(), successful_executor)

    with pytest.raises(DuplicateToolError, match="knowledge.search"):
        registry.register(make_definition(), successful_executor)


def test_registry_rejects_access_and_registration_before_or_after_freeze() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(), successful_executor)

    with pytest.raises(FrozenToolRegistryError, match="freeze"):
        registry.get("knowledge.search")

    registry.freeze()
    with pytest.raises(FrozenToolRegistryError, match="frozen"):
        registry.register(make_definition("knowledge.other"), successful_executor)


def test_definition_payloads_are_sorted_and_immutable() -> None:
    registry = ToolRegistry()
    registry.register(make_definition("knowledge.zeta"), successful_executor)
    registry.register(make_definition("knowledge.alpha"), successful_executor)
    registry.freeze()

    payloads = registry.definition_payloads()

    assert isinstance(payloads, tuple)
    assert [payload["function"]["name"] for payload in payloads] == [
        "knowledge.alpha",
        "knowledge.zeta",
    ]


def test_registry_raises_stable_error_for_unknown_tool() -> None:
    registry = ToolRegistry()
    registry.freeze()

    with pytest.raises(UnknownToolError, match="unknown.tool"):
        registry.get("unknown.tool")


@pytest.mark.asyncio
async def test_registry_executes_a_validated_call() -> None:
    registry = ToolRegistry()
    definition = make_definition()
    registry.register(definition, successful_executor)
    registry.freeze()
    call = ToolCall.from_untrusted(definition, "call-1", {"query": "声灵"})

    result = await registry.execute(call)

    assert result.status == "succeeded"
    assert result.data == {"query": "声灵"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "private_path",
    [r"C:\private\secret.txt", "/home/private/secret.txt"],
)
async def test_registry_converts_executor_exception_without_leaking_path(
    private_path: str,
) -> None:
    async def failing_executor(call: ToolCall) -> ToolResult:
        raise RuntimeError(f"failed at {private_path}")

    registry = ToolRegistry()
    definition = make_definition()
    registry.register(definition, failing_executor)
    registry.freeze()

    result = await registry.execute(
        ToolCall.from_untrusted(definition, "call-1", {"query": "声灵"})
    )

    assert result.status == "failed"
    assert result.error_code == "tool_execution_failed"
    assert result.data == {}
    assert "private" not in result.user_summary
    assert "C:\\" not in result.user_summary


@pytest.mark.asyncio
async def test_registry_times_out_executor() -> None:
    async def slow_executor(call: ToolCall) -> ToolResult:
        await asyncio.sleep(0.05)
        return await successful_executor(call)

    registry = ToolRegistry()
    definition = make_definition(timeout=0.001)
    registry.register(definition, slow_executor)
    registry.freeze()

    result = await registry.execute(
        ToolCall.from_untrusted(definition, "call-1", {"query": "声灵"})
    )

    assert result.status == "failed"
    assert result.error_code == "tool_execution_failed"


@pytest.mark.asyncio
async def test_registry_propagates_executor_cancellation() -> None:
    async def cancelled_executor(call: ToolCall) -> ToolResult:
        raise asyncio.CancelledError

    registry = ToolRegistry()
    definition = make_definition()
    registry.register(definition, cancelled_executor)
    registry.freeze()

    with pytest.raises(asyncio.CancelledError):
        await registry.execute(
            ToolCall.from_untrusted(definition, "call-1", {"query": "声灵"})
        )
