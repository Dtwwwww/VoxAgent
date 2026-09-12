from __future__ import annotations

import asyncio
import importlib
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from voxagent.agent.events import ToolApprovalRequired, ToolCompleted, ToolFailed, TurnDone
from voxagent.agent.service import AgentService
from voxagent.db.migrations import migrate
from voxagent.llm.ollama import AssistantStreamDone, AssistantToolCall
from voxagent.mcp.capability import CapabilityIssuer, CapabilityVerifier
from voxagent.mcp.models import McpDiscoveredTool
from voxagent.mcp.server_tools import ToolServices
from voxagent.tools.builtin import build_builtin_registry, builtin_definitions
from voxagent.tools.confirmation import ConfirmationService
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


async def collect(iterator):
    return [event async for event in iterator]


@pytest.fixture
def runtime():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    clock = [NOW]
    source = SimpleNamespace(search_knowledge=lambda query, limit: [])
    key = b"s" * 32
    services = ToolServices(connection, source, now_utc=lambda: clock[0])
    verifier = CapabilityVerifier(connection, key)
    wire = []

    async def remote_call(name, arguments):
        wire.append((name, arguments.copy()))
        business = arguments.copy()
        capability = business.pop("_voxagent_capability", "")
        result = await services.execute(name, business, verifier, capability)
        return result.model_dump(mode="json")

    client = SimpleNamespace(call=remote_call)

    def make_service(mode, model):
        if mode == "native":
            registry = build_builtin_registry(connection, source, None, now_utc=lambda: clock[0])
        else:
            module = importlib.import_module("voxagent.mcp.provider")
            discovered = []
            for definition in builtin_definitions():
                if definition.name in {"files.search_authorized", "apps.open_allowlisted"}:
                    continue
                schema = definition.arguments_model.model_json_schema()
                if definition.permission is not PermissionLevel.L0:
                    schema["properties"]["_voxagent_capability"] = {"type": "string"}
                    schema.setdefault("required", []).append("_voxagent_capability")
                discovered.append(
                    McpDiscoveredTool(
                        definition.name, definition.description, schema, definition.permission
                    )
                )
            provider = module.McpToolProvider(
                client,
                CapabilityIssuer(key),
                connection,
                tuple(discovered),
                now_utc=lambda: clock[0],
            )
            registry = module.build_mcp_registry(connection, source, None, provider)
        repository = ToolRepository(connection)
        return AgentService(
            model_name="test",
            model=model,
            registry=registry,
            repository=repository,
            confirmation=ConfirmationService(repository, registry),
            checkpointer=InMemorySaver(),
            now_utc=lambda: clock[0],
        )

    yield SimpleNamespace(
        connection=connection, clock=clock, wire=wire, make=make_service, client=client
    )
    connection.close()


class WriteModel:
    async def stream_agent(self, model, messages, tools):
        if not any(getattr(message, "tool_name", None) for message in messages):
            yield AssistantToolCall(
                ToolCall(
                    call_id="reused-id",
                    name="reminders.create",
                    arguments={"title": "meeting", "due_at_utc": "2026-09-12T08:00:00+08:00"},
                )
            )
        yield AssistantStreamDone()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "mcp"])
async def test_approved_agent_write_binds_exact_request_even_when_call_ids_repeat(runtime, mode):
    service = runtime.make(mode, WriteModel())
    for turn in (1, 2):
        initial = await collect(
            service.start_turn("session", turn, [{"role": "user", "content": "add"}])
        )
        approval = initial[0]
        assert isinstance(approval, ToolApprovalRequired)
        assert len(runtime.wire) == (turn - 1 if mode == "mcp" else 0)
        resumed = await collect(
            service.resume_confirmation(approval.confirmation_id, "session", turn, approved=True)
        )
        assert any(
            isinstance(event, ToolCompleted) and event.call_id == "reused-id" for event in resumed
        )
        assert not any(isinstance(event, ToolFailed) for event in resumed)
    rows = runtime.connection.execute("SELECT id, status FROM tool_requests ORDER BY id").fetchall()
    assert len(rows) == 2 and all(row["status"] == "succeeded" for row in rows)
    assert runtime.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 2
    audits = runtime.connection.execute(
        "SELECT tool_request_id, COUNT(*) FROM tool_audit WHERE event_type = 'request.succeeded' "
        "GROUP BY tool_request_id"
    ).fetchall()
    assert [(row[0], row[1]) for row in audits] == [(row["id"], 1) for row in rows]
    if mode == "mcp":
        nonces = runtime.connection.execute(
            "SELECT tool_request_id FROM mcp_capability_nonces ORDER BY tool_request_id"
        ).fetchall()
        assert [row[0] for row in nonces] == [row["id"] for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["native", "mcp"])
