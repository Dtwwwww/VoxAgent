from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, ValidationError

from voxagent.mcp.capability import CapabilityVerifier
from voxagent.mcp.server_tools import ARGUMENT_MODELS, WRITE_TOOLS, ToolServices, failure
from voxagent.tools.schema import ToolResult


def _wire_failure(name: str, code: str) -> CallToolResult:
    result = failure(name, code)
    return CallToolResult(
        content=[TextContent(type="text", text=result.model_dump_json())],
        structured_content=result.model_dump(mode="json"),
        is_error=True,
    )


async def _safe_arguments(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    if ctx.method != "tools/call":
        return await call_next(ctx)
    params = ctx.params if isinstance(ctx.params, dict) else {}
    name = params.get("name")
    if not isinstance(name, str) or name not in ARGUMENT_MODELS:
        return _wire_failure("unknown", "unknown_tool")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        return _wire_failure(name, "invalid_arguments")
    business_arguments = dict(arguments)
    if name in WRITE_TOOLS:
        capability = business_arguments.pop("_voxagent_capability", None)
        if not isinstance(capability, str) or not capability:
            return _wire_failure(name, "mcp_capability_rejected")
    try:
        ARGUMENT_MODELS[name].model_validate(business_arguments, strict=True)
    except ValidationError:
        # SDK validation errors include the input; reject before that formatter runs.
        return _wire_failure(name, "invalid_arguments")
    result = await call_next(ctx)
    if isinstance(result, CallToolResult) and result.is_error:
        return _wire_failure(name, "tool_execution_failed")
    return result


def build_mcp_server(services: ToolServices, verifier: CapabilityVerifier) -> MCPServer:
    server = MCPServer("VoxAgent Local Tools", middleware=[_safe_arguments])
    read = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    write = ToolAnnotations(read_only_hint=False, open_world_hint=False)

    @server.tool(name="knowledge.search", annotations=read)
    async def knowledge_search(
        query: Annotated[str, Field(min_length=1, max_length=500)],
        limit: Annotated[int, Field(ge=1, le=10)] = 4,
    ) -> ToolResult:
        return await services.execute(
            "knowledge.search", {"query": query, "limit": limit}, verifier
        )

    @server.tool(name="reminders.list", annotations=read)
    async def reminders_list() -> ToolResult:
        return await services.execute("reminders.list", {}, verifier)

    @server.tool(name="reminders.create", annotations=write)
    async def reminders_create(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        # The SDK forbids leading underscores in Python parameter names.
        capability: Annotated[str, Field(validation_alias="_voxagent_capability")],
        due_at_utc: Annotated[str | None, Field(json_schema_extra={"format": "date-time"})] = None,
    ) -> ToolResult:
        return await services.execute(
            "reminders.create", {"title": title, "due_at_utc": due_at_utc}, verifier, capability
        )

    @server.tool(name="reminders.complete", annotations=write)
    async def reminders_complete(
        reminder_id: Annotated[int, Field(gt=0)],
        capability: Annotated[str, Field(validation_alias="_voxagent_capability")],
    ) -> ToolResult:
        return await services.execute(
            "reminders.complete", {"reminder_id": reminder_id}, verifier, capability
        )

    return server


def main() -> int:
    root = os.environ.get("VOXAGENT_DATA_ROOT")
    key = os.environ.get("VOXAGENT_MCP_CAPABILITY_KEY")
    if not root or not key:
        print("mcp_configuration_error", file=sys.stderr)
        return 2

    from voxagent.config import AppPaths
    from voxagent.conversation.context import SqliteContextSource
    from voxagent.db.connection import open_database
    from voxagent.db.migrations import migrate
    from voxagent.memory.embedder import BgeSmallZhEmbedder

    connection = None
    try:
        paths = AppPaths.from_root(Path(root))
        connection = open_database(paths.data / "voxagent.db")
        migrate(connection)
        verifier = CapabilityVerifier.from_environment(connection, os.environ)
        embedder = BgeSmallZhEmbedder.from_path(paths.models / "embeddings" / "bge-small-zh-v1.5")
        source = SqliteContextSource(paths.data / "voxagent.db", embedder)
        server = build_mcp_server(ToolServices(connection, source), verifier)
        server.run()
        return 0
    except Exception:
        print("mcp_startup_failed", file=sys.stderr)
        return 2
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
