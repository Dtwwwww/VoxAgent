from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, ConfigDict

from voxagent.agent.events import (
    AgentTextDelta,
    ToolApprovalRequired,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    TurnDone,
)
from voxagent.agent.service import AgentService
from voxagent.db.migrations import migrate
from voxagent.llm.ollama import (
    AssistantStreamDone,
    AssistantTextDelta,
    AssistantToolCall,
    ModelMessage,
    ToolPayload,
)
from voxagent.tools.confirmation import ConfirmationService
from voxagent.tools.registry import ToolRegistry
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition, ToolResult

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class DemoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class ScriptedModel:
    def __init__(self, scripts: Sequence[Sequence[object]]) -> None:
        self.scripts = list(scripts)
        self.messages: list[Sequence[ModelMessage]] = []
        self.tools: list[Sequence[ToolPayload]] = []

    async def stream_agent(
        self,
        model: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolPayload],
    ) -> AsyncIterator[object]:
        del model
        self.messages.append(messages)
        self.tools.append(tools)
        for event in self.scripts.pop(0):
            yield event


def make_registry(
    counters: dict[str, int],
    *,
    permission: PermissionLevel = PermissionLevel.L0,
) -> ToolRegistry:
    async def execute(call: ToolCall) -> ToolResult:
        counters[call.name] = counters.get(call.name, 0) + 1
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="succeeded",
            data={"echo": call.arguments["value"]},
            user_summary="done",
            duration_ms=1,
        )

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="demo.action",
            description="A bounded demo action.",
            permission=permission,
            arguments_model=DemoArgs,
            timeout_seconds=1,
            provider="native",
        ),
        execute,
    )
    registry.freeze()
    return registry


@pytest.fixture
def connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    yield connection
    connection.close()


def make_service(
    connection: sqlite3.Connection,
    model: ScriptedModel,
    counters: dict[str, int],
    *,
    permission: PermissionLevel = PermissionLevel.L0,
    checkpointer: object | None = None,
) -> AgentService:
    registry = make_registry(counters, permission=permission)
    repository = ToolRepository(connection)
    return AgentService(
        model_name="qwen3:4b",
        model=model,
        registry=registry,
        repository=repository,
        confirmation=ConfirmationService(repository, registry),
        checkpointer=checkpointer or InMemorySaver(),
        now_utc=lambda: NOW,
    )


async def collect(iterator: AsyncIterator[object]) -> list[object]:
    return [event async for event in iterator]


@pytest.mark.asyncio
async def test_plain_chat_emits_delta_then_one_done_without_tool_execution(
    connection: sqlite3.Connection,
) -> None:
    model = ScriptedModel([[AssistantTextDelta("你好"), AssistantStreamDone()]])
    counters: dict[str, int] = {}
    service = make_service(connection, model, counters)

    events = await collect(service.start_turn("session-a", 1, [{"role": "user", "content": "hi"}]))

    assert events == [AgentTextDelta("你好"), TurnDone()]
    assert counters == {}


