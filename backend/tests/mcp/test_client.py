from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import psutil
import pytest
from mcp import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    REQUEST_TIMEOUT,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from voxagent.mcp.capability import CapabilityIssuer
from voxagent.tools.schema import PermissionLevel, ToolResult

NAMES = ("knowledge.search", "reminders.list", "reminders.create", "reminders.complete")
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_client_module_exists():
    assert importlib.util.find_spec("voxagent.mcp.client"), "MCP client is not implemented"


def page(names=NAMES, cursor=None):
    return ListToolsResult(
        tools=[
            Tool(name=name, description=name, input_schema={"type": "object"}) for name in names
        ],
        next_cursor=cursor,
    )


def result(data=None, **kwargs):
    return CallToolResult(
        content=[TextContent(type="text", text="ok")],
        structured_content={"ok": True} if data is None else data,
        **kwargs,
    )


class InProcessClient:
    def __init__(
        self, *, pages=None, response=None, failure=None, startup=None, delay=0, exit_failure=None
    ):
        self.pages = pages or [page()]
        self.response = response if response is not None else result()
        self.failure = failure
        self.startup = startup
        self.delay = delay
        self.exit_failure = exit_failure
        self.cursors = []
        self.calls = []
        self.entries = 0
        self.exits = 0
        self.owner = None

    async def __aenter__(self):
        self.entries += 1
        self.owner = asyncio.current_task()
        if self.startup:
            raise self.startup
        return self

    async def __aexit__(self, *args):
        assert asyncio.current_task() is self.owner
        self.exits += 1
        if self.exit_failure:
            raise self.exit_failure

    async def list_tools(self, *, cursor=None):
        self.cursors.append(cursor)
        return self.pages[min(len(self.cursors) - 1, len(self.pages) - 1)]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return self.response


class Factory:
    def __init__(self, *clients):
        self.clients = clients or (InProcessClient(),)
        self.parameters = []

    def __call__(self, parameters, *, read_timeout_seconds):
        self.parameters.append((parameters, read_timeout_seconds))
        return self.clients[min(len(self.parameters) - 1, len(self.clients) - 1)]


@pytest.fixture
def module():
    assert importlib.util.find_spec("voxagent.mcp.client"), "MCP client is not implemented"
    return importlib.import_module("voxagent.mcp.client")


def local(module, tmp_path, factory):
    return module.McpLocalClient(tmp_path, CapabilityIssuer(b"k" * 32), client_factory=factory)


@pytest.mark.asyncio
async def test_one_lifetime_fixed_launch_and_paginated_discovery(module, tmp_path, monkeypatch):
    monkeypatch.setenv("UNTRUSTED_SECRET", "must not pass")
    fake = InProcessClient(pages=[page(NAMES[:2], "next"), page(NAMES[2:])])
    factory = Factory(fake)
    client = local(module, tmp_path, factory)
    discovered = await asyncio.create_task(client.start())
    assert await client.start() == discovered
    assert tuple(tool.name for tool in discovered) == NAMES
    assert [tool.permission for tool in discovered] == [
        PermissionLevel.L0,
        PermissionLevel.L0,
        PermissionLevel.L2,
        PermissionLevel.L2,
    ]
    assert discovered[0].input_schema == {"type": "object"}
    assert fake.cursors == [None, "next"]
    assert await client.call("reminders.list", {}) == {"ok": True}
    assert await client.call("reminders.list", {}) == {"ok": True}
    await asyncio.create_task(client.close())
    await client.close()
    assert fake.entries == fake.exits == len(factory.parameters) == 1
    parameters, timeout = factory.parameters[0]
    assert parameters.command == sys.executable
    assert parameters.args == ["-m", "voxagent.mcp.server"]
    assert Path(parameters.cwd) == BACKEND_ROOT
    assert parameters.env == {
        "VOXAGENT_DATA_ROOT": str(tmp_path),
        "VOXAGENT_MCP_CAPABILITY_KEY": (b"k" * 32).hex(),
        "LANGGRAPH_STRICT_MSGPACK": "true",
    }
    assert timeout == 5.0
    assert parameters.encoding == "utf-8"
    assert parameters.encoding_error_handler == "strict"
    with pytest.raises(module.McpClientError, match="closed"):
        await client.call("reminders.list", {})
    with pytest.raises(module.McpClientError, match="closed"):
        await client.start()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [page(["unknown.tool"])],
        [page([NAMES[0]], "next"), page([NAMES[0]])],
        [page([], "forever")],
        [page([NAMES[0]] * 101)],
    ],
)
async def test_discovery_rejects_unknown_duplicates_and_limits(module, tmp_path, pages):
    fake = InProcessClient(pages=pages)
    factory = Factory(fake)
    client = local(module, tmp_path, factory)
    with pytest.raises(module.McpClientError, match="discovery"):
        await client.start()
    await client.close()
    assert fake.exits == 1
    assert len(fake.cursors) <= 10
    assert len(factory.parameters) == 1


