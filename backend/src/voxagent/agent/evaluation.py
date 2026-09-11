from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voxagent.conversation.history import ChatMessage
from voxagent.llm.ollama import AssistantTextDelta, AssistantToolCall, ToolPayload
from voxagent.tools.builtin import builtin_definitions
from voxagent.tools.schema import PermissionLevel, ToolCall, ToolDefinition


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1, max_length=4000)
    expected_tool: str | None
    expected_permission: PermissionLevel | None
    fake_tool: str | None
    fake_arguments: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fake_plan(self) -> EvaluationCase:
        if self.fake_tool is None and self.fake_arguments:
            raise ValueError("fake_arguments require fake_tool")
        return self


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    passed: bool
    observed_tool: str | None
    observed_permission: PermissionLevel | None
    error: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["fake", "local"]
    total: int
    passed: int
    unauthorized_executions: Literal[0] = 0
    results: tuple[EvaluationResult, ...]


class EvaluationModel(Protocol):
    def stream_agent(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolPayload],
    ): ...


def load_dataset(path: Path) -> tuple[EvaluationCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("evaluation dataset must be a non-empty JSON array")
    cases = tuple(EvaluationCase.model_validate(item) for item in raw)
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("evaluation case ids must be unique")
    return cases


async def evaluate_agent(
    cases: Sequence[EvaluationCase],
    *,
    mode: Literal["fake", "local"],
    model_name: str,
    model: EvaluationModel | None = None,
) -> EvaluationReport:
    definitions = {item.name: item for item in builtin_definitions()}
    results: list[EvaluationResult] = []
    for case in cases:
        if mode == "fake":
            call = (
                ToolCall(
                    call_id=f"eval-{case.id}",
                    name=case.fake_tool,
                    arguments=case.fake_arguments,
                )
                if case.fake_tool is not None
                else None
            )
            results.append(_score(case, call, definitions))
            continue
        if model is None:
            raise ValueError("local evaluation requires a model client")
        call: ToolCall | None = None
        text_seen = False
        async for event in model.stream_agent(
            model_name,
            (ChatMessage("user", case.prompt),),
            tuple(item.ollama_payload() for item in definitions.values()),
        ):
            if isinstance(event, AssistantToolCall):
                if call is not None:
                    results.append(
                        EvaluationResult(
                            id=case.id,
                            passed=False,
                            observed_tool=event.call.name,
                            observed_permission=None,
                            error="multiple_tool_calls",
                        )
                    )
                    break
                call = event.call
            elif isinstance(event, AssistantTextDelta) and event.delta.strip():
                text_seen = True
        else:
            result = _score(case, call, definitions)
            if call is None and case.expected_tool is None and not text_seen:
                result = result.model_copy(
                    update={"passed": False, "error": "empty_response"}
                )
            results.append(result)
    passed = sum(item.passed for item in results)
    return EvaluationReport(
        mode=mode,
        total=len(results),
        passed=passed,
        results=tuple(results),
    )


def _score(
    case: EvaluationCase,
    call: ToolCall | None,
    definitions: dict[str, ToolDefinition],
) -> EvaluationResult:
    observed_tool = call.name if call is not None else None
    definition = definitions.get(observed_tool) if observed_tool else None
    permission = definition.permission if definition is not None else None
    error: str | None = None
    if observed_tool != case.expected_tool:
        error = "unexpected_tool"
    elif observed_tool is not None and definition is None:
        error = "unknown_tool"
    elif call is not None and definition is not None:
        try:
            ToolCall.from_untrusted(definition, call.call_id, call.arguments)
        except Exception:
            error = "invalid_arguments"
    if error is None and permission != case.expected_permission:
        error = "unexpected_permission"
    return EvaluationResult(
        id=case.id,
        passed=error is None,
        observed_tool=observed_tool,
        observed_permission=permission,
        error=error,
    )
