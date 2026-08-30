import pytest

from voxagent.diagnostics.llm_benchmark import run_llm_benchmark


class FakeClient:
    async def stream_chat(self, model, messages):
        assert model == "test-model"
        assert messages[-1]["role"] == "user"
        yield "第一段"
        yield "第二段"


@pytest.mark.asyncio
async def test_benchmark_records_every_prompt(monkeypatch):
    times = iter([10.0, 10.4, 10.8, 20.0, 20.3, 20.7])
    monkeypatch.setattr("voxagent.diagnostics.llm_benchmark.perf_counter", lambda: next(times))
    result = await run_llm_benchmark(FakeClient(), "test-model", ("你好", "打开记事本"))
    assert len(result.runs) == 2
    assert result.runs[0].ttft_seconds == pytest.approx(0.4)
    assert result.runs[0].total_seconds == pytest.approx(0.8)
    assert result.runs[0].text == "第一段第二段"