@pytest.mark.asyncio
async def test_exactly_ten_pages_are_allowed(module, tmp_path):
    fake = InProcessClient(pages=[page([], str(i)) for i in range(9)] + [page()])
    client = local(module, tmp_path, Factory(fake))
    assert len(await client.start()) == 4
    await client.close()
    assert len(fake.cursors) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exit_failure,code",
    [
        (RuntimeError("private shutdown detail"), "mcp_server_error"),
        (MCPError(CONNECTION_CLOSED, "closed"), "mcp_connection_closed"),
        (MCPError(REQUEST_TIMEOUT, "timeout"), "mcp_timeout"),
    ],
)
async def test_shutdown_failure_surfaces_and_repeated_close_is_safe(
    module, tmp_path, exit_failure, code
):
    fake = InProcessClient(exit_failure=exit_failure)
    client = local(module, tmp_path, Factory(fake))
    await client.start()
    with pytest.raises(module.McpClientError, match=code):
        await client.close()
    await client.close()
    assert fake.entries == fake.exits == 1


@pytest.mark.asyncio
async def test_explicit_start_failure_is_stable_until_close(module, tmp_path):
    factory = Factory(InProcessClient(startup=OSError("spawn failed")))
    client = local(module, tmp_path, factory)
    for _ in range(2):
        with pytest.raises(module.McpClientError, match="mcp_startup_failed"):
            await client.start()
    await client.close()
    await client.close()
    assert len(factory.parameters) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        result(is_error=True),
        result(data=[]),
        CallToolResult(content=[], structured_content=None),
        result().model_copy(update={"content": [{"type": "future"}]}),
        result(data={"value": "汉" * 30_000}),
    ],
)
async def test_invalid_result_is_rejected_without_retry(module, tmp_path, response):
    fake = InProcessClient(response=response)
    factory = Factory(fake)
    client = local(module, tmp_path, factory)
    with pytest.raises(module.McpClientError):
        await client.call("reminders.list", {})
    await client.close()
    assert len(factory.parameters) == len(fake.calls) == 1


@pytest.mark.asyncio
async def test_compact_utf8_result_size_boundary(module, tmp_path):
    data = {"v": "x" * (65_536 - len('{"v":""}'))}
    assert len(json.dumps(data, separators=(",", ":")).encode()) == 65_536
    fake = InProcessClient(response=result(data=data))
    client = local(module, tmp_path, Factory(fake))
    assert await client.call("reminders.list", {}) == data
    fake.response = result(data={"v": data["v"] + "x"})
    with pytest.raises(module.McpClientError, match="result"):
        await client.call("reminders.list", {})
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        ("not", "json"),
        {1: "not a string key"},
        {None: "not a string key"},
        {False: "not a string key"},
        float("nan"),
        float("inf"),
        float("-inf"),
        b"not json",
        {"not", "json"},
    ],
)
async def test_results_require_recursive_json_values(module, tmp_path, value):
    response = result().model_copy(update={"structured_content": {"nested": [value]}})
    fake = InProcessClient(response=response)
    client = local(module, tmp_path, Factory(fake))
    try:
        with pytest.raises(module.McpClientError, match="^mcp_result_object$"):
            await client.call("reminders.list", {})
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_success_result_is_a_detached_json_roundtrip(module, tmp_path):
    response = result(data={"nested": [{"values": [None, True, False, 1, 1.5, "汉字"]}]})
    fake = InProcessClient(response=response)
    client = local(module, tmp_path, Factory(fake))
    try:
        parsed = await client.call("reminders.list", {})
        assert parsed == response.structured_content
        assert parsed is not response.structured_content
        parsed["nested"][0]["values"].append("local mutation")
        assert "local mutation" not in response.structured_content["nested"][0]["values"]
    finally:
        await client.close()


