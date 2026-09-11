from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mcp import Client
from mcp.server import MCPServer

from voxagent.db.migrations import migrate
from voxagent.mcp.capability import CapabilityIssuer, CapabilityVerifier
from voxagent.tools.confirmation import arguments_sha256
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
KEY = b"k" * 32


@pytest.fixture
def modules():
    assert importlib.util.find_spec("voxagent.mcp.server"), "MCP server is not implemented"
    return (
        importlib.import_module("voxagent.mcp.server"),
        importlib.import_module("voxagent.mcp.server_tools"),
    )


@pytest.fixture
def runtime(modules, monkeypatch):
    server_module, server_tools = modules
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    source = SimpleNamespace(search_knowledge=lambda query, limit: [{
        "chunk_id": 1, "document_id": 2, "display_name": "Notes", "content": query,
        "page_number": None, "score": 0.8, "source_path": r"C:\private\notes.txt",
    }])
    write_calls = []
    real_create = server_tools.build_reminder_create_executor

    def tracked_create(connection, now_utc):
        execute = real_create(connection, now_utc)

        async def tracked(call):
            write_calls.append(call.name)
            return await execute(call)

        return tracked

    monkeypatch.setattr(server_tools, "build_reminder_create_executor", tracked_create)
    services = server_tools.ToolServices(connection, source, now_utc=lambda: NOW)
    server = server_module.build_mcp_server(services, CapabilityVerifier(connection, KEY))
    yield SimpleNamespace(
        server=server, connection=connection, source=source, write_calls=write_calls
    )
    connection.close()


def approve(runtime, name, arguments, *, now=NOW):
    repository = ToolRepository(runtime.connection)
    digest = arguments_sha256(arguments)
    request = repository.create_request(
        "session", 1, ToolCall(call_id=name, name=name, arguments=arguments),
        PermissionLevel.L2, digest, now,
    )
    ticket = repository.create_confirmation(request.id, digest, now)
    assert repository.consume_confirmation(ticket.confirmation_id, digest, "session", 1, now)
    token = CapabilityIssuer(KEY).issue(request.id, name, arguments, now)
    return request.id, token


def payload(result):
    data = result.structured_content
    assert isinstance(data, dict)
    assert json.loads(result.content[0].text) == data
    return data


@pytest.mark.asyncio
async def test_discovery_has_only_approved_tools_and_flat_jr02_schemas(runtime):
    assert isinstance(runtime.server, MCPServer)
    assert runtime.server.name == "VoxAgent Local Tools"
    async with Client(runtime.server, raise_exceptions=True) as client:
        result = await client.list_tools()
    tools = {tool.name: tool for tool in result.tools}
    expected = {
        "knowledge.search": {"query", "limit"}, "reminders.list": set(),
        "reminders.create": {"title", "due_at_utc", "_voxagent_capability"},
        "reminders.complete": {"reminder_id", "_voxagent_capability"},
    }
    assert set(tools) == set(expected)
    for name, fields in expected.items():
        tool = tools[name]
        assert set(tool.input_schema["properties"]) == fields
        assert tool.annotations.read_only_hint == (name in {"knowledge.search", "reminders.list"})
        assert tool.annotations.open_world_hint is False
        assert tool.output_schema is not None
    assert tools["knowledge.search"].input_schema["properties"]["query"]["maxLength"] == 500
    assert tools["knowledge.search"].input_schema["properties"]["limit"]["maximum"] == 10
    assert tools["reminders.create"].input_schema["properties"]["title"]["maxLength"] == 200
    reminder_id_schema = tools["reminders.complete"].input_schema["properties"]["reminder_id"]
    assert reminder_id_schema["exclusiveMinimum"] == 0


@pytest.mark.asyncio
async def test_read_tools_need_no_capability_and_return_structured_safe_data(runtime, capsys):
    async with Client(runtime.server, raise_exceptions=True) as client:
        search = await client.call_tool("knowledge.search", {"query": "agent"})
        reminders = await client.call_tool("reminders.list", {})
    assert payload(search)["data"]["results"][0]["content"] == "agent"
    assert "private" not in search.model_dump_json()
    assert payload(reminders)["data"] == {"reminders": []}
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "forged", "expired"])
async def test_invalid_capability_never_reaches_write_executor(runtime, failure):
    arguments = {"title": "drink water", "due_at_utc": None}
    _, token = approve(runtime, "reminders.create", arguments, now=NOW - timedelta(seconds=31))
    call_arguments = dict(arguments)
    if failure != "missing":
        call_arguments["_voxagent_capability"] = token if failure == "expired" else "forged-secret"
    writes = []
    runtime.connection.set_trace_callback(writes.append)
    async with Client(runtime.server, raise_exceptions=True) as client:
        result = await client.call_tool("reminders.create", call_arguments)
    assert payload(result)["error_code"] == "mcp_capability_rejected"
    assert runtime.write_calls == []
    assert not any("INSERT INTO reminders" in statement for statement in writes)
    assert runtime.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
    assert token not in result.model_dump_json()
    assert "forged-secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_valid_writes_execute_once_and_finish_original_audit(runtime):
    arguments = {"title": "drink water", "due_at_utc": "2026-09-12T12:00:00Z"}
    request_id, token = approve(runtime, "reminders.create", arguments)
    async with Client(runtime.server, raise_exceptions=True) as client:
        wire_arguments = {**arguments, "_voxagent_capability": token}
        created = await client.call_tool("reminders.create", wire_arguments)
        replayed = await client.call_tool("reminders.create", wire_arguments)
        reminder_id = payload(created)["data"]["reminder"]["id"]
        complete_args = {"reminder_id": reminder_id}
        complete_id, complete_token = approve(runtime, "reminders.complete", complete_args)
        completed = await client.call_tool(
            "reminders.complete", {**complete_args, "_voxagent_capability": complete_token}
        )
    assert payload(created)["status"] == payload(completed)["status"] == "succeeded"
    assert payload(replayed)["error_code"] == "mcp_capability_rejected"
    assert runtime.write_calls == ["reminders.create"]
    assert runtime.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
    events = runtime.connection.execute(
        "SELECT tool_request_id FROM tool_audit WHERE event_type = 'request.succeeded' ORDER BY id"
    ).fetchall()
    assert [row[0] for row in events] == [request_id, complete_id]
    audit = json.dumps([
        tuple(row) for row in runtime.connection.execute("SELECT * FROM tool_audit")
    ])
    assert token not in audit and complete_token not in audit and KEY.hex() not in audit


