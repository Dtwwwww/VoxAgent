from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from voxagent.conversation.history import (
    SYSTEM_INSTRUCTION,
    ChatMessage,
    TrustedSystemMessage,
)

ModelMessage = ChatMessage | TrustedSystemMessage | Mapping[str, str]


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
    serialized: list[dict[str, str]] = [{"role": "system", "content": system}]
    for message in messages:
        if isinstance(message, TrustedSystemMessage):
            continue
        payload = _message_payload(message)
        if payload["role"] != "system":
            serialized.append(payload)
    return serialized


def _message_payload(message: ChatMessage | Mapping[str, str]) -> dict[str, str]:
    if isinstance(message, ChatMessage):
        return message.as_payload()
    role: Any = message.get("role")
    content: Any = message.get("content")
    if role not in {"system", "user", "assistant"} or not isinstance(content, str):
        raise ValueError("Ollama messages require only a valid role and string content")
    return {"role": role, "content": content}
