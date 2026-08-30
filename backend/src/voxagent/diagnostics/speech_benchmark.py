from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import perf_counter

TTS_BENCHMARK_TEXTS = (
    "你好，我是声灵，很高兴陪你聊聊天。",
    "下午三点提醒我喝水，然后打开记事本。",
    "今天的 meeting 改到晚上八点，请不要忘记。",
)


@dataclass(frozen=True, slots=True)
class TimedResult[T]:
    label: str
    elapsed_seconds: float
    result: T

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def measure_call[T](label: str, operation: Callable[[], T]) -> TimedResult[T]:
    started = perf_counter()
    result = operation()
    finished = perf_counter()
    return TimedResult(label, round(finished - started, 3), result)
