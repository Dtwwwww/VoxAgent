from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from voxagent.config import AppPaths
from voxagent.db.migrations import migrate
from voxagent.mcp.capability import CapabilityIssuer
from voxagent.mcp.client import McpClientError, McpLocalClient, McpToolError
from voxagent.tools.builtin import build_builtin_registry
from voxagent.tools.confirmation import ConfirmationService
from voxagent.tools.policy import PolicyContext
from voxagent.tools.repository import ToolRepository
from voxagent.tools.schema import ToolCall


@dataclass(frozen=True, slots=True)
class McpDemoReport:
    tools: tuple[str, ...]
    read_status: str
    unapproved_error: str
    approved_status: str
    replay_error: str
    reminder_count: int
    audit_events: tuple[str, ...]


class _EmptyKnowledgeSource:
    def search_knowledge(self, query: str, limit: int) -> list[object]:
        return []


def _create_paths(data_root: Path) -> AppPaths:
    paths = AppPaths.from_root(data_root.resolve())
    paths.create()
    return paths


async def run_demo(data_root: Path) -> McpDemoReport:
    paths = _create_paths(data_root)
    connection = sqlite3.connect(
        paths.data / "voxagent.db", timeout=5, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    issuer = CapabilityIssuer()
    client = McpLocalClient(paths.root, issuer)
    now = datetime.now(UTC)
    try:
        migrate(connection)
        discovered = await client.start()
        tools = tuple(sorted(item.name for item in discovered))

        read = await client.call("reminders.list", {})
        read_status = str(read.get("status", ""))

        arguments: dict[str, object] = {"title": "MCP demo reminder", "due_at_utc": None}
        try:
            await client.call("reminders.create", arguments)
        except McpToolError as error:
            unapproved_error = error.code
        else:
            raise AssertionError("unapproved MCP write executed")

        registry = build_builtin_registry(
            connection, _EmptyKnowledgeSource(), None, now_utc=lambda: now
        )
        call = ToolCall.from_untrusted(
            registry.get("reminders.create")[0], "mcp-demo-write", arguments
        )
        repository = ToolRepository(connection)
        confirmation = ConfirmationService(repository, registry)
        ticket = confirmation.request(
            call,
            PolicyContext("mcp-demo", 1, (), 0, 0, False),
            now,
        )
        approved = confirmation.approve(
            ticket.confirmation_id, "mcp-demo", 1, datetime.now(UTC)
        )
        capability = issuer.issue(
            ticket.tool_request_id,
            approved.name,
            approved.arguments,
            datetime.now(UTC),
        )
        wire_arguments = dict(approved.arguments)
        wire_arguments["_voxagent_capability"] = capability
        result = await client.call(approved.name, wire_arguments)
        approved_status = str(result.get("status", ""))

        try:
            await client.call(approved.name, wire_arguments)
        except McpToolError as error:
            replay_error = error.code
        else:
            raise AssertionError("replayed MCP capability executed")

        reminder_count = int(connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0])
        audit_events = tuple(record.event_type for record in repository.list_audit())
        report = McpDemoReport(
            tools=tools,
            read_status=read_status,
            unapproved_error=unapproved_error,
            approved_status=approved_status,
            replay_error=replay_error,
            reminder_count=reminder_count,
            audit_events=audit_events,
        )
        if (
            read_status != "succeeded"
            or unapproved_error != "mcp_capability_rejected"
            or approved_status != "succeeded"
            or replay_error != "mcp_capability_rejected"
            or reminder_count != 1
            or "request.succeeded" not in audit_events
        ):
            raise AssertionError("MCP demo assertions failed")
        return report
    finally:
        await client.close()
        connection.close()


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="voxagent-mcp-demo-") as directory:
            report = asyncio.run(run_demo(Path(directory)))
    except AssertionError as error:
        print(f"MCP demo failed: {error}")
        return 1
    except (McpClientError, OSError, RuntimeError) as error:
        print(f"MCP demo environment error: {error}")
        return 2

    print("MCP tools: " + ", ".join(report.tools))
    print(f"L0 reminders.list: {report.read_status}")
    print(f"L2 without capability: {report.unapproved_error}")
    print(f"L2 approved once: {report.approved_status}")
    print(f"L2 replay: {report.replay_error}")
    print("Audit: request.succeeded")
    print("MCP demo passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
