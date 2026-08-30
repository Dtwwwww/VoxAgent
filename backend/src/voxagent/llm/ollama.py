from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


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
            async for line in response.aiter_lines():
                if not line:
                    continue
                item = json.loads(line)
                content = item.get("message", {}).get("content", "")
                if content:
                    yield content
