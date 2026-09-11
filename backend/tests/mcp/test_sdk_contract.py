from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer

from voxagent.mcp.models import (
    McpDiscoveredTool,
    McpProviderConfig,
    tool_provider_from_environment,
)
from voxagent.tools.schema import PermissionLevel


def test_mcp_v2_surface_is_available() -> None:
    assert Client is not None
    assert StdioServerParameters is not None
    assert MCPServer is not None
    assert callable(MCPServer.run)


def test_local_provider_config_uses_the_fixed_stdio_boundary() -> None:
    config = McpProviderConfig.local_default()

    assert config.command == sys.executable
    assert config.args == ("-m", "voxagent.mcp.server")
    assert config.cwd == Path(__file__).resolve().parents[2]
    assert config.environment == {}
    assert config.timeout_seconds == 5.0
    assert config.maximum_result_bytes == 65_536


def test_discovered_tool_keeps_protocol_metadata() -> None:
    tool = McpDiscoveredTool(
        name="reminders.list",
        description="List reminders",
        input_schema={"type": "object"},
        permission=PermissionLevel.L0,
    )

    assert tool.name == "reminders.list"
    assert tool.description == "List reminders"
    assert tool.input_schema == {"type": "object"}
    assert tool.permission is PermissionLevel.L0


def test_tool_provider_defaults_to_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOXAGENT_TOOL_PROVIDER", raising=False)

    assert tool_provider_from_environment() == "native"


def test_tool_provider_allows_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOXAGENT_TOOL_PROVIDER", "mcp")

    assert tool_provider_from_environment() == "mcp"


def test_tool_provider_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOXAGENT_TOOL_PROVIDER", "remote")

    with pytest.raises(ValueError, match="VOXAGENT_TOOL_PROVIDER"):
        tool_provider_from_environment()