def business_error(**updates):
    return {
        "call_id": "remote-private-call-id",
        "tool_name": "reminders.complete",
        "status": "failed",
        "data": {"credential": "private-child-credential"},
        "user_summary": "private-server-summary",
        "error_code": "reminder_not_open",
        "duration_ms": 17,
        **updates,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status,summary",
    [
        ("mcp_capability_rejected", "denied", "Capability rejected."),
        ("invalid_arguments", "failed", "Invalid tool arguments."),
        ("reminder_not_open", "failed", "Reminder is not open."),
        ("tool_execution_failed", "failed", "Tool execution failed."),
        ("unknown_tool", "failed", "Unknown tool."),
    ],
)
async def test_business_errors_propagate_only_sanitized_tool_result(
    module, tmp_path, code, status, summary
):
    fake = InProcessClient(
        response=result(data=business_error(error_code=code, status=status), is_error=True)
    )
    factory = Factory(fake)
    client = local(module, tmp_path, factory)
    try:
        with pytest.raises(module.McpClientError) as raised:
            await client.call("reminders.complete", {})
        safe_result = getattr(raised.value, "result", None)
        assert isinstance(safe_result, ToolResult)
        assert isinstance(raised.value, module.McpToolError)
        assert raised.value.code == code
        assert str(raised.value) == code
        assert safe_result.model_dump() == {
            "call_id": "",
            "tool_name": "reminders.complete",
            "status": status,
            "data": {},
            "user_summary": summary,
            "error_code": code,
            "duration_ms": 17,
        }
        assert len(factory.parameters) == len(fake.calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [
        {"tool_name": "private-unrecognized-tool"},
        {"status": "succeeded"},
        {"status": "pending"},
        {"error_code": "private-child-credential"},
        {"error_code": None},
        {"duration_ms": -1},
        {"duration_ms": 300_001},
        {"duration_ms": True},
        {"duration_ms": "17"},
        {"data": []},
        {"call_id": []},
        {"user_summary": []},
        {"extra": "private-child-credential"},
    ],
)
async def test_untrusted_business_error_envelopes_stay_generic(module, tmp_path, updates):
    fake = InProcessClient(response=result(data=business_error(**updates), is_error=True))
    client = local(module, tmp_path, Factory(fake))
    try:
        with pytest.raises(module.McpClientError) as raised:
            await client.call("reminders.complete", {})
        assert type(raised.value) is module.McpClientError
        assert str(raised.value) == "mcp_tool_error"
        assert not hasattr(raised.value, "result")
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,code",
    [
        (
            {"content": [{"type": "future", "text": "private-child-credential"}]},
            "mcp_result_content",
        ),
        ({"structured_content": None}, "mcp_result_object"),
        ({"structured_content": business_error(data={"bad": (1, 2)})}, "mcp_result_object"),
        ({"structured_content": business_error(data={"bad": {1: "value"}})}, "mcp_result_object"),
        ({"structured_content": business_error(data={"bad": float("inf")})}, "mcp_result_object"),
        (
            {"structured_content": business_error(data={"large": "汉" * 30_000})},
            "mcp_result_too_large",
        ),
    ],
)
async def test_business_errors_pass_content_json_and_size_checks(module, tmp_path, updates, code):
    response = result(data=business_error(), is_error=True).model_copy(update=updates)
    fake = InProcessClient(response=response)
    client = local(module, tmp_path, Factory(fake))
    try:
        with pytest.raises(module.McpClientError) as raised:
            await client.call("reminders.complete", {})
        assert type(raised.value) is module.McpClientError
        assert str(raised.value) == code
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,startup",
    [
        (MCPError(CONNECTION_CLOSED, "closed"), None),
        (MCPError(REQUEST_TIMEOUT, "timeout"), None),
        (None, OSError("spawn failed")),
    ],
)
async def test_l0_rebuilds_once_on_only_transient_errors(module, tmp_path, failure, startup):
    first = InProcessClient(failure=failure, startup=startup)
    second = InProcessClient()
    factory = Factory(first, second)
    client = local(module, tmp_path, factory)
    assert await client.call("reminders.list", {}) == {"ok": True}
    await client.close()
    assert len(factory.parameters) == 2
    assert second.entries == second.exits == 1
    assert first.exits == (0 if startup else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [CONNECTION_CLOSED, REQUEST_TIMEOUT])
