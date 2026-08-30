from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter

from voxagent.llm.ollama import OllamaClient

LLM_BENCHMARK_PROMPTS = (
    "请用两句自然中文介绍你自己，每句不超过二十个字。",
    "用户说他喜欢喝无糖咖啡。请只输出一条适合长期保存的记忆。",
    "用户要求打开记事本。请说明需要调用工具，不要声称已经完成。",
)


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