@pytest.mark.asyncio
async def test_business_failure_has_stable_code_and_original_request_audit(runtime):
    arguments = {"reminder_id": 999}
    request_id, token = approve(runtime, "reminders.complete", arguments)
    async with Client(runtime.server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "reminders.complete", {**arguments, "_voxagent_capability": token}
        )
    assert payload(result)["error_code"] == "reminder_not_open"
    assert runtime.connection.execute(
        "SELECT status FROM tool_requests WHERE id = ?", (request_id,)
    ).fetchone()[0] == "failed"


@pytest.mark.asyncio
async def test_protocol_validation_does_not_echo_capability_or_paths(runtime):
    arguments = {"title": "drink water", "due_at_utc": None}
    _, token = approve(runtime, "reminders.create", arguments)
    async with Client(runtime.server, raise_exceptions=True) as client:
        result = await client.call_tool("reminders.create", {
            "title": {"sqlite_path": r"C:\private\voxagent.db", "secret": KEY.hex()},
            "_voxagent_capability": token,
        })
    assert payload(result)["error_code"] == "invalid_arguments"
    wire = result.model_dump_json()
    assert all(value not in wire for value in [token, KEY.hex(), "private", "Traceback"])
    count = runtime.connection.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_unexpected_business_exception_is_sanitized(runtime):
    def fail(query, limit):
        raise sqlite3.OperationalError(r"C:\private\voxagent.db api_key=secret Traceback")

    runtime.source.search_knowledge = fail
    async with Client(runtime.server, raise_exceptions=True) as client:
        result = await client.call_tool("knowledge.search", {"query": "agent"})
    assert payload(result)["error_code"] == "tool_execution_failed"
    wire = result.model_dump_json()
    assert all(value not in wire for value in ["private", "secret", "Traceback"])


@pytest.mark.parametrize("missing", ["VOXAGENT_DATA_ROOT", "VOXAGENT_MCP_CAPABILITY_KEY"])
def test_main_requires_explicit_environment_without_stdout(modules, monkeypatch, capsys, missing):
    server_module, _ = modules
    monkeypatch.setenv("VOXAGENT_DATA_ROOT", "unused")
    monkeypatch.setenv("VOXAGENT_MCP_CAPABILITY_KEY", KEY.hex())
    monkeypatch.delenv(missing)
    assert server_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "mcp_configuration_error\n"


def test_module_import_has_no_database_stdout_or_server_side_effects():
    assert importlib.util.find_spec("voxagent.mcp.server"), "MCP server is not implemented"
    code = """
import sqlite3
from mcp.server import MCPServer
def forbidden(*args, **kwargs):
    raise AssertionError('import side effect')
sqlite3.connect = forbidden
MCPServer.run = forbidden
import voxagent.mcp.server
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_main_builds_dependencies_runs_default_stdio_and_closes_database(
    modules, monkeypatch, tmp_path, capsys
):
    server_module, _ = modules
    from voxagent.db import connection as connection_module
    from voxagent.memory.embedder import BgeSmallZhEmbedder

    monkeypatch.setenv("VOXAGENT_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("VOXAGENT_MCP_CAPABILITY_KEY", KEY.hex())
    monkeypatch.setattr(BgeSmallZhEmbedder, "from_path", lambda path: object())
    seen = []
    connections = []
    real_open = connection_module.open_database

    def open_tracked(path):
        connection = real_open(path)
        connections.append(connection)
        return connection

    def run(server):
        seen.append(server.name)

    monkeypatch.setattr(MCPServer, "run", run)
    monkeypatch.setattr(connection_module, "open_database", open_tracked)
    assert server_module.main() == 0
    assert seen == ["VoxAgent Local Tools"]
    assert (tmp_path / "data" / "voxagent.db").is_file()
    assert capsys.readouterr().out == ""
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
