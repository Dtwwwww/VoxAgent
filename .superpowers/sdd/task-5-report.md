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

- `backend/tests/llm/test_ollama.py`: 15 passed.
- Ruff on changed module and test: passed.
- `git diff --check`: passed.
- Full repository suite intentionally not run, per MVP scope.