@pytest.mark.asyncio
async def test_l0_executes_once_audits_and_continues_with_untrusted_result(
    connection: sqlite3.Connection,
) -> None:
    call = ToolCall(call_id="call-1", name="demo.action", arguments={"value": "ok"})
    model = ScriptedModel(
        [
            [AssistantToolCall(call), AssistantStreamDone()],
            [AssistantTextDelta("完成"), AssistantStreamDone()],
        ]
    )
    counters: dict[str, int] = {}
    service = make_service(connection, model, counters)

    events = await collect(service.start_turn("session-a", 1, [{"role": "user", "content": "run"}]))

    assert events == [
        ToolStarted("call-1", "demo.action"),
        ToolCompleted("call-1", "demo.action", "done"),
        AgentTextDelta("完成"),
        TurnDone(),
    ]
    assert counters == {"demo.action": 1}
    assert [item.event_type for item in ToolRepository(connection).list_audit()] == [
        "request.succeeded",
        "request.started",
        "request.created",
    ]
    assert "untrusted tool data" in model.messages[1][-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", [PermissionLevel.L1, PermissionLevel.L2])
async def test_confirmation_executes_once_and_replay_is_unavailable(
    connection: sqlite3.Connection,
    permission: PermissionLevel,
) -> None:
    call = ToolCall(call_id="call-1", name="demo.action", arguments={"value": "ok"})
    model = ScriptedModel(
        [
            [AssistantToolCall(call), AssistantStreamDone()],
            [AssistantTextDelta("完成"), AssistantStreamDone()],
        ]
    )
    counters: dict[str, int] = {}
    service = make_service(connection, model, counters, permission=permission)

    initial = await collect(
        service.start_turn("session-a", 1, [{"role": "user", "content": "run"}])
    )
    approval = initial[0]
    assert isinstance(approval, ToolApprovalRequired)
    assert counters == {}

    resumed = await collect(
        service.resume_confirmation(approval.confirmation_id, "session-a", 1, approved=True)
    )
    replay = await collect(
        service.resume_confirmation(approval.confirmation_id, "session-a", 1, approved=True)
    )

    assert resumed == [
        ToolStarted("call-1", "demo.action"),
        ToolCompleted("call-1", "demo.action", "done"),
        AgentTextDelta("完成"),
        TurnDone(),
    ]
    assert replay == [ToolFailed("call-1", "demo.action", "confirmation_unavailable")]
    assert counters == {"demo.action": 1}


@pytest.mark.asyncio
async def test_confirmation_denial_is_durable_and_never_executes(
    connection: sqlite3.Connection,
) -> None:
    call = ToolCall(call_id="call-1", name="demo.action", arguments={"value": "ok"})
    model = ScriptedModel([[AssistantToolCall(call), AssistantStreamDone()]])
    counters: dict[str, int] = {}
    service = make_service(connection, model, counters, permission=PermissionLevel.L2)
    initial = await collect(
        service.start_turn("session-a", 1, [{"role": "user", "content": "run"}])
    )
    approval = initial[0]
    assert isinstance(approval, ToolApprovalRequired)

    denied = await collect(
        service.resume_confirmation(approval.confirmation_id, "session-a", 1, approved=False)
    )
    replay = await collect(
        service.resume_confirmation(approval.confirmation_id, "session-a", 1, approved=False)
    )

    assert denied == [ToolFailed("call-1", "demo.action", "confirmation_denied"), TurnDone()]
    assert replay == [ToolFailed("call-1", "demo.action", "confirmation_unavailable")]
    assert counters == {}
    assert (
        ToolRepository(connection).get_confirmation_request(approval.confirmation_id)[1].status
        == "denied"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "cancelled", "code"),
    [
        (
            [
                AssistantToolCall(ToolCall(call_id="x", name="unknown.tool", arguments={})),
                AssistantStreamDone(),
            ],
            False,
            "unknown_tool",
        ),
        (
            [
                AssistantToolCall(
                    ToolCall(call_id="x", name="demo.action", arguments={"extra": "bad"})
                ),
                AssistantStreamDone(),
            ],
            False,
            "invalid_arguments",
        ),
        (
            [
                *[
                    AssistantToolCall(
                        ToolCall(
                            call_id=f"x-{index}",
                            name="demo.action",
                            arguments={"value": "ok"},
                        )
                    )
                    for index in range(4)
                ],
                AssistantStreamDone(),
            ],
            False,
            "tool_call_limit_exceeded",
        ),
        (
            [
                AssistantToolCall(
                    ToolCall(call_id="x", name="demo.action", arguments={"value": "ok"})
                ),
                AssistantStreamDone(),
            ],
            True,
            "turn_cancelled",
        ),
    ],
)
async def test_guard_failures_never_execute(
    connection: sqlite3.Connection,
    events: list[object],
    cancelled: bool,
    code: str,
) -> None:
    model = ScriptedModel([events])
    counters: dict[str, int] = {}
    service = make_service(connection, model, counters)
    if cancelled:
        service.cancel("session-a", 1)

    result = await collect(service.start_turn("session-a", 1, [{"role": "user", "content": "run"}]))

    assert isinstance(result[0], ToolFailed)
    assert result[0].error_code == code
    assert counters == {}


@pytest.mark.asyncio
async def test_graph_contains_required_nodes_and_checkpoints_are_thread_isolated(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    model = ScriptedModel(
        [
            [AssistantTextDelta("a"), AssistantStreamDone()],
            [AssistantTextDelta("b"), AssistantStreamDone()],
        ]
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        service = make_service(connection, model, {}, checkpointer=saver)

        await collect(service.start_turn("session-a", 1, [{"role": "user", "content": "a"}]))
        await collect(service.start_turn("session-b", 1, [{"role": "user", "content": "b"}]))

        assert set(service.graph.nodes) >= {
            "route",
            "retrieve",
            "call_model",
            "authorize",
            "await_confirmation",
            "execute_tool",
            "respond",
            "persist",
        }
        state_a = await service.graph.aget_state({"configurable": {"thread_id": "session-a:1"}})
        state_b = await service.graph.aget_state({"configurable": {"thread_id": "session-b:1"}})
        assert state_a.values["session_id"] == "session-a"
        assert state_b.values["session_id"] == "session-b"
