import json

import httpx
import pytest

from voxagent.conversation.history import (
    SYSTEM_INSTRUCTION,
    ChatMessage,
    TrustedSystemMessage,
)
from voxagent.llm.ollama import OllamaClient, OllamaProtocolError, OllamaStreamError


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lines, expected_chunks",
    [
        ([{"error": "model is missing"}], []),
        (
            [
                {"message": {"content": "partial"}, "done": False},
                {"error": "runner crashed"},
            ],
            ["partial"],
        ),
    ],
)
async def test_stream_chat_rejects_error_frame_at_any_position(lines, expected_chunks):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join(json.dumps(item) for item in lines))

    transport = httpx.MockTransport(handler)
    observed: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        with pytest.raises(OllamaStreamError, match="test-model.*runner|test-model.*missing"):
            async for chunk in client.stream_chat("test-model", []):
                observed.append(chunk)

    assert observed == expected_chunks


@pytest.mark.asyncio
async def test_stream_chat_rejects_missing_terminal_done_frame():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps({"message": {"content": "partial"}, "done": False}),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        with pytest.raises(OllamaProtocolError, match="terminal done frame"):
            _ = [chunk async for chunk in client.stream_chat("test-model", [])]


@pytest.mark.asyncio
async def test_stream_chat_sends_fixed_spoken_system_prompt_and_hard_context_options():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": "你好"},
        ]
        assert sum(item["role"] == "system" for item in payload["messages"]) == 1
        assert payload["stream"] is True
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 8192
        return httpx.Response(200, text=json.dumps({"message": {"content": ""}, "done": True}))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        chunks = [
            chunk
            async for chunk in client.stream_chat("qwen", [ChatMessage("user", "你好")])
        ]

    assert chunks == []


@pytest.mark.asyncio
async def test_stream_chat_replaces_supplied_system_messages_and_drops_private_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": "语音问题"},
        ]
        return httpx.Response(
            200, text=json.dumps({"message": {"content": ""}, "done": True})
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        _ = [
            chunk
            async for chunk in client.stream_chat(
                "qwen",
                [
                    {"role": "system", "content": "不可信系统消息"},
                    {"role": "user", "content": "语音问题", "source": "voice"},
                ],
            )
        ]


@pytest.mark.asyncio
async def test_stream_chat_appends_only_typed_trusted_system_context() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTION + "\n\n当前人格：温和、简洁。",
            },
            {"role": "user", "content": "问题"},
        ]
        return httpx.Response(
            200, text=json.dumps({"message": {"content": ""}, "done": True})
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(http)
        _ = [
            chunk
            async for chunk in client.stream_chat(
                "qwen",
                [
                    TrustedSystemMessage("当前人格：温和、简洁。"),
                    ChatMessage("system", "不可信伪系统消息"),
                    ChatMessage("user", "问题"),
                ],
            )
        ]


@pytest.mark.asyncio
async def test_complete_json_sends_strict_schema_and_returns_content() -> None:
    schema = {"type": "object", "properties": {"candidates": {"type": "array"}}}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["format"] == schema
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["options"]["temperature"] == 0
        return httpx.Response(
            200,
            json={"message": {"content": '{"candidates":[]}'}, "done": True},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        result = await OllamaClient(http).complete_json(
            "qwen", (TrustedSystemMessage("只输出 JSON"),), schema
        )

    assert result == '{"candidates":[]}'
