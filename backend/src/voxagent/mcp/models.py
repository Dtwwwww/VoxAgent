from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from voxagent.tools.schema import PermissionLevel

ToolProvider = Literal["native", "mcp"]


@dataclass(frozen=True, slots=True)
class McpProviderConfig:
    command: str
    args: tuple[str, ...]
    cwd: Path
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 5.0
    maximum_result_bytes: int = 65_536

    @classmethod
    def local_default(cls) -> McpProviderConfig:
        return cls(
            command=sys.executable,
            args=("-m", "voxagent.mcp.server"),
            cwd=Path(__file__).resolve().parents[3],
        )


@dataclass(frozen=True, slots=True)
class McpDiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, object]
    permission: PermissionLevel


def tool_provider_from_environment() -> ToolProvider:
    provider = os.environ.get("VOXAGENT_TOOL_PROVIDER", "native")
    if provider not in {"native", "mcp"}:
        raise ValueError("VOXAGENT_TOOL_PROVIDER must be 'native' or 'mcp'")
    return cast(ToolProvider, provider)