@pytest.mark.parametrize("outcome", ["denied", "expired", "cancelled"])
async def test_unapproved_agent_turns_never_write_or_call_mcp(runtime, mode, outcome):
    service = runtime.make(mode, WriteModel())
    initial = await collect(service.start_turn("session", 1, [{"role": "user", "content": "add"}]))
    approval = initial[0]
    assert isinstance(approval, ToolApprovalRequired)
    if outcome == "expired":
        runtime.clock[0] += timedelta(minutes=3)
    elif outcome == "cancelled":
        service.cancel("session", 1)
    events = await collect(
        service.resume_confirmation(
            approval.confirmation_id, "session", 1, approved=outcome != "denied"
        )
    )
    assert any(isinstance(event, ToolFailed) for event in events)
    assert runtime.wire == []
    assert runtime.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("release_before_deadline", [True, False])
async def test_shutdown_rejects_new_turns_and_drains_or_cancels_active_graph(
    runtime,
    release_before_deadline,
):
    entered = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingModel:
        async def stream_agent(self, model, messages, tools):
            entered.set()
            try:
                await release.wait()
                yield AssistantStreamDone()
            finally:
                stopped.set()

    service = runtime.make("native", BlockingModel())
    turn = asyncio.create_task(
        collect(service.start_turn("session", 1, [{"role": "user", "content": "wait"}]))
    )
    await asyncio.wait_for(entered.wait(), 1)
    assert hasattr(service, "shutdown"), "Agent shutdown boundary is missing"
    shutdown = asyncio.create_task(service.shutdown(timeout_seconds=0.05))
    await asyncio.sleep(0)
    rejected = await collect(service.start_turn("session", 2, []))
    assert rejected == [ToolFailed("", "", "agent_shutting_down"), TurnDone()]
    if release_before_deadline:
        assert not shutdown.done()
        release.set()
    await asyncio.wait_for(shutdown, 1)
    assert stopped.is_set()
    await asyncio.gather(turn, return_exceptions=True)
    assert await collect(service.resume_confirmation("missing", "session", 1, approved=True)) == [
        ToolFailed("", "", "agent_shutting_down")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_before_deadline", [True, False])
async def test_shutdown_waits_for_mcp_call_and_revokes_request_on_deadline(
    runtime,
    complete_before_deadline,
):
    entered = asyncio.Event()
    release = asyncio.Event()
    real_call = runtime.client.call

    async def blocked_call(name, arguments):
        entered.set()
        await release.wait()
        return await real_call(name, arguments)

    runtime.client.call = blocked_call
    service = runtime.make("mcp", WriteModel())
    initial = await collect(service.start_turn("session", 1, [{"role": "user", "content": "add"}]))
    approval = initial[0]
    resumed = asyncio.create_task(
        collect(service.resume_confirmation(approval.confirmation_id, "session", 1, approved=True))
    )
    await asyncio.wait_for(entered.wait(), 1)
    shutdown = asyncio.create_task(service.shutdown(timeout_seconds=0.05))
    await asyncio.sleep(0)
    assert not shutdown.done()
    if complete_before_deadline:
        release.set()
    await asyncio.wait_for(shutdown, 1)
    await asyncio.gather(resumed, return_exceptions=True)
    request = runtime.connection.execute("SELECT status FROM tool_requests").fetchone()
    assert request[0] == ("succeeded" if complete_before_deadline else "failed")
    assert runtime.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == int(
        complete_before_deadline
    )


@pytest.mark.asyncio
async def test_interrupted_shutdown_still_joins_graph_before_returning(runtime):
    entered = asyncio.Event()
    stopped = asyncio.Event()

    class BlockingModel:
        async def stream_agent(self, model, messages, tools):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield AssistantStreamDone()
            finally:
                stopped.set()

    service = runtime.make("native", BlockingModel())
    turn = asyncio.create_task(collect(service.start_turn("session", 1, [])))
    await asyncio.wait_for(entered.wait(), 1)
    shutdown = asyncio.create_task(service.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    assert stopped.is_set()
    await asyncio.gather(turn, return_exceptions=True)
