from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from voxagent.conversation.history import (
    SYSTEM_INSTRUCTION,
    ChatMessage,
    TrustedSystemMessage,
)
from voxagent.tools.schema import ToolCall

ToolPayload = Mapping[str, object]


@dataclass(frozen=True)
class AssistantTextDelta:
    delta: str


@dataclass(frozen=True)
class AssistantToolCall:
    call: ToolCall


@dataclass(frozen=True)
class AssistantStreamDone:
    pass


@dataclass(frozen=True)
class TrustedAssistantToolCallMessage:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TrustedToolResultMessage:
    call_id: str
    tool_name: str
    content: str


ModelMessage = (
    ChatMessage
    | TrustedSystemMessage
    | TrustedAssistantToolCallMessage
    | TrustedToolResultMessage
    | Mapping[str, str]
)
AssistantStreamEvent = AssistantTextDelta | AssistantToolCall | AssistantStreamDone


class OllamaStreamError(RuntimeError):
    """The Ollama service terminated a stream with a model/runtime error."""

    def __init__(self, model: str, service_error: str) -> None:
        self.model = model
        self.service_error = service_error
        super().__init__(f"Ollama model {model} failed: {service_error}")


class OllamaProtocolError(RuntimeError):
    """The Ollama service returned an incomplete or invalid NDJSON stream."""


class OllamaClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def stream_chat(
        self,
        model: str,
        messages: Sequence[ModelMessage],
    ) -> AsyncIterator[str]:
        serialized = _serialize_messages(messages)
        payload = {
            "model": model,
            "messages": serialized,
            "stream": True,
            "think": False,
            "options": {"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8},
        }
        async with self._http.stream("POST", "/api/chat", json=payload, timeout=120) as response:
            response.raise_for_status()
            terminal_done = False
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError) as error:
                    raise OllamaProtocolError(
                        f"Ollama model {model} returned invalid NDJSON"
                    ) from error
                if not isinstance(item, dict):
                    raise OllamaProtocolError(
                        f"Ollama model {model} returned a non-object NDJSON frame"
                    )
                service_error = item.get("error")
                if service_error:
                    raise OllamaStreamError(model, str(service_error))
                content = item.get("message", {}).get("content", "")
                if content:
                    yield content
                if item.get("done") is True:
                    terminal_done = True
                    break
            if not terminal_done:
                raise OllamaProtocolError(
                    f"Ollama model {model} stream ended without a terminal done frame"
                )

    async def stream_agent(
        self,
        model: str,
        messages: Sequence[ModelMessage],
        tools: Sequence[ToolPayload],
    ) -> AsyncIterator[AssistantStreamEvent]:
        payload = {
            "model": model,
            "messages": _serialize_messages(messages),
            "tools": [dict(tool) for tool in tools],
            "stream": True,
            "think": False,
            "options": {"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8},
        }
        async with self._http.stream("POST", "/api/chat", json=payload, timeout=120) as response:
            response.raise_for_status()
            terminal_done = False
            tool_call_count = 0
            generated_id = 0
            async for line in response.aiter_lines():
                if not line:
                    continue
                item = _decode_ndjson_frame(model, line)
                service_error = item.get("error")
                if service_error:
                    raise OllamaStreamError(model, str(service_error))
                message = item.get("message")
                if not isinstance(message, dict):
                    raise OllamaProtocolError(
                        f"Ollama model {model} returned a malformed message frame"
                    )
                content = message.get("content", "")
                if not isinstance(content, str):
                    raise OllamaProtocolError(
                        f"Ollama model {model} returned a malformed message frame"
                    )
                tool_calls = message.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    raise OllamaProtocolError(
                        f"Ollama model {model} returned a malformed message frame"
                    )
                if content:
                    yield AssistantTextDelta(content)
                if tool_calls:
                    for raw_call in tool_calls:
                        tool_call_count += 1
                        if tool_call_count > 3:
                            raise OllamaProtocolError(
                                f"Ollama model {model} returned too many tool calls"
                            )
                        if not (
                            isinstance(raw_call, dict)
                            and isinstance(raw_call.get("id"), str)
                            and raw_call["id"]
                        ):
                            generated_id += 1
                        yield AssistantToolCall(
                            _parse_tool_call(model, raw_call, f"tool-{generated_id}")
                        )
                if item.get("done") is True:
                    terminal_done = True
                    yield AssistantStreamDone()
                    break
            if not terminal_done:
                raise OllamaProtocolError(
                    f"Ollama model {model} stream ended without a terminal done frame"
                )

    async def complete_json(
        self,
        model: str,
        messages: Sequence[ModelMessage],
        schema: dict[str, object],
    ) -> str:
        payload = {
            "model": model,
            "messages": _serialize_messages(messages),
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"num_ctx": 8192, "temperature": 0},
        }
        response = await self._http.post("/api/chat", json=payload, timeout=120)
        response.raise_for_status()
        try:
            item = response.json()
        except (json.JSONDecodeError, TypeError) as error:
            raise OllamaProtocolError(
                f"Ollama model {model} returned invalid JSON"
            ) from error
        if not isinstance(item, dict):
            raise OllamaProtocolError(f"Ollama model {model} returned a non-object response")
        if item.get("error"):
            raise OllamaStreamError(model, str(item["error"]))
        content = item.get("message", {}).get("content")
        if item.get("done") is not True or not isinstance(content, str):
            raise OllamaProtocolError(f"Ollama model {model} returned incomplete JSON output")
        return content


