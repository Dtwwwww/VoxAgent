from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


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
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": messages,
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
