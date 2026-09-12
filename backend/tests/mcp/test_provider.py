from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import Client

from voxagent.db.migrations import migrate
from voxagent.mcp.capability import CapabilityIssuer, CapabilityVerifier
from voxagent.mcp.client import McpClientError, McpLocalClient
from voxagent.mcp.server import build_mcp_server
from voxagent.mcp.server_tools import ToolServices
from voxagent.tools.builtin import build_builtin_registry, builtin_definitions
from voxagent.tools.confirmation import ConfirmationService, arguments_sha256
from voxagent.tools.policy import AuthorizationDecision, PolicyContext
from voxagent.tools.registry import FrozenToolRegistryError
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import PermissionLevel, ToolCall

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
KEY = b"p" * 32


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def provider_module():
    assert importlib.util.find_spec("voxagent.mcp.provider"), "MCP provider is missing"
    return importlib.import_module("voxagent.mcp.provider")


@asynccontextmanager
async def runtime(connection, module):
    source = SimpleNamespace(search_knowledge=lambda query, limit: [{"content": query}])
    issuer = CapabilityIssuer(KEY)
    services = ToolServices(connection, source, now_utc=lambda: NOW)
    server = build_mcp_server(services, CapabilityVerifier(connection, KEY))
    wire = []

    @asynccontextmanager
    async def factory(*args, **kwargs):
        async with Client(server, raise_exceptions=True) as session:

            async def call_tool(name, arguments):
                wire.append((name, arguments.copy()))
                return await session.call_tool(name, arguments)

            yield SimpleNamespace(list_tools=session.list_tools, call_tool=call_tool)

    client = McpLocalClient(Path("."), issuer, client_factory=factory)
    try:
        discovered = await client.start()
        provider = module.McpToolProvider(
            client, issuer, connection, discovered, now_utc=lambda: NOW
        )
        registry = module.build_mcp_registry(connection, source, None, provider)
        yield SimpleNamespace(
            registry=registry,
            provider=provider,
            client=client,
            wire=wire,
            discovered=discovered,
            source=source,
        )
    finally:
        await client.close()


def authorize(connection, registry, name, arguments, *, approved=True, call_id="agent-call"):
    definition = registry.get(name)[0]
    session = definition.provider
    call = ToolCall.from_untrusted(definition, call_id, arguments)
    repository = ToolRepository(connection)
    if definition.permission is PermissionLevel.L0:
        digest = arguments_sha256(call.arguments)
        request = repository.create_request(session, 1, call, definition.permission, digest, NOW)
        assert repository.start_request(request.id, digest, NOW)
        return call, request.id
    confirmation = ConfirmationService(repository, registry)
    ticket = confirmation.request(call, PolicyContext(session, 1, (), 0, 0, False), NOW)
    if approved:
        call = confirmation.approve(ticket.confirmation_id, session, 1, NOW)
    return call, ticket.tool_request_id


@pytest.mark.asyncio
async def test_native_confirmation_keeps_datetime_business_arguments_json_safe(database):
    registry = build_builtin_registry(database, None, None, now_utc=lambda: NOW)
    call, _ = authorize(
        database,
        registry,
        "reminders.create",
        {"title": "Time", "due_at_utc": "2026-09-12T08:00:00+08:00"},
    )
    assert isinstance(call.arguments["due_at_utc"], str)
    result = await registry.execute(call)
    assert result.status == "succeeded"
    assert result.data["reminder"]["due_at_utc"] == "2026-09-12T00:00:00.000Z"


