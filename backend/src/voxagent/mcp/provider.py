from __future__ import annotations

import sqlite3
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter

from pydantic import ValidationError

from voxagent.mcp.capability import CapabilityIssuer
from voxagent.mcp.client import McpClientError, McpLocalClient, McpToolError
from voxagent.mcp.models import McpDiscoveredTool
from voxagent.tools.app_launcher import Launcher, build_app_launcher_executor
from voxagent.tools.builtin import builtin_definitions
from voxagent.tools.confirmation import arguments_sha256
from voxagent.tools.file_search import build_file_search_executor
from voxagent.tools.knowledge_search import KnowledgeSource
from voxagent.tools.policy import AuthorizationDecision
from voxagent.tools.registry import ToolRegistry
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition, ToolResult

_REMOTE_NAMES = frozenset(
    {"knowledge.search", "reminders.list", "reminders.create", "reminders.complete"}
)


class McpToolProvider:
    def __init__(
        self,
        client: McpLocalClient,
        issuer: CapabilityIssuer,
        connection: sqlite3.Connection,
        discovered_tools: tuple[McpDiscoveredTool, ...],
        *,
        now_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._issuer = issuer
        self._connection = connection
        self._now_utc = now_utc
        native = {item.name: item for item in builtin_definitions()}
        if (
            len(discovered_tools) != len(_REMOTE_NAMES)
            or {item.name for item in discovered_tools} != _REMOTE_NAMES
        ):
            raise McpClientError("mcp_discovery_rejected")
        definitions = {}
        for discovered in discovered_tools:
            definition = native[discovered.name]
            schema = deepcopy(discovered.input_schema)
            properties = schema.get("properties")
            required = schema.get("required", [])
            if not isinstance(properties, dict) or not isinstance(required, list):
                raise McpClientError("mcp_discovery_rejected")
            properties.pop("_voxagent_capability", None)
            schema["required"] = [item for item in required if item != "_voxagent_capability"]
            if discovered.permission != definition.permission or set(properties) != set(
                definition.arguments_model.model_fields
            ):
                raise McpClientError("mcp_discovery_rejected")
            # This is a fixed local tool set. Keep the same trusted business models
            # and constraints as native; transport credentials never enter them.
            definitions[definition.name] = definition.model_copy(update={"provider": "mcp"})
        self._definitions = definitions

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    async def execute(
        self, call: ToolCall, authorization: AuthorizationDecision | None
    ) -> ToolResult:
        started = perf_counter()
        definition = self._definitions.get(call.name)
        if definition is None:
            return self._failure(call, "unknown_tool", started)
        try:
            validated = ToolCall.from_untrusted(definition, call.call_id, call.arguments)
        except ValidationError:
            return self._failure(call, "invalid_arguments", started)
        if not self._authorized(validated, definition, authorization):
            return self._failure(call, "mcp_authorization_rejected", started, denied=True)
        arguments = dict(validated.arguments)
        if definition.permission is not PermissionLevel.L0:
            assert authorization is not None and authorization.tool_request_id is not None
            arguments["_voxagent_capability"] = self._issuer.issue(
                authorization.tool_request_id, validated.name, arguments, self._now_utc()
            )
        try:
            payload = await self._client.call(validated.name, arguments)
            result = ToolResult.model_validate(payload)
        except McpToolError as error:
            result = error.result
        except McpClientError as error:
            return self._failure(call, error.code, started)
        except ValidationError:
            return self._failure(call, "mcp_result_object", started)
        if result.tool_name != call.name:
            return self._failure(call, "mcp_result_object", started)
        return result.model_copy(update={"call_id": call.call_id})

    def _authorized(
        self,
        call: ToolCall,
        definition: ToolDefinition,
        authorization: AuthorizationDecision | None,
    ) -> bool:
        if (
            authorization is None
            or authorization.action != "execute"
            or type(authorization.tool_request_id) is not int
        ):
            return False
        return (
            self._connection.execute(
                """
            SELECT 1 FROM tool_requests AS request
            WHERE request.id = ? AND request.call_id = ? AND request.tool_name = ?
              AND request.arguments_sha256 = ? AND request.permission = ?
              AND request.status = 'running'
              AND (request.permission = 'L0' OR EXISTS (
                  SELECT 1 FROM tool_confirmations AS confirmation
                  WHERE confirmation.tool_request_id = request.id
                    AND confirmation.arguments_sha256 = request.arguments_sha256
                    AND confirmation.decision = 'approved'
                    AND confirmation.consumed_at_utc IS NOT NULL
              ))
            """,
                (
                    authorization.tool_request_id,
                    call.call_id,
                    call.name,
                    arguments_sha256(call.arguments),
                    definition.permission.value,
                ),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _failure(call: ToolCall, code: str, started: float, *, denied: bool = False) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status="denied" if denied else "failed",
            user_summary="工具未获授权。" if denied else "工具执行失败。",
            error_code=code,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
        )


def build_mcp_registry(
    connection: sqlite3.Connection,
    knowledge_source: KnowledgeSource,
    launcher: Launcher,
    provider: McpToolProvider,
) -> ToolRegistry:
    # The two host-only tools retain their native executors and policy levels.
    registry = ToolRegistry()
    registry.register_provider(provider)
    executors = {
        "files.search_authorized": build_file_search_executor(connection),
        "apps.open_allowlisted": build_app_launcher_executor(launcher),
    }
    for definition in builtin_definitions():
        if definition.name in executors:
            registry.register(definition, executors[definition.name])
    registry.freeze()
    return registry
