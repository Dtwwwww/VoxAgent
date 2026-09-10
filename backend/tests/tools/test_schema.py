from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from voxagent.tools.schema import (
    PermissionLevel,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class QueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def make_definition() -> ToolDefinition:
    return ToolDefinition(
        name="knowledge.search",
        description="Search imported knowledge",
        permission=PermissionLevel.L0,
        arguments_model=QueryArgs,
        timeout_seconds=3.0,
        provider="native",
    )


def test_tool_call_rejects_extra_arguments() -> None:
    with pytest.raises(ValueError, match="arguments"):
        ToolCall.from_untrusted(
            make_definition(),
            "call-1",
            {"query": "声灵", "extra": 1},
        )


def test_tool_call_validates_arguments_strictly() -> None:
    with pytest.raises(ValidationError):
        ToolCall.from_untrusted(make_definition(), "call-1", {"query": 1})


def test_tool_definition_exports_ollama_function_schema() -> None:
    payload = make_definition().ollama_payload()

    assert payload["type"] == "function"
    assert payload["function"]["name"] == "knowledge.search"
    assert payload["function"]["description"] == "Search imported knowledge"
    assert payload["function"]["parameters"]["additionalProperties"] is False
    assert payload["function"]["parameters"]["required"] == ["query"]


@pytest.mark.parametrize("name", ["A.bad", "x", "bad-name", "a" * 65])
def test_tool_definition_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(
            name=name,
            description="Invalid",
            permission=PermissionLevel.L0,
            arguments_model=QueryArgs,
            timeout_seconds=3.0,
            provider="native",
        )


def test_tool_result_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            call_id="call-1",
            tool_name="knowledge.search",
            status="failed",
            user_summary="失败",
            duration_ms=-1,
        )
