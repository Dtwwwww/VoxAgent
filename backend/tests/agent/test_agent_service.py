from __future__ import annotations

import asyncio
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
    execution_started: asyncio.Event | None = None,
    execution_release: asyncio.Event | None = None,
) -> ToolRegistry:
    async def execute(call: ToolCall) -> ToolResult:
        counters[call.name] = counters.get(call.name, 0) + 1
        if execution_started is not None:
            execution_started.set()
        if execution_release is not None:
            await execution_release.wait()
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
    execution_started: asyncio.Event | None = None,
    execution_release: asyncio.Event | None = None,
) -> AgentService:
    registry = make_registry(
        counters,
        permission=permission,
        execution_started=execution_started,
        execution_release=execution_release,
    )
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
async def test_tool_started_streams_before_blocked_execution_completes(
    connection: sqlite3.Connection,
) -> None:
    call = ToolCall(call_id="call-1", name="demo.action", arguments={"value": "ok"})
    model = ScriptedModel(
        [
            [AssistantToolCall(call), AssistantStreamDone()],
            [AssistantTextDelta("完成"), AssistantStreamDone()],
        ]
    )
    execution_started = asyncio.Event()
    execution_release = asyncio.Event()
    service = make_service(
        connection,
        model,
        {},
        execution_started=execution_started,
        execution_release=execution_release,
    )
    iterator = service.start_turn("session-a", 1, [{"role": "user", "content": "run"}])

    first = await anext(iterator)

    assert first == ToolStarted("call-1", "demo.action")
    await asyncio.wait_for(execution_started.wait(), timeout=1)
    execution_release.set()
    assert isinstance((await collect(iterator))[0], ToolCompleted)


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
    await service.graph.aupdate_state(
        {"configurable": {"thread_id": "session-a:1"}},
        {
            "current_call": {
                "call_id": "call-1",
                "name": "demo.action",
                "arguments": {"value": "checkpoint-tamper"},
            }
        },
    )

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
    assert '"echo":"ok"' in model.messages[1][-1].content
    state = await service.graph.aget_state(
        {"configurable": {"thread_id": "session-a:1"}}
    )
    assert state.values["node_visit_count"] == 8


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
async def test_cancel_during_model_stream_and_node_limit_prevent_later_execution(
    connection: sqlite3.Connection,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    call = ToolCall(call_id="x", name="demo.action", arguments={"value": "ok"})

    class DelayedModel(ScriptedModel):
        async def stream_agent(self, model, messages, tools):
            entered.set()
            await release.wait()
            async for event in super().stream_agent(model, messages, tools):
                yield event

    cancelled_model = DelayedModel([[AssistantToolCall(call), AssistantStreamDone()]])
    cancelled_counters: dict[str, int] = {}
    cancelled_service = make_service(connection, cancelled_model, cancelled_counters)
    pending = asyncio.create_task(
        collect(
            cancelled_service.start_turn("session-cancel", 1, [{"role": "user", "content": "run"}])
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    cancelled_service.cancel("session-cancel", 1)
    release.set()

    cancelled_events = await pending

    assert isinstance(cancelled_events[0], ToolFailed)
    assert cancelled_events[0].error_code == "turn_cancelled"
    assert cancelled_counters == {}

    calls = [
        ToolCall(call_id=f"limit-{index}", name="demo.action", arguments={"value": "ok"})
        for index in range(3)
    ]
    limited_model = ScriptedModel(
        [
            [AssistantToolCall(calls[0]), AssistantStreamDone()],
            [AssistantToolCall(calls[1]), AssistantStreamDone()],
            [AssistantToolCall(calls[2]), AssistantStreamDone()],
        ]
    )
    limited_counters: dict[str, int] = {}
    limited_service = make_service(connection, limited_model, limited_counters)

    limited_events = await collect(
        limited_service.start_turn("session-limit", 1, [{"role": "user", "content": "run"}])
    )

    assert any(
        isinstance(event, ToolFailed) and event.error_code == "node_visit_limit_exceeded"
        for event in limited_events
    )
    assert limited_counters == {"demo.action": 1}
    limited_state = await limited_service.graph.aget_state(
        {"configurable": {"thread_id": "session-limit:1"}}
    )
    assert limited_state.values["node_visit_count"] == 8


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