def _serialize_messages(messages: Sequence[ModelMessage]) -> list[dict[str, str]]:
    trusted = [
        message.content for message in messages if isinstance(message, TrustedSystemMessage)
    ]
    system = SYSTEM_INSTRUCTION
    if trusted:
        system += "\n\n" + "\n\n".join(trusted)
    serialized: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if isinstance(message, TrustedSystemMessage):
            continue
        if isinstance(message, TrustedAssistantToolCallMessage):
            serialized.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": message.call_id,
                            "type": "function",
                            "function": {
                                "name": message.name,
                                "arguments": message.arguments,
                            },
                        }
                    ],
                }
            )
            continue
        if isinstance(message, TrustedToolResultMessage):
            serialized.append(
                {
                    "role": "tool",
                    "tool_name": message.tool_name,
                    "tool_call_id": message.call_id,
                    "content": message.content,
                }
            )
            continue
        if isinstance(message, Mapping) and message.get("role") == "tool":
            continue
        payload = _message_payload(message)
        if payload["role"] != "system":
            serialized.append(payload)
    return serialized


def _message_payload(message: ChatMessage | Mapping[str, str]) -> dict[str, str]:
    if isinstance(message, ChatMessage):
        role: Any = message.role
        content: Any = message.content
    else:
        role = message.get("role")
        content = message.get("content")
    if role not in {"system", "user", "assistant"} or not isinstance(content, str):
        raise ValueError("Ollama messages require only a valid role and string content")
    return {"role": role, "content": content}


def _decode_ndjson_frame(model: str, line: str) -> dict[str, Any]:
    try:
        item = json.loads(line)
    except (json.JSONDecodeError, TypeError) as error:
        raise OllamaProtocolError(
            f"Ollama model {model} returned invalid NDJSON"
        ) from error
    if not isinstance(item, dict):
        raise OllamaProtocolError(
            f"Ollama model {model} returned a non-object NDJSON frame"
        )
    return item


def _parse_tool_call(model: str, raw_call: object, fallback_id: str) -> ToolCall:
    if not isinstance(raw_call, dict):
        raise OllamaProtocolError(f"Ollama model {model} returned malformed tool call")
    function = raw_call.get("function")
    if not isinstance(function, dict):
        raise OllamaProtocolError(f"Ollama model {model} returned malformed tool call")
    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name or not isinstance(arguments, dict):
        raise OllamaProtocolError(f"Ollama model {model} returned malformed tool call")
    raw_id = raw_call.get("id")
    call_id = raw_id if isinstance(raw_id, str) and raw_id else fallback_id
    try:
        return ToolCall(call_id=call_id, name=name, arguments=arguments)
    except ValidationError as error:
        raise OllamaProtocolError(
            f"Ollama model {model} returned malformed tool call"
        ) from error
