import json

import httpx
import pytest

from voxagent.llm.ollama import OllamaClient


@pytest.mark.asyncio
async def test_stream_chat_yields_only_content_chunks():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        lines = [
            json.dumps({"message": {"content": "你"}, "done": False}),
            json.dumps({"message": {"content": "好"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]
        return httpx.Response(200, text="\n".join(lines))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        chunks = [
            chunk
            async for chunk in client.stream_chat("qwen", [{"role": "user", "content": "你好"}])
        ]
    assert chunks == ["你", "好"]