@pytest.mark.asyncio
async def test_mcp_registry_has_six_frozen_tools_and_no_capability_in_model_schema(
    database,
    provider_module,
):
    async with runtime(database, provider_module) as current:
        payloads = current.registry.definition_payloads()
        native = {item.name: item for item in builtin_definitions()}
        assert {item["function"]["name"] for item in payloads} == set(native)
        assert "_voxagent_capability" not in json.dumps(payloads)
        for payload in payloads:
            definition = current.registry.get(payload["function"]["name"])[0]
            assert definition.permission == native[definition.name].permission
            assert (
                payload["function"]["parameters"]
                == native[definition.name].ollama_payload()["function"]["parameters"]
            )
            assert definition.provider == (
                "native"
                if definition.name in {"files.search_authorized", "apps.open_allowlisted"}
                else "mcp"
            )
        with pytest.raises(FrozenToolRegistryError):
            current.registry.register(native["reminders.list"], None)
        assert "_voxagent_capability" in json.dumps(
            [item.input_schema for item in current.discovered]
        )
        with pytest.raises(McpClientError, match="mcp_discovery_rejected"):
            provider_module.McpToolProvider(
                current.client, CapabilityIssuer(KEY), database, current.discovered[:-1]
            )
        changed = tuple(
            replace(item, permission=PermissionLevel.L0)
            if item.name == "reminders.create"
            else item
            for item in current.discovered
        )
        with pytest.raises(McpClientError, match="mcp_discovery_rejected"):
            provider_module.McpToolProvider(
                current.client, CapabilityIssuer(KEY), database, changed
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("due", [None, "2026-09-12T08:00:00+08:00"])
async def test_mcp_write_preserves_agent_call_id_business_fields_and_signed_wire_hash(
    database,
    provider_module,
    due,
):
    async with runtime(database, provider_module) as current:
        call, request_id = authorize(
            database, current.registry, "reminders.create", {"title": "Time", "due_at_utc": due}
        )
        before = call.model_dump(mode="json")
        result = await current.registry.execute(
            call, AuthorizationDecision("execute", "confirmation_approved", request_id)
        )
        assert result.status == "succeeded"
        assert result.call_id == "agent-call"
        assert set(result.data["reminder"]) == {
            "id",
            "title",
            "due_at_utc",
            "status",
            "created_at_utc",
            "completed_at_utc",
        }
        assert result.data["reminder"]["due_at_utc"] == (
            None if due is None else "2026-09-12T00:00:00.000Z"
        )
        assert call.model_dump(mode="json") == before
        assert len(current.wire) == 1
        arguments = current.wire[0][1]
        assert isinstance(arguments.pop("_voxagent_capability"), str)
        assert arguments == before["arguments"]
        record = database.execute(
            "SELECT * FROM tool_requests WHERE id = ?", (request_id,)
        ).fetchone()
        assert arguments_sha256(arguments) == record["arguments_sha256"]
        assert record["status"] == "succeeded"
        assert database.execute("SELECT COUNT(*) FROM mcp_capability_nonces").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("knowledge.search", {"query": "local"}),
        ("reminders.list", {}),
        ("reminders.complete", {"reminder_id": 999}),
    ],
)
async def test_native_and_mcp_business_data_and_stable_errors_match(
    database,
    provider_module,
    name,
    arguments,
):
    async with runtime(database, provider_module) as current:
        native = build_builtin_registry(database, current.source, None, now_utc=lambda: NOW)
        native_call, _ = authorize(database, native, name, arguments)
        expected = await native.execute(native_call)
        call, request_id = authorize(database, current.registry, name, arguments)
        actual = await current.registry.execute(
            call, AuthorizationDecision("execute", "tool_execute", request_id)
        )
        assert (
            actual.call_id,
            actual.tool_name,
            actual.status,
            actual.data,
            actual.error_code,
        ) == (
            expected.call_id,
            expected.tool_name,
            expected.status,
            expected.data,
            expected.error_code,
        )
        assert len(current.wire) == 1
        if name != "reminders.complete":
            assert "_voxagent_capability" not in current.wire[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["deny", "confirm", "pending", "expired", "cancelled", "wrong_id"]
)
async def test_unapproved_or_inactive_authorization_never_calls_client(
    database,
    provider_module,
    state,
):
    async with runtime(database, provider_module) as current:
        call, request_id = authorize(
            database,
            current.registry,
            "reminders.create",
            {"title": "blocked"},
            approved=state != "pending",
        )
        if state in {"expired", "cancelled"}:
            ToolRepository(database).finish_request(
                request_id, "expired" if state == "expired" else "failed", NOW
            )
        decision = AuthorizationDecision(
            state if state in {"deny", "confirm"} else "execute",
            "tool_execute",
            request_id + 1 if state == "wrong_id" else request_id,
        )
        result = await current.provider.execute(call, decision)
        assert result.status != "succeeded"
        assert current.wire == []
        assert database.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_write_transport_failure_is_not_retried(database, provider_module):
    async with runtime(database, provider_module) as current:
        calls = []

        async def failed_call(name, arguments):
            calls.append(name)
            raise McpClientError("mcp_connection_closed")

        current.client.call = failed_call
        call, request_id = authorize(
            database, current.registry, "reminders.create", {"title": "once"}
        )
        result = await current.provider.execute(
            call, AuthorizationDecision("execute", "tool_execute", request_id)
        )
        assert result.error_code == "mcp_connection_closed"
        assert calls == ["reminders.create"]
