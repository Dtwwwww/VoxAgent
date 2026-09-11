from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import anyio
from mcp import Client, MCPError, StdioServerParameters
from mcp.types import (
    CONNECTION_CLOSED,
    REQUEST_TIMEOUT,
    CallToolResult,
    ListToolsResult,
    TextContent,
)

from voxagent.mcp.capability import CapabilityIssuer
from voxagent.mcp.models import McpDiscoveredTool, McpProviderConfig
from voxagent.tools.schema import PermissionLevel

_PERMISSIONS = {
    "knowledge.search": PermissionLevel.L0,
    "reminders.list": PermissionLevel.L0,
    "reminders.create": PermissionLevel.L2,
    "reminders.complete": PermissionLevel.L2,
}
_TRANSIENT = frozenset({"mcp_startup_failed", "mcp_connection_closed", "mcp_timeout"})


class McpClientError(RuntimeError):
    """Fixed client errors never expose server output or child credentials."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Session(Protocol):
    async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult: ...


ClientFactory = Callable[..., AbstractAsyncContextManager[_Session]]


@dataclass(slots=True)
class _Request:
    name: str
    arguments: dict[str, object]
    result: asyncio.Future[dict[str, object]]


def _failure(error: BaseException, *, starting: bool = False) -> McpClientError:
    if isinstance(error, McpClientError):
        return error
    if isinstance(error, BaseExceptionGroup):
        errors = [_failure(item, starting=starting) for item in error.exceptions]
        if len({item.code for item in errors}) == 1:
            return errors[0]
    if isinstance(error, TimeoutError):
        return McpClientError("mcp_timeout")
    if isinstance(error, MCPError):
        if error.code == REQUEST_TIMEOUT:
            return McpClientError("mcp_timeout")
        if error.code == CONNECTION_CLOSED:
            return McpClientError("mcp_connection_closed")
    if isinstance(error, (anyio.ClosedResourceError, anyio.BrokenResourceError, ConnectionError)):
        return McpClientError("mcp_connection_closed")
    if starting and isinstance(error, OSError):
        return McpClientError("mcp_startup_failed")
    return McpClientError("mcp_server_error")


class McpLocalClient:
    """One owner task holds the SDK context from startup through application shutdown.

    Public operations may run in different application tasks. The SDK's AnyIO
    cancel scopes are always entered and exited by the same owner task.
    """

    def __init__(
        self,
        data_root: Path,
        capability_issuer: CapabilityIssuer,
        *,
        client_factory: ClientFactory = Client,
    ) -> None:
        self._config = McpProviderConfig.local_default()
        self._parameters = StdioServerParameters(
            command=self._config.command,
            args=list(self._config.args),
            cwd=self._config.cwd,
            env={
                "VOXAGENT_DATA_ROOT": str(data_root),
                "VOXAGENT_MCP_CAPABILITY_KEY": capability_issuer.child_environment()[
                    "VOXAGENT_MCP_CAPABILITY_KEY"
                ],
                "LANGGRAPH_STRICT_MSGPACK": "true",
            },
            encoding="utf-8",
            encoding_error_handler="strict",
        )
        self._factory = client_factory
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[_Request | None] = asyncio.Queue()
        self._owner: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[tuple[McpDiscoveredTool, ...]] | None = None
        self._tools: tuple[McpDiscoveredTool, ...] = ()
        self._error: McpClientError | None = None
        self._closed = False

    async def start(self) -> tuple[McpDiscoveredTool, ...]:
        async with self._lock:
            return await self._start()

    async def _start(self) -> tuple[McpDiscoveredTool, ...]:
        if self._closed:
            raise McpClientError("mcp_client_closed")
        if self._owner is None:
            self._ready = asyncio.get_running_loop().create_future()
            self._owner = asyncio.create_task(self._run(), name="voxagent-mcp-client")
        if self._error is not None:
            raise self._error
        assert self._ready is not None
        return await asyncio.shield(self._ready)

    async def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        async with self._lock:
            if self._closed:
                raise McpClientError("mcp_client_closed")
            if name not in _PERMISSIONS:
                raise McpClientError("mcp_unknown_tool")
            for attempt in range(2):
                try:
                    tools = await self._start()
                    if name not in {tool.name for tool in tools}:
                        raise McpClientError("mcp_unknown_tool")
                    future = asyncio.get_running_loop().create_future()
                    self._queue.put_nowait(_Request(name, arguments, future))
                    return await asyncio.shield(future)
                except McpClientError as error:
                    if (
                        attempt == 1
                        or _PERMISSIONS[name] is not PermissionLevel.L0
                        or error.code not in _TRANSIENT
                    ):
                        raise
                    await self._stop(recovering=True)
            raise AssertionError("unreachable")

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._stop()

    async def _stop(self, *, recovering: bool = False) -> None:
        shutdown_error = None
        if self._owner is not None:
            if not self._owner.done():
                self._queue.put_nowait(None)
            await asyncio.shield(self._owner)
            if self._ready is not None and self._ready.exception() is None:
                shutdown_error = self._error
        self._owner = None
        self._ready = None
        self._tools = ()
        self._error = None
        self._queue = asyncio.Queue()
        # The owner has exited and state is cleared before a recovery can proceed.
        # Explicit close still reports every shutdown error to its caller.
        if shutdown_error is not None and not (recovering and shutdown_error.code in _TRANSIENT):
            raise shutdown_error

    async def _run(self) -> None:
        assert self._ready is not None
        starting = True
        pending: _Request | None = None
        try:
            async with asyncio.timeout(self._config.timeout_seconds) as deadline:
                async with self._factory(
                    self._parameters, read_timeout_seconds=self._config.timeout_seconds
                ) as session:
                    starting = False
                    self._tools = await self._discover(session)
                    deadline.reschedule(None)
                    self._ready.set_result(self._tools)
                    while (pending := await self._queue.get()) is not None:
                        try:
                            async with asyncio.timeout(self._config.timeout_seconds):
                                response = await session.call_tool(pending.name, pending.arguments)
                            value = self._parse(response)
                        except Exception as error:
                            pending.result.set_exception(_failure(error))
                        else:
                            pending.result.set_result(value)
        except BaseException as error:
            self._error = _failure(error, starting=starting)
            if not self._ready.done():
                self._ready.set_exception(self._error)
            if pending is not None and not pending.result.done():
                pending.result.set_exception(self._error)
            while not self._queue.empty():
                queued = self._queue.get_nowait()
                if queued is not None and not queued.result.done():
                    queued.result.set_exception(self._error)

    async def _discover(self, session: _Session) -> tuple[McpDiscoveredTool, ...]:
        cursor = None
        discovered: dict[str, McpDiscoveredTool] = {}
        for _ in range(10):
            page = await session.list_tools(cursor=cursor)
            if len(discovered) + len(page.tools) > 100:
                raise McpClientError("mcp_discovery_limit")
            for tool in page.tools:
                if tool.name not in _PERMISSIONS or tool.name in discovered:
                    raise McpClientError("mcp_discovery_rejected")
                discovered[tool.name] = McpDiscoveredTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.input_schema,
                    permission=_PERMISSIONS[tool.name],
                )
            cursor = page.next_cursor
            if cursor is None:
                return tuple(discovered.values())
        raise McpClientError("mcp_discovery_limit")

    def _parse(self, result: CallToolResult) -> dict[str, object]:
        if result.is_error is not False:
            raise McpClientError("mcp_tool_error")
        if any(not isinstance(block, TextContent) for block in result.content):
            raise McpClientError("mcp_result_content")
        if not isinstance(result.structured_content, dict):
            raise McpClientError("mcp_result_object")
        try:
            encoded = json.dumps(
                result.structured_content,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise McpClientError("mcp_result_object") from None
        if len(encoded) > self._config.maximum_result_bytes:
            raise McpClientError("mcp_result_too_large")
        return result.structured_content
