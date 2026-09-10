from __future__ import annotations

import asyncio
from time import perf_counter
from types import MappingProxyType

from voxagent.tools.schema import ToolCall, ToolDefinition, ToolExecutor, ToolResult


class ToolRegistryError(RuntimeError):
    """Base error for invalid tool registry operations."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a tool name is registered more than once."""


class FrozenToolRegistryError(ToolRegistryError):
    """Raised when an operation violates the registry freeze boundary."""


class UnknownToolError(ToolRegistryError):
    """Raised when a requested tool is not in the frozen registry."""


ToolRegistration = tuple[ToolDefinition, ToolExecutor]


class ToolRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, ToolRegistration] = {}
        self._frozen_registrations: MappingProxyType[str, ToolRegistration] | None = None

    def register(self, definition: ToolDefinition, executor: ToolExecutor) -> None:
        if self._frozen_registrations is not None:
            raise FrozenToolRegistryError("tool registry is frozen")
        if definition.name in self._registrations:
            raise DuplicateToolError(f"tool is already registered: {definition.name}")
        self._registrations[definition.name] = (definition, executor)

    def freeze(self) -> None:
        if self._frozen_registrations is None:
            self._frozen_registrations = MappingProxyType(dict(self._registrations))

    def get(self, name: str) -> ToolRegistration:
        if self._frozen_registrations is None:
            raise FrozenToolRegistryError("tool registry must freeze before access")
        try:
            return self._frozen_registrations[name]
        except KeyError as error:
            raise UnknownToolError(f"unknown tool: {name}") from error

    def definition_payloads(self) -> tuple[dict[str, object], ...]:
        if self._frozen_registrations is None:
            raise FrozenToolRegistryError("tool registry must freeze before access")
        return tuple(
            self._frozen_registrations[name][0].ollama_payload()
            for name in sorted(self._frozen_registrations)
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        definition, executor = self.get(call.name)
        validated_call = ToolCall.from_untrusted(
            definition,
            call.call_id,
            call.arguments,
        )
        started = perf_counter()
        try:
            async with asyncio.timeout(definition.timeout_seconds):
                return await executor(validated_call)
        except Exception:
            duration_ms = max(0, round((perf_counter() - started) * 1000))
            return ToolResult(
                call_id=validated_call.call_id,
                tool_name=validated_call.name,
                status="failed",
                user_summary="工具执行失败。",
                error_code="tool_execution_failed",
                duration_ms=duration_ms,
            )
