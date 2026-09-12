from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PermissionLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_.]{2,63}$")
    description: str = Field(min_length=1, max_length=300)
    permission: PermissionLevel
    arguments_model: type[BaseModel]
    timeout_seconds: float = Field(gt=0, le=30)
    provider: Literal["native", "mcp"]

    def ollama_payload(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=128)
    name: str
    arguments: dict[str, Any]

    @classmethod
    def from_untrusted(
        cls,
        definition: ToolDefinition,
        call_id: str,
        arguments: object,
    ) -> ToolCall:
        try:
            parsed = definition.arguments_model.model_validate(arguments, strict=True)
        except ValidationError as error:
            raise ValidationError.from_exception_data(
                "tool arguments",
                error.errors(include_url=False),
            ) from error
        return cls(
            call_id=call_id,
            name=definition.name,
            arguments=parsed.model_dump(mode="json"),
        )


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool_name: str
    status: Literal["succeeded", "failed", "denied", "expired"]
    data: dict[str, Any] = Field(default_factory=dict)
    user_summary: str
    error_code: str | None = None
    duration_ms: int = Field(ge=0)


ToolExecutor = Callable[[ToolCall], Awaitable[ToolResult]]