async def test_l0_rebuild_continues_after_transient_shutdown_error(module, tmp_path, exit_code):
    first = InProcessClient(
        failure=MCPError(REQUEST_TIMEOUT, "call timed out"),
        exit_failure=MCPError(exit_code, "old connection shutdown failed"),
    )
    second = InProcessClient()
    factory = Factory(first, second)
    client = local(module, tmp_path, factory)
    try:
        assert await client.call("reminders.list", {}) == {"ok": True}
        assert len(factory.parameters) == 2
        assert first.exits == 1
        assert len(first.calls) == len(second.calls) == 1
    finally:
        await client.close()
    assert second.exits == 1


@pytest.mark.asyncio
async def test_l0_recovery_propagates_nontransient_shutdown_error(module, tmp_path):
    first = InProcessClient(
        failure=MCPError(REQUEST_TIMEOUT, "call timed out"),
        exit_failure=MCPError(INTERNAL_ERROR, "shutdown failed"),
    )
    factory = Factory(first, InProcessClient())
    client = local(module, tmp_path, factory)
    with pytest.raises(module.McpClientError, match="mcp_server_error"):
        await client.call("reminders.list", {})
    await client.close()
    assert len(factory.parameters) == first.exits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,failure,startup,attempts",
    [
        ("reminders.list", MCPError(REQUEST_TIMEOUT, "timeout"), None, 2),
        ("reminders.create", MCPError(REQUEST_TIMEOUT, "timeout"), None, 1),
        ("reminders.complete", MCPError(CONNECTION_CLOSED, "closed"), None, 1),
        ("reminders.create", None, OSError("spawn failed"), 1),
        ("reminders.list", MCPError(INTERNAL_ERROR, "private details"), None, 1),
        ("reminders.list", ValueError("private details"), None, 1),
        ("reminders.list", OSError("not a spawn failure"), None, 1),
    ],
)
async def test_retry_budget_and_writes_never_retry(
    module,
    tmp_path,
    name,
    failure,
    startup,
    attempts,
):
    factory = Factory(*[InProcessClient(failure=failure, startup=startup) for _ in range(2)])
    client = local(module, tmp_path, factory)
    with pytest.raises(module.McpClientError) as raised:
        await client.call(name, {})
    assert "private details" not in str(raised.value)
    await client.close()
    assert len(factory.parameters) == attempts


@pytest.mark.asyncio
async def test_unknown_or_undiscovered_call_never_reaches_server(module, tmp_path):
    fake = InProcessClient(pages=[page(["reminders.list"])])
    client = local(module, tmp_path, Factory(fake))
    for name in ("unknown.tool", "knowledge.search"):
        with pytest.raises(module.McpClientError, match="unknown_tool"):
            await client.call(name, {})
    await client.close()
    assert not fake.calls


@pytest.mark.asyncio
async def test_five_second_call_deadline_without_write_retry(module, tmp_path):
    fake = InProcessClient(delay=60)
    factory = Factory(fake)
    client = local(module, tmp_path, factory)
    before = time.monotonic()
    with pytest.raises(module.McpClientError, match="timeout"):
        await client.call("reminders.create", {})
    elapsed = time.monotonic() - before
    await client.close()
    assert 4.8 <= elapsed < 8
    assert len(fake.calls) == len(factory.parameters) == 1


@pytest.mark.asyncio
async def test_real_stdio_discovery_list_and_process_cleanup(module, tmp_path):
    # Ordinary integration smoke: no model, GPU, microphone, or optional service.
    client = module.McpLocalClient(tmp_path, CapabilityIssuer(b"k" * 32))
    children = []
    try:
        discovered = await asyncio.create_task(client.start())
        assert {tool.name for tool in discovered} == set(NAMES)
        children = [
            process
            for process in psutil.Process().children(recursive=True)
            if "voxagent.mcp.server" in process.cmdline()
        ]
        assert children
        response = await client.call("reminders.list", {})
        assert response["status"] == "succeeded"
        assert response["data"] == {"reminders": []}
    finally:
        await asyncio.create_task(client.close())
    assert all(not process.is_running() for process in children)
