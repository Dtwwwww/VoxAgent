from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter

from voxagent.llm.ollama import OllamaClient


@dataclass(frozen=True, slots=True)
class LlmRun:
    prompt: str
    ttft_seconds: float
    total_seconds: float
    text: str


@dataclass(frozen=True, slots=True)
class LlmBenchmark:
    model: str
    runs: tuple[LlmRun, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


async def run_llm_benchmark(
    client: OllamaClient,
    model: str,
    prompts: tuple[str, ...],
) -> LlmBenchmark:
    runs: list[LlmRun] = []
    for prompt in prompts:
        started = perf_counter()
        first_chunk_at: float | None = None
        chunks: list[str] = []
        async for chunk in client.stream_chat(model, [{"role": "user", "content": prompt}]):
            if first_chunk_at is None:
                first_chunk_at = perf_counter()
            chunks.append(chunk)
        finished = perf_counter()
        if first_chunk_at is None:
            raise RuntimeError(f"Model {model} returned no text for prompt: {prompt}")
        runs.append(
            LlmRun(
                prompt=prompt,
                ttft_seconds=round(first_chunk_at - started, 3),
                total_seconds=round(finished - started, 3),
                text="".join(chunks),
            )
        )
    return LlmBenchmark(model=model, runs=tuple(runs))
