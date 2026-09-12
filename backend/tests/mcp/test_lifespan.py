from __future__ import annotations

import base64
import json
import sqlite3
from types import SimpleNamespace

import pytest
from mcp.types import ListToolsResult, Tool

from voxagent import cli
from voxagent.mcp.client import McpClientError, McpLocalClient
from voxagent.tools.builtin import builtin_definitions
from voxagent.tools.registry import FrozenToolRegistryError

SESSION_TOKEN = base64.urlsafe_b64encode(b"x" * 32).decode("ascii").rstrip("=")
REMOTE_NAMES = {"knowledge.search", "reminders.list", "reminders.create", "reminders.complete"}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    baseline = tmp_path / "benchmarks" / "target-machine-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(
        json.dumps({
            "selection": {"selected_model": "local-test-model"},
            "asr_candidates": [{"partial_model_id": "sensevoice-int8"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("VOXAGENT_TOOL_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_production_catalog", lambda: object())
    monkeypatch.setattr(cli.BgeSmallZhEmbedder, "from_path", lambda _: object())
    state = SimpleNamespace(
        events=[], agents=[], clients=[], registries=[], checkpoints=[], app=None,
        startup_error=None, shutdown_error=None, transport_entries=0, transport_exits=0,
        discovery_names=REMOTE_NAMES,
    )

    class Transport:
        async def __aenter__(self):
            state.transport_entries += 1
            state.events.append("mcp.start")
            if state.startup_error is not None:
                raise state.startup_error
            return self

        async def __aexit__(self, *_):
            state.transport_exits += 1
            state.events.append("mcp.close")
            assert state.app.state.plan3_database.execute("SELECT 1").fetchone()[0] == 1
            assert not state.app.state.ollama_http.is_closed
            if state.shutdown_error == "mcp":
                raise RuntimeError("transport close failed")

        async def list_tools(self, *, cursor=None):
            return ListToolsResult(tools=[
                Tool(
                    name=item.name,
                    description=item.description,
                    input_schema=item.arguments_model.model_json_schema(),
                )
                for item in builtin_definitions() if item.name in state.discovery_names
            ])

    def local_client(root, issuer):
        client = McpLocalClient(root, issuer, client_factory=lambda *args, **kwargs: Transport())
        state.clients.append(client)
        return client

    monkeypatch.setattr(cli, "McpLocalClient", local_client, raising=False)
    real_agent = cli.AgentService

    class ObservedAgent(real_agent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            state.agents.append(self)
            state.registries.append(kwargs["registry"])
            state.events.append("agent.start")

        async def shutdown(self, timeout_seconds=3.0):
            state.events.append("agent.shutdown")
            await super().shutdown(timeout_seconds)
            if state.shutdown_error == "agent":
                raise RuntimeError("agent shutdown failed")

    monkeypatch.setattr(cli, "AgentService", ObservedAgent)
    real_connect = cli.aiosqlite.connect

    async def connect(*args, **kwargs):
        connection = await real_connect(*args, **kwargs)
        state.checkpoints.append(connection)
        real_close = connection.close

        async def close():
            state.events.append("checkpoint.close")
            await real_close()
            if state.shutdown_error == "checkpoint":
                raise RuntimeError("checkpoint close failed")

        connection.close = close
        return connection

    monkeypatch.setattr(cli.aiosqlite, "connect", connect)
    real_backup = cli.DailyBackupManager.create

    def backup(manager, database, day):
        state.events.append("database.backup")
        if state.shutdown_error == "backup":
            raise RuntimeError("database backup failed")
        return real_backup(manager, database, day)

    monkeypatch.setattr(cli.DailyBackupManager, "create", backup)

    def create(provider):
        state.app = cli._create_production_app(SESSION_TOKEN, tool_provider=provider)
        http = state.app.state.ollama_http
        real_close = http.aclose

        async def close():
            state.events.append("http.close")
            await real_close()
            if state.shutdown_error == "http":
                raise RuntimeError("http close failed")

        http.aclose = close
        return state.app

    state.create = create
    return state


async def assert_resources_closed(runtime):
    assert runtime.app.state.ollama_http.is_closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        runtime.app.state.plan3_database.execute("SELECT 1")
    for connection in runtime.checkpoints:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["mcp", None])
async def test_mcp_lifespan_starts_client_and_registers_exactly_six_tools(
    runtime, monkeypatch, provider
):
    monkeypatch.setenv("VOXAGENT_TOOL_PROVIDER", "mcp")
    application = runtime.create(provider)
    assert runtime.transport_entries == 0

    async with application.router.lifespan_context(application):
        assert runtime.transport_entries == 1
        assert len(runtime.agents) == len(runtime.clients) == 1
        registry = runtime.registries[0]
        assert len(registry.definition_payloads()) == 6
        for definition in builtin_definitions():
            selected, executor = registry.get(definition.name)
            assert selected.provider == ("mcp" if definition.name in REMOTE_NAMES else "native")
        with pytest.raises(FrozenToolRegistryError):
            registry.register(selected, executor)

    assert runtime.events == [
        "mcp.start", "agent.start", "agent.shutdown", "mcp.close",
        "http.close", "checkpoint.close", "database.backup",
    ]
    assert runtime.transport_exits == 1
    await assert_resources_closed(runtime)
    with pytest.raises(McpClientError, match="closed"):
        await runtime.clients[0].start()


@pytest.mark.asyncio
async def test_native_lifespan_shuts_agent_down_without_starting_mcp(runtime, monkeypatch):
    monkeypatch.setenv("VOXAGENT_TOOL_PROVIDER", "invalid")
    application = runtime.create("native")
    async with application.router.lifespan_context(application):
        assert len(runtime.agents) == 1
        assert all(
            runtime.registries[0].get(item.name)[0].provider == "native"
            for item in builtin_definitions()
        )

    assert runtime.clients == []
    assert runtime.events == [
        "agent.start", "agent.shutdown", "http.close", "checkpoint.close", "database.backup",
    ]
    await assert_resources_closed(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["spawn", "discovery"])
async def test_mcp_startup_failure_aborts_lifespan_and_closes_resources(runtime, failure):
    if failure == "spawn":
        runtime.startup_error = OSError("cannot start child")
        expected = "mcp_startup_failed"
    else:
        runtime.discovery_names = {"reminders.list"}
        expected = "mcp_discovery_rejected"
    application = runtime.create("mcp")

    with pytest.raises(McpClientError, match=expected):
        async with application.router.lifespan_context(application):
            pytest.fail("failed MCP startup must not serve requests")

    assert runtime.agents == []
    assert runtime.transport_entries == 1
    await assert_resources_closed(runtime)
    with pytest.raises(McpClientError, match="closed"):
        await runtime.clients[0].start()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["agent", "mcp", "http", "checkpoint", "backup"])
async def test_shutdown_error_still_closes_remaining_resources(runtime, failure):
    runtime.shutdown_error = failure
    application = runtime.create("mcp")

    with pytest.raises(RuntimeError):
        async with application.router.lifespan_context(application):
            pass

    assert runtime.events == [
        "mcp.start", "agent.start", "agent.shutdown", "mcp.close",
        "http.close", "checkpoint.close", "database.backup",
    ]
    await assert_resources_closed(runtime)
