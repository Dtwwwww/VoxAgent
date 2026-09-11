# Task 5: Ollama Agent Tool-Call Stream

## Scope

Added the smallest streaming interface required by the Agent loop without changing
the existing `stream_chat` or `complete_json` contracts.

## TDD evidence

- RED: `pytest backend/tests/llm/test_ollama.py -q -k keeps_provider_id` failed
  because a provider-supplied id consumed the generated-id counter (`tool-2` was
  returned instead of `tool-1`).
- GREEN: generated ids now advance only when the provider omits a usable id;
  the focused test passed after the change.

## Delivered

- Frozen assistant stream event types for text deltas, tool calls, and terminal
  completion.
- Trusted-only continuation message types for assistant tool calls and tool
  results; raw `system` and `tool` mappings do not enter the provider payload.
- `OllamaClient.stream_agent()` posts tools with `stream=true`, `think=false`,
  and `num_ctx=8192`, parses NDJSON tool calls, assigns deterministic fallback
  ids, emits one terminal event, and rejects malformed or over-limit calls.

## Verification

- Follow-up RED: five focused cases failed before the review fixes (missing or
  malformed `message`, oversized provider call id, and a forged `ChatMessage`
  tool role).
- `backend/tests/llm/test_ollama.py`: 20 passed after the fixes.
- Ruff on changed module and test: passed.
- `git diff --check`: passed.
- Full repository suite intentionally not run, per MVP scope.

## Review fixes

- Agent stream frames now require a mapping `message`; present `content` and
  `tool_calls` values must respectively be a string and a list.
- Pydantic validation failures while constructing a provider `ToolCall` are
  relabeled as `OllamaProtocolError`.
- `ChatMessage` receives the same runtime role/content validation as raw
  mappings, so a forged `tool` role cannot reach the provider payload.
